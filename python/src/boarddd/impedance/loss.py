"""Loss and frequency dependence of PCB transmission lines (impedance phase I9), written for boarddd.

The Python twin of src/impedance/loss.js (same formulas, same names in snake_case; fixtures/impedance/loss-cases.json
keeps the two in step): Djordjevic-Sarkar dielectrics, skin effect with Hammerstad and Huray (cannonball)
roughness, Wheeler's incremental inductance rule per metal, Kirschning-Jansen microstrip and coupled-microstrip
dispersion, RLGC per mode, S-parameters and Touchstone. Each function cites its paper in the JS file and here;
docs/impedance.md ("Loss and frequency") has the method and the validation. No code from KiCad, Qucs or
js_2d_fields.

Units: geometry in mm, frequency in Hz, R Ω/m, L H/m, G S/m, C F/m, α Np/m. Stdlib only (cmath), so tier-1 loss
needs no extra; tier 2 (:func:`section_loss`) takes a :func:`~boarddd.impedance.fieldsolver.solve_cross_section`
result computed with ``loss=True``.
"""

from __future__ import annotations

import cmath
import math
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from math import atan, atan2, exp, log, pi, sqrt

from .closedform import ETA0, calculate

C_LIGHT = 299792458.0
EPS0 = 8.8541878128e-12
MU0 = 1 / (EPS0 * C_LIGHT * C_LIGHT)
NP_TO_DB = 20 / math.log(10)  # 8.686 dB per neper
INCH = 25.4  # mm

LOSS_DEFAULTS = {"conductivity": 5.8e7, "frequency": 1e9, "f_low": 1e3, "f_high": 1e12}
"""Annealed copper (S/m, IEC 60028); the frequency at which Er/Df are given when the source does not say; the
Djordjevic-Sarkar corner frequencies (Hz)."""


# ── dielectrics: Djordjevic-Sarkar ────────────────────────────────────────────────────────────────────────────
# A. R. Djordjevic, R. M. Biljić, V. D. Likar-Smiljanić, T. K. Sarkar, "Wideband frequency-domain characterization of
# FR-4 and time-domain causality", IEEE Trans. EMC 43(4), 2001, pp. 662-667:
#   ε(ω) = ε∞ + Δε / ln(ω2/ω1) · ln((ω2 + jω) / (ω1 + jω)), fitted to (Dk, Df) at a reference frequency.


def dielectric_at(f: float, m: dict) -> dict:
    """A dielectric's ``{"er", "tand"}`` at f (Hz). ``m``: er, tand at ``frequency`` (default 1 GHz), ``model``
    ('djordjevic_sarkar', the default, or 'constant'), ``f_low``, ``f_high``."""
    er = m["er"]
    tand = m.get("tand") or 0
    if er is None or not er >= 1:
        raise ValueError(f"er must be >= 1 (got {er})")
    if not tand >= 0:
        raise ValueError(f"tand must be >= 0 (got {tand})")
    model = m.get("model") or "djordjevic_sarkar"
    if model == "constant" or tand == 0:
        return {"er": er, "tand": tand}
    if model != "djordjevic_sarkar":
        raise ValueError(f"unknown dielectric model {model}")
    f0 = m.get("frequency") or LOSS_DEFAULTS["frequency"]
    f1 = m.get("f_low") or LOSS_DEFAULTS["f_low"]
    f2 = m.get("f_high") or LOSS_DEFAULTS["f_high"]
    if not (f2 > f1 > 0):
        raise ValueError("djordjevic_sarkar needs 0 < f_low < f_high")

    def ln(x):
        return 0.5 * log((f2 * f2 + x * x) / (f1 * f1 + x * x)), atan2(x, f2) - atan2(x, f1)

    a0, b0 = ln(f0)
    k = (-er * tand) / b0
    einf = er - k * a0
    a, b = ln(f)
    e1, e2 = einf + k * a, -k * b
    return {"er": e1, "tand": e2 / e1}


# ── conductors ────────────────────────────────────────────────────────────────────────────────────────────────


def skin_depth(f: float, sigma: float = LOSS_DEFAULTS["conductivity"]) -> float:
    """Skin depth δ = 1/√(π f μ0 σ), metres."""
    return 1 / sqrt(pi * f * MU0 * sigma)


def surface_resistance(f: float, sigma: float = LOSS_DEFAULTS["conductivity"]) -> float:
    """Surface resistance Rs = √(π f μ0 / σ), Ω per square."""
    return sqrt((pi * f * MU0) / sigma)


