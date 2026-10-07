"""Tier-1 closed-form (quasi-static) PCB transmission-line impedance, written for boarddd.

Implemented from the original papers (cited per function; no code from KiCad, Qucs, Transcalc or js_2d_fields).
Where a published correction was more than ~2 % off a field solver (thickness in CPW and coupled lines, offset
stripline, solder mask) boarddd uses its own physically-based terms with fitted constants, marked "boarddd" and
measured in docs/impedance.md. src/impedance/closedform.js is the same code in JS; fixtures/impedance/cases.json
keeps them in step, so change both together.

Lengths are in any one unit (boarddd uses mm); only their ratios matter. Every model returns numbers plus `flags`:
the inputs that are outside the range the formula was published or checked for.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from math import cosh, exp, log, pi, sin, sinh, sqrt, tanh

ETA0 = 376.730313668
"""Impedance of free space, Ω (CODATA 2018)."""


@dataclass(frozen=True)
class Flag:
    """An input outside the range the formula was published or checked for."""

    code: str
    value: float
    min: float | None
    max: float | None
    message: str


@dataclass(frozen=True)
class LineResult:
    model: str
    method: str
    Z0: float
    eps_eff: float
    flags: list[Flag] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class CoupledResult:
    model: str
    method: str
    Zdiff: float
    Zcommon: float
    Zodd: float
    Zeven: float
    eps_eff_odd: float
    eps_eff_even: float
    flags: list[Flag] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class ComparisonResult:
    """An IPC-2141 rule of thumb: never a design value."""

    model: str
    method: str
    flags: list[Flag]
    Z0: float | None = None
    Zdiff: float | None = None
    comparison: bool = True

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v is not None}


Result = LineResult | CoupledResult | ComparisonResult

# ── elliptic integrals ──────────────────────────────────────────────────────────────────────────────────────


def _agm(a: float, b: float) -> float:
    i = 0
    while i < 64 and abs(a - b) > 1e-15 * a:
        a, b = (a + b) / 2, sqrt(a * b)
        i += 1
    return (a + b) / 2


def elliptic_k(k: float) -> float:
    """Complete elliptic integral of the first kind K(k), modulus k (0 <= k < 1), by the AGM."""
    return pi / (2 * _agm(1, sqrt((1 - k) * (1 + k))))


def elliptic_ratio(k: float, kp: float | None = None) -> float:
    """K(k)/K(k'); pass `kp` when k is close to 1 and k' is known more accurately than sqrt(1 - k²)."""
    if kp is None:
        kp = sqrt((1 - k) * (1 + k))
    return _agm(1, k) / _agm(1, kp)


# ── validity flags ─────────────────────────────────────────────────────────────────────────────────────────


def _fmt(x: float) -> str:
    """JS String(Number(x.toPrecision(4))), so flag messages match the JS ones."""
    v = float(f"{x:.4g}")
    if v == int(v) and abs(v) < 1e21:
        return str(int(v))
    if abs(v) < 1e-6:
        m, e = f"{v:e}".split("e")
        return f"{float(m):g}e{int(e)}"
    return repr(v)


def _range(flags: list[Flag], code: str, value: float, lo: float | None, hi: float | None, source: str) -> None:
    if (lo is not None and value < lo) or (hi is not None and value > hi):
        span = f"<= {_fmt(hi)}" if lo is None else f">= {_fmt(lo)}" if hi is None else f"{_fmt(lo)}..{_fmt(hi)}"
        flags.append(Flag(code, value, lo, hi, f"{code} = {_fmt(value)} is outside {span} ({source})"))


def _positive(p: dict, names: list[str]) -> None:
    for n in names:
        v = p.get(n)
        if not (isinstance(v, int | float) and not isinstance(v, bool) and math.isfinite(v) and v > 0):
            raise ValueError(f"{n} must be a positive number (got {v})")
    t = p.get("t")
    if t is not None and not (math.isfinite(t) and t >= 0):
        raise ValueError(f"t must be a number >= 0 (got {t})")
    er = p.get("er")
    if not (isinstance(er, int | float) and er >= 1):
        raise ValueError(f"er must be >= 1 (got {er})")


def _ratio(a: float, b: float) -> float:
    return a / b if b > 0 else math.inf


# ── microstrip: Hammerstad & Jensen 1980 ───────────────────────────────────────────────────────────────────
# E. Hammerstad, Ø. Jensen, "Accurate models for microstrip computer-aided design", IEEE MTT-S Int. Microwave
# Symp. Digest, 1980, pp. 407-409: eq. (1)-(2) Z01 of the air line, (3)-(6) εe, and the strip-thickness correction
# (widths u1 for the air line and ur for the dielectric line). Published accuracy: Z01 0.01 % for u <= 1 and
# 0.03 % for u <= 1000; εe 0.2 % for 0.01 <= u <= 100 and εr <= 128.


def _z01(u: float) -> float:
    """Air-filled microstrip impedance for u = w/h, zero thickness (H-J eq. 1-2)."""
    f = 6 + (2 * pi - 6) * exp(-((30.666 / u) ** 0.7528))
    return (ETA0 / (2 * pi)) * log(f / u + sqrt(1 + 4 / (u * u)))


def _eps_eff0(u: float, er: float) -> float:
    """Zero-thickness effective permittivity (H-J eq. 3-6)."""
    a = 1 + log((u**4 + (u / 52) ** 2) / (u**4 + 0.432)) / 49 + log(1 + (u / 18.1) ** 3) / 18.7
    b = 0.564 * ((er - 0.9) / (er + 3)) ** 0.053
    return (er + 1) / 2 + ((er - 1) / 2) * (1 + 10 / u) ** (-a * b)


def _hj_thickness(u: float, T: float, er: float) -> tuple[float, float]:
    """H-J thickness correction: (Δu1 (air), Δur (dielectric)) for T = t/h."""
    if T <= 0:
        return 0.0, 0.0
    du1 = (T / pi) * log(1 + (4 * math.e) / (T / tanh(sqrt(6.517 * u)) ** 2))
    return du1, 0.5 * (1 + 1 / cosh(sqrt(er - 1))) * du1


def _microstrip_core(u: float, T: float, er: float) -> tuple[float, float, float]:
    """(Z0, εeff, air impedance), thickness included."""
    du1, dur = _hj_thickness(u, T, er)
    u1, ur = u + du1, u + dur
    ee0 = _eps_eff0(ur, er)
    return _z01(ur) / sqrt(ee0), ee0 * (_z01(u1) / _z01(ur)) ** 2, _z01(u1)


def _microstrip_flags(flags: list[Flag], u: float, T: float, er: float, src: str = "Hammerstad-Jensen") -> None:
    _range(flags, "w/h", u, 0.01, 100, src)
    _range(flags, "er", er, 1, 128, src)
    _range(flags, "t/h", T, 0, 0.2, src)


def microstrip(*, w: float, h: float, er: float, t: float = 0) -> LineResult:
    """Surface microstrip over one plane (Hammerstad-Jensen 1980, thickness included)."""
    _positive(locals(), ["w", "h"])
    u, T = w / h, t / h
    Z0, eps_eff, _ = _microstrip_core(u, T, er)
    flags: list[Flag] = []
    _microstrip_flags(flags, u, T, er)
    return LineResult("microstrip", "Hammerstad-Jensen 1980", Z0, eps_eff, flags)


# ── coated microstrip (solder mask) ────────────────────────────────────────────────────────────────────────
# A conformal coating of thickness c and permittivity εc over the trace and the laminate only adds dielectric
# where the bare line has air, so Cair (and the H-J air impedance) is unchanged and εeff rises:
#   εeff = εeff,bare + (1 - q) F (εc - 1) / (1 + 0.076 (εc - 1)),   q = (εeff,bare - 1)/(εr - 1)
#   F = 1 - exp(-2.66 u^-0.316 (c/h)^0.848 (1 - 0.606 t/h))
# (1 - q) is the air's share of the bare line's field (Wheeler's filling factor); F is the share of that air
# field inside the coating, and the denominator its partly series (normal-field) character. The form is
# boarddd's: the published covered-microstrip models (Bahl-Stuchly 1980, Svačina 1992, Wan-Hoorfar 2000) are for
# a planar cover, not a conformal mask, and the constants are fitted to 180 quasi-static field solutions with a
# conformal mask (0.3 <= u <= 3, 0.006 <= c/h <= 0.4, t/h <= 0.35, εc 3.3-4): within 0.65 % of Z0 there.


def coated_microstrip(*, w: float, h: float, er: float, c: float, erc: float, t: float = 0) -> LineResult:
    """Microstrip under a conformal solder mask of thickness `c` (over laminate and trace) and εr `erc`."""
    _positive(locals(), ["w", "h"])
    if not (isinstance(c, int | float) and c >= 0):
        raise ValueError(f"c must be a number >= 0 (got {c})")
    if not (isinstance(erc, int | float) and erc >= 1):
        raise ValueError(f"erc must be >= 1 (got {erc})")
    u, T, C = w / h, t / h, c / h
    _, ee_bare, z_air = _microstrip_core(u, T, er)
    q = (ee_bare - 1) / ((er - 1) or 1)
    F = 1 - exp(-2.66 * u**-0.316 * C**0.848 * max(0.0, 1 - 0.606 * T))
    eps_eff = ee_bare + ((1 - q) * F * (erc - 1)) / (1 + 0.076 * (erc - 1))
    flags: list[Flag] = []
    _microstrip_flags(flags, u, T, er)
    src = "boarddd mask model"
    _range(flags, "w/h", u, 0.25, 4, src)
    _range(flags, "c/h", C, 0, 0.4, src)
    _range(flags, "erc", erc, 2.5, 5, src)
    return LineResult(
        "coated_microstrip", "Hammerstad-Jensen 1980 + boarddd mask model", z_air / sqrt(eps_eff), eps_eff, flags
    )


# ── stripline: Cohn 1954 (exact, t = 0) + Wheeler 1978 thickness ──────────────────────────────────────────
# S. B. Cohn, "Characteristic impedance of the shielded-strip transmission line", IRE Trans. MTT-2, July 1954,
# pp. 52-57: the conformal map of a zero-thickness strip centred between planes b apart is exact,
# Z0 = (η0 / 4√εr) K(k)/K(k'), k = sech(πw / 2b).
# H. A. Wheeler, "Transmission-line properties of a strip line between parallel planes", IEEE Trans. MTT-26,
# Nov. 1978, pp. 866-876, eq. (9)-(11) (also Wadell, "Transmission Line Design Handbook", 1991, §3.5.1): the
# thick strip as an equivalent zero-thickness width. We use it only for the thickness: Z0(t) = Z0_Cohn(0) ·
# Z0_W(t)/Z0_W(0), so the result is exact at t = 0 and continuous in t (Wheeler alone is within 0.5 %).
# Offset strip (h1 ≠ h2). The textbook parallel combination of two symmetric lines (Wadell §3.5.3) is up to 6 %
# high at 4:1, so boarddd moves Cohn's centred strip off centre with two exact limits and blends them:
#   wide strips: parallel plates w/h1 + w/h2 plus the exact fringing of an offset half-plane edge,
#     Cf/ε = (1/π) [(b/a) ln(b/c) + (b/c) ln(b/a)] (a, c: distances of the strip's centre line to the planes),
#     added as capacitance to the centred line;
#   narrow strips: the exact image-series result for a thin conductor between planes,
#     Z_off = Z_centred + (η0 / 2π√εr) ln sin(πa/b);
#   weighted by a smoothstep in log(w / min(a, c)) from 0.2 (narrow) to 6 (wide), a boarddd choice checked
#   against quasi-static field solutions (within 1.5 % up to h_max/h_min = 4).


def _cohn_stripline0(w: float, b: float) -> float:
    a = (pi * w) / (2 * b)
    return (ETA0 / 4) * elliptic_ratio(1 / cosh(a), tanh(a))  # K(k)/K(k'), k = sech(a), k' = tanh(a)


def _wheeler_stripline(w: float, b: float, t: float) -> float:
    x = t / b
    m = w / (b - t)
    if x > 0:
        n = 2 / (1 + (2 / 3) * (x / (1 - x)))
        m += (x / (pi * (1 - x))) * (1 - 0.5 * log((x / (2 - x)) ** 2 + ((0.0796 * x) / (w / b + 1.1 * x)) ** n))
    q = 8 / (pi * m)
    return (ETA0 / (4 * pi)) * log(1 + (4 / (pi * m)) * (q + sqrt(q * q + 6.27)))


def _sym_stripline_air(w: float, b: float, t: float) -> float:
    """Air impedance (εr = 1) of a strip centred between planes b apart."""
    z0 = _cohn_stripline0(w, b)
    return (z0 * _wheeler_stripline(w, b, t)) / _wheeler_stripline(w, b, 0) if t > 0 else z0


def _parallel_air(z: Callable[[float], float], h1: float, h2: float, t: float) -> float:
    """The textbook offset approximation: two symmetric lines (plane spacings 2h1+t, 2h2+t) in parallel."""
    za, zb = z(2 * h1 + t), z(2 * h2 + t)
    return (2 * za * zb) / (za + zb)


def _offset_fringe(a: float, c: float) -> float:
    """Total edge fringing capacitance / ε of a zero-thickness half-plane a above one plane and c below the other."""
    b = a + c
    return ((b / a) * log(b / c) + (b / c) * log(b / a)) / pi


def _stripline_air(w: float, h1: float, h2: float, t: float) -> float:
    b = h1 + t + h2
    zs = _sym_stripline_air(w, b, t)
    if h1 == h2:
        return zs
    a, c = h1 + t / 2, h2 + t / 2
    zw = ETA0 / (
        ETA0 / zs + (w / h1 + w / h2 - (4 * w) / (b - t)) + 2 * (_offset_fringe(a, c) - _offset_fringe(b / 2, b / 2))
    )
    zn = zs + (ETA0 / (2 * pi)) * log(sin((pi * a) / b))
    q = min(1.0, max(0.0, log(w / min(a, c) / 0.2) / log(30)))
    f = q * q * (3 - 2 * q)
    return zn * (1 - f) + zw * f if zn > 0 else zw


def stripline(*, w: float, h1: float, er: float, h2: float | None = None, t: float = 0) -> LineResult:
    """Stripline between two planes: h1 of dielectric to one plane, h2 (default h1) to the other; b = h1 + t + h2."""
    h2 = h1 if h2 is None else h2
    _positive(locals(), ["w", "h1", "h2"])
    b = h1 + t + h2
    flags: list[Flag] = []
    _range(flags, "t/b", t / b, 0, 0.25, "Wheeler 1978 thickness")
    _range(flags, "w/(b-t)", w / (b - t), 0.05, None, "Wheeler 1978 thickness")
    off = max(h1, h2) / min(h1, h2)
    _range(flags, "h_max/h_min", off, 1, 4, "offset stripline model")
    if off > 1:
        _range(flags, "h_min/t", _ratio(min(h1, h2), t), 2, None, "offset stripline model")
    method = ("Cohn 1954 + Wheeler 1978 thickness" if t > 0 else "Cohn 1954 (exact)") + (
        " + boarddd offset" if off > 1 else ""
    )
    return LineResult("stripline", method, _stripline_air(w, h1, h2, t) / sqrt(er), er, flags)


# ── coplanar waveguide: Ghione & Naldi ────────────────────────────────────────────────────────────────────
# G. Ghione, C. U. Naldi, "Analytical formulas for coplanar lines in hybrid and monolithic MICs", Electronics
# Letters 20(4), 1984, pp. 179-181 (CPW on a substrate of finite height h), and "Coplanar waveguides for MMIC
# applications: effect of upper shielding, conductor backing, finite-extent ground planes, and line-to-line
# coupling", IEEE Trans. MTT-35(3), 1987, pp. 260-267 (conductor-backed CPW). Conformal maps, exact for t = 0
# and infinite coplanar grounds:
#   CPW:  εeff = 1 + (εr-1)/2 · K(k1)/K(k1') · K(k0')/K(k0),  Z0 = η0/(4√εeff) · K(k0')/K(k0)
#         k0 = w/(w+2g), k1 = sinh(πw/4h) / sinh(π(w+2g)/4h)
#   CPWG: C/2ε0 = K(k0)/K(k0') (air above) + εr K(k3)/K(k3') (substrate to the backing plane),
#         k3 = tanh(πw/4h) / tanh(π(w+2g)/4h)
# Strip thickness. The textbook correction (K. C. Gupta et al., "Microstrip Lines and Slotlines", 2nd ed. 1996,
# §7.3: edges widened by Δ = (1.25t/π)(1 + ln(4πw/t))) is up to ±8 % off a field solver for 1 oz copper in
# 0.1-0.4 mm gaps, because Δ approaches the gap. boarddd instead adds the thickness as air capacitance above the
# t = 0 map (per 2ε0): the gaps' sidewalls as parallel plates, t/g, plus corner and backing terms fitted to
# quasi-static field solutions (CPWG, 0.25 <= h/g <= 10, t/g <= 0.7):
#   ΔC = t/g + 0.1 √(t/w) [+ 0.05 √(t/h) g/h with a backing plane].


def _tanh_ratio(a: float, b: float) -> tuple[float, float]:
    """k = tanh(a)/tanh(b) (b > a > 0) and its exact complement k' = sqrt(1-k²)."""
    k = tanh(a) / tanh(b)
    one = sinh(b - a) / (cosh(a) * sinh(b))  # 1 - k
    return k, sqrt(one * (1 + k))


def _coplanar(w: float, gap: float, h: float, er: float, t: float, grounded: bool) -> LineResult:
    _positive({"w": w, "gap": gap, "h": h, "er": er, "t": t}, ["w", "gap", "h"])
    g = gap
    k0, k0p = w / (w + 2 * g), (2 * sqrt(g * (w + g))) / (w + 2 * g)
    r0 = elliptic_ratio(k0, k0p)  # K(k0)/K(k0')
    dt = t / g + 0.1 * sqrt(t / w) + (0.05 * sqrt(t / h) * (g / h) if grounded else 0) if t > 0 else 0
    flags: list[Flag] = []
    src = "Ghione-Naldi 1987" if grounded else "Ghione-Naldi 1984"
    _range(flags, "t/gap", t / g, 0, 0.7, "boarddd coplanar thickness")
    if grounded:
        k3, k3p = _tanh_ratio((pi * w) / (4 * h), (pi * (w + 2 * g)) / (4 * h))
        r3 = elliptic_ratio(k3, k3p)
        c_air = r0 + dt + r3
        c = r0 + dt + er * r3
        # Ghione-Naldi splits the field at the coplanar plane; with the backing plane closer than about one gap the
        # line is mostly a microstrip and the split is 2-6 % high (docs/impedance.md).
        _range(flags, "h/gap", h / g, 1, None, src)
    else:
        k1 = sinh((pi * w) / (4 * h)) / sinh((pi * (w + 2 * g)) / (4 * h))
        r1 = elliptic_ratio(k1)
        # Air above and below (2 r0), plus the dielectric's share of the lower half-plane.
        c_air = 2 * r0 + dt
        c = 2 * r0 + dt + (er - 1) * r1
    method = src + (" + boarddd thickness" if t > 0 else "")
    return LineResult("cpwg" if grounded else "cpw", method, ETA0 / (2 * sqrt(c * c_air)), c / c_air, flags)


def cpw(*, w: float, gap: float, h: float, er: float, t: float = 0) -> LineResult:
    """Coplanar waveguide on a substrate of height h with no plane under it (Ghione-Naldi 1984)."""
    return _coplanar(w, gap, h, er, t, False)


def cpwg(*, w: float, gap: float, h: float, er: float, t: float = 0) -> LineResult:
    """Grounded (conductor-backed) CPW (Ghione-Naldi 1987): infinite coplanar grounds, the plane h below."""
    return _coplanar(w, gap, h, er, t, True)


# ── edge-coupled microstrip: Kirschning & Jansen 1984 ─────────────────────────────────────────────────────
# M. Kirschning, R. H. Jansen, "Accurate wide-range design equations for the frequency-dependent
# characteristic of parallel coupled microstrip lines", IEEE Trans. MTT-32(1), Jan. 1984, pp. 83-90, with the
# corrections in MTT-33(3), Mar. 1985, p. 288: static even/odd εeff (eq. 3-4) and impedances (eq. 8-9) from
# the single line of Hammerstad-Jensen; stated accuracy 0.6 % (εeff 0.7 %) for 0.1 <= u <= 10, 0.1 <= g <= 10,
# 1 <= εr <= 18 (u = w/h, g = s/h), for zero thickness.
# Thickness. Jansen's mode-width corrections (R. H. Jansen, IEEE Trans. MTT-26(2), 1978) are up to 28 % off a
# field solver for the odd mode of 1 oz pairs with s ~ t, so boarddd adds thickness as capacitance on top of the
# t = 0 modes, as for coupled stripline below: ΔCs (C and Cair) is the single line's H-J thickness increase,
# shared by its two edges; even: ΔCe = ΔCs (1 - ψe/2), odd: ΔCo = ΔCs + ψo 2t/s (air, a parallel plate across
# the gap). ψe = (1 + g) exp(-g) and ψo = (1 + g) exp(-0.8g) are boarddd's fit to quasi-static field solutions
# (0.2 <= g <= 3, t/h <= 0.35).


def _kj_even(u: float, g: float, er: float) -> float:
    v = (u * (20 + g * g)) / (10 + g * g) + g * exp(-g)
    return _eps_eff0(v, er)  # K-J eq. 3: H-J εe at the even-mode width v


def _kj_odd(u: float, g: float, er: float, ee0: float) -> float:
    ao = 0.7287 * (ee0 - (er + 1) / 2) * (1 - exp(-0.179 * u))
    bo = (0.747 * er) / (0.15 + er)
    co = bo - (bo - 0.207) * exp(-0.414 * u)
    d0 = 0.593 + 0.694 * exp(-0.562 * u)
    return ((er + 1) / 2 + ao - ee0) * exp(-co * g**d0) + ee0


def _kj_modes(u: float, g: float, er: float) -> tuple[float, float, float, float]:
    """K-J static (Zeven, εeff even, Zodd, εeff odd) for one normalised width, zero thickness."""
    ee0 = _eps_eff0(u, er)
    zl = _z01(u) / sqrt(ee0)
    q1 = 0.8695 * u**0.194
    q2 = 1 + 0.7519 * g + 0.189 * g**2.31
    q3 = 0.1975 + (16.6 + (8.4 / g) ** 6) ** -0.387 + log(g**10 / (1 + (g / 3.4) ** 10)) / 241
    q4 = ((2 * q1) / q2) / (exp(-g) * u**q3 + (2 - exp(-g)) * u**-q3)
    q5 = 1.794 + 1.14 * log(1 + 0.638 / (g + 0.517 * g**2.43))
    q6 = 0.2305 + log(g**10 / (1 + (g / 5.8) ** 10)) / 281.3 + log(1 + 0.598 * g**1.154) / 5.1
    q7 = (10 + 190 * g * g) / (1 + 82.3 * g**3)
    q8 = exp(-6.5 - 0.95 * log(g) - (g / 0.15) ** 5)
    q9 = log(q7) * (q8 + 1 / 16.5)
    q10 = q4 - (q5 / q2) * exp((q6 * log(u)) / u**q9)
    ee_e, ee_o = _kj_even(u, g, er), _kj_odd(u, g, er, ee0)
    k = (zl / ETA0) * sqrt(ee0)
    return (zl * sqrt(ee0 / ee_e)) / (1 - k * q4), ee_e, (zl * sqrt(ee0 / ee_o)) / (1 - k * q10), ee_o


def _coupled(model, method, z_even, z_odd, ee_even, ee_odd, flags) -> CoupledResult:
    return CoupledResult(model, method, 2 * z_odd, z_even / 2, z_odd, z_even, ee_odd, ee_even, flags)


def _caps(Z: float, ee: float) -> tuple[float, float]:
    """(C, Cair) per ε0 from Z and εeff."""
    return (ETA0 * sqrt(ee)) / Z, ETA0 / (Z * sqrt(ee))


def coupled_microstrip(*, w: float, s: float, h: float, er: float, t: float = 0) -> CoupledResult:
    """Edge-coupled microstrip pair: each trace w wide, s edge to edge (Kirschning-Jansen 1984, boarddd thickness)."""
    _positive(locals(), ["w", "s", "h"])
    u, g, T = w / h, s / h, t / h
    z_even, ee_even, z_odd, ee_odd = _kj_modes(u, g, er)
    if T > 0:
        # Thickness as capacitance (per ε0) on top of the t = 0 modes.
        c0, a0 = _caps(_z01(u) / sqrt(_eps_eff0(u, er)), _eps_eff0(u, er))
        zt, eet, _ = _microstrip_core(u, T, er)
        ct, at = _caps(zt, eet)
        pe, po, plate = (1 + g) * exp(-g), (1 + g) * exp(-0.8 * g), (2 * T) / g

        def mode(Z: float, ee: float, dC: float, dA: float) -> tuple[float, float]:
            c, a = _caps(Z, ee)
            return ETA0 / sqrt((c + dC) * (a + dA)), (c + dC) / (a + dA)

        z_even, ee_even = mode(z_even, ee_even, (ct - c0) * (1 - pe / 2), (at - a0) * (1 - pe / 2))
        z_odd, ee_odd = mode(z_odd, ee_odd, ct - c0 + po * plate, at - a0 + po * plate)
    flags: list[Flag] = []
    src = "Kirschning-Jansen 1984"
    _range(flags, "w/h", u, 0.1, 10, src)
    _range(flags, "s/h", g, 0.1, 10, src)
    _range(flags, "er", er, 1, 18, src)
    _range(flags, "t/h", T, 0, 0.35, "boarddd coupled thickness")
    method = src + (" + boarddd thickness" if T > 0 else "")
    return _coupled("coupled_microstrip", method, z_even, z_odd, ee_even, ee_odd, flags)


# ── edge-coupled stripline: Cohn 1955 ──────────────────────────────────────────────────────────────────────
# S. B. Cohn, "Shielded coupled-strip transmission line", IRE Trans. MTT-3(5), Oct. 1955, pp. 29-38. Exact for
# zero-thickness strips centred between planes b apart:
#   Zoe,o = (η0 / 4√εr) K(k')/K(k),  ke = tanh(πw/2b) tanh(π(w+s)/2b),  ko = tanh(πw/2b) coth(π(w+s)/2b).
# Thickness. Cohn's thin-strip corrections (same paper; Wadell §4.6.2) are 2-6 % off a field solver for 1 oz
# copper on thin cores (docs/impedance.md), so boarddd adds thickness the way it does for the single strip, as
# capacitance (C/ε0 = η0/Z_air) on top of Cohn's exact t = 0 modes:
#   ΔCs   thickness increase of one strip alone (Cohn + Wheeler above),
#   e     its share per edge: (ΔCs - ΔCpp)/2, ΔCpp = 4w/(b-t) - 4w/b the parallel-plate part,
#   ψ     the part of the inner edge that couples to the other strip, (1 + 2x) exp(-2.8x), x = s/(b-t),
#   even: ΔCe = ΔCs - ψ e        (the inner sidewall faces a magnetic wall),
#   odd:  ΔCo = ΔCs + ψ 2t/s     (the inner sidewalls form a parallel plate across the gap, electric wall at s/2).
# ψ is boarddd's own fit to quasi-static field solutions (0.2 <= x <= 1.1, t/b <= 0.12); it gives the exact
# t = 0 limit and ψ -> 0 (uncoupled) for wide gaps.
# Offset pairs (h1 ≠ h2): each mode is the parallel combination of two symmetric pairs, scaled by the ratio of
# the offset single strip (above) to the same parallel combination for one strip.


def _sym_coupled_air(w: float, s: float, b: float, t: float) -> tuple[float, float]:
    """Air (Zoe, Zoo) of a pair centred between planes b apart."""
    a1, a2 = (pi * w) / (2 * b), (pi * (w + s)) / (2 * b)
    # ke = tanh(a1) tanh(a2) and 1 - ke = cosh(a2 - a1) / (cosh a1 cosh a2), exact for ke near 1.
    ke = tanh(a1) * tanh(a2)
    kep = sqrt((cosh(a2 - a1) / (cosh(a1) * cosh(a2))) * (1 + ke))
    ko, kop = _tanh_ratio(a1, a2)
    zoe0, zoo0 = (ETA0 / 4) / elliptic_ratio(ke, kep), (ETA0 / 4) / elliptic_ratio(ko, kop)
    if t <= 0:
        return zoe0, zoo0
    dcs = ETA0 / _sym_stripline_air(w, b, t) - ETA0 / _cohn_stripline0(w, b)
    e = (dcs - ((4 * w) / (b - t) - (4 * w) / b)) / 2
    x = s / (b - t)
    psi = (1 + 2 * x) * exp(-2.8 * x)
    return ETA0 / (ETA0 / zoe0 + dcs - psi * e), ETA0 / (ETA0 / zoo0 + dcs + (psi * 2 * t) / s)


def coupled_stripline(
    *, w: float, s: float, h1: float, er: float, h2: float | None = None, t: float = 0
) -> CoupledResult:
    """Edge-coupled stripline pair (Cohn 1955, boarddd thickness), symmetric or offset."""
    h2 = h1 if h2 is None else h2
    _positive(locals(), ["w", "s", "h1", "h2"])
    b = h1 + t + h2
    if h1 == h2:
        ze, zo = _sym_coupled_air(w, s, b, t)
    else:
        # Each mode as two symmetric pairs in parallel, scaled by how far that combination is off for one strip.
        k = _stripline_air(w, h1, h2, t) / _parallel_air(lambda bb: _sym_stripline_air(w, bb, t), h1, h2, t)
        ze = k * _parallel_air(lambda bb: _sym_coupled_air(w, s, bb, t)[0], h1, h2, t)
        zo = k * _parallel_air(lambda bb: _sym_coupled_air(w, s, bb, t)[1], h1, h2, t)
    n = sqrt(er)
    flags: list[Flag] = []
    _range(flags, "t/b", t / b, 0, 0.12, "boarddd coupled thickness")
    _range(flags, "s/(b-t)", s / (b - t), 0.15, None, "boarddd coupled thickness")
    _range(flags, "h_max/h_min", max(h1, h2) / min(h1, h2), 1, 3, "offset coupled stripline model")
    if h1 != h2:
        _range(flags, "h_min/t", _ratio(min(h1, h2), t), 2, None, "offset coupled stripline model")
    method = ("Cohn 1955 + boarddd thickness" if t > 0 else "Cohn 1955 (exact)") + (
        " + boarddd offset" if h1 != h2 else ""
    )
    return _coupled("coupled_stripline", method, ze / n, zo / n, er, er, flags)


# ── IPC-2141 (comparison only) ─────────────────────────────────────────────────────────────────────────────
# IPC-2141A (2004) / IPC-D-317A rules of thumb, as fabs and old calculators quote them. Results carry
# `comparison=True`: they are up to 30 % off (docs/impedance.md) and are never a design value.

_IPC = "IPC-2141 (comparison only)"


def ipc2141_microstrip(*, w: float, h: float, er: float, t: float = 0) -> ComparisonResult:
    """IPC-2141 surface microstrip (comparison only)."""
    _positive(locals(), ["w", "h"])
    flags: list[Flag] = []
    _range(flags, "w/h", w / h, 0.1, 2, "IPC-2141")
    _range(flags, "er", er, 1, 15, "IPC-2141")
    return ComparisonResult(
        "ipc2141_microstrip", _IPC, flags, Z0=(87 / sqrt(er + 1.41)) * log((5.98 * h) / (0.8 * w + t))
    )


def ipc2141_stripline(*, w: float, h1: float, er: float, h2: float | None = None, t: float = 0) -> ComparisonResult:
    """IPC-2141 symmetric stripline (comparison only; b = h1 + t + h2)."""
    h2 = h1 if h2 is None else h2
    _positive(locals(), ["w", "h1", "h2"])
    b = h1 + t + h2
    flags: list[Flag] = []
    _range(flags, "w/(b-t)", w / (b - t), None, 0.35, "IPC-2141")
    _range(flags, "t/b", t / b, None, 0.25, "IPC-2141")
    return ComparisonResult("ipc2141_stripline", _IPC, flags, Z0=(60 / sqrt(er)) * log((1.9 * b) / (0.8 * w + t)))


def ipc2141_coupled_microstrip(*, w: float, s: float, h: float, er: float, t: float = 0) -> ComparisonResult:
    """IPC-2141 edge-coupled microstrip, Zdiff (comparison only)."""
    _positive({"s": s, "er": er}, ["s"])
    r = ipc2141_microstrip(w=w, h=h, er=er, t=t)
    zdiff = 2 * r.Z0 * (1 - 0.48 * exp((-0.96 * s) / h))
    return ComparisonResult("ipc2141_coupled_microstrip", _IPC, r.flags, Zdiff=zdiff)


def ipc2141_coupled_stripline(
    *, w: float, s: float, h1: float, er: float, h2: float | None = None, t: float = 0
) -> ComparisonResult:
    """IPC-2141 edge-coupled stripline, Zdiff (comparison only)."""
    _positive({"s": s, "er": er}, ["s"])
    r = ipc2141_stripline(w=w, h1=h1, er=er, h2=h2, t=t)
    b = h1 + t + (h1 if h2 is None else h2)
    zdiff = 2 * r.Z0 * (1 - 0.347 * exp((-2.9 * s) / b))
    return ComparisonResult("ipc2141_coupled_stripline", _IPC, r.flags, Zdiff=zdiff)


# ── registry and synthesis ─────────────────────────────────────────────────────────────────────────────────

MODELS: dict[str, Callable[..., Result]] = {
    "microstrip": microstrip,
    "coated_microstrip": coated_microstrip,
    "stripline": stripline,
    "cpw": cpw,
    "cpwg": cpwg,
    "coupled_microstrip": coupled_microstrip,
    "coupled_stripline": coupled_stripline,
    "ipc2141_microstrip": ipc2141_microstrip,
    "ipc2141_stripline": ipc2141_stripline,
    "ipc2141_coupled_microstrip": ipc2141_coupled_microstrip,
    "ipc2141_coupled_stripline": ipc2141_coupled_stripline,
}
"""Every model by its id (the `model` field of its result; also the JS MODELS keys)."""


def calculate(model: str, params: dict) -> Result:
    """Run a model by id with a dict of its parameters."""
    if model not in MODELS:
        raise ValueError(f"unknown model {model} (one of {', '.join(MODELS)})")
    return MODELS[model](**params)


@dataclass(frozen=True)
class Synthesis:
    value: float
    params: dict
    result: Result
    iterations: int


def _sign(x: float) -> int:
    return (x > 0) - (x < 0)


def synthesize(
    model: str,
    params: dict,
    target: float,
    *,
    vary: str = "w",
    key: str | None = None,
    lo: float | None = None,
    hi: float | None = None,
    tol: float = 1e-9,
) -> Synthesis:
    """Solve for one dimension (default `w`) giving `target` Ω, by bisection in log space.

    Impedance is monotonic in w (and in s, h, gap) for every model, so the bracket (default 1e-3..1e2 times the
    dielectric height) holds at most one solution. `key` is the result field to match (default Zdiff for coupled
    models, else Z0); `tol` the relative tolerance on the dimension. Raises ValueError when out of reach.
    """

    def run(x: float) -> Result:
        return calculate(model, {**params, vary: x})

    if key is None:
        key = "Zdiff" if getattr(run(params.get(vary, 1)), "Zdiff", None) is not None else "Z0"
    scale = params["h"] if "h" in params else params["h1"] + params.get("h2", params["h1"]) + params.get("t", 0)
    lo = 1e-3 * scale if lo is None else lo
    hi = 1e2 * scale if hi is None else hi

    def f(x: float) -> float:
        return getattr(run(x), key) - target

    flo, fhi = f(lo), f(hi)
    if not (_sign(flo) != _sign(fhi) or flo == 0 or fhi == 0):
        raise ValueError(
            f"{key} = {_fmt(target)} is not reachable for {vary} in [{_fmt(lo)}, {_fmt(hi)}] "
            f"({_fmt(flo + target)}..{_fmt(fhi + target)} Ω)"
        )
    i = 0
    while i < 200 and hi / lo - 1 > tol:
        mid = sqrt(lo * hi)
        if _sign(f(mid)) == _sign(flo):
            lo = mid
        else:
            hi = mid
        i += 1
    value = sqrt(lo * hi)
    return Synthesis(value, {**params, vary: value}, run(value), i)
