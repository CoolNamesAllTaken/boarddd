"""Gerber X2 copper layers -> ``boarddd/copper@1`` (tracks, arcs, vias, zone regions, pads, nets).

KiCad (and other X2 writers) attach the net to every copper object with ``%TO.N,<net>*%``, the pad with
``%TO.P,<ref>,<pin>*%`` and say what each aperture is for with ``%TA.AperFunction,...*%``. That is enough to
rebuild the routed copper without the design files:

* a draw (``D01``, straight or ``G02``/``G03`` arc) with a ``Conductor`` (or ``EtchedComponent``: copper a
  footprint draws) circular aperture is a track, its width the aperture's diameter; a full circle is two half arcs;
* a ``Conductor`` region (``G36``/``G37``) is filled copper (a zone fill, a teardrop or a copper drawing; Gerber
  does not say which): ``kind: 'region'``; its cut-ins are removed (``boarddd.copper.unfracture``) so it has
  holes. ``EtchedComponent`` regions (copper a footprint draws: net-tie bridges, antennas) are ``kind: 'shape'``;
* a ``ViaPad`` flash is a via ring; rings at the same point on several layers are one via spanning them, with
  its drill from the drill files when they are in the package;
* other pad flashes (``SMDPad``, ``ComponentPad``, ``HeatsinkPad``...) and pad regions are pads, one per
  layer, with the aperture's shape as polygons.

Everything else on a copper layer (``NonConductor`` text and graphics, the board profile) is skipped and
counted in a warning. Object attributes stay in force until ``%TD`` deletes them, as the X2 spec says (KiCad
writes ``%TO.N`` only when the net changes). Not supported: step and repeat (``%SR``), the ``LM``/``LR``/``LS``
transforms and clear polarity (``%LPC``), each reported in a warning. Gerber without X2 attributes gives tracks
and regions on no net (connectivity would have to come from geometry; not done here).

The browser twin is ``boarddd/copper`` ``copperFromGerbers`` (src/copper/gerber.js): the same rules, checked
against this module by python/tests/io/test_gerber_copper.py.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from .. import __version__
from .. import copper as cu
from .. import model as m
from . import package
from .pads import _evaluate

__all__ = ["read_gerber_copper", "read_gerber_copper_files", "parse_copper_layer", "LayerCopper", "PAD_FUNCTIONS"]

#: Aperture functions of pads (X2 spec 5.6.10): flashed (or drawn as regions) copper that a part connects to.
PAD_FUNCTIONS = frozenset(
    {
        "smdpad",
        "componentpad",
        "heatsinkpad",
        "connectorpad",
        "castellatedpad",
        "testpad",
        "fiducialpad",
        "washerpad",
        "otherpad",
        "bgapad",
        "pressfitpad",
    }
)
NDIGITS = 6
#: Points per quarter turn when circles, rounded corners and region arcs are flattened.
SEGMENTS = 8


def r(v: float) -> float:
    v = round(float(v), NDIGITS)
    return 0.0 if v == 0 else v


def _rp(p) -> tuple[float, float]:
    return (r(p[0]), r(p[1]))


# ---------------------------------------------------------------------------------------------------------------------
# one layer


@dataclass
class _Aperture:
    template: str  # 'C', 'R', 'O', 'P' or a macro name
    loops: list[list[tuple[float, float]]]  # mm, aperture-local
    diameter: float | None  # circles: the stroke width
    size: tuple[float, float]  # bounding box, mm
    function: str  # 'Conductor', 'SMDPad,CuDef'... ('' when the file has none)


@dataclass
class LayerCopper:
    """What one copper Gerber holds, before vias are joined across layers."""

    layer: str
    tracks: list[cu.Track] = field(default_factory=list)
    pads: list[cu.CopperPad] = field(default_factory=list)
    zones: list[cu.Zone] = field(default_factory=list)
    via_rings: list[tuple[tuple[float, float], float, str]] = field(default_factory=list)  # (at, diameter, net)
    skipped: Counter = field(default_factory=Counter)  # what was not copper with a net, by reason
    generator: str | None = None


_UNICODE = re.compile(r"\\u([0-9A-Fa-f]{4})")


def _unescape(s: str) -> str:
    return _UNICODE.sub(lambda mt: chr(int(mt.group(1), 16)), s)


def _split_fields(s: str) -> list[str]:
    """Attribute values: comma separated, ``\\,`` not a separator."""
    out, cur, i = [], "", 0
    while i < len(s):
        if s[i] == "\\" and i + 1 < len(s) and s[i + 1] == ",":
            cur += ","
            i += 2
            continue
        if s[i] == ",":
            out.append(cur)
            cur = ""
        else:
            cur += s[i]
        i += 1
    out.append(cur)
    return [_unescape(x) for x in out]


def _tokens(text: str):
    """('ext', 'FSLAX46Y46*') for each %...% block's commands, ('word', 'X1Y2D01') for the rest."""
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c in " \r\n\t":
            i += 1
            continue
        if c == "%":
            j = text.find("%", i + 1)
            j = n if j < 0 else j
            block = text[i + 1 : j]
            if block.startswith("AM"):
                yield ("ext", block.replace("\r", "").replace("\n", ""))
            else:
                for cmd in block.replace("\r", "").replace("\n", "").split("*"):
                    if cmd:
                        yield ("ext", cmd)
            i = j + 1
            continue
        j = text.find("*", i)
        j = n if j < 0 else j
        word = text[i:j].replace("\r", "").replace("\n", "").strip()
        if word:
            yield ("word", word)
        i = j + 1