# Roughness (factor K(f) on Rs). Hammerstad: E. Hammerstad, Ø. Bekkadal, "Microstrip Handbook", ELAB report STF44
# A74169, 1975: K = 1 + (2/π) atan(1.4 (Δ/δ)²). Huray: P. G. Huray, "The Foundations of Signal Integrity", Wiley
# 2009: K = A_matte/A_flat + (3/2) (N 4π a²/A_flat) / (1 + δ/a + δ²/(2a²)). Cannonball: B. Simonovich, DesignCon
# 2015, and Polar Instruments AP8195 (2017): 14 spheres of radius Rz/16.73 on a square tile of side 6a.


def cannonball(rz: float) -> dict:
    """Huray parameters of the cannonball stack for a 10-point roughness Rz (mm)."""
    if rz is None or not rz > 0:
        raise ValueError(f"rz must be > 0 (got {rz})")
    radius = rz / 16.73
    return {"radius": radius, "count": 14, "area": 36 * radius * radius, "ratio": (14 * 4 * pi) / 36, "matte": 1}


def _roughness_spec(r: dict | None) -> dict | None:
    if r is None or r.get("model") == "none":
        return None
    model = r.get("model")
    if model is None:
        if r.get("radius") is not None:
            model = "huray"
        elif r.get("rz") is not None:
            model = "cannonball"
        elif r.get("rq") is not None:
            model = "hammerstad"
        else:
            return None
    if model == "hammerstad":
        if r.get("rq") is None or not r["rq"] >= 0:
            raise ValueError(f"hammerstad roughness needs rq >= 0 (got {r.get('rq')})")
        return {"model": model, "rq": r["rq"]}
    if model == "cannonball":
        return {**cannonball(r.get("rz")), "model": "huray", "rz": r["rz"]}
    if model == "huray":
        a = r.get("radius")
        if a is None or not a > 0:
            raise ValueError(f"huray roughness needs radius > 0 (got {a})")
        ratio = r.get("ratio")
        if ratio is None and r.get("count") is not None and r.get("area") is not None:
            ratio = (r["count"] * 4 * pi * a * a) / r["area"]
        if ratio is None or not ratio >= 0:
            raise ValueError("huray roughness needs ratio (N 4π a² / A_flat) or count and area")
        return {"model": model, "radius": a, "ratio": ratio, "matte": 1 if r.get("matte") is None else r["matte"]}
    raise ValueError(f"unknown roughness model {model}")


def roughness_factor(f: float, r: dict | None, sigma: float = LOSS_DEFAULTS["conductivity"]) -> float:
    """Roughness factor K(f) >= 1 on the surface resistance; ``r`` as the JS ``roughnessFactor`` (lengths in mm)."""
    s = _roughness_spec(r)
    if not s:
        return 1.0
    d = skin_depth(f, sigma) * 1e3
    if s["model"] == "hammerstad":
        return 1 + (2 / pi) * atan(1.4 * (s["rq"] / d) ** 2)
    a = s["radius"]
    return s["matte"] + (1.5 * s["ratio"]) / (1 + d / a + (d * d) / (2 * a * a))


# ── microstrip dispersion: Kirschning-Jansen ──────────────────────────────────────────────────────────────────
# Kirschning, Jansen, Electronics Letters 18(6) 1982 (εeff(f)); Jansen, Kirschning, AEÜ 37 1983 (Z0(f));
# Kirschning, Jansen, IEEE Trans. MTT-32(1) 1984 with the MTT-33(3) 1985 corrections (coupled lines).
# fn = f/GHz · h/mm.


def _clamp_exp(x: float) -> float:
    return min(x, 20.0)


def _kj_p(u, er, fn):
    P1 = 0.27488 + (0.6315 + 0.525 / (1 + 0.0157 * fn) ** 20) * u - 0.065683 * exp(-8.7513 * u)
    P2 = 0.33622 * (1 - exp(-0.03442 * er))
    P3 = 0.0363 * exp(-4.6 * u) * (1 - exp(-((fn / 38.7) ** 4.97)))
    P4 = 1 + 2.751 * (1 - exp(-((er / 15.916) ** 8)))
    return P1, P2, P3, P4


def _kj_eps_eff(u, er, ee0, fn):
    P1, P2, P3, P4 = _kj_p(u, er, fn)
    P = P1 * P2 * ((0.1844 + P3 * P4) * fn) ** 1.5763
    return er - (er - ee0) / (1 + P)


