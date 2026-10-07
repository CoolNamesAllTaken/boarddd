"""Impedance along a net's route on a real board (impedance phase I6): route → cross-sections → impedance.

``analyze_net(board, copper, net | [p, n], **options)`` walks the net's tracks in order (vias join layers), cuts a
station every ``step`` mm, and at each station casts a line across the trace and intersects it with the copper
(boarddd/copper@1) on the trace's layer (coplanar grounds, the pair partner, other signals) and on the layers above
and below (the reference planes: solid under the trace and 3h beyond, an edge, a void or split, or none). That gives
the station's real cross-section in the board@1 stackup, which is classified (microstrip, embedded microstrip,
stripline, offset stripline, CPW, CPWG; edge-coupled for a pair) and solved (tier 2 by default, cached by its
quantised geometry). Stations with the same geometry merge into sections; the result is a boarddd/impedance@1
document (``result.ImpedanceAnalysis``) with the sections, their flags, the discontinuities along the route and a
length-weighted summary against the net class's target.

The JS twin is ``boarddd/impedance`` ``analyzeNet`` (src/impedance/route.js): the same code, line for line
(python/tests/test_impedance_route.py checks the two give the same document). The field solver needs the ``field``
extra (numpy, scipy). docs/impedance.md "Along a route" describes the method and its thresholds.
"""

from __future__ import annotations

import dataclasses
import math
import re
import time
from decimal import Decimal
from typing import Any

from .. import model as _model
from .closedform import calculate
from .result import ImpedanceAnalysis
from .stackup import line_from_stackup

__all__ = ["analyze_net", "net_route", "ROUTE_DEFAULTS", "SCHEMA_ID"]

SCHEMA_ID = "boarddd/impedance@1"

#: Defaults of analyze_net's options (mm unless said otherwise); the JS ROUTE_DEFAULTS in snake case.
ROUTE_DEFAULTS: dict[str, Any] = {
    "step": 0.25,  # station spacing along the route
    "solver": "field",  # 'field' (tier 2) or 'closedform' (tier 1, field where tier 1 has no model)
    "tolerance_pct": 10,  # when the target gives none
    "window": None,  # half-width of the cut line; default max(1, w/2 + 8 h)
    "coplanar_window": None,  # a ground this close to the trace edge makes it coplanar; default max(3 w, 5 h)
    "pair_window": None,  # the partner of a pair this close (edge to edge) and parallel couples; default max(0.5, 4 w)
    "parallel_deg": 20,  # largest angle between the pair's tracks that still counts as parallel
    "ref_margin": 3,  # a reference plane must reach this many h beyond the trace edges ('ref_edge' otherwise)
    "neighbours": "ignore",  # 'ground': other nets' copper within the coplanar window is solved as grounded
    "no_plane": "skip",  # no reference plane (via antipads, voids): no Z ('solve': CPW between coplanar grounds)
    # cross-sections covering >= short_length mm of route: within 0.5 % of fine solves; the rest within ~3 % (tested)
    "field_options": {"level": -1, "max_level": 0, "tol": 0.05},
    "short_field_options": {"level": -2, "max_level": -1, "tol": 0.05},
    "short_length": 1,
}

GROUND_NAME = re.compile(r"^(?:[ADPS]?GND|VSS|GROUND|EARTH|CHASSIS)(?:[_\-.].*)?$", re.I)
INF = math.inf


def _now() -> float:
    return time.perf_counter() * 1000


def R6(v: float) -> float:
    """JS Math.round(v * 1e6) / 1e6 (round half up), -0 as 0."""
    x = math.floor(v * 1e6 + 0.5) / 1e6
    return 0.0 if x == 0 else x


def _q(v: float, step: float) -> float:
    x = math.floor(v / step + 0.5) * step
    x = math.floor(x * 1e6 + 0.5) / 1e6
    return 0.0 if x == 0 else x


def _qlog(v: float, rel: float, floor: float) -> float:
    """v rounded to a geometric grid of ratio 1 + rel (at least `floor`): gaps that differ by less share a solve."""
    if not (v > floor):
        return R6(floor if max(v, 0) > floor / 2 else 0)
    k = math.floor(math.log(v / floor) / math.log(1 + rel) + 0.5)
    return R6(floor * (1 + rel) ** k)