def _circle(d: float, cx: float = 0.0, cy: float = 0.0, segments: int = SEGMENTS) -> list[tuple[float, float]]:
    n = 4 * segments
    return [(cx + d / 2 * math.cos(2 * math.pi * i / n), cy + d / 2 * math.sin(2 * math.pi * i / n)) for i in range(n)]


def _rect(w: float, h: float, cx: float = 0.0, cy: float = 0.0) -> list[tuple[float, float]]:
    return [(cx - w / 2, cy - h / 2), (cx + w / 2, cy - h / 2), (cx + w / 2, cy + h / 2), (cx - w / 2, cy + h / 2)]


def _obround(w: float, h: float, segments: int = SEGMENTS) -> list[tuple[float, float]]:
    rad = min(w, h) / 2
    hx, hy = w / 2 - rad, h / 2 - rad
    pts = []
    for k, (cx, cy) in enumerate(((hx, -hy), (hx, hy), (-hx, hy), (-hx, -hy))):
        a0 = -math.pi / 2 + k * math.pi / 2
        pts += [
            (cx + rad * math.cos(a0 + math.pi / 2 * i / segments), cy + rad * math.sin(a0 + math.pi / 2 * i / segments))
            for i in range(segments + 1)
        ]
    return pts


def _rot(p, deg: float) -> tuple[float, float]:
    if not deg:
        return (p[0], p[1])
    a = math.radians(deg)
    c, s = math.cos(a), math.sin(a)
    return (p[0] * c - p[1] * s, p[0] * s + p[1] * c)