def _kj_z0(u, er, ee0, eef, Z00, fn):
    R1 = 0.03891 * er**1.4
    R2 = 0.2671 * u**7
    R3 = 4.766 * exp(-3.228 * u**0.641)
    R4 = 0.016 + (0.0514 * er) ** 4.524
    R5 = (fn / 28.843) ** 12
    R6 = 22.2 * u**1.92
    R7 = 1.206 - 0.3144 * exp(-_clamp_exp(R1)) * (1 - exp(-_clamp_exp(R2)))
    R8 = 1 + 1.275 * (1 - exp(-0.004625 * R3 * er**1.674 * (fn / 18.365) ** 2.745))
    R9 = (
        ((5.086 * R4 * R5) / (0.3838 + 0.386 * R4))
        * (exp(-_clamp_exp(R6)) / (1 + 1.2992 * R5))
        * ((er - 1) ** 6 / (1 + 10 * (er - 1) ** 6))
    )
    R10 = 0.00044 * er**2.136 + 0.0184
    R11 = (fn / 19.47) ** 6 / (1 + 0.0962 * (fn / 19.47) ** 6)
    R12 = 1 / (1 + 0.00245 * u * u)
    R13 = 0.9408 * eef**R8 - 0.9603
    R14 = (0.9408 - R9) * ee0**R8 - 0.9603
    R15 = 0.707 * R10 * (fn / 12.3) ** 1.097
    R16 = 1 + 0.0503 * er * er * R11 * (1 - exp(-((u / 15) ** 6)))
    R17 = R7 * (1 - ((1.1241 * R12) / R16) * exp(-0.026 * fn**1.15656 - R15))
    return Z00 * (R13 / R14) ** R17, R17


def microstrip_dispersion(p: dict, f: float) -> dict:
    """Microstrip ``{"eps_eff", "Z0"}`` at f from the static ``eps_eff``, ``Z0`` (Kirschning-Jansen); p: w, h, er."""
    u = p["w"] / p["h"]
    fn = (f / 1e9) * p["h"]
    eef = _kj_eps_eff(u, p["er"], p["eps_eff"], fn)
    return {"eps_eff": eef, "Z0": _kj_z0(u, p["er"], p["eps_eff"], eef, p["Z0"], fn)[0]}