def _jsnum(v) -> str:
    """JSON.stringify of a number."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int) or (v == int(v) and abs(v) < 1e21):
        return str(int(v))
    r = repr(float(v))
    if "e" not in r:
        return r
    m, e = r.split("e")
    e = int(e)
    if -7 < e < 21:
        return format(Decimal(r), "f")
    return f"{m}e{'+' if e > 0 else '-'}{abs(e)}"


def _js(v) -> str:
    """JSON.stringify (compact; key order kept) — for keys and the canonical mirror choice, as the JS does."""
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return _jsnum(v)
    if isinstance(v, str):
        import json

        return json.dumps(v, ensure_ascii=False)
    if isinstance(v, (list, tuple)):
        return "[" + ",".join(_js(x) for x in v) + "]"
    return "{" + ",".join(f"{_js(str(k))}:{_js(x)}" for k, x in v.items()) + "}"


def _plain(obj):
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return _model.to_dict(obj)
    return obj


# ── geometry ─────────────────────────────────────────────────────────────────────────────────────────────────


def _arc_of(t: dict):
    (ax, ay), (mx, my), (bx, by) = t["start"], t["mid"], t["end"]
    d = 2 * (ax * (my - by) + mx * (by - ay) + bx * (ay - my))
    if abs(d) < 1e-12:
        return None
    ux = ((ax * ax + ay * ay) * (my - by) + (mx * mx + my * my) * (by - ay) + (bx * bx + by * by) * (ay - my)) / d
    uy = ((ax * ax + ay * ay) * (bx - mx) + (mx * mx + my * my) * (ax - bx) + (bx * bx + by * by) * (mx - ax)) / d
    r = math.sqrt((ax - ux) ** 2 + (ay - uy) ** 2)
    a0, am, a1 = math.atan2(ay - uy, ax - ux), math.atan2(my - uy, mx - ux), math.atan2(by - uy, bx - ux)

    def ccw(a, b):
        x = b - a
        while x < 0:
            x += 2 * math.pi
        while x >= 2 * math.pi:
            x -= 2 * math.pi
        return x

    sweep = ccw(a0, a1)
    if ccw(a0, am) > sweep:  # mid is not on the counter-clockwise way: clockwise
        sweep -= 2 * math.pi
    return {"c": (ux, uy), "r": r, "a0": a0, "sweep": sweep}


class _Path:
    """A track as a path: length, point/direction at a distance along it, capsules."""

    def __init__(self, t: dict):
        self.arc = _arc_of(t) if t.get("mid") is not None else None
        if self.arc:
            self.len = abs(self.arc["sweep"]) * self.arc["r"]
            self.sg = math.copysign(1, self.arc["sweep"]) if self.arc["sweep"] != 0 else 0
        else:
            (ax, ay), (bx, by) = t["start"], t["end"]
            self.len = math.sqrt((bx - ax) ** 2 + (by - ay) ** 2)
            self.a = (ax, ay)
            self.d = ((bx - ax) / self.len, (by - ay) / self.len) if self.len > 0 else (1.0, 0.0)
        self.t = t

    def at(self, s: float):
        if self.arc:
            arc = self.arc
            a = arc["a0"] + (arc["sweep"] * s) / self.len
            p = (arc["c"][0] + arc["r"] * math.cos(a), arc["c"][1] + arc["r"] * math.sin(a))
            return p, (-math.sin(a) * self.sg, math.cos(a) * self.sg)
        return (self.a[0] + self.d[0] * s, self.a[1] + self.d[1] * s), self.d

    def pieces(self):
        if self.arc:
            n = max(1, math.ceil((abs(self.arc["sweep"]) / (math.pi / 2)) * 8))
            pts = [self.at((self.len * i) / n)[0] for i in range(n + 1)]
            return [(pts[i], pts[i + 1]) for i in range(n)]
        return [(tuple(self.t["start"]), tuple(self.t["end"]))]


def _bbox(pts):
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return (min(xs), min(ys), max(xs), max(ys))


def _layer_index(copper: dict, layer: str) -> dict:
    """The copper of one layer on a 1 mm grid: capsules (tracks), discs (vias), polygons with holes (pads, zones)."""
    items: list[dict] = []
    for t in copper.get("tracks") or []:
        if t["layer"] != layer:
            continue
        w = t["width"]
        for a, b in _Path(t).pieces():
            items.append(
                {
                    "type": "capsule",
                    "a": a,
                    "b": b,
                    "r": w / 2,
                    "net": t["net"],
                    "kind": "track",
                    "id": t.get("id"),
                    "width": w,
                    "box": (
                        min(a[0], b[0]) - w / 2,
                        min(a[1], b[1]) - w / 2,
                        max(a[0], b[0]) + w / 2,
                        max(a[1], b[1]) + w / 2,
                    ),
                }
            )
    order = {lid: i for i, lid in enumerate(copper.get("layers") or [])}
    for v in copper.get("vias") or []:
        lo, hi, at = order.get(v["span"][0]), order.get(v["span"][1]), order.get(layer)
        if at is None or lo is None or hi is None or at < lo or at > hi:
            continue
        if v.get("pad_layers") and layer not in v["pad_layers"]:
            continue
        dia = next((p["diameter"] for p in v.get("padstack") or [] if p["layer"] == layer), v["diameter"])
        r = dia / 2
        c = v["at"]
        items.append(
            {
                "type": "disc",
                "c": c,
                "r": r,
                "net": v["net"],
                "kind": "via",
                "box": (c[0] - r, c[1] - r, c[0] + r, c[1] + r),
            }
        )
    for p in copper.get("pads") or []:
        if layer not in p["layers"]:
            continue
        for ring in p.get("polygons") or []:
            if len(ring) >= 3:
                items.append({"type": "poly", "rings": [ring], "net": p["net"], "kind": "pad", "box": _bbox(ring)})
    for z in copper.get("zones") or []:
        if z["layer"] != layer:
            continue
        for f in z["fill"]:
            items.append(
                {
                    "type": "poly",
                    "rings": [f["outline"], *f.get("holes", [])],
                    "net": z["net"],
                    "kind": "teardrop" if z["kind"] == "teardrop" else "zone",
                    "box": _bbox(f["outline"]),
                }
            )
    grid: dict[tuple[int, int], list[int]] = {}
    for i, it in enumerate(items):
        b = it["box"]
        for gx in range(math.floor(b[0]), math.floor(b[2]) + 1):
            for gy in range(math.floor(b[1]), math.floor(b[3]) + 1):
                grid.setdefault((gx, gy), []).append(i)
    return {"items": items, "grid": grid}


def _query(idx: dict, p0, p1) -> list[dict]:
    x0, x1 = math.floor(min(p0[0], p1[0])), math.floor(max(p0[0], p1[0]))
    y0, y1 = math.floor(min(p0[1], p1[1])), math.floor(max(p0[1], p1[1]))
    seen: set[int] = set()
    for gx in range(x0, x1 + 1):
        for gy in range(y0, y1 + 1):
            seen.update(idx["grid"].get((gx, gy), ()))
    return [idx["items"][i] for i in sorted(seen)]


def _crossings(it: dict, p, d, n) -> list[list[float]]:
    """Where an item crosses the cut line through p along n, as [lo, hi] intervals (u = distance along n)."""

    def loc(q):
        dx, dy = q[0] - p[0], q[1] - p[1]
        return (dx * d[0] + dy * d[1], dx * n[0] + dy * n[1])

    def disc(c, r):
        a, u = loc(c)
        if abs(a) > r:
            return None
        h = math.sqrt(r * r - a * a)
        return [u - h, u + h]

    def ring_cuts(ring):
        out = []
        a1, u1 = loc(ring[-1])
        for qq in ring:
            a2, u2 = loc(qq)
            if (a1 > 0) != (a2 > 0):
                out.append(u1 + ((0 - a1) * (u2 - u1)) / (a2 - a1))
            a1, u1 = a2, u2
        return out

    if it["type"] == "disc":
        iv = disc(it["c"], it["r"])
        return [iv] if iv else []
    if it["type"] == "capsule":
        parts = [x for x in (disc(it["a"], it["r"]), disc(it["b"], it["r"])) if x]
        dx, dy = it["b"][0] - it["a"][0], it["b"][1] - it["a"][1]
        L = math.sqrt(dx * dx + dy * dy)
        if L > 0:
            m = ((-dy / L) * it["r"], (dx / L) * it["r"])
            a, b = it["a"], it["b"]
            quad = [
                (a[0] + m[0], a[1] + m[1]),
                (b[0] + m[0], b[1] + m[1]),
                (b[0] - m[0], b[1] - m[1]),
                (a[0] - m[0], a[1] - m[1]),
            ]
            c = sorted(ring_cuts(quad))
            if len(c) >= 2:
                parts.append([c[0], c[-1]])
        if not parts:
            return []
        return [[min(x[0] for x in parts), max(x[1] for x in parts)]]
    cuts = sorted(c for ring in it["rings"] for c in ring_cuts(ring))
    return [[cuts[i], cuts[i + 1]] for i in range(0, len(cuts) - 1, 2)]


def _union(ivs) -> list[list[float]]:
    out: list[list[float]] = []
    for lo, hi in sorted(([a, b] for a, b in ivs), key=lambda x: (x[0], x[1])):
        if out and lo <= out[-1][1] + 1e-6:
            out[-1][1] = max(out[-1][1], hi)
        else:
            out.append([lo, hi])
    return out


# ── the route ────────────────────────────────────────────────────────────────────────────────────────────────


def net_route(copper, net: str) -> list[dict]:
    """A net's tracks in route order (the JS netRoute): depth first from the end with the lowest (x, y), each track
    oriented the way it is walked, with ``s0`` its distance from the start and ``run`` its unbroken stretch."""
    copper = _plain(copper)
    tracks = [(i, t) for i, t in enumerate(copper.get("tracks") or []) if t["net"] == net]

    def key(p):
        return (math.floor(p[0] * 1000 + 0.5), math.floor(p[1] * 1000 + 0.5))

    at: dict = {}
    pos: dict = {}
    for i, t in tracks:
        for end in (t["start"], t["end"]):
            k = key(end)
            if k not in at:
                at[k] = []
                pos[k] = end
            at[k].append((i, t))
    visited: set[int] = set()
    out: list[dict] = []
    run = -1
    s_base = 0.0
    nodes = sorted(at, key=lambda k: (0 if len(at[k]) == 1 else 1, pos[k][0], pos[k][1]))
    for start in nodes:
        if all(i in visited for i, _ in at[start]):
            continue
        stack = [(start, s_base)]
        last = None
        while stack:
            node, s = stack.pop()
            nxt = sorted((e for e in at[node] if e[0] not in visited), key=lambda e: e[0])
            if not nxt:
                continue
            i, t = nxt[0]
            visited.add(i)
            if len(nxt) > 1:
                stack.append((node, s))
            forward = key(t["start"]) == node
            if last != node:
                run += 1
            path = _Path(t)
            out.append(
                {"track": t, "index": i, "forward": forward, "s0": s, "s1": s + path.len, "run": run, "path": path}
            )
            other = key(t["end"] if forward else t["start"])
            last = other
            stack.append((other, s + path.len))
            s_base = max(s_base, s + path.len)
    return out


# ── stackup ──────────────────────────────────────────────────────────────────────────────────────────────────


class _Stack:
    def __init__(self, stackup: dict):
        self.layers = (stackup or {}).get("layers") or []
        self.copper = [
            {
                "name": lay.get("layer") or lay["name"],
                "index": i,
                "t": 0.035 if lay.get("thickness") is None else lay["thickness"],
            }
            for i, lay in enumerate(self.layers)
            if lay.get("kind") == "copper"
        ]
        self.order = {c["name"]: i for i, c in enumerate(self.copper)}

    def between(self, a: int, b: int) -> float:
        """Dielectric (and voided copper) distance between copper a and b, edge to edge."""
        i, j = sorted((self.copper[a]["index"], self.copper[b]["index"]))
        h = 0.0
        for k in range(i + 1, j):
            lay = self.layers[k]
            if lay.get("kind") in ("dielectric", "copper"):
                h += lay.get("thickness") or 0
        return h


# ── one station ──────────────────────────────────────────────────────────────────────────────────────────────


def _station(ctx: dict, st: dict) -> dict:
    """The environment of the trace at a station (the JS station)."""
    o, stack, index, ground_nets = ctx["o"], ctx["stack"], ctx["index"], ctx["ground_nets"]
    li = stack.order[st["layer"]]
    d = st["d"]
    n = (-d[1], d[0])
    w = st["width"]
    near_h = [stack.between(li, j) for j in (li - 1, li + 1) if 0 <= j < len(stack.copper)]
    h0 = min(near_h) if near_h else 0.2
    R = o["window"] if o["window"] is not None else max(1, w / 2 + 8 * h0)
    cop = o["coplanar_window"] if o["coplanar_window"] is not None else max(3 * w, 5 * h0)
    pair_win = o["pair_window"] if o["pair_window"] is not None else max(0.5, 4 * w)
    p = st["p"]
    p0, p1 = (p[0] - n[0] * R, p[1] - n[1] * R), (p[0] + n[0] * R, p[1] + n[1] * R)
    flags: list[str] = []
    hits: list[dict] = []
    for it in _query(index(st["layer"]), p0, p1):
        if it["net"] == st["net"]:
            continue
        for lo, hi in _crossings(it, p, d, n):
            if hi < -R or lo > R:
                continue
            hits.append({"lo": max(lo, -R), "hi": min(hi, R), "it": it})
    partner = None
    if st["partner"]:
        cos = math.cos((o["parallel_deg"] * math.pi) / 180)
        for h in hits:
            it = h["it"]
            if it["net"] != st["partner"] or it["kind"] != "track":
                continue
            dx, dy = it["b"][0] - it["a"][0], it["b"][1] - it["a"][1]
            L = math.sqrt(dx * dx + dy * dy)
            if not (L > 0) or abs((dx * d[0] + dy * d[1]) / L) < cos:
                continue
            side = 1 if h["lo"] + h["hi"] > 0 else -1
            gap = h["lo"] - w / 2 if side > 0 else -w / 2 - h["hi"]
            if gap < 0 or gap > pair_win:
                continue
            if partner is None or gap < partner["gap"]:
                partner = {"side": side, "gap": gap, "width": it["width"]}
    traces = [{"x0": -w / 2, "x1": w / 2, "net": "p" if partner else "sig"}]
    if partner:
        g, wn = _qlog(partner["gap"], 0.02, 0.01), _q(partner["width"], 0.001)
        if partner["side"] > 0:
            traces.append({"x0": R6(w / 2 + g), "x1": R6(w / 2 + g + wn), "net": "n"})
        else:
            traces.append({"x0": R6(-w / 2 - g - wn), "x1": R6(-w / 2 - g), "net": "n"})
    left, right = min(t["x0"] for t in traces), max(t["x1"] for t in traces)

    def is_ground(it):
        return it["kind"] == "zone" or it["net"] in ground_nets

    grounds: list[dict] = []
    coplanar: list[float | None] = [None, None]
    for side in (-1, 1):
        edge = right if side > 0 else left
        excl = st["partner"] if partner else None
        beyond = [h for h in hits if h["it"]["net"] != excl and (h["hi"] > edge if side > 0 else h["lo"] < edge)]
        near_g = near_s = None
        for h in beyond:
            dist = h["lo"] - edge if side > 0 else edge - h["hi"]
            if dist < 0:
                if "overlap" not in flags:
                    flags.append("overlap")
                continue
            if is_ground(h["it"]):
                if near_g is None or dist < near_g[0]:
                    near_g = (dist, h)
            elif near_s is None or dist < near_s[0]:
                near_s = (dist, h)
        if near_s and near_s[0] <= cop and (near_g is None or near_s[0] < near_g[0]):
            if "neighbour" not in flags:
                flags.append("neighbour")
            if o["neighbours"] == "ground":
                near_g = near_s
        if near_g and near_g[0] <= cop:
            ivs = _union([h["lo"], h["hi"]] for h in beyond if is_ground(h["it"]) or h is near_g[1])
            g0 = edge + near_g[0] if side > 0 else edge - near_g[0]
            run = next((iv for iv in ivs if iv[0] <= g0 + 1e-6 and iv[1] >= g0 - 1e-6), [g0, g0])
            gap = _qlog(near_g[0], 0.1, 0.005)
            near = R6(edge + gap if side > 0 else edge - gap)
            far = run[1] if side > 0 else run[0]
            # a ground at least max(2 h, 2 w, 0.2 mm) wide is solved as unbounded; a narrower strip as it is (0.1 mm)
            wide = abs(far) >= R - 1e-6 or abs(far - near) >= max(2 * h0, 2 * w, 0.2)
            if wide:
                far_q = None
            elif side > 0:
                far_q = R6(max(near + 0.1, _q(far, 0.1)))
            else:
                far_q = R6(min(near - 0.1, _q(far, 0.1)))
            grounds.append({"x0": near, "x1": far_q} if side > 0 else {"x0": far_q, "x1": near})
            coplanar[1 if side > 0 else 0] = gap
    refs: dict[str, dict | None] = {"top": None, "bottom": None}
    planes: dict[str, dict | None] = {"top": None, "bottom": None}
    for side, step in (("top", -1), ("bottom", 1)):
        skipped: list[str] = []
        j = li + step
        while 0 <= j < len(stack.copper):
            name = stack.copper[j]["name"]
            h = stack.between(li, j)
            zs = [it for it in _query(index(name), p0, p1) if it["kind"] == "zone"]
            cov = _union(
                [max(lo, -R), min(hi, R)] for it in zs for lo, hi in _crossings(it, p, d, n) if min(hi, R) > max(lo, -R)
            )
            under = next((iv for iv in cov if iv[0] <= left + 1e-6 and iv[1] >= right - 1e-6), None)
            if under is None:
                touched = any(iv[1] > left and iv[0] < right for iv in cov)
                if (touched or name in ctx["plane_layers"]) and "plane_gap" not in flags:
                    flags.append("plane_gap")
                skipped.append(name)
                j += step
                continue
            mid = (left + right) / 2
            z_net = next(
                (it["net"] for it in zs if any(lo <= mid and hi >= mid for lo, hi in _crossings(it, p, d, n))), ""
            )
            margin = o["ref_margin"] * h
            x0 = None if under[0] <= -R + 1e-6 else R6(_q(under[0], 0.1))
            x1 = None if under[1] >= R - 1e-6 else R6(_q(under[1], 0.1))
            if (x0 is not None and x0 > left - margin) or (x1 is not None and x1 < right + margin):
                if "ref_edge" not in flags:
                    flags.append("ref_edge")
                planes[side] = {"x0": x0, "x1": x1}
            refs[side] = {"side": side, "layer": name, "net": z_net, "h": R6(h), "extent": [x0, x1], "skipped": skipped}
            break
    structure = _classify(stack, li, refs, len(grounds) > 0)
    if not refs["top"] and not refs["bottom"]:
        flags.append("no_ref")
    layout = {
        "traces": [{"x0": R6(t["x0"]), "x1": R6(t["x1"]), "net": t["net"]} for t in traces],
        "grounds": grounds,
        "planes": planes,
    }
    return {
        "structure": structure,
        "partner": partner,
        "coplanar": coplanar,
        "refs": refs,
        "flags": flags,
        "layout": layout,
        "kind": "differential" if partner else "single",
    }


def _classify(stack: _Stack, li: int, refs: dict, coplanar: bool) -> str:
    outer = li == 0 or li == len(stack.copper) - 1
    n = (1 if refs["top"] else 0) + (1 if refs["bottom"] else 0)
    if n == 0:
        return "cpw" if coplanar else "none"
    if n == 1:
        return "cpwg" if coplanar else "microstrip" if outer else "embedded_microstrip"
    if coplanar:
        return "cpwg"
    h1, h2 = refs["top"]["h"], refs["bottom"]["h"]
    return "offset_stripline" if abs(h1 - h2) / max(h1, h2) > 0.1 else "stripline"


def _overridden(env: dict, ov: dict | None, stack: _Stack, layer: str) -> dict:
    """Apply a user override ({structure?, ref_top?, ref_bottom?, coplanar?}) to a station's environment."""
    if not ov:
        return env
    import copy

    e = copy.deepcopy(env)
    e["flags"].append("override")

    def drop(side):
        e["refs"][side] = None
        e["layout"]["planes"][side] = None

    if ov.get("ref_top") is False:
        drop("top")
    if ov.get("ref_bottom") is False:
        drop("bottom")
    if ov.get("coplanar") is False or ov.get("structure") in (
        "microstrip",
        "embedded_microstrip",
        "stripline",
        "offset_stripline",
    ):
        e["layout"]["grounds"] = []
        e["coplanar"] = [None, None]
    if ov.get("structure") == "cpw":
        drop("top")
        drop("bottom")
    for side, name in (("top", ov.get("ref_top")), ("bottom", ov.get("ref_bottom"))):
        if isinstance(name, str) and name != (e["refs"][side] or {}).get("layer"):
            li, j = stack.order.get(layer), stack.order.get(name)
            if j is None:
                raise ValueError(f"override: no copper layer {name}")
            e["refs"][side] = {
                "side": side,
                "layer": name,
                "net": "",
                "h": R6(stack.between(li, j)),
                "extent": [None, None],
                "skipped": [],
            }
            e["layout"]["planes"][side] = None
    e["structure"] = ov.get("structure") or _classify(
        stack, stack.order[layer], e["refs"], len(e["layout"]["grounds"]) > 0
    )
    return e


