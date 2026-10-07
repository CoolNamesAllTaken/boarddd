"""Tier-2 2D quasi-static field solver for PCB transmission-line cross-sections, written for boarddd (MIT).

The Python twin of src/impedance/fieldsolver.js: the same grid, discretisation, symmetry handling and
extrapolation, so the two agree to about 1e-9 (fixtures/impedance/field-cases.json). The method is described in
the JS file and docs/impedance.md: Laplace's equation by finite volumes on a graded rectilinear grid, the stored
energy of unit excitations gives the capacitance matrices with and without dielectrics, a second grid with every
cell size divided by √2 gives a Richardson extrapolation and an error estimate. Here the sparse system is
solved by scipy (SuperLU) instead of the JS nested-dissection Cholesky; both are direct, so the answers match.

Needs numpy and scipy: ``pip install "boarddd[field]"``. Units: rectangles in any one length unit (boarddd uses
mm, y up); C and C0 in F/m and L in H/m.
"""

from __future__ import annotations

import math
import time
from dataclasses import asdict, dataclass, field
from typing import Any

C_LIGHT = 299792458.0
EPS0 = 8.8541878128e-12
MU0 = 1 / (EPS0 * C_LIGHT * C_LIGHT)

MESH = {"edgeFraction": 0.01, "floorFraction": 1e-3, "interfaceFraction": 0.125, "growth": 1.4, "margin": 50}
"""Mesh constants (level 0), as in the JS module: cell size at conductor edges as a fraction of the smallest
conductor feature, its floor as a fraction of the structure, the cap at dielectric interfaces, the growth ratio
and the far boundary's distance in structure sizes."""

RATIO = 0.55
"""Measured error ratio between grid levels (0.51-0.57 on the exact Cohn cases and microstrip; docs)."""

KEYS = ("Z0", "eps_eff", "C", "L", "C0", "Zdiff", "Zcommon", "Zodd", "Zeven", "eps_eff_odd", "eps_eff_even")
INF = math.inf


def _np():
    try:
        import numpy as np
        import scipy.sparse as sp
        import scipy.sparse.linalg as spla
    except ImportError as e:  # pragma: no cover - exercised only without the extra
        raise ImportError('the field solver needs numpy and scipy: pip install "boarddd[field]"') from e
    return np, sp, spla