def coupled_microstrip_dispersion(p: dict, f: float) -> dict:
    """Coupled microstrip even/odd ``eps_eff`` and ``Z`` at f (Kirschning-Jansen 1984); as the JS function."""
    u = p["w"] / p["h"]
    g = p["s"] / p["h"]
    er = p["er"]
    fn = (f / 1e9) * p["h"]
    P1, P2, P3, P4 = _kj_p(u, er, fn)
    P5 = 0.334 * exp(-3.3 * (er / 15) ** 3) + 0.746
    P6 = P5 * exp(-((fn / 18) ** 0.368))
    P7 = 1 + 4.069 * P6 * g**0.479 * exp(-1.347 * g**0.595 - 0.17 * g**2.5)
    Fe = P1 * P2 * ((P3 * P4 + 0.1844 * P7) * fn) ** 1.5763
    P8 = 0.7168 * (1 + 1.076 / (1 + 0.0576 * (er - 1)))
    P9 = P8 - 0.7913 * (1 - exp(-((fn / 20) ** 1.424))) * atan(2.481 * (er / 8) ** 0.946)
    P10 = 0.242 * (er - 1) ** 0.55
    P11 = 0.6366 * (exp(-0.3401 * fn) - 1) * atan(1.263 * (u / 3) ** 1.629)
    P12 = P9 + (1 - P9) / (1 + 1.183 * u**1.376)
    P13 = (1.695 * P10) / (0.414 + 1.605 * P10)
    P14 = 0.8928 + 0.1072 * (1 - exp(-0.42 * (fn / 20) ** 3.215))
    P15 = abs(1 - (0.8928 * (1 + P11) * exp(-P13 * g**1.092) * P12) / P14)
    Fo = P1 * P2 * ((P3 * P4 + 0.1844) * fn * P15) ** 1.5763
    eef_e = er - (er - p["eps_eff_even"]) / (1 + Fe)
    eef_o = er - (er - p["eps_eff_odd"]) / (1 + Fo)
    ee0 = p["single"]["eps_eff"]
    eef = _kj_eps_eff(u, er, ee0, fn)
    ZLf, Q0 = _kj_z0(u, er, ee0, eef, p["single"]["Z0"], fn)
    Q11 = 0.893 * (1 - 0.3 / (1 + 0.7 * (er - 1)))
    Q12 = ((2.121 * (fn / 20) ** 4.91) / (1 + Q11 * (fn / 20) ** 4.91)) * exp(-2.87 * g) * g**0.902
    Q13 = 1 + 0.038 * (er / 8) ** 5.1
    Q14 = 1 + (1.203 * (er / 15) ** 4) / (1 + (er / 15) ** 4)
    Q15 = (1.887 * exp(-1.5 * g**0.84) * g**Q14) / (
        1 + 0.41 * (fn / 15) ** 3 * (u ** (2 / Q13) / (0.125 + u ** (1.626 / Q13)))
    )
    Q16 = Q15 * (1 + 9 / (1 + 0.403 * (er - 1) ** 2))
    Q17 = 0.394 * (1 - exp(-1.47 * (u / 7) ** 0.672)) * (1 - exp(-4.25 * (fn / 20) ** 1.87))
    Q18 = (0.61 * (1 - exp(-2.13 * (u / 8) ** 1.593))) / (1 + 6.544 * g**4.17)
    Q19 = (0.21 * g**4) / ((1 + 0.18 * g**4.9) * (1 + 0.1 * u * u) * (1 + (fn / 24) ** 3))
    Q20 = Q19 * (0.09 + 1 / (1 + 0.1 * (er - 1) ** 2.7))
    Q21 = abs(1 - 42.54 * g**0.133 * exp(-0.812 * g) * (u**2.5 / (1 + 0.033 * u**2.5)))
    re = (fn / 28.843) ** 12
    qe = 0.016 + (0.0514 * er * Q21) ** 4.524
    pe = 4.766 * exp(-3.228 * u**0.641)
    de = (
        ((5.086 * qe * re) / (0.3838 + 0.386 * qe))
        * (exp(-_clamp_exp(22.2 * u**1.92)) / (1 + 1.2992 * re))
        * ((er - 1) ** 6 / (1 + 10 * (er - 1) ** 6))
    )
    Ce = 1 + 1.275 * (1 - exp(-0.004625 * pe * er**1.674 * (fn / 18.365) ** 2.745)) - Q12 + Q16 - Q17 + Q18 + Q20
    z_even = p["Zeven"] * ((0.9408 * eef**Ce - 0.9603) / ((0.9408 - de) * ee0**Ce - 0.9603)) ** Q0
    Q29 = 15.16 / (1 + 0.196 * (er - 1) ** 2)
    Q28 = (0.149 * (er - 1) ** 3) / (94.5 + 0.038 * (er - 1) ** 3)
    Q27 = 0.4 * g**0.84 * (1 + (2.5 * (er - 1) ** 1.5) / (5 + (er - 1) ** 1.5))
    Q26 = 30 - (22.2 * ((er - 1) / 13) ** 12) / (1 + 3 * ((er - 1) / 13) ** 12) - Q29
    Q25 = ((0.3 * fn * fn) / (10 + fn * fn)) * (1 + (2.333 * (er - 1) ** 2) / (5 + (er - 1) ** 2))
    Q24 = ((2.506 * Q28 * u**0.894) / (3.575 + u**0.894)) * (((1 + 1.3 * u) * fn) / 99.25) ** 4.29
    Q23 = 1 + (0.005 * fn * Q27) / ((1 + 0.812 * (fn / 15) ** 1.9) * (1 + 0.025 * u * u))
    Q22 = (0.925 * (fn / Q26) ** 1.536) / (1 + 0.3 * (fn / 30) ** 1.536)
    z_odd = ZLf + (p["Zodd"] * (eef_o / p["eps_eff_odd"]) ** Q22 - ZLf * Q23) / (1 + Q24 + (0.46 * g) ** 2.2 * Q25)
    return {"eps_eff_even": eef_e, "eps_eff_odd": eef_o, "Zeven": z_even, "Zodd": z_odd}


# ── one mode ──────────────────────────────────────────────────────────────────────────────────────────────────


def _conductor_rl(f, mode):
    rac = 0.0
    lint = 0.0
    for m in mode["metals"]:
        sigma = m.get("conductivity") or LOSS_DEFAULTS["conductivity"]
        rac += surface_resistance(f, sigma) * roughness_factor(f, m.get("roughness"), sigma) * m["gamma"]
        ft = max(f, 4 / (pi * MU0 * sigma * (mode["t"] * 1e-3) ** 2))
        lint += (surface_resistance(ft, sigma) * m["gamma"]) / (2 * pi * ft)
    return sqrt(mode["Rdc"] * mode["Rdc"] + rac * rac), lint