# ── solving ──────────────────────────────────────────────────────────────────────────────────────────────────


def _mirrored(layout: dict) -> dict:
    def ext(e):
        if not e:
            return None
        return {"x0": None if e["x1"] is None else R6(-e["x1"]), "x1": None if e["x0"] is None else R6(-e["x0"])}

    return {
        "traces": sorted(
            ({"x0": R6(-t["x1"]), "x1": R6(-t["x0"]), "net": t["net"]} for t in layout["traces"]), key=lambda t: t["x0"]
        ),
        "grounds": sorted((ext(g) for g in layout["grounds"]), key=lambda g: -INF if g["x0"] is None else g["x0"]),
        "planes": {"top": ext(layout["planes"]["top"]), "bottom": ext(layout["planes"]["bottom"])},
    }


def _canonical(layout: dict) -> dict:
    """Of the two mirror images, the one whose JSON sorts first (one solve for both)."""
    a = {**layout, "traces": sorted(layout["traces"], key=lambda t: t["x0"])}
    b = _mirrored(layout)
    return b if _js(b) < _js(a) else a


def _geometry_key(layer: str, env: dict, solver: str) -> str:
    refs = env["refs"]
    return _js(
        [
            layer,
            solver,
            env["structure"],
            refs["top"]["layer"] if refs["top"] else None,
            refs["bottom"]["layer"] if refs["bottom"] else None,
            _canonical(env["layout"]),
        ]
    )