def _finite(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


@dataclass(frozen=True)
class FieldResult:
    """A solved cross-section (the JS result object; `to_dict()` gives it without the absent keys)."""

    solver: str
    method: str
    signals: list[str]
    matrices: dict[str, list[list[float]]]
    error: dict[str, float]
    error_pct: float
    symmetry: str
    levels: list[dict[str, Any]]
    ms: float
    model: str | None = None
    flags: list = field(default_factory=list)
    Z0: float | None = None
    eps_eff: float | None = None
    C: float | None = None
    L: float | None = None
    C0: float | None = None
    Zdiff: float | None = None
    Zcommon: float | None = None
    Zodd: float | None = None
    Zeven: float | None = None
    eps_eff_odd: float | None = None
    eps_eff_even: float | None = None

    def to_dict(self) -> dict:
        d = {k: v for k, v in asdict(self).items() if v is not None}
        if self.model is None:
            d.pop("flags")
        return d


# ── section → normalised geometry ──────────────────────────────────────────────────────────────────────────


def _normalise(section: dict) -> dict:
    if not isinstance(section, dict):
        raise TypeError("section must be a dict")
    ground = set(section.get("ground") or ["gnd"])
    background = section.get("background", 1)
    if not background >= 1:
        raise ValueError(f"background must be >= 1 (got {background})")

    def rect(r, what, i):
        for k in ("y0", "y1"):
            if not _finite(r.get(k)):
                raise ValueError(f"{what}[{i}].{k} must be a finite number")
        for k in ("x0", "x1"):
            if r.get(k) is not None and not _finite(r[k]):
                raise ValueError(f"{what}[{i}].{k} must be a finite number or absent")
        if r["y1"] < r["y0"] or (r.get("x0") is not None and r.get("x1") is not None and r["x1"] < r["x0"]):
            raise ValueError(f"{what}[{i}] has negative size")
        x0 = r.get("x0")
        x1 = r.get("x1")
        return {"x0": -INF if x0 is None else x0, "x1": INF if x1 is None else x1, "y0": r["y0"], "y1": r["y1"]}

    conductors = []
    for i, c in enumerate(section.get("conductors") or []):
        if c.get("net") is None:
            raise ValueError(f"conductors[{i}] has no net")
        conductors.append({**rect(c, "conductors", i), "net": str(c["net"])})
    dielectrics = []
    for i, d in enumerate(section.get("dielectrics") or []):
        if not (d.get("er") is not None and d["er"] >= 1):
            raise ValueError(f"dielectrics[{i}].er must be >= 1 (got {d.get('er')})")
        dielectrics.append({**rect(d, "dielectrics", i), "er": d["er"]})
    signals: list[str] = []
    for c in conductors:
        if c["net"] not in ground and c["net"] not in signals:
            signals.append(c["net"])
    if not signals:
        raise ValueError("section has no signal conductor (every net is ground)")
    if not any(c["net"] in ground for c in conductors):
        raise ValueError(f"section has no ground conductor (nets {', '.join(sorted(ground))})")
    for c in conductors:
        if c["net"] not in ground and not (_finite(c["x0"]) and _finite(c["x1"])):
            raise ValueError(f"signal {c['net']} must be bounded in x")

    xa, xb, ya, yb = INF, -INF, INF, -INF
    for r in conductors + dielectrics:
        for x in (r["x0"], r["x1"]):
            if _finite(x):
                xa = min(xa, x)
                xb = max(xb, x)
        ya = min(ya, r["y0"])
        yb = max(yb, r["y1"])
    sig = [c for c in conductors if c["net"] not in ground]
    sy0 = min(c["y0"] for c in sig)
    sy1 = max(c["y1"] for c in sig)
    below, above = -INF, INF
    for c in conductors:
        if c["net"] not in ground or c["x0"] != -INF or c["x1"] != INF:
            continue
        if c["y1"] <= sy0:
            below = max(below, c["y1"])
        if c["y0"] >= sy1:
            above = min(above, c["y0"])
    lo = ya if below == -INF else below
    hi = yb if above == INF else above
    size = max(xb - xa, hi - lo)
    M = MESH["margin"] * size
    domain = {
        "x0": xa - M,
        "x1": xb + M,
        "y0": ya - M if below == -INF else below,
        "y1": yb + M if above == INF else above,
    }

    def clip(r):
        return {
            **r,
            "x0": max(r["x0"], domain["x0"]),
            "x1": min(r["x1"], domain["x1"]),
            "y0": max(r["y0"], domain["y0"]),
            "y1": min(r["y1"], domain["y1"]),
        }

    def keep(r):
        return r["x0"] <= r["x1"] and r["y0"] <= r["y1"]

    return {
        "ground": ground,
        "background": background,
        "signals": signals,
        "size": size,
        "domain": domain,
        "extent": (xa, xb),
        "conductors": [r for r in map(clip, conductors) if keep(r)],
        "dielectrics": [r for r in map(clip, dielectrics) if keep(r)],
    }


# ── mirror symmetry ────────────────────────────────────────────────────────────────────────────────────────


def _mirror_map(g: dict, m: float) -> str | None:
    tol = 1e-9 * g["size"]

    def eq(a, b):
        return a == b or abs(a - b) <= tol

    def mirror(r):
        return {**r, "x0": 2 * m - r["x1"], "x1": 2 * m - r["x0"]}

    def same(a, b):
        return all(eq(a[k], b[k]) for k in ("x0", "x1", "y0", "y1"))

    def matches(lst, key):
        return all(any(same(mirror(r), s) and key(r, s) for s in lst) for r in lst)

    if not matches(g["dielectrics"], lambda a, b: a["er"] == b["er"]):
        return None

    def net_ok(fn):
        return lambda a, b: (b["net"] in g["ground"]) if a["net"] in g["ground"] else fn(a["net"]) == b["net"]

    if matches(g["conductors"], net_ok(lambda n: n)):
        return "same"
    if len(g["signals"]) == 2:
        p, q = g["signals"]
        if matches(g["conductors"], net_ok(lambda n: q if n == p else p)):
            return "swap"
    return None


# ── graded grid ────────────────────────────────────────────────────────────────────────────────────────────


def _axis(breaks: list[tuple[float, float]], lo: float, hi: float, growth: float) -> list[float]:
    pts = sorted((b for b in breaks if lo <= b[0] <= hi), key=lambda b: b[0])
    xs: list[float] = []
    hs: list[float] = []
    for x, h in pts:
        if xs and x - xs[-1] <= 0:
            hs[-1] = min(hs[-1], h)
            continue
        xs.append(x)
        hs.append(h)
    k = growth - 1

    def size_at(x):
        h = INF
        for xi, hi_ in zip(xs, hs, strict=True):
            v = hi_ + k * (x - xi if x > xi else xi - x)
            if v < h:
                h = v
        return h

    out = [xs[0]]
    for s in range(len(xs) - 1):
        a, b = xs[s], xs[s + 1]
        seg = [a]
        x, h = a, size_at(a)
        while x + h < b:
            x += h
            seg.append(x)
            h = size_at(x)
        L = b - a
        end = x + h
        if len(seg) > 1 and (x - a) * (end - a) > L * L:
            seg.pop()
            end = x
        c = L / (end - a)
        out.extend(a + (v - a) * c for v in seg[1:])
        out.append(b)
    return out


def _breakpoints(g, axis_: str, hc: float, hd: float, lo: float, hi: float) -> list[tuple[float, float]]:
    a, b = ("x0", "x1") if axis_ == "x" else ("y0", "y1")
    raw = [(lo, INF), (hi, INF)]
    for c in g["conductors"]:
        corner = axis_ == "x" or _finite(c["x0"]) or _finite(c["x1"])
        for v in (c[a], c[b]):
            if _finite(v):
                raw.append((v, hc if corner else hd))
    for d in g["dielectrics"]:
        for v in (d[a], d[b]):
            if _finite(v):
                raw.append((v, hd))
    xs = sorted({x for x, _ in raw})
    out = []
    for x, h in raw:
        i = xs.index(x)
        gap = INF
        if i > 0:
            gap = min(gap, x - xs[i - 1])
        if i + 1 < len(xs):
            gap = min(gap, xs[i + 1] - x)
        out.append((x, min(h, gap / 2)))
    return out


def _conductor_scale(g) -> float:
    d = INF
    for a, b in (("x0", "x1"), ("y0", "y1")):
        v = sorted({x for c in g["conductors"] for x in (c[a], c[b]) if _finite(x)})
        for i in range(1, len(v)):
            if v[i] - v[i - 1] < d:
                d = v[i] - v[i - 1]
    return max(min(d, g["size"]), MESH["floorFraction"] * g["size"])


def _make_grid(g, level: int, mirror_at: float | None):
    f = 1.0
    for _ in range(level):
        f /= math.sqrt(2)
    for _ in range(-level):
        f *= math.sqrt(2)
    hc = MESH["edgeFraction"] * _conductor_scale(g) * f
    hd = MESH["interfaceFraction"] * g["size"] * f
    growth = 1 + (MESH["growth"] - 1) * f
    D = g["domain"]
    x0 = D["x0"] if mirror_at is None else mirror_at
    X = _axis(_breakpoints(g, "x", hc, hd, x0, D["x1"]), x0, D["x1"], growth)
    Y = _axis(_breakpoints(g, "y", hc, hd, D["y0"], D["y1"]), D["y0"], D["y1"], growth)
    return X, Y, hc, growth


# ── discretisation and solution ────────────────────────────────────────────────────────────────────────────


def _paint(g, X, Y):
    np, _, _ = _np()
    X = np.asarray(X)
    Y = np.asarray(Y)
    cx = 0.5 * (X[:-1] + X[1:])
    cy = 0.5 * (Y[:-1] + Y[1:])
    er = np.full((len(Y) - 1, len(X) - 1), float(g["background"]))
    for d in g["dielectrics"]:
        mx = (cx >= d["x0"]) & (cx <= d["x1"])
        my = (cy >= d["y0"]) & (cy <= d["y1"])
        er[np.ix_(my, mx)] = d["er"]
    owner = np.full((len(Y), len(X)), -1, dtype=np.int64)
    for i, c in enumerate(g["conductors"]):
        mx = (X >= c["x0"]) & (X <= c["x1"])
        my = (Y >= c["y0"]) & (Y <= c["y1"])
        owner[np.ix_(my, mx)] = i
    return er, owner


def _conductances(X, Y, eps):
    """gx[j, i] couples node (i, j) to (i+1, j), gy[j, i] to (i, j+1) (per ε0), as in the JS module."""
    np, _, _ = _np()
    X = np.asarray(X)
    Y = np.asarray(Y)
    ny, nx = len(Y), len(X)
    dx, dy = np.diff(X), np.diff(Y)
    e = np.zeros((ny + 1, nx + 1))  # cell (i, j) at e[j + 1, i + 1]; zero outside
    e[1:ny, 1:nx] = eps
    below = np.concatenate(([0.0], dy))[:, None]
    above = np.concatenate((dy, [0.0]))[:, None]
    gx = (0.5 * (e[0:ny, 1:nx] * below + e[1 : ny + 1, 1:nx] * above)) / dx[None, :]
    left = np.concatenate(([0.0], dx))[None, :]
    right = np.concatenate((dx, [0.0]))[None, :]
    gy = (0.5 * (e[1:ny, 0:nx] * left + e[1:ny, 1 : nx + 1] * right)) / dy[:, None]
    return gx, gy  # shapes (ny, nx-1) and (ny-1, nx)


def _quad(gx, gy, p, q=None):
    np, _, _ = _np()
    q = p if q is None else q
    return float(
        np.sum(gx * (p[:, :-1] - p[:, 1:]) * (q[:, :-1] - q[:, 1:]))
        + np.sum(gy * (p[:-1, :] - p[1:, :]) * (q[:-1, :] - q[1:, :]))
    )


def _solve(gx, gy, fixed, excitations):
    """Potentials for each excitation (a full (ny, nx) array holding the fixed nodes' values)."""
    np, sp, spla = _np()
    ny, nx = fixed.shape
    N = nx * ny
    idx = np.arange(N).reshape(ny, nx)
    a = np.concatenate((idx[:, :-1].ravel(), idx[:-1, :].ravel()))
    b = np.concatenate((idx[:, 1:].ravel(), idx[1:, :].ravel()))
    w = np.concatenate((gx.ravel(), gy.ravel()))
    A = sp.coo_matrix(
        (np.concatenate((w, w, -w, -w)), (np.concatenate((a, b, a, b)), np.concatenate((a, b, b, a)))),
        shape=(N, N),
    ).tocsr()
    free = ~fixed.ravel()
    Aff = A[free][:, free].tocsc()
    Afc = A[free][:, ~free]
    lu = spla.splu(Aff, permc_spec="MMD_AT_PLUS_A")
    out = []
    for v in excitations:
        flat = v.ravel().copy()
        flat[free] = lu.solve(-(Afc @ flat[~free]))
        out.append(flat.reshape(ny, nx))
    return out, int(free.sum())


def _capacitances(g, level: int, sym: str | None, m: float):
    np, _, _ = _np()
    X, Y, hc, growth = _make_grid(g, level, m if sym else None)
    nx, ny = len(X), len(Y)
    er, owner = _paint(g, X, Y)
    nets = np.array([c["net"] for c in g["conductors"]] + [""], dtype=object)
    node_net = nets[owner]  # owner -1 picks the trailing ""
    fixed = owner >= 0
    nS = len(g["signals"])

    def unit(net):
        return (node_net == net).astype(float)

    stats = {"nx": nx, "ny": ny, "nodes": nx * ny, "unknowns": 0, "hc": hc, "growth": growth}
    uniform = float(er.flat[0]) if np.all(er == er.flat[0]) else None
    media = [np.ones_like(er)] if uniform else [er, np.ones_like(er)]
    mats = [[[0.0] * nS for _ in range(nS)] for _ in media]

    def solve(fx, eps, ex):
        gx, gy = _conductances(X, Y, eps)
        phi, n = _solve(gx, gy, fx, ex)
        stats["unknowns"] += n
        return gx, gy, phi

    if sym == "swap":
        v = unit(g["signals"][0]) + unit(g["signals"][1])
        odd_fixed = fixed.copy()
        odd_fixed[:, 0] = True
        v_odd = v.copy()
        v_odd[:, 0] = 0.0
        for fx, ex, sign in ((fixed, v, 1), (odd_fixed, v_odd, -1)):
            for e, eps in enumerate(media):
                gx, gy, phi = solve(fx, eps, [ex])
                c = EPS0 * _quad(gx, gy, phi[0])
                mats[e][0][0] += c / 2
                mats[e][1][1] += c / 2
                mats[e][0][1] += sign * c / 2
                mats[e][1][0] += sign * c / 2
    else:
        ex = [unit(s) for s in g["signals"]]
        w = 2 if sym == "same" else 1
        for e, eps in enumerate(media):
            gx, gy, phi = solve(fixed, eps, ex)
            for a in range(nS):
                for b in range(a, nS):
                    mats[e][a][b] = mats[e][b][a] = w * EPS0 * _quad(gx, gy, phi[a], phi[b])
    K0 = mats[-1]
    K = [[uniform * v for v in row] for row in K0] if uniform else mats[0]
    return K, K0, stats


# ── line parameters ────────────────────────────────────────────────────────────────────────────────────────


def _inverse(M):
    n = len(M)
    A = [list(r) + [1.0 if i == j else 0.0 for j in range(n)] for i, r in enumerate(M)]
    for c in range(n):
        p = c
        for r in range(c + 1, n):
            if abs(A[r][c]) > abs(A[p][c]):
                p = r
        A[c], A[p] = A[p], A[c]
        d = A[c][c]
        A[c] = [v / d for v in A[c]]
        for r in range(n):
            if r != c:
                f = A[r][c]
                A[r] = [A[r][k] - f * A[c][k] for k in range(2 * n)]
    return [r[n:] for r in A]


def _line_params(K, K0) -> dict:
    n = len(K)
    Linv = [[MU0 * EPS0 * v for v in r] for r in _inverse(K0)]
    out: dict = {}
    if n == 1:
        C, C0 = K[0][0], K0[0][0]
        out.update(Z0=1 / (C_LIGHT * math.sqrt(C * C0)), eps_eff=C / C0, C=C, L=Linv[0][0], C0=C0)
    elif n == 2:

        def cd(M):
            return (M[0][0] + M[1][1] - 2 * M[0][1]) / 4

        def cc(M):
            return M[0][0] + M[1][1] + 2 * M[0][1]

        Cd, Cd0, Cc, Cc0 = cd(K), cd(K0), cc(K), cc(K0)
        Zdiff = 1 / (C_LIGHT * math.sqrt(Cd * Cd0))
        Zcommon = 1 / (C_LIGHT * math.sqrt(Cc * Cc0))
        out.update(
            Zdiff=Zdiff,
            Zcommon=Zcommon,
            Zodd=Zdiff / 2,
            Zeven=2 * Zcommon,
            eps_eff_odd=Cd / Cd0,
            eps_eff_even=Cc / Cc0,
        )
    out["matrices"] = {"C": K, "C0": K0, "L": Linv}
    return out


def solve_cross_section(
    section: dict, *, tol: float = 0.01, level: int = 0, max_level: int = 4, symmetry: bool = True
) -> FieldResult:
    """Solve a cross-section (the JS `solveCrossSection`; same section dict and options).

    section: ``{"conductors": [{x0?, x1?, y0, y1, net}], "dielectrics": [{x0?, x1?, y0, y1, er}],
    "ground": ["gnd"], "background": 1}``; a missing x0/x1 extends to the domain edge; later dielectrics win.
    tol: target relative error as estimated by the last refinement; level: the first grid level.
    """
    t0 = time.perf_counter()
    g = _normalise(section)
    max_level = max(level + 1, max_level)
    m = 0.5 * (g["extent"][0] + g["extent"][1])
    sym = _mirror_map(g, m) if symmetry else None
    levels: list[dict] = []
    est: dict = {}
    error: dict = {}
    worst = 0.0
    for lv in range(level, max_level + 1):
        K, K0, stats = _capacitances(g, lv, sym, m)
        levels.append({"level": lv, **_line_params(K, K0), "grid": stats})
        if len(levels) < 2:
            continue
        a, b = levels[-2], levels[-1]
        est, error = {}, {}
        for k in KEYS:
            if k in b:
                corr = ((b[k] - a[k]) * RATIO) / (1 - RATIO)
                est[k] = b[k] + corr
                error[k] = abs(corr / est[k])
        watch = [error[k] for k in ("Z0", "Zdiff", "Zcommon") if k in error]
        if not watch:
            for M in ("C", "C0"):
                for i, row in enumerate(b["matrices"][M]):
                    watch.append(abs((row[i] - a["matrices"][M][i][i]) * RATIO / (1 - RATIO) / row[i]))
        worst = max(watch)
        if worst <= tol:
            break
    fine = levels[-1]
    return FieldResult(
        solver="field",
        method="boarddd 2D quasi-static field solver (finite volumes)",
        signals=g["signals"],
        matrices=fine["matrices"],
        error=error,
        error_pct=100 * max([worst, *error.values()]),
        symmetry=sym or "none",
        levels=[
            {"level": lv["level"], **{k: lv[k] for k in KEYS if k in lv}, "grid": lv["grid"]} for lv in levels
        ],
        ms=1000 * (time.perf_counter() - t0),
        **est,
    )


# ── standard structures ────────────────────────────────────────────────────────────────────────────────────


def _req(p: dict, keys) -> None:
    for k in keys:
        if not (_finite(p.get(k)) and p[k] > 0):
            raise ValueError(f"{k} must be a positive number (got {p.get(k)})")
    if p.get("t") is not None and not p["t"] >= 0:
        raise ValueError(f"t must be >= 0 (got {p['t']})")
    if not (p.get("er") is not None and p["er"] >= 1):
        raise ValueError(f"er must be >= 1 (got {p.get('er')})")


def _traces(w, s, y, t):
    if s is None:
        return [{"x0": -w / 2, "x1": w / 2, "y0": y, "y1": y + t, "net": "sig"}]
    return [
        {"x0": -s / 2 - w, "x1": -s / 2, "y0": y, "y1": y + t, "net": "p"},
        {"x0": s / 2, "x1": s / 2 + w, "y0": y, "y1": y + t, "net": "n"},
    ]


def _coating(rects, y, c, erc):
    if not c > 0:
        return []
    out = [{"y0": y, "y1": y + c, "er": erc}]
    for r in rects:
        d = {"y0": y, "y1": r["y1"] + c, "er": erc}
        if r.get("x0") is not None:
            d["x0"] = r["x0"] - c
        if r.get("x1") is not None:
            d["x1"] = r["x1"] + c
        out.append(d)
    return out


def section_for(model: str, p: dict) -> dict:
    """The cross-section of a tier-1 model's parameters (the JS `sectionFor`; same extras c/erc, fence, gnd)."""
    t = p.get("t") or 0
    if model in ("microstrip", "coated_microstrip", "coupled_microstrip"):
        pair = model == "coupled_microstrip"
        _req(p, ["w", "h", "s"] if pair else ["w", "h"])
        tr = _traces(p["w"], p["s"] if pair else None, p["h"], t)
        return {
            "conductors": [{"y0": -max(t, p["h"] / 20), "y1": 0, "net": "gnd"}, *tr],
            "dielectrics": [
                {"y0": 0, "y1": p["h"], "er": p["er"]},
                *_coating(tr, p["h"], p.get("c") or 0, p.get("erc") or 1),
            ],
        }
    if model in ("stripline", "coupled_stripline"):
        pair = model == "coupled_stripline"
        h2 = p["h2"] if p.get("h2") is not None else p.get("h1")
        _req({**p, "h2": h2}, ["w", "h1", "h2", "s"] if pair else ["w", "h1", "h2"])
        b = p["h1"] + t + h2
        tp = max(t, b / 20)
        return {
            "conductors": [
                {"y0": -tp, "y1": 0, "net": "gnd"},
                {"y0": b, "y1": b + tp, "net": "gnd"},
                *_traces(p["w"], p["s"] if pair else None, p["h1"], t),
            ],
            "dielectrics": [{"y0": 0, "y1": b, "er": p["er"]}],
        }
    if model in ("cpw", "cpwg", "coupled_cpw", "coupled_cpwg"):
        pair = model.startswith("coupled")
        grounded = model.endswith("cpwg")
        _req(p, ["w", "gap", "h", "s"] if pair else ["w", "gap", "h"])
        h = p["h"]
        tr = _traces(p["w"], p["s"] if pair else None, h, t)
        e = (p["s"] / 2 + p["w"] if pair else p["w"] / 2) + p["gap"]
        tg = max(t, h / 20)
        gw = p.get("gnd")
        left = {"x1": -e, "y0": h, "y1": h + t, "net": "gnd"}
        right = {"x0": e, "y0": h, "y1": h + t, "net": "gnd"}
        if gw is not None:
            left = {"x0": -(e + gw), **left}
            right = {**right, "x1": e + gw}
        gnds = [left, right]
        conductors = [*tr, *gnds]
        if grounded:
            conductors.append({"y0": -tg, "y1": 0, "net": "gnd"})
            if p.get("fence") is not None:
                f = p["fence"]
                conductors += [
                    {"x1": -(e + f), "y0": 0, "y1": h + t, "net": "gnd"},
                    {"x0": e + f, "y0": 0, "y1": h + t, "net": "gnd"},
                ]
        return {
            "conductors": conductors,
            "dielectrics": [
                {"y0": 0, "y1": h, "er": p["er"]},
                *_coating([*tr, *gnds], h, p.get("c") or 0, p.get("erc") or 1),
            ],
        }
    raise ValueError(f"the field solver has no builder for model {model}")


def field_calculate(model: str, params: dict, **opts) -> FieldResult:
    """A tier-1 model (or coupled_cpw / coupled_cpwg) solved by the field solver (the JS `fieldCalculate`)."""
    r = solve_cross_section(section_for(model, params), **opts)
    return FieldResult(**{**asdict(r), "model": model, "flags": []})