def _mode_at(f, mode, Zair, eps):
    w = 2 * pi * f
    R, lint = _conductor_rl(f, mode)
    L = Zair / C_LIGHT + lint
    C = eps[0] / (C_LIGHT * Zair)
    G = (w * eps[1]) / (C_LIGHT * Zair)
    Zs = complex(R, w * L)
    Yp = complex(G, w * C)
    gamma = cmath.sqrt(Zs * Yp)
    Zc = cmath.sqrt(Zs / Yp)
    Z0 = sqrt(L / C)
    return {
        "R": R,
        "L": L,
        "G": G,
        "C": C,
        "alpha": gamma.real,
        "alpha_c": R / (2 * Z0),
        "alpha_d": (G * Z0) / 2,
        "beta": gamma.imag,
        "eps_eff": eps[0],
        "Zc": Zc,
    }


_COLUMNS = ("R", "L", "G", "C", "alpha", "alpha_c", "alpha_d", "beta", "eps_eff")


def _mode_columns(rows):
    out = {k: [r[k] for r in rows] for k in _COLUMNS}
    out["Zc"] = [r["Zc"].real for r in rows]
    out["Zc_im"] = [r["Zc"].imag for r in rows]
    out["db_per_mm"] = [(r["alpha"] * NP_TO_DB) / 1000 for r in rows]
    out["db_per_inch"] = [(r["alpha"] * NP_TO_DB * INCH) / 1000 for r in rows]
    return out


def _check_frequencies(frequencies) -> list[float]:
    fs = [frequencies] if isinstance(frequencies, (int, float)) else list(frequencies)
    if not fs:
        raise ValueError("frequencies is empty")
    for f in fs:
        if not (isinstance(f, (int, float)) and math.isfinite(f) and f > 0):
            raise ValueError(f"frequency must be > 0 Hz (got {f})")
    return [float(f) for f in fs]


@dataclass(frozen=True)
class LossResult:
    """Loss and frequency dependence of a line (the JS object; ``to_dict()`` gives it). Per frequency (lists):
    a single line has the mode columns at the top level (``Z0`` = Re Zc); a pair has ``Zdiff``, ``Zcommon``,
    ``db_per_mm`` (differential), ``db_per_mm_common`` and the ``odd`` / ``even`` mode columns."""

    model: str | None
    method: str
    solver: str
    key: str
    frequency: list[float]
    Z0: list[float] | None = None
    R: list[float] | None = None
    L: list[float] | None = None
    G: list[float] | None = None
    C: list[float] | None = None
    alpha: list[float] | None = None
    alpha_c: list[float] | None = None
    alpha_d: list[float] | None = None
    beta: list[float] | None = None
    eps_eff: list[float] | None = None
    Zc: list[float] | None = None
    Zc_im: list[float] | None = None
    db_per_mm: list[float] | None = None
    db_per_inch: list[float] | None = None
    Zdiff: list[float] | None = None
    Zcommon: list[float] | None = None
    db_per_mm_common: list[float] | None = None
    odd: dict | None = None
    even: dict | None = None

    def to_dict(self) -> dict:
        d = {k: v for k, v in asdict(self).items() if v is not None}
        d["model"] = self.model
        return d


def _assemble(base: dict, fs, modes) -> LossResult:
    if "single" in modes:
        m = modes["single"]
        return LossResult(**base, key="Z0", frequency=fs, **m, Z0=m["Zc"])
    odd, even = modes["odd"], modes["even"]
    return LossResult(
        **base,
        key="Zdiff",
        frequency=fs,
        Zdiff=[2 * z for z in odd["Zc"]],
        Zcommon=[z / 2 for z in even["Zc"]],
        db_per_mm=odd["db_per_mm"],
        db_per_inch=odd["db_per_inch"],
        db_per_mm_common=even["db_per_mm"],
        odd=odd,
        even=even,
    )


# ── tier 1 ────────────────────────────────────────────────────────────────────────────────────────────────────

_TIER1_MODES = {
    "microstrip": "single",
    "coated_microstrip": "single",
    "stripline": "single",
    "cpw": "single",
    "cpwg": "single",
    "coupled_microstrip": "pair",
    "coupled_stripline": "pair",
}


def _recede(model, p, metal, d):
    q = dict(p)
    if metal == "signal":
        q["w"] = p["w"] - 2 * d
        q["t"] = p["t"] - 2 * d
        if p.get("s") is not None:
            q["s"] = p["s"] + 2 * d
        if p.get("gap") is not None:
            q["gap"] = p["gap"] + 2 * d
    if model == "cpw":
        return q
    if p.get("h1") is not None:
        q["h1"] = p["h1"] + d
        q["h2"] = (p["h1"] if p.get("h2") is None else p["h2"]) + d
    else:
        q["h"] = p["h"] + d
    return q


def _air_z(r) -> dict:
    if getattr(r, "Z0", None) is not None:
        return {"single": r.Z0 * sqrt(r.eps_eff)}
    return {"odd": r.Zodd * sqrt(r.eps_eff_odd), "even": r.Zeven * sqrt(r.eps_eff_even)}