def _z_of(r, pair: bool) -> dict:
    if pair:
        return {
            "Z0": None,
            "Zdiff": R6(r.Zdiff),
            "Zcommon": R6(r.Zcommon),
            "Zodd": R6(r.Zodd),
            "Zeven": R6(r.Zeven),
            "eps_eff": None,
        }
    return {"Z0": R6(r.Z0), "Zdiff": None, "Zcommon": None, "Zodd": None, "Zeven": None, "eps_eff": R6(r.eps_eff)}


def _solve(ctx: dict, layer: str, env: dict, width: float, fo: dict):
    if env["structure"] == "none" or (env["structure"] == "cpw" and ctx["o"]["no_plane"] != "solve"):
        return None
    from .fieldsolver import solve_cross_section

    pair = env["kind"] == "differential"
    refs = env["refs"]
    ref_top = refs["top"]["layer"] if refs["top"] else False
    ref_bottom = refs["bottom"]["layer"] if refs["bottom"] else False
    inner2 = bool(refs["top"] and refs["bottom"])
    if ctx["o"]["solver"] == "closedform":
        t1 = _tier1(ctx, layer, env, width)
        if t1:
            return t1
    line = line_from_stackup(
        ctx["stackup"],
        layer,
        width=width,
        kind="single",
        ref_top=ref_top,
        ref_bottom=ref_bottom,
        structure="stripline" if inner2 else "microstrip",
        solver="field",
        layout=_canonical(env["layout"]),
    )
    r = solve_cross_section(line.section, tol=fo["tol"], level=fo["level"], max_level=fo["max_level"])
    return {**_z_of(r, pair), "error_pct": R6(r.error_pct), "solver": "field", "model": None, "warnings": line.warnings}