def _hull(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Convex hull, counter-clockwise (monotone chain)."""
    pts = sorted(set(points))
    if len(pts) < 3:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower: list = []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper: list = []
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def _macro_loops(body: list[str], args: list[float], segments: int) -> list[list[tuple[float, float]]]:
    """An aperture macro's exposed primitives as loops (file units). Clear (exposure 0) primitives are dropped."""
    args = list(args)
    loops = []
    for stmt in body:
        stmt = stmt.strip()
        if not stmt or stmt.startswith("0"):
            continue
        if stmt.startswith("$") and "=" in stmt:
            name, expr = stmt.split("=", 1)
            k = int(name[1:])
            while len(args) < k:
                args.append(0.0)
            args[k - 1] = _evaluate(expr, args)
            continue
        v = [_evaluate(x, args) for x in stmt.split(",")]
        code = int(v[0])
        p = v[1:]
        if code == 1 and len(p) >= 4:  # circle: exposure, diameter, cx, cy[, rotation]
            loop = _circle(p[1], p[2], p[3], segments)
            rot = p[4] if len(p) > 4 else 0.0
        elif code in (20, 2) and len(p) >= 7:  # vector line: exposure, width, sx, sy, ex, ey, rotation
            w, sx, sy, ex, ey = p[1:6]
            dx, dy = ex - sx, ey - sy
            ln = math.hypot(dx, dy) or 1.0
            nx, ny = -dy / ln * w / 2, dx / ln * w / 2
            loop = [(sx + nx, sy + ny), (sx - nx, sy - ny), (ex - nx, ey - ny), (ex + nx, ey + ny)]
            rot = p[6]
        elif code == 21 and len(p) >= 6:  # centre line: exposure, width, height, cx, cy, rotation
            loop = _rect(p[1], p[2], p[3], p[4])
            rot = p[5]
        elif code == 22 and len(p) >= 6:  # lower-left line (deprecated): exposure, width, height, x, y, rotation
            loop = _rect(p[1], p[2], p[3] + p[1] / 2, p[4] + p[2] / 2)
            rot = p[5]
        elif code == 4 and len(p) >= 2:  # outline: exposure, n, x0, y0, ..., xn, yn, rotation
            n = int(p[1])
            coords = p[2 : 2 + 2 * (n + 1)]
            loop = [(coords[2 * i], coords[2 * i + 1]) for i in range(n)]
            rot = p[2 + 2 * (n + 1)] if len(p) > 2 + 2 * (n + 1) else 0.0
        elif code == 5 and len(p) >= 5:  # polygon: exposure, vertices, cx, cy, diameter[, rotation]
            n, cx, cy, d = int(p[1]), p[2], p[3], p[4]
            loop = [
                (cx + d / 2 * math.cos(2 * math.pi * i / n), cy + d / 2 * math.sin(2 * math.pi * i / n))
                for i in range(n)
            ]
            rot = p[5] if len(p) > 5 else 0.0
        elif code == 7 and len(p) >= 6:  # thermal: cx, cy, outer, inner, gap, rotation (as its outer disc)
            loop = _circle(p[2], p[0], p[1], segments)
            loops.append([_rot(q, p[5]) for q in loop])
            continue
        else:
            continue
        if p[0] == 0:  # exposure off
            continue
        loops.append([_rot(q, rot) for q in loop])
    return loops


def _aperture(
    template: str, params: list[float], macros: dict[str, list[str]], scale: float, function: str, segments: int
) -> _Aperture:
    t = template
    diameter = None
    if t == "C":
        d = params[0] if params else 0.0
        loops = [_circle(d, segments=segments)]
        diameter = d * scale
    elif t == "R":
        loops = [_rect(params[0], params[1] if len(params) > 1 else params[0])]
    elif t == "O":
        loops = [_obround(params[0], params[1] if len(params) > 1 else params[0], segments)]
    elif t == "P":
        d, n = params[0], int(params[1]) if len(params) > 1 else 3
        rot = params[2] if len(params) > 2 else 0.0
        loops = [
            [
                _rot((d / 2 * math.cos(2 * math.pi * i / n), d / 2 * math.sin(2 * math.pi * i / n)), rot)
                for i in range(n)
            ]
        ]
    elif t in macros:
        loops = _macro_loops(macros[t], params, segments)
        if t == "RoundRect" and len(loops) > 1:  # KiCad's rounded rectangle: convex, so its hull is its union
            loops = [_hull([q for lp in loops for q in lp])]
    else:
        loops = []
    loops = [[(q[0] * scale, q[1] * scale) for q in lp] for lp in loops if len(lp) >= 3]
    pts = [q for lp in loops for q in lp]
    size = (
        (max(q[0] for q in pts) - min(q[0] for q in pts), max(q[1] for q in pts) - min(q[1] for q in pts))
        if pts
        else (0.0, 0.0)
    )
    return _Aperture(t, loops, diameter, size, function)


_WORD = re.compile(r"^((?:G\d+)*)((?:[XYIJ][+-]?\d+)*)(?:D(\d+))?$")
_COORD = re.compile(r"([XYIJ])([+-]?\d+)")
_SHAPE = {"C": "circle", "R": "rect", "O": "oval", "P": "polygon"}


def _arc(start, end, center, clockwise: bool):
    """(sweep radians, ccw positive) of an arc; start == end is a full circle."""
    a0 = math.atan2(start[1] - center[1], start[0] - center[0])
    a1 = math.atan2(end[1] - center[1], end[0] - center[0])
    sweep = a1 - a0
    if clockwise:
        while sweep >= 0:
            sweep -= 2 * math.pi
        if sweep < -2 * math.pi + 1e-12:
            sweep += 2 * math.pi
        if math.dist(start, end) < 1e-9:
            sweep = -2 * math.pi
    else:
        while sweep <= 0:
            sweep += 2 * math.pi
        if sweep > 2 * math.pi - 1e-12:
            sweep -= 2 * math.pi
        if math.dist(start, end) < 1e-9:
            sweep = 2 * math.pi
    return a0, sweep


def _arc_points(start, end, center, clockwise: bool, segments: int = SEGMENTS) -> list[tuple[float, float]]:
    """The arc's points after ``start``, flattened (``segments`` per quarter turn), ``end`` exact."""
    a0, sweep = _arc(start, end, center, clockwise)
    rad = math.dist(start, center)
    n = max(1, math.ceil(abs(sweep) / (math.pi / 2) * segments))
    pts = [
        (center[0] + rad * math.cos(a0 + sweep * i / n), center[1] + rad * math.sin(a0 + sweep * i / n))
        for i in range(1, n)
    ]
    return [*pts, end]


def _single_quadrant_center(start, end, i: float, j: float, clockwise: bool):
    """G74: I/J are unsigned; the centre is the sign combination that is equidistant and spans <= 90 degrees."""
    best = None
    for sx in (1, -1):
        for sy in (1, -1):
            c = (start[0] + sx * i, start[1] + sy * j)
            err = abs(math.dist(start, c) - math.dist(end, c))
            _, sweep = _arc(start, end, c, clockwise)
            if abs(sweep) <= math.pi / 2 + 1e-6 and (best is None or err < best[0]):
                best = (err, c)
    return best[1] if best else (start[0] + i, start[1] + j)


def parse_copper_layer(text: str, layer: str, *, segments: int = SEGMENTS) -> LayerCopper:
    """One copper Gerber's tracks, pads, regions and via rings, board frame (Gerber's own), mm."""
    out = LayerCopper(layer=layer)
    scale_units = 1.0
    xdec = ydec = 6
    trailing = False
    xint = yint = 3
    apertures: dict[int, _Aperture] = {}
    macros: dict[str, list[str]] = {}
    ta: dict[str, str] = {}  # aperture attributes being defined
    to: dict[str, list[str]] = {}  # object attributes in force
    current: _Aperture | None = None
    interp = 1
    multi = True
    region: list[list[tuple[float, float]]] | None = None
    contour: list[tuple[float, float]] = []
    region_attrs: tuple[str, dict] = ("", {})
    x = y = 0.0
    dark = True

    def coord(raw: str, dec: int, intd: int) -> float:
        if trailing:
            neg = raw.startswith("-")
            digits = raw.lstrip("+-").ljust(intd + dec, "0")
            v = int(digits) / 10**dec
            return (-v if neg else v) * scale_units
        return int(raw) / 10**dec * scale_units

    def net() -> str:
        v = to.get("N")
        return v[0] if v else ""

    def ref_pin() -> tuple[str | None, str]:
        v = to.get("P")
        if not v:
            return None, ""
        return (v[0] or None), (v[1] if len(v) > 1 else "")

    def close_contour():
        nonlocal contour
        if region is not None and len(contour) >= 3:
            region.append(contour)
        contour = []

    def finish_region():
        func, attrs = region_attrs
        f = func.split(",")[0].lower()
        loops = region or []
        n = attrs.get("N", [""])[0] if attrs.get("N") else ""
        if not loops:
            return
        if not dark:
            out.skipped["clear-polarity objects"] += 1
        elif f in ("conductor", ""):
            fill = [p for lp in loops for p in cu.unfracture([_rp(q) for q in lp])]
            if fill:
                out.zones.append(
                    cu.Zone(layer=layer, net=n, kind="region", fill=fill, area=round(cu.fill_area(fill), 6))
                )
        elif f == "etchedcomponent":  # copper drawn by a footprint (net tie bridges, antennas)
            fill = [p for lp in loops for p in cu.unfracture([_rp(q) for q in lp])]
            if fill:
                out.zones.append(
                    cu.Zone(layer=layer, net=n, kind="shape", fill=fill, area=round(cu.fill_area(fill), 6))
                )
        elif f in PAD_FUNCTIONS:
            pts = [q for lp in loops for q in lp]
            xs, ys = [q[0] for q in pts], [q[1] for q in pts]
            cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
            pv = attrs.get("P") or []
            polys = []
            for lp in loops:
                ring = [_rp(q) for q in lp]
                polys.append(ring if cu.signed_area(ring) > 0 else ring[::-1])
            out.pads.append(
                cu.CopperPad(
                    ref=(pv[0] or None) if pv else None,
                    number=pv[1] if len(pv) > 1 else "",
                    net=n,
                    layers=[layer],
                    at=_rp((cx, cy)),
                    shape="polygon",
                    size=_rp((max(xs) - min(xs), max(ys) - min(ys))),
                    polygons=polys,
                    function=func,
                )
            )
        else:
            out.skipped[f"{func or 'unlabelled'} regions"] += 1

    for kind, cmd in _tokens(text):
        if kind == "ext":
            head = cmd[:2]
            if head == "FS":
                mt = re.match(r"FS([LT]?)([AI]?)X(\d)(\d)Y(\d)(\d)", cmd)
                if mt:
                    trailing = mt.group(1) == "T"
                    xint, xdec, yint, ydec = int(mt.group(3)), int(mt.group(4)), int(mt.group(5)), int(mt.group(6))
            elif head == "MO":
                scale_units = 25.4 if cmd[2:4] == "IN" else 1.0
            elif head == "AM":
                name, _, rest = cmd[2:].partition("*")
                macros[name] = [s for s in rest.split("*") if s.strip()]
            elif head == "AD":
                mt = re.match(r"ADD(\d+)([^,]*)(?:,(.*))?$", cmd)
                if mt:
                    params = [float(v) for v in (mt.group(3) or "").split("X") if v.strip()]
                    apertures[int(mt.group(1))] = _aperture(
                        mt.group(2), params, macros, scale_units, ta.get("AperFunction", ""), segments
                    )
            elif head == "TA":
                name, _, val = cmd[3:].partition(",")
                ta[name] = val
            elif head == "TO":
                name, _, val = cmd[3:].partition(",")
                # X2: 'N/C' is a pad on no net (a single-pad net); '' no net either
                to[name] = ([""] if val == "N/C" else [_unescape(val)]) if name == "N" else _split_fields(val)
            elif head == "TD":
                name = cmd[2:].lstrip(".")
                if not name:
                    ta.clear()
                    to.clear()
                else:
                    ta.pop(name, None)
                    to.pop(name, None)
            elif head == "TF":
                if cmd.startswith("TF.GenerationSoftware,"):
                    out.generator = " ".join(_split_fields(cmd.split(",", 1)[1]))
            elif head == "LP":
                dark = cmd[2:3] != "C"
            elif head == "SR" and cmd not in ("SR", "SRX1Y1I0J0"):
                out.skipped["step-and-repeat blocks (%SR, not supported)"] += 1
            elif head in ("LM", "LR", "LS") and cmd not in ("LMN", "LR0", "LS1"):
                out.skipped[f"%{head} transforms (not supported)"] += 1
            continue
        if cmd.startswith("G04") or cmd in ("M02", "M00", "M01"):
            continue
        mt = _WORD.match(cmd)
        if not mt:
            continue
        for g in re.findall(r"G(\d+)", mt.group(1)):
            g = int(g)
            if g in (1, 2, 3):
                interp = g
            elif g == 74:
                multi = False
            elif g == 75:
                multi = True
            elif g == 36:
                region, contour = [], []
                region_attrs = (ta.get("AperFunction", ""), dict(to))
            elif g == 37:
                close_contour()
                finish_region()
                region = None
        d = int(mt.group(3)) if mt.group(3) else None
        if d is not None and d >= 10:
            current = apertures.get(d)
            continue
        coords = dict(_COORD.findall(mt.group(2)))
        if d is None and not coords:
            continue
        x0, y0 = x, y
        if "X" in coords:
            x = coord(coords["X"], xdec, xint)
        if "Y" in coords:
            y = coord(coords["Y"], ydec, yint)
        i = coord(coords["I"], xdec, xint) if "I" in coords else 0.0
        j = coord(coords["J"], ydec, yint) if "J" in coords else 0.0
        if d is None:
            d = 1  # deprecated modal D01
        start, end = (x0, y0), (x, y)
        if region is not None:
            if d == 2:
                close_contour()
                contour = [end]
            elif d == 1:
                if not contour:
                    contour = [start]
                if interp == 1:
                    contour.append(end)
                else:
                    c = (x0 + i, y0 + j) if multi else _single_quadrant_center(start, end, abs(i), abs(j), interp == 2)
                    contour += _arc_points(start, end, c, interp == 2, segments)
            continue
        if d == 2 or current is None:
            continue
        func = current.function
        f = func.split(",")[0].lower()
        if not dark:
            out.skipped["clear-polarity objects"] += 1
            continue
        if d == 1:
            if f not in ("conductor", "", "etchedcomponent"):  # EtchedComponent: copper a footprint draws
                out.skipped[f"{func} draws"] += 1
                continue
            if current.diameter is None:
                out.skipped["draws with a non-circular aperture"] += 1
                continue
            w = r(current.diameter)
            if interp == 1:
                out.tracks.append(cu.Track(layer=layer, net=net(), width=w, start=_rp(start), end=_rp(end)))
                continue
            c = (x0 + i, y0 + j) if multi else _single_quadrant_center(start, end, abs(i), abs(j), interp == 2)
            if math.dist(start, end) < 1e-9:  # a full circle: two half arcs, as boarddd.copper.circle_halves
                for s_, e_, m_ in cu.circle_halves(c, start):
                    out.tracks.append(cu.Track(layer=layer, net=net(), width=w, start=s_, end=e_, mid=m_))
                continue
            a0, sweep = _arc(start, end, c, interp == 2)
            rad = math.dist(start, c)
            mid = _rp((c[0] + rad * math.cos(a0 + sweep / 2), c[1] + rad * math.sin(a0 + sweep / 2)))
            out.tracks.append(cu.Track(layer=layer, net=net(), width=w, start=_rp(start), end=_rp(end), mid=mid))
        elif d == 3:
            if f == "viapad":
                out.via_rings.append((_rp(end), r(current.size[0]), net()))
            elif f in PAD_FUNCTIONS or f == "":
                ref, pin = ref_pin()
                polys = []
                for lp in current.loops:
                    ring = [_rp((x + q[0], y + q[1])) for q in lp]
                    polys.append(ring if cu.signed_area(ring) > 0 else ring[::-1])
                out.pads.append(
                    cu.CopperPad(
                        ref=ref,
                        number=pin,
                        net=net(),
                        layers=[layer],
                        at=_rp(end),
                        shape=_SHAPE.get(current.template, current.template),
                        size=_rp(current.size),
                        polygons=polys,
                        function=func or None,
                    )
                )
            else:
                out.skipped[f"{func} flashes"] += 1
    return out


# ---------------------------------------------------------------------------------------------------------------------
# a package


def read_gerber_copper(source: str | Path, *, name: str | None = None, check_fills: bool = True) -> cu.Copper:
    """The copper of a fab package (folder or .zip of Gerber X2 + drill files) as ``boarddd/copper@1``."""
    path = Path(source)
    return read_gerber_copper_files(package.package_files(path), name=name or path.stem, check_fills=check_fills)


def read_gerber_copper_files(
    files: Mapping[str, bytes], *, name: str | None = None, check_fills: bool = True, board: m.Board | None = None
) -> cu.Copper:
    """``read_gerber_copper`` on files in memory ({relpath: bytes}). ``board``: the package's board@1 when already
    read (``io.package.read_files``), for its layer ids, drills and outline."""
    board = board or package.read_files(files, name=name)
    copper = sorted((la for la in board.layers if la.role == "copper"), key=lambda la: la.order)
    warnings: list[str] = []
    doc = cu.Copper(
        board=board.name,
        source=m.Source(
            kind="gerber",
            files=[f for f in board.source.files if f.role in ("copper", "drill")],
            generator=board.source.generator,
            reader=f"boarddd {__version__} io.gerber_copper",
        ),
        layers=[la.id for la in copper],
        warnings=warnings,
    )
    rings: dict[tuple[float, float], list[tuple[str, float, str]]] = {}
    skipped: Counter = Counter()
    no_net = 0
    for la in copper:
        text = files[la.files[0]].decode("utf-8", "replace")
        lc = parse_copper_layer(text, la.id)
        doc.tracks += lc.tracks
        doc.pads += lc.pads
        doc.zones += lc.zones
        for at, dia, net in lc.via_rings:
            rings.setdefault(at, []).append((la.id, dia, net))
        skipped.update(lc.skipped)
        if "%TO.N" not in text and (lc.tracks or lc.zones):
            no_net += 1
    named = {(p.at, p.net): (p.ref, p.number) for p in doc.pads if p.ref is not None}
    for p in doc.pads:  # KiCad writes %TO.P on the outer layers only
        if p.ref is None and (p.at, p.net) in named:
            p.ref, p.number = named[(p.at, p.net)]
    order = {lid: i for i, lid in enumerate(doc.layers)}
    holes = [d for d in board.drills if d.plated and d.x2 is None]
    by_xy = {(round(d.x, 3), round(d.y, 3)): d for d in holes if d.function in (None, "via")}
    for at, found in rings.items():
        found.sort(key=lambda t: order[t[0]])
        span = (found[0][0], found[-1][0])
        inside = doc.layers[order[span[0]] : order[span[1]] + 1]
        dias = {lid: dia for lid, dia, _ in found}
        hole = by_xy.get((round(at[0], 3), round(at[1], 3)))
        via = cu.Via(
            at=at,
            net=next((n for _, _, n in found if n), ""),
            diameter=max(dias.values()),
            drill=hole.diameter if hole is not None else 0.0,
            span=span,
            type="through"
            if span == (doc.layers[0], doc.layers[-1])
            else "blind"
            if doc.layers[0] in span or doc.layers[-1] in span
            else "buried",
        )
        if len(dias) < len(inside):
            via.pad_layers = [lid for lid in inside if lid in dias]
        if len(set(dias.values())) > 1:
            via.padstack = [cu.ViaPad(layer=lid, diameter=d) for lid, d in dias.items()]
        doc.vias.append(via)
    if holes and not by_xy and doc.vias:
        warnings.append("the drill files have no via holes: via drills are 0")
    elif not holes and doc.vias:
        warnings.append("no drill files: via drills are 0")
    if no_net:
        warnings.append(
            f"{no_net} copper layer(s) have no %TO.N net attributes (not Gerber X2): their copper is on no net"
        )
    for what, n in sorted(skipped.items()):
        warnings.append(f"{n} {what} on copper layers skipped (not tracks, pads, vias or filled copper)")
    nets: dict[str, None] = {}
    for item in (*doc.pads, *doc.tracks, *doc.vias, *doc.zones):
        nets.setdefault(item.net, None)
    doc.nets = ([""] if "" in nets else []) + [n for n in nets if n]
    doc.planes = cu.planes(doc.zones, cu.outline_area(board.outline), doc.layers)
    if check_fills:
        cu.check_fills(doc)
    return doc