def _wheeler_tier1(model, p):
    metals = ["signal"] if model == "cpw" else ["signal", "ground"]
    d = 1e-4 * min(v for v in (p.get("w"), p.get("t"), p.get("s"), p.get("gap")) if v is not None)
    out: dict = {}
    for m in metals:
        a = _air_z(calculate(model, _recede(model, p, m, d)))
        b = _air_z(calculate(model, _recede(model, p, m, -d)))
        for k in a:
            out.setdefault(k, {})[m] = ((a[k] - b[k]) / (2 * d)) * 1e3 / ETA0
    return out


def _static_modes(r) -> dict:
    if getattr(r, "Z0", None) is not None:
        return {"single": (r.Z0, r.eps_eff)}
    return {"odd": (r.Zodd, r.eps_eff_odd), "even": (r.Zeven, r.eps_eff_even)}


def line_loss(
    model: str,
    params: dict,
    frequencies: float | Iterable[float],
    *,
    dielectric: dict | None = None,
    mask: dict | None = None,
    conductor: dict | None = None,
    signal: dict | None = None,
    ground: dict | None = None,
    dispersion: bool = True,
    materials: dict | None = None,
    metals: dict | None = None,
) -> LossResult:
    """Loss and frequency dependence of a tier-1 line (the JS ``lineLoss``; same parameters and options).

    params: the model's parameters in mm (``er``, ``erc``: Dk at the dielectric's reference frequency; t > 0).
    dielectric / mask: ``{tand, frequency, model, f_low, f_high}``; conductor: ``{conductivity, roughness}`` for every
    metal, ``signal`` / ``ground`` per metal; dispersion: Kirschning-Jansen for (coupled) microstrip. materials and
    metals (section_loss's options) are accepted and ignored, so a ``Line.loss`` dict serves both.
    """
    kind = _TIER1_MODES.get(model)
    if kind is None:
        raise ValueError(f"no loss model for {model} (one of {', '.join(_TIER1_MODES)})")
    fs = _check_frequencies(frequencies)
    if not (params.get("t") is not None and params["t"] > 0):
        raise ValueError("conductor loss needs copper thickness t > 0")
    calculate(model, params)
    sub = {**(dielectric or {}), "er": params["er"]}
    msk = {**(mask or {}), "er": params["erc"]} if params.get("erc") is not None else None
    per = {"signal": signal or {}, "ground": ground or {}}

    def metal(name):
        return {"name": name, **(conductor or {}), **per.get(name, {})}

    gammas = _wheeler_tier1(model, params)
    sigma = (signal or {}).get("conductivity") or (conductor or {}).get("conductivity") or LOSS_DEFAULTS["conductivity"]
    Rdc = 1 / (sigma * params["w"] * params["t"] * 1e-6)
    disp_on = dispersion and model in ("microstrip", "coated_microstrip", "coupled_microstrip")
    rows: dict = {}
    for f in fs:
        ds = dielectric_at(f, sub)
        dm = dielectric_at(f, msk) if msk else None

        def at(er, erc, dm=dm):
            q = {**params, "er": er}
            if dm:
                q["erc"] = erc
            return _static_modes(calculate(model, q))

        base = at(ds["er"], dm and dm["er"])
        hs = 1e-6 * ds["er"]
        s_lo = max(1.0, ds["er"] - hs)
        up = at(ds["er"] + hs, dm and dm["er"])
        dn = at(s_lo, dm and dm["er"])
        if dm:
            hm = 1e-6 * dm["er"]
            m_lo = max(1.0, dm["er"] - hm)
            mup = at(ds["er"], dm["er"] + hm)
            mdn = at(ds["er"], m_lo)
        disp = None
        if disp_on and kind == "single":
            d = microstrip_dispersion(
                {
                    "w": params["w"],
                    "h": params["h"],
                    "er": ds["er"],
                    "eps_eff": base["single"][1],
                    "Z0": base["single"][0],
                },
                f,
            )
            disp = {"single": (d["Z0"], d["eps_eff"])}
        elif disp_on:
            s1 = calculate("microstrip", {"w": params["w"], "h": params["h"], "er": ds["er"]})
            c = coupled_microstrip_dispersion(
                {
                    "w": params["w"],
                    "s": params["s"],
                    "h": params["h"],
                    "er": ds["er"],
                    "Zeven": base["even"][0],
                    "Zodd": base["odd"][0],
                    "eps_eff_even": base["even"][1],
                    "eps_eff_odd": base["odd"][1],
                    "single": {"Z0": s1.Z0, "eps_eff": s1.eps_eff},
                },
                f,
            )
            disp = {"even": (c["Zeven"], c["eps_eff_even"]), "odd": (c["Zodd"], c["eps_eff_odd"])}
        for k in base:
            e2 = ((up[k][1] - dn[k][1]) / (ds["er"] + hs - s_lo)) * ds["er"] * ds["tand"]
            if dm:
                e2 += ((mup[k][1] - mdn[k][1]) / (dm["er"] + hm - m_lo)) * dm["er"] * dm["tand"]
            Z, ee = base[k]
            if disp:
                if ee > 1:
                    e2 *= (disp[k][1] - 1) / (ee - 1)
                Z, ee = disp[k]
            mode = {
                "Rdc": Rdc,
                "t": params["t"],
                "metals": [{**metal(n), "gamma": gamma} for n, gamma in gammas[k].items()],
            }
            rows.setdefault(k, []).append(_mode_at(f, mode, Z * sqrt(ee), (ee, e2)))
    modes = {k: _mode_columns(r) for k, r in rows.items()}
    method = ["Wheeler incremental inductance", "Djordjevic-Sarkar"]
    if disp_on:
        method.append("Kirschning-Jansen dispersion")
    return _assemble({"model": model, "method": " + ".join(method), "solver": "closedform"}, fs, modes)