def _tier1(ctx: dict, layer: str, env: dict, width: float):
    pair = env["kind"] == "differential"
    st = {
        "microstrip": "microstrip",
        "stripline": "stripline",
        "offset_stripline": "stripline",
        "cpwg": "coplanar_grounded",
        "cpw": "coplanar",
    }.get(env["structure"])
    refs = env["refs"]
    if not st or (pair and st.startswith("coplanar")):
        return None
    if st == "coplanar_grounded" and refs["top"] and refs["bottom"]:
        return None
    gaps = [g for g in env["coplanar"] if g is not None]
    nt = next((t for t in env["layout"]["traces"] if t["net"] == "n"), None)
    kw: dict = {}
    if pair:
        kw["gap"] = nt["x0"] - width / 2 if nt["x0"] > 0 else -width / 2 - nt["x1"]
    if gaps:
        kw["coplanar_gap"] = min(gaps)
    try:
        line = line_from_stackup(
            ctx["stackup"],
            layer,
            width=width,
            kind=env["kind"],
            structure=st,
            ref_top=refs["top"]["layer"] if refs["top"] else None,
            ref_bottom=refs["bottom"]["layer"] if refs["bottom"] else None,
            **kw,
        )
    except ValueError:
        return None
    r = calculate(line.model, line.params)
    return {**_z_of(r, pair), "error_pct": None, "solver": "closedform", "model": line.model, "warnings": line.warnings}


# ── the analysis ─────────────────────────────────────────────────────────────────────────────────────────────


def _target_of(board: dict, net: str, kind: str, o: dict, warnings: list[str]):
    key = "Zdiff" if kind == "differential" else "Z0"
    if o.get("target") is not None:
        t = {"value": o["target"]} if isinstance(o["target"], (int, float)) else o["target"]
        tol = t.get("tolerance_pct")
        return {
            "value": t["value"],
            "key": key,
            "tolerance_pct": o["tolerance_pct"] if tol is None else tol,
            "source": "option",
            "net_class": None,
        }
    cls = next((x.get("net_class") for x in board.get("nets") or [] if x["name"] == net), None)
    nc = next((c for c in board.get("net_classes") or [] if c["name"] == cls), None)
    imp = (nc or {}).get("impedance")
    if not imp:
        return None
    if (imp["kind"] == "differential") != (kind == "differential"):
        what = "pair" if kind == "differential" else "net"
        warnings.append(
            f"net class {nc['name']}: its {_jsnum(imp['target'])} Ω target is {imp['kind']}; applied to the {what} as {key}"
        )
    tol = imp.get("tolerance_pct")
    return {
        "value": imp["target"],
        "key": key,
        "tolerance_pct": o["tolerance_pct"] if tol is None else tol,
        "source": "net_class",
        "net_class": nc["name"],
    }