# ── tier 2 ────────────────────────────────────────────────────────────────────────────────────────────────────


def _cd(M):
    return (M[0][0] + M[1][1] - 2 * M[0][1]) / 4


def _cc(M):
    return M[0][0] + M[1][1] + 2 * M[0][1]


def section_loss(
    result,
    frequencies: float | Iterable[float],
    *,
    dielectric: dict | None = None,
    materials: dict | None = None,
    conductor: dict | None = None,
    metals: dict | None = None,
    mask: dict | None = None,
    signal: dict | None = None,
    ground: dict | None = None,
) -> LossResult:
    """Loss and frequency dependence from ``solve_cross_section(section, loss=True)`` (the JS ``sectionLoss``).

    dielectric: ``{frequency, model, f_low, f_high}`` for every dielectric (er and tand come from the section);
    materials: the same per section ``material``; conductor: ``{conductivity, roughness}`` for every metal, metals:
    per section ``metal``. mask, signal and ground (line_loss's options) are accepted and ignored.
    """
    r = result.to_dict() if hasattr(result, "to_dict") else result
    ls = r.get("loss")
    if not ls:
        raise ValueError("section_loss needs a solve_cross_section result with loss=True")
    fs = _check_frequencies(frequencies)
    n = len(r["signals"])
    if n > 2:
        raise ValueError("section_loss handles one or two signals")
    if not ls["t"] > 0:
        raise ValueError("conductor loss needs copper with thickness > 0")
    if n == 1:
        reduce = {"single": lambda M: M[0][0]}
        stat = {"single": (r["Z0"], r["eps_eff"])}
    else:
        reduce = {"odd": lambda M: 2 * _cd(M), "even": lambda M: _cc(M) / 2}
        stat = {"odd": (r["Zodd"], r["eps_eff_odd"]), "even": (r["Zeven"], r["eps_eff_even"])}
    mats = [
        {**(dielectric or {}), **m, **((materials or {}).get(m["material"], {}) if m["material"] is not None else {})}
        for m in ls["materials"]
    ]
    mets = [{"name": name, **(conductor or {}), **(metals or {}).get(name, {})} for name in ls["metals"]]
    sigma_sig = (
        ((metals or {}).get(ls["signalMetal"]) or {}).get("conductivity")
        or (conductor or {}).get("conductivity")
        or LOSS_DEFAULTS["conductivity"]
    )
    Rdc = 1 / (sigma_sig * (ls["area"] / n) * 1e-6)
    modes = {}
    for k, red in reduce.items():
        C0 = red(ls["K0"])
        parts = [red(P) if P else 0.0 for P in ls["parts"]]
        total = sum(parts)
        Z, ee = stat[k]
        Zair = Z * sqrt(ee)
        mode = {
            "Rdc": Rdc,
            "t": ls["t"],
            "metals": [{**m, "gamma": (-EPS0 * red(ls["dK0"][i]) * 1e3) / (C0 * C0)} for i, m in enumerate(mets)],
        }
        rows = []
        for f in fs:
            re = 0.0
            im = 0.0
            for i, P in enumerate(parts):
                if i == len(mats):
                    re += P
                    continue
                d = dielectric_at(f, mats[i])
                s = d["er"] / mats[i]["er"]
                re += P * s
                im += P * s * d["tand"]
            rows.append(_mode_at(f, mode, Zair, ((ee * re) / total, (ee * im) / total)))
        modes[k] = _mode_columns(rows)
    method = "boarddd field solver + Wheeler incremental inductance + Djordjevic-Sarkar"
    return _assemble({"model": r.get("model"), "method": method, "solver": "field"}, fs, modes)