def analyze_net(board, copper, net: str | list[str] | tuple[str, str], *, cache: dict | None = None, **options):
    """Impedance along a net's route (the JS analyzeNet): a boarddd/impedance@1 ``ImpedanceAnalysis``.

    ``board`` and ``copper``: boarddd/board@1 and boarddd/copper@1 (dataclasses or their dicts); ``net``: a net, or
    ``[p, n]`` for a differential pair. Options (snake case of the JS ones): see ROUTE_DEFAULTS, plus ``target`` (Ω
    or {value, tolerance_pct}), ``ground_nets``, ``overrides`` ({net: {...}, tracks: {id: {...}}} with structure,
    ref_top, ref_bottom (a layer or False), coplanar: False) and ``cache`` (a dict shared between calls).
    """
    t0 = _now()
    board, copper = _plain(board), _plain(copper)
    o = {**ROUTE_DEFAULTS, **options}
    o["field_options"] = {**ROUTE_DEFAULTS["field_options"], **(options.get("field_options") or {})}
    o["short_field_options"] = {**ROUTE_DEFAULTS["short_field_options"], **(options.get("short_field_options") or {})}
    nets = list(net) if isinstance(net, (list, tuple)) else [net]
    if not 1 <= len(nets) <= 2:
        raise ValueError("analyze_net takes a net or a [p, n] pair")
    kind = "differential" if len(nets) == 2 else "single"
    stack = _Stack(board.get("stackup"))
    if not stack.copper:
        raise ValueError("the board has no stackup copper layers")
    warnings: list[str] = []
    indexes: dict[str, dict] = {}
    plane_layers = {p["layer"] for p in copper.get("planes") or [] if p["solid"]}
    ground_nets = {p["net"] for p in copper.get("planes") or [] if p["solid"]}
    ground_nets |= {x for x in copper.get("nets") or [] if GROUND_NAME.match(x)}
    ground_nets |= set(o.get("ground_nets") or [])
    ground_nets -= set(nets)

    def index(layer):
        if layer not in indexes:
            indexes[layer] = _layer_index(copper, layer)
        return indexes[layer]

    ctx = {
        "o": o,
        "stackup": board.get("stackup"),
        "stack": stack,
        "ground_nets": ground_nets,
        "plane_layers": plane_layers,
        "index": index,
    }
    cache = cache if cache is not None else {}
    solves = hits = stations = 0
    sections: list[dict] = []
    routes = [net_route(copper, x) for x in nets]
    if not routes[0]:
        raise ValueError(f"net {nets[0]} has no tracks")
    overrides = o.get("overrides") or {}
    for k, x in enumerate(nets):
        for e in routes[k]:
            t = e["track"]
            if t["layer"] not in stack.order:
                warnings.append(f"{t['layer']} is not in the stackup: track skipped")
                continue
            plen = e["path"].len
            nb = max(1, math.ceil(plen / o["step"] - 1e-9))
            ov = (overrides.get("tracks") or {}).get(t.get("id")) or overrides.get("net")
            for b in range(nb):
                f0, f1 = (plen * b) / nb, (plen * (b + 1)) / nb
                fm = (f0 + f1) / 2

                def g(f, e=e, plen=plen):
                    return f if e["forward"] else plen - f

                p, dd = e["path"].at(g(fm))
                if not e["forward"]:
                    dd = (-dd[0], -dd[1])
                st = {
                    "net": x,
                    "partner": nets[1 - k] if kind == "differential" else None,
                    "layer": t["layer"],
                    "width": t["width"],
                    "p": p,
                    "d": dd,
                }
                stations += 1
                env = _station(ctx, st)
                if k == 1 and env["partner"]:
                    continue  # the coupled stretches are the first net's sections
                if kind == "differential" and not env["partner"]:
                    env["flags"].append("uncoupled")
                env = _overridden(env, ov, stack, t["layer"])
                key = _geometry_key(t["layer"], env, o["solver"])
                prev = sections[-1] if sections else None
                s0, s1 = R6(e["s0"] + f0), R6(e["s0"] + f1)
                pa, pb = e["path"].at(g(f0))[0], e["path"].at(g(f1))[0]
                if (
                    prev
                    and prev["_key"] == key
                    and prev["net"] == x
                    and prev["_run"] == e["run"]
                    and abs(prev["s1"] - s0) < 1e-6
                    and prev["geometry"]["width"] == t["width"]
                ):
                    prev["s1"] = s1
                    prev["end"] = [R6(pb[0]), R6(pb[1])]
                    prev["length"] = R6(prev["s1"] - prev["s0"])
                    if t.get("id") and t["id"] not in prev["tracks"]:
                        prev["tracks"].append(t["id"])
                    continue
                nt = next((tr for tr in env["layout"]["traces"] if tr["net"] == "n"), None)
                refs = env["refs"]
                sections.append(
                    {
                        "_key": key,
                        "_run": e["run"],
                        "_env": env,
                        "net": x,
                        "start": [R6(pa[0]), R6(pa[1])],
                        "end": [R6(pb[0]), R6(pb[1])],
                        "s0": s0,
                        "s1": s1,
                        "length": R6(s1 - s0),
                        "layer": t["layer"],
                        "structure": env["structure"],
                        "kind": env["kind"],
                        "geometry": {
                            "width": t["width"],
                            "thickness": stack.copper[stack.order[t["layer"]]]["t"],
                            "partner_width": R6(nt["x1"] - nt["x0"]) if nt else None,
                            "gap": (
                                R6(nt["x0"] - t["width"] / 2 if nt["x0"] > 0 else -t["width"] / 2 - nt["x1"])
                                if nt
                                else None
                            ),
                            "coplanar_gap": [env["coplanar"][0], env["coplanar"][1]],
                            "h_top": refs["top"]["h"] if refs["top"] else None,
                            "h_bottom": refs["bottom"]["h"] if refs["bottom"] else None,
                        },
                        "z": None,
                        "refs": [
                            {k2: r[k2] for k2 in ("side", "layer", "net", "h", "extent", "skipped")}
                            for r in (refs["top"], refs["bottom"])
                            if r
                        ],
                        "flags": sorted(set(env["flags"])),
                        "tracks": [t["id"]] if t.get("id") else [],
                    }
                )
    t_solve = _now()
    # how much route each cross-section covers decides its accuracy (field_options or short_field_options)
    covers: dict[str, float] = {}
    for sec in sections:
        covers[sec["_key"]] = covers.get(sec["_key"], 0.0) + sec["length"]
    for sec in sections:
        fine = covers[sec["_key"]] >= o["short_length"] - 1e-9
        had = cache.get(sec["_key"])
        if not had or (fine and not had["fine"]):
            solves += 1
            fo = o["field_options"] if fine else o["short_field_options"]
            try:
                z = _solve(ctx, sec["layer"], sec["_env"], sec["geometry"]["width"], fo)
            except (ValueError, ArithmeticError) as err:
                z = None
                sec["flags"].append("solver_error")
                warnings.append(f"{sec['net']} at s={_jsnum(sec['s0'])}: {err}")
            cache[sec["_key"]] = {"z": z, "fine": fine}
        else:
            hits += 1
        z = cache[sec["_key"]]["z"]
        if z:
            for w in z.get("warnings") or []:
                if w not in warnings:
                    warnings.append(w)
            sec["z"] = {
                k2: z[k2]
                for k2 in ("Z0", "Zdiff", "Zcommon", "Zodd", "Zeven", "eps_eff", "error_pct", "solver", "model")
            }
            if kind == "differential" and sec["kind"] == "single":
                sec["z"]["Zdiff"] = R6(2 * z["Z0"])  # uncoupled: two single lines
    target = _target_of(board, nets[0], kind, o, warnings)
    summary = _summarise([s for s in sections if s["net"] == nets[0]], target, kind)
    discontinuities = _discontinuities(sections)
    for s in sections:
        for k2 in ("_key", "_run", "_env"):
            del s[k2]
    solve_ms = _now() - t_solve
    ms = _now() - t0
    doc = {
        "schema": SCHEMA_ID,
        "board": board.get("name"),
        "nets": nets,
        "kind": kind,
        "solver": o["solver"],
        "target": target,
        "sections": sections,
        "summary": summary,
        "discontinuities": discontinuities,
        "options": {
            "step": o["step"],
            "solver": o["solver"],
            "tolerance_pct": o["tolerance_pct"],
            "window": o["window"],
            "coplanar_window": o["coplanar_window"],
            "pair_window": o["pair_window"],
            "parallel_deg": o["parallel_deg"],
            "ref_margin": o["ref_margin"],
            "neighbours": o["neighbours"],
            "no_plane": o["no_plane"],
            "short_length": o["short_length"],
        },
        "warnings": warnings,
        "timing": {
            "ms": math.floor(ms * 10 + 0.5) / 10,
            "solve_ms": math.floor(solve_ms * 10 + 0.5) / 10,
            "stations": stations,
            "solves": solves,
            "cache_hits": hits,
        },
    }
    return ImpedanceAnalysis.from_dict(doc)


def _summarise(secs: list[dict], target, kind: str) -> dict:
    key = "Zdiff" if kind == "differential" else "Z0"
    length = with_z = total = out = 0.0
    lo = hi = None
    for s in secs:
        length += s["length"]
        v = (s["z"] or {}).get(key)
        if v is None:
            continue
        with_z += s["length"]
        total += v * s["length"]
        lo = v if lo is None else min(lo, v)
        hi = v if hi is None else max(hi, v)
        if target and abs(v - target["value"]) > (target["value"] * target["tolerance_pct"]) / 100:
            out += s["length"]
    return {
        "key": key,
        "length": R6(length),
        "length_with_z": R6(with_z),
        "z_weighted": R6(total / with_z) if with_z > 0 else None,
        "z_min": lo,
        "z_max": hi,
        "out_of_tolerance_length": R6(out) if target else None,
        "out_of_tolerance_pct": R6((100 * (out + (length - with_z))) / length) if target and length > 0 else None,
        "within": (out == 0 and with_z == length) if target else None,
    }


def _discontinuities(sections: list[dict]) -> list[dict]:
    out: list[dict] = []

    def add(kind, s, detail):
        out.append(
            {"type": kind, "at": s["start"], "s": s["s0"], "layer": s["layer"], "net": s["net"], "detail": detail}
        )

    def refs_of(x):
        return " ".join(f"{r['side']}:{r['layer']}:{r['net']}" for r in x["refs"])

    for i, s in enumerate(sections):
        p = sections[i - 1] if i > 0 else None
        cont = bool(p and p["net"] == s["net"] and abs(p["s1"] - s["s0"]) < 1e-6 and p["end"] == s["start"])
        if cont and p["layer"] != s["layer"]:
            add("via", s, f"{p['layer']} → {s['layer']}")
        elif cont and refs_of(p) != refs_of(s):
            add("ref_change", s, f"{refs_of(p) or 'none'} → {refs_of(s) or 'none'}")
        if cont and p["geometry"]["width"] != s["geometry"]["width"]:
            add("width_change", s, f"{_jsnum(p['geometry']['width'])} → {_jsnum(s['geometry']['width'])} mm")
        for f in ("plane_gap", "no_ref", "uncoupled", "ref_edge"):
            if f in s["flags"] and not (cont and f in p["flags"]):
                detail = "; ".join(f"skipped {', '.join(r['skipped'])}" for r in s["refs"] if r["skipped"])
                add(f, s, detail)
    return out