# ── S-parameters and Touchstone ───────────────────────────────────────────────────────────────────────────────


def _line_s(Zc: complex, gamma: complex, length: float, z0: float):
    gl = gamma * length
    A = cmath.cosh(gl)
    sh = cmath.sinh(gl)
    B = Zc * sh
    C = sh / Zc
    den = A + A + B / z0 + C * z0
    return (B / z0 - C * z0) / den, 2 / den


def s_parameters(loss, length: float, *, z0: float = 50) -> list[list[list[complex]]]:
    """S-parameters of ``length`` mm of line (the JS ``sParameters``): per frequency a 2x2 (single) or 4x4 (pair:
    ports 1 → 2 on the first line, 3 → 4 on the second) matrix of complex numbers."""
    d = loss.to_dict() if hasattr(loss, "to_dict") else loss
    if not length > 0:
        raise ValueError(f"length must be > 0 mm (got {length})")
    L = length * 1e-3

    def mode_s(m, i):
        return _line_s(complex(m["Zc"][i], m["Zc_im"][i]), complex(m["alpha"][i], m["beta"][i]), L, z0)

    out = []
    for i in range(len(d["frequency"])):
        if d["key"] == "Z0":
            s11, s21 = mode_s(d, i)
            out.append([[s11, s21], [s21, s11]])
            continue
        e11, e21 = mode_s(d["even"], i)
        o11, o21 = mode_s(d["odd"], i)
        r, x, t, c = (e11 + o11) / 2, (e11 - o11) / 2, (e21 + o21) / 2, (e21 - o21) / 2
        out.append([[r, t, x, c], [t, r, c, x], [x, c, r, t], [c, x, t, r]])
    return out


def _js_str(x: float) -> str:
    """JS Number.prototype.toString of a double (the shortest round-trip digits, JS's exponent rules)."""
    if x == 0:
        return "0"
    sign = "-" if x < 0 else ""
    r = repr(abs(x))
    if "e" in r:
        m, e = r.split("e")
        exp10 = int(e)
    else:
        m, exp10 = r, 0
    if "." in m:
        ip, fp = m.split(".")
    else:
        ip, fp = m, ""
    digits = (ip + fp).lstrip("0")
    n = len(ip.lstrip("0")) + exp10 if ip.strip("0") else exp10 - (len(fp) - len(fp.lstrip("0")))
    digits = digits.rstrip("0") or "0"
    k = len(digits)
    if k <= n <= 21:
        out = digits + "0" * (n - k)
    elif 0 < n <= 21:
        out = digits[:n] + "." + digits[n:]
    elif -6 < n <= 0:
        out = "0." + "0" * (-n) + digits
    else:
        e = n - 1
        out = digits[0] + ("." + digits[1:] if k > 1 else "") + "e" + ("+" if e > 0 else "-") + str(abs(e))
    return sign + out


def _g(v: float) -> str:
    """Number(v.toPrecision(10)).toString() of JS, for byte-identical Touchstone files."""
    return _js_str(float(f"{v:.10g}")) if v != 0 else "0"


def touchstone(loss, length: float, *, z0: float = 50, comment: str | None = None) -> str:
    """A Touchstone 1.1 file (.s2p single, .s4p pair) of ``length`` mm of line (the JS ``touchstone``)."""
    d = loss.to_dict() if hasattr(loss, "to_dict") else loss
    S = s_parameters(d, length, z0=z0)
    n = len(S[0])
    what = "single line" if n == 2 else "pair (ports 1-2 line A, 3-4 line B)"
    lines = [f"! boarddd {d['solver']} {d.get('model') or 'section'}: {_g(length)} mm, {what}"]
    if comment:
        lines += [f"! {c}" for c in str(comment).split("\n")]
    lines.append(f"# Hz S RI R {_g(z0)}")
    for i, f in enumerate(d["frequency"]):
        M = S[i]
        if n == 2:
            vals = [M[0][0], M[1][0], M[0][1], M[1][1]]
            lines.append(" ".join(_g(v) for v in [f, *[x for z in vals for x in (z.real, z.imag)]]))
        else:
            for r, row in enumerate(M):
                head = [f] if r == 0 else []
                lines.append(" ".join(_g(v) for v in [*head, *[x for z in row for x in (z.real, z.imag)]]))
    return "\n".join(lines) + "\n"
