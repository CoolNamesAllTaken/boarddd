"""Copper connectivity from the Gerbers and drill files alone: which copper is one net, without X2 net attributes.

Each copper layer is painted in drawing order (draws, flashes and regions, dark and clear, a ``%IPNEG`` layer as the
board minus what is drawn); the painted image falls apart into islands. A plated hole joins the islands it touches on
every layer it spans (a through hole: all of them; a blind or buried drill file: its span). The joined islands are
the nets. So pours and planes with their clearance cut-outs, thermal spokes, negative planes and clear-polarity
anti-pads all come out as the fab would etch them.

Names: the X2 ``%TO.N`` net of the island's copper when the files have one (the most common one, when an island holds
several, which a net tie does), otherwise ``N1``, ``N2``... by pad count (largest first; ties by position, so the
same files give the same names). ``Connectivity.nets`` says which; ``anchor`` is a point on each net's copper (a
pad's centre when it has one) for an app that keeps the customer's own name for a net across revisions.

Plating comes from the files only (``hole_plating``): the drill file's X2 FileFunction or its name (PTH/NPTH,
Plated/NonPlated, Eagle's .drd beside a .hol), its tools' attributes (KiCad) or sections (Altium ``;TYPE=PLATED``), a
Gerber X2 drill file's, else the X2 copper's pads (a hole in a ViaPad or a component pad is plated, one in a WasherPad
or in no pad is not). When a board of two or more copper layers has a hole none of these settles, ``connect_files``
raises ``PlatingUnknown`` (``PLATING_MESSAGE``) rather than join layers on a guess. A via is a plated hole its drill
file calls one (``ViaDrill``) or that X2 flashes a ViaPad on; other plated holes are pads' holes.

``connect_files`` returns the copper@1 document (``boarddd.io.gerber_copper``'s, with every track, pad, via and zone
on its net; zones on layers with clear or negative copper are the etched islands) and a ``Connectivity`` summary.
Needs shapely (``pip install "boarddd[connect]"``).
"""

from __future__ import annotations

import math
import time
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from . import copper as cu
from . import model as m
from .io import gerber_copper as gc
from .io import package
from .io.pads import _evaluate

__all__ = [
    "connect_files",
    "hole_plating",
    "Connectivity",
    "NetInfo",
    "Plating",
    "PlatingUnknown",
    "PLATING_MESSAGE",
    "PLATING_FORMATS",
    "TOLERANCE",
    "compare_nets",
]

#: Copper closer than this (mm) is one piece: what rounding in the files leaves between touching shapes.
TOLERANCE = 0.002
#: Said when the files do not say which holes are plated.
PLATING_MESSAGE = "Upload drill files that say which holes are plated (PTH and NPTH), or Gerber X2."
#: What does say it, for the people uploading.
PLATING_FORMATS = (
    "Separate plated and non-plated drill files: PTH and NPTH (KiCad), -Plated and -NonPlated (Altium), "
    ".drd and .hol (Eagle)",
    "A drill file whose header says Plated or NonPlated (TF.FileFunction, as KiCad writes)",
    "One drill file that marks each tool plated or not (KiCad's attribute comments, Altium's ;TYPE=PLATED)",
    "Gerber X2 drill files",
    "Gerber X2 copper layers: their pads say which holes are plated",
)
#: X2 pad functions that are not round a plated hole.
_UNPLATED_PADS = frozenset({"washerpad", "fiducialpad"})
#: Points per quarter circle for round shapes (shapely's quad_segs).
QUAD = 4


@dataclass
class NetInfo:
    name: str
    #: True for a name made here (N1, N2...); False when the files named it (X2).
    auto: bool
    pads: int
    layers: list[str]
    #: A point on the net's copper: (layer, x, y), a pad centre when it has pads.
    anchor: tuple[str, float, float]
    #: X2 names found on its copper when it holds more than one (a short, or a net tie).
    merged: list[str] = field(default_factory=list)


@dataclass
class Plating:
    """Which holes are plated, from the files alone: one entry per ``board.drills``."""

    #: True, False, or None when nothing in the files says.
    plated: list[bool | None]
    #: How each was settled: 'file', 'name', 'tool' (the drill files, as ``Drill.plating``), 'x2' (the copper's pads).
    how: list[str | None]
    copper_layers: int

    @property
    def unknown(self) -> int:
        return sum(p is None for p in self.plated)

    @property
    def known(self) -> bool:
        """Enough to join layers: every hole settled, or one copper layer (nothing to join)."""
        return self.copper_layers < 2 or not self.unknown

    def to_json(self) -> dict:
        return {
            "known": self.known,
            "holes": len(self.plated),
            "unknown": self.unknown,
            "how": dict(sorted(Counter(h for h in self.how if h).items())),
        }


class PlatingUnknown(ValueError):
    """The files do not say which holes are plated (``PLATING_MESSAGE``); ``plating`` says how many."""

    def __init__(self, plating: Plating):
        super().__init__(PLATING_MESSAGE)
        self.plating = plating


@dataclass
class Connectivity:
    nets: list[NetInfo]
    #: 'x2' when every net came named from the files, 'geometry' when none did, 'mixed'.
    names: str
    islands: int
    holes: int
    vias: int
    warnings: list[str]
    #: Seconds per phase: parse, paint, join, assign.
    timings: dict[str, float]
    #: How the holes' plating was settled (``Plating.to_json``).
    plating: dict = field(default_factory=dict)

    def to_json(self) -> dict:
        return {
            "names": self.names,
            "islands": self.islands,
            "holes": self.holes,
            "vias": self.vias,
            "plating": self.plating,
            "warnings": self.warnings,
            "timings": {k: round(v, 3) for k, v in self.timings.items()},
            "nets": [
                {"name": n.name, "auto": n.auto, "pads": n.pads, "layers": n.layers, "anchor": list(n.anchor)}
                | ({"merged": n.merged} if n.merged else {})
                for n in self.nets
            ],
        }


# ---------------------------------------------------------------------------------------------------------------------
# geometry of the painted objects


def _shapely():
    try:
        import shapely
    except ImportError as e:  # pragma: no cover - the extra is documented
        raise ImportError('boarddd.connectivity needs shapely: pip install "boarddd[connect]"') from e
    return shapely


def _macro_geometry(body: list[str], args: list[float], scale: float):
    """An aperture macro as one shapely geometry (mm, aperture-local): exposure off primitives cut what is before
    them, thermals are rings with their four gaps."""
    sh = _shapely()
    from shapely import affinity
    from shapely.geometry import LineString, Point, Polygon, box

    args = list(args)
    acc = None
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
        code, p = int(v[0]), v[1:]
        exposure, rot = True, 0.0
        g = None
        if code == 1 and len(p) >= 4:
            exposure, g = p[0] != 0, Point(p[2], p[3]).buffer(p[1] / 2, quad_segs=QUAD)
            rot = p[4] if len(p) > 4 else 0.0
        elif code in (20, 2) and len(p) >= 7:
            exposure = p[0] != 0
            g = LineString([(p[2], p[3]), (p[4], p[5])]).buffer(p[1] / 2, cap_style="flat")
            rot = p[6]
        elif code == 21 and len(p) >= 6:
            exposure, g = p[0] != 0, box(p[3] - p[1] / 2, p[4] - p[2] / 2, p[3] + p[1] / 2, p[4] + p[2] / 2)
            rot = p[5]
        elif code == 22 and len(p) >= 6:
            exposure, g = p[0] != 0, box(p[3], p[4], p[3] + p[1], p[4] + p[2])
            rot = p[5]
        elif code == 4 and len(p) >= 2:
            n = int(p[1])
            coords = p[2 : 2 + 2 * (n + 1)]
            exposure = p[0] != 0
            g = Polygon([(coords[2 * i], coords[2 * i + 1]) for i in range(n)]).buffer(0)
            rot = p[2 + 2 * (n + 1)] if len(p) > 2 + 2 * (n + 1) else 0.0
        elif code == 5 and len(p) >= 5:
            n, cx, cy, d = int(p[1]), p[2], p[3], p[4]
            exposure = p[0] != 0
            g = Polygon(
                [
                    (cx + d / 2 * math.cos(2 * math.pi * i / n), cy + d / 2 * math.sin(2 * math.pi * i / n))
                    for i in range(n)
                ]
            )
            rot = p[5] if len(p) > 5 else 0.0
        elif code == 7 and len(p) >= 6:  # thermal: centre, outer, inner diameter, gap, rotation
            cx, cy, do, di, gap = p[:5]
            rot = p[5]
            ring = Point(cx, cy).buffer(do / 2, quad_segs=QUAD).difference(Point(cx, cy).buffer(di / 2, quad_segs=QUAD))
            cut = sh.union_all(
                [box(cx - do, cy - gap / 2, cx + do, cy + gap / 2), box(cx - gap / 2, cy - do, cx + gap / 2, cy + do)]
            )
            g = ring.difference(cut)
        if g is None or g.is_empty:
            continue
        if rot:
            g = affinity.rotate(g, rot, origin=(0, 0))
        if acc is None:
            acc = g if exposure else None
        else:
            acc = acc.union(g) if exposure else acc.difference(g)
    if acc is None:
        return None
    return affinity.scale(acc, scale, scale, origin=(0, 0))


class _Shapes:
    """shapely geometry per aperture (cached) and per painted object."""

    def __init__(self):
        self.cache: dict[int, object] = {}

    def aperture(self, ap: gc._Aperture):
        key = id(ap)
        if key not in self.cache:
            self.cache[key] = self._aperture(ap)
        return self.cache[key]

    def _aperture(self, ap: gc._Aperture):
        from shapely.geometry import Point, Polygon

        if ap.template == "C" and ap.diameter is not None:
            return Point(0, 0).buffer(ap.diameter / 2, quad_segs=QUAD)
        if ap.macro is not None:
            g = _macro_geometry(ap.macro, ap.params, ap.scale)
            if g is not None:
                return g
        polys = [Polygon(lp).buffer(0) for lp in ap.loops if len(lp) >= 3]
        polys = [p for p in polys if not p.is_empty]
        if not polys:
            return None
        return _shapely().union_all(polys)

    def draw(self, ap: gc._Aperture, pts):
        from shapely import affinity
        from shapely.geometry import LineString, Point

        if ap.diameter is not None or ap.template == "C":
            w = ap.diameter or 0.0
            if len(pts) == 2 and math.dist(pts[0], pts[1]) < 1e-9:
                return Point(pts[0]).buffer(w / 2 + TOLERANCE / 2, quad_segs=QUAD)
            return LineString(pts).buffer(w / 2 + TOLERANCE / 2, quad_segs=QUAD)
        # a draw with a rectangle or another shape: the aperture swept along the path (its hull per straight piece)
        shape = self.aperture(ap)
        if shape is None:
            return None
        pieces = []
        for a, b in zip(pts, pts[1:], strict=False):
            pieces.append(
                _shapely()
                .union_all([affinity.translate(shape, a[0], a[1]), affinity.translate(shape, b[0], b[1])])
                .convex_hull
            )
        if not pieces:
            pieces = [affinity.translate(shape, pts[0][0], pts[0][1])]
        return _shapely().union_all(pieces)

    def flash(self, ap: gc._Aperture, at):
        from shapely import affinity

        shape = self.aperture(ap)
        return None if shape is None else affinity.translate(shape, at[0], at[1])

    @staticmethod
    def region(loops):
        from shapely.geometry import Polygon

        parts = []
        for lp in loops:
            if len(lp) < 3:
                continue
            g = Polygon(lp)
            if not g.is_valid:  # a fractured contour (cut-ins to its holes), or a sloppy one
                g = g.buffer(0)
            parts.append(g)
        if not parts:
            return None
        # several contours in one region are XORed by the even-odd fill KiCad and others rely on
        acc = parts[0]
        for g in parts[1:]:
            acc = acc.symmetric_difference(g)
        return acc


def _paint(lc: gc.LayerCopper, shapes: _Shapes, outline):
    """The etched copper of one layer (a shapely geometry), the union of its regions (for zones), and whether it
    has clear (or negative) copper."""
    sh = _shapely()
    acc = None
    run: list = []  # shapes of the current polarity run
    lines: dict = defaultdict(list)  # round draws of the run by width: one buffer per width is far cheaper
    run_dark = True
    regions: list = []
    clears = False

    def flush():
        nonlocal acc, run
        parts = list(run)
        for w, paths in lines.items():
            parts.append(sh.buffer(sh.MultiLineString(paths), w / 2 + TOLERANCE / 2, quad_segs=QUAD))
        lines.clear()
        run = []
        if not parts:
            return
        g = sh.union_all(parts)
        if run_dark:
            acc = g if acc is None else acc.union(g)
        elif acc is not None:
            acc = acc.difference(g)

    for dark, kind, a, b in lc.paint or []:
        if dark != run_dark:
            flush()
            run_dark = dark
        if not dark:
            clears = True
        if (
            kind == "draw"
            and (a.diameter is not None or a.template == "C")
            and not (len(b) == 2 and math.dist(b[0], b[1]) < 1e-9)
        ):
            lines[a.diameter or 0.0].append(b)
            continue
        if kind == "draw":
            g = shapes.draw(a, b)
        elif kind == "flash":
            g = shapes.flash(a, b)
        else:
            g = shapes.region(a)
            if g is not None and dark:
                regions.append(g)
        if g is None or g.is_empty:
            continue
        run.append(g)
    flush()
    if acc is None:
        from shapely.geometry import Polygon

        acc = Polygon()
    if lc.negative and outline is not None:
        acc = outline.difference(acc)
        clears = True
    return acc, (sh.union_all(regions) if regions else None), clears


# ---------------------------------------------------------------------------------------------------------------------
# the package


def _outline_polygon(board: m.Board):
    from shapely.geometry import Polygon

    o = board.outline
    if o is None or len(o.board) < 3:
        return None
    g = Polygon(o.board, [c for c in o.cutouts if len(c) >= 3] if getattr(o, "cutouts", None) else None)
    return g if g.is_valid else _shapely().make_valid(g)


def _drill_span(board: m.Board, drill: m.Drill, layers: list[str]) -> list[str]:
    """The copper layers a plated drill joins: its drill file's X2 span (Plated,1,2,Blind), else all of them."""
    la = next((x for x in board.layers if x.id == drill.layer), None)
    fn = (la.function or "") if la else ""
    parts = fn.split(",")
    if len(parts) >= 3 and parts[1].isdigit() and parts[2].isdigit():
        a, b = sorted((int(parts[1]), int(parts[2])))
        if 1 <= a <= len(layers) and b <= len(layers):
            return layers[a - 1 : b]
    return layers


class _Union:
    def __init__(self):
        self.parent: dict = {}

    def find(self, a):
        p = self.parent.setdefault(a, a)
        while p != self.parent.setdefault(p, p):
            self.parent[p] = self.parent[self.parent[p]]
            p = self.parent[p]
        self.parent[a] = p
        return p

    def join(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[max(ra, rb)] = min(ra, rb)


def _in_ring(x: float, y: float, ring) -> bool:
    inside = False
    n = len(ring)
    for i in range(n):
        (x0, y0), (x1, y1) = ring[i], ring[(i + 1) % n]
        if (y0 > y) != (y1 > y) and x < x0 + (y - y0) * (x1 - x0) / (y1 - y0):
            inside = not inside
    return inside


def hole_plating(
    files: Mapping[str, bytes], board: m.Board | None = None, *, parsed: Mapping[str, gc.LayerCopper] | None = None
) -> Plating:
    """Which of ``board.drills`` are plated, from the drill files, else (Gerber X2 copper) from the pads round them.
    ``parsed``: the copper layers already parsed ({layer id: LayerCopper}). Pure Python (no shapely)."""
    board = board or package.read_files(files)
    copper = [la for la in board.layers if la.role == "copper"]
    plated: list[bool | None] = [d.plated if d.plating else None for d in board.drills]
    how: list[str | None] = [d.plating for d in board.drills]
    open_ = [i for i, p in enumerate(plated) if p is None]
    x2 = bool(copper) and all(b"%TA.AperFunction" in files.get(la.files[0], b"") for la in copper if la.files)
    if open_ and x2:
        cell = 2.0
        grid: dict = defaultdict(list)  # (i, j) -> [(polygons | None, x, y, radius)]

        def put(xs, ys, item):
            for i in range(math.floor(min(xs) / cell), math.floor(max(xs) / cell) + 1):
                for j in range(math.floor(min(ys) / cell), math.floor(max(ys) / cell) + 1):
                    grid[(i, j)].append(item)

        for la in copper:
            lc = (parsed or {}).get(la.id) or gc.parse_copper_layer(
                files[la.files[0]].decode("utf-8", "replace"), la.id
            )
            for at, dia, _net in lc.via_rings:
                put([at[0] - dia / 2, at[0] + dia / 2], [at[1] - dia / 2, at[1] + dia / 2], (None, at, dia / 2))
            for p in lc.pads:
                if not p.function or p.function.split(",")[0].lower() in _UNPLATED_PADS:
                    continue
                pts = [q for ring in p.polygons or [] for q in ring]
                if pts:
                    put([q[0] for q in pts], [q[1] for q in pts], (p.polygons, p.at, 0.0))

        def on_pad(x: float, y: float) -> bool:
            for polys, at, rad in grid.get((math.floor(x / cell), math.floor(y / cell)), []):
                if polys is None:
                    if math.dist((x, y), at) <= rad + TOLERANCE:
                        return True
                elif any(len(ring) >= 3 and _in_ring(x, y, ring) for ring in polys):
                    return True
            return False

        for i in open_:
            d = board.drills[i]
            pts = [(d.x, d.y)] if d.x2 is None else [((d.x + d.x2) / 2, (d.y + d.y2) / 2), (d.x, d.y), (d.x2, d.y2)]
            plated[i] = any(on_pad(x, y) for x, y in pts)
            how[i] = "x2"
    return Plating(plated=plated, how=how, copper_layers=len(copper))


def connect_files(
    files: Mapping[str, bytes],
    *,
    name: str | None = None,
    board: m.Board | None = None,
    progress: Callable[[str, float], None] | None = None,
) -> tuple[cu.Copper, Connectivity]:
    """The copper of a fab package ({relpath: bytes}) with every object on its net, nets from the copper itself.
    ``progress(phase, fraction)`` is called as it goes (phases: parse, paint, join, assign). Raises
    ``PlatingUnknown`` when the files do not say which holes are plated (``hole_plating``)."""
    sh = _shapely()
    import numpy as np

    say = progress or (lambda phase, f: None)
    timings: dict[str, float] = {}
    t0 = time.perf_counter()
    board = board or package.read_files(files, name=name)
    parsed: dict[str, gc.LayerCopper] = {}
    doc = gc.read_gerber_copper_files(files, name=name, board=board, check_fills=False, parsed=parsed)
    layers = list(doc.layers)
    by_id = {la.id: la for la in board.layers}
    for lid in layers:
        if by_id[lid].polarity == "negative":
            parsed[lid].negative = True
    plating = hole_plating(files, board, parsed=parsed)
    if not plating.known:
        raise PlatingUnknown(plating)
    say("parse", 1.0)
    timings["parse"] = time.perf_counter() - t0

    t1 = time.perf_counter()
    outline = _outline_polygon(board)
    shapes = _Shapes()
    islands: dict[str, list] = {}
    regions: dict[str, object] = {}
    warnings = [w for w in doc.warnings if "%TO.N" not in w]
    for k, lid in enumerate(layers):
        img, reg, _clears = _paint(parsed[lid], shapes, outline)
        if parsed[lid].negative and outline is None:
            warnings.append(f"{lid} is a negative layer and the board has no outline: read as positive")
        islands[lid] = [g for g in sh.get_parts(img) if g.geom_type == "Polygon" and not g.is_empty]
        if reg is not None:
            regions[lid] = reg
        say("paint", (k + 1) / max(1, len(layers)))
    timings["paint"] = time.perf_counter() - t1

    # ---- plated holes join layers
    t2 = time.perf_counter()
    for lid in layers:
        sh.prepare(islands[lid])

    def pairs(lid: str, geoms: list):
        """(geometry index, island index) of each of ``geoms`` touching an island of the layer. The tree holds the
        small shapes and the (prepared) islands ask it: a pour has thousands of points, a hole a few."""
        if not geoms or not islands[lid]:
            return []
        q = sh.STRtree(geoms).query(np.array(islands[lid], dtype=object), predicate="intersects")
        return sorted(zip((int(b) for b in q[1]), (int(a) for a in q[0]), strict=True))

    base, n_isl = {}, 0
    for lid in layers:
        base[lid] = n_isl
        n_isl += len(islands[lid])
    uf = list(range(n_isl))

    def find(a: int) -> int:
        while uf[a] != a:
            uf[a] = uf[uf[a]]
            a = uf[a]
        return a

    def join(a: int, b: int):
        ra, rb = find(a), find(b)
        if ra != rb:
            uf[max(ra, rb)] = min(ra, rb)

    holes = [d for d, p in zip(board.drills, plating.plated, strict=True) if p]
    if not board.drills and len(layers) > 1:
        warnings.append("no drill files: layers are not joined (every layer's copper is its own nets)")
    hole_geo = [
        sh.LineString([(d.x, d.y), (d.x2, d.y2)]).buffer(d.diameter / 2 + TOLERANCE, quad_segs=QUAD)
        if d.x2 is not None
        else sh.Point(d.x, d.y).buffer(max(d.diameter, 0.05) / 2 + TOLERANCE, quad_segs=QUAD)
        for d in holes
    ]
    spans = [_drill_span(board, d, layers) for d in holes]
    hits: list[list[int]] = [[] for _ in holes]
    for lid in layers:
        want = [i for i, s in enumerate(spans) if lid in s]
        if not want or not islands[lid]:
            continue
        for a, b in pairs(lid, [hole_geo[i] for i in want]):
            hits[want[a]].append(base[lid] + b)
    via_holes = []
    for i, d in enumerate(holes):
        for a, b in zip(hits[i], hits[i][1:], strict=False):
            join(a, b)
        if d.x2 is None and d.function == "via":
            via_holes.append((d, spans[i], hits[i]))
    timings["join"] = time.perf_counter() - t2

    # ---- nets: components, names
    t3 = time.perf_counter()
    comp: dict = defaultdict(list)
    for gi in range(n_isl):
        comp[find(gi)].append(gi)
    where = [(lid, i) for lid in layers for i in range(len(islands[lid]))]

    def locate_all(lid: str, pts: list[tuple[float, float]], radii: list[float]) -> list[int | None]:
        """The component each point (a disc of radius r) is on, on one layer."""
        out: list[int | None] = [None] * len(pts)
        if not pts or not islands.get(lid):
            return out
        geo = [sh.Point(p) if r <= 0 else sh.Point(p).buffer(r, quad_segs=2) for p, r in zip(pts, radii, strict=True)]
        for a, b in pairs(lid, geo):
            if out[a] is None:
                out[a] = find(base[lid] + b)
        miss = [i for i, v in enumerate(out) if v is None]
        if miss:  # a point just off the copper (rounding): the nearest island within 10 um
            for a, b in pairs(lid, [sh.Point(pts[i]).buffer(max(radii[i], 0.01), quad_segs=2) for i in miss]):
                if out[miss[a]] is None:
                    out[miss[a]] = find(base[lid] + b)
        return out

    def locate_items(items, layer_of, point_of, radius_of):
        groups: dict = defaultdict(list)
        for k, it in enumerate(items):
            groups[layer_of(it)].append(k)
        res: list[int | None] = [None] * len(items)
        for lid, ks in groups.items():
            got = locate_all(lid, [point_of(items[k]) for k in ks], [radius_of(items[k]) for k in ks])
            for k, v in zip(ks, got, strict=True):
                res[k] = v
        return res

    via_at = {(round(d.x, 4), round(d.y, 4)): d.diameter for d, _, _ in via_holes}

    def is_via_ring(p: cu.CopperPad) -> bool:  # a round flash about a via's size, centred on it (not a via in a pad)
        d = via_at.get((round(p.at[0], 4), round(p.at[1], 4)))
        return d is not None and not p.function and p.shape == "circle" and p.size[0] <= d + 0.8

    pads = [p for p in doc.pads if not is_via_ring(p)]
    pad_roots = locate_items(pads, lambda p: p.layers[0], lambda p: p.at, lambda p: 0.0)
    track_roots = locate_items(doc.tracks, lambda t: t.layer, lambda t: t.start, lambda t: t.width / 2)
    item_root = list(zip(pads, pad_roots, strict=True)) + list(zip(doc.tracks, track_roots, strict=True))
    x2: dict = defaultdict(Counter)
    for item, root in item_root:
        if root is not None and item.net:
            x2[root][item.net] += 1
    pad_count: Counter = Counter()
    pad_anchor: dict = {}
    seen_pads: dict = defaultdict(set)
    for item, root in zip(pads, pad_roots, strict=True):
        if root is None:
            continue
        key = (round(item.at[0], 3), round(item.at[1], 3))
        if key not in seen_pads[root]:
            seen_pads[root].add(key)
            pad_count[root] += 1
            pad_anchor.setdefault(root, (item.layers[0], item.at[0], item.at[1]))
    area = {r: sum(islands[where[g][0]][where[g][1]].area for g in gs) for r, gs in comp.items()}
    first: dict = {}
    for r, gs in comp.items():
        lid, i = where[min(gs)]
        p = islands[lid][i].representative_point()
        first[r] = (lid, round(p.x, 4), round(p.y, 4))
    roots = sorted(comp, key=lambda r: (-pad_count[r], -round(area[r], 3), first[r][1], first[r][2]))
    names: dict = {}
    taken: set[str] = set()
    for root in roots:
        if x2[root]:
            best = sorted(x2[root].items(), key=lambda kv: (-kv[1], kv[0]))
            if best[0][0] not in taken:
                names[root] = best[0][0]
                taken.add(best[0][0])
    infos: list[NetInfo] = []
    k = 0
    for root in roots:
        auto = root not in names
        if auto:
            k += 1
            while f"N{k}" in taken:
                k += 1
            names[root] = f"N{k}"
            taken.add(names[root])
        infos.append(
            NetInfo(
                name=names[root],
                auto=auto,
                pads=pad_count[root],
                layers=[lid for lid in layers if any(where[g][0] == lid for g in comp[root])],
                anchor=pad_anchor.get(root) or first[root],
                merged=sorted(x2[root]) if len(x2[root]) > 1 else [],
            )
        )

    # ---- the copper@1 document on these nets
    for item, root in item_root:
        item.net = names.get(root, "") if root is not None else ""
    doc.pads = pads
    flashes: dict = defaultdict(list)  # (x, y) -> [(layer, size)]
    for lid in layers:
        for dark, kind, a, at in parsed[lid].paint or []:
            if kind == "flash" and dark and (round(at[0], 4), round(at[1], 4)) in via_at:
                g = shapes.aperture(a)
                if g is not None:
                    flashes[(round(at[0], 4), round(at[1], 4))].append((lid, g.bounds[2] - g.bounds[0]))
    vias = []
    for d, span, hit in via_holes:
        net = names.get(find(hit[0]), "") if hit else ""
        found = flashes.get((round(d.x, 4), round(d.y, 4)), [])
        on = [lid for lid in span if any(f[0] == lid for f in found)] or span
        via = cu.Via(
            at=(gc.r(d.x), gc.r(d.y)),
            net=net,
            diameter=gc.r(max([f[1] for f in found] + [d.diameter])),
            drill=d.diameter,
            span=(span[0], span[-1]),
            type="through"
            if (span[0], span[-1]) == (layers[0], layers[-1])
            else "blind"
            if layers[0] in span or layers[-1] in span
            else "buried",
        )
        if len(on) < len(span):
            via.pad_layers = on
        vias.append(via)
    if doc.vias:  # X2 via rings: keep them, on the copper's nets
        roots_v = locate_items(doc.vias, lambda v: v.span[0], lambda v: v.at, lambda v: 0.0)
        for v, root in zip(doc.vias, roots_v, strict=True):
            v.net = names.get(root, "") if root is not None else ""
    else:
        doc.vias = vias
    zones = []
    for lid in layers:  # the etched islands that are (or hold) poured copper
        reg = regions.get(lid)
        if reg is None:
            continue
        isl = islands[lid]
        probes = []  # points on each region's boundary (and inside it): an island holding one is poured copper
        for part in sh.get_parts(reg):
            ring = list(part.exterior.coords)
            step = max(1, len(ring) // 200)
            probes += [sh.Point(q).buffer(TOLERANCE, quad_segs=1) for q in ring[::step]]
            probes.append(part.representative_point().buffer(TOLERANCE, quad_segs=1))
        for i in sorted({b for _, b in pairs(lid, probes)}):
            g = isl[i]
            zones.append(
                cu.Zone(
                    layer=lid,
                    net=names[find(base[lid] + i)],
                    kind="region",
                    fill=[_fill_polygon(g)],
                    area=round(g.area, 6),
                )
            )
    doc.zones = zones
    doc.nets = [n.name for n in infos]
    doc.warnings = warnings
    doc.planes = cu.planes(doc.zones, cu.outline_area(board.outline), doc.layers)
    doc.source.reader = (doc.source.reader or "") + " + connectivity"
    timings["assign"] = time.perf_counter() - t3
    say("assign", 1.0)
    named = sum(not n.auto for n in infos)
    return doc, Connectivity(
        nets=infos,
        names="x2" if infos and named == len(infos) else "geometry" if not named else "mixed",
        islands=n_isl,
        holes=len(holes),
        vias=len(doc.vias),
        warnings=warnings,
        timings=timings,
        plating=plating.to_json(),
    )


def _inside_point(fill: cu.FillPolygon) -> tuple[float, float]:
    from shapely.geometry import Polygon

    g = Polygon(fill.outline, [h for h in fill.holes if len(h) >= 3])
    if not g.is_valid:
        g = _shapely().make_valid(g)
    p = g.representative_point()
    return p.x, p.y


def _fill_polygon(g) -> cu.FillPolygon:
    def ring(coords, ccw: bool):
        pts = [(gc.r(x), gc.r(y)) for x, y in list(coords)[:-1]]
        if (cu.signed_area(pts) > 0) != ccw:
            pts.reverse()
        return pts

    return cu.FillPolygon(outline=ring(g.exterior.coords, True), holes=[ring(h.coords, False) for h in g.interiors])


def compare_nets(truth: Mapping[tuple[float, float, str], str], doc: cu.Copper, *, tol: float = 0.05) -> dict:
    """How well ``doc``'s nets match a reference netlist given as pads {(x, y, layer): net}: a reference net
    matches when all its pads land on one net of ``doc`` and that net holds no pad of another reference net.
    Single-pad nets count too. Returns {'nets', 'matched', 'pct', 'mismatches': [...]}."""
    sh = _shapely()
    from shapely.geometry import Point

    geoms, owners = [], []
    for p in doc.pads:
        for ring in p.polygons or []:
            if len(ring) >= 3:
                geoms.append(sh.Polygon(ring).buffer(0))
                owners.append((p.net, tuple(p.layers)))
    for t in doc.tracks:
        pts = [t.start, t.mid, t.end] if t.mid else [t.start, t.end]
        geoms.append(sh.LineString(pts).buffer(t.width / 2))
        owners.append((t.net, (t.layer,)))
    for z in doc.zones:
        for f in z.fill:
            geoms.append(sh.Polygon(f.outline, f.holes).buffer(0))
            owners.append((z.net, (z.layer,)))
    tree = sh.STRtree(geoms)
    got: dict = {}
    for (x, y, layer), _net in truth.items():
        found = [
            owners[i][0] for i in tree.query(Point(x, y).buffer(tol), predicate="intersects") if layer in owners[i][1]
        ]
        got[(x, y, layer)] = Counter(found).most_common(1)[0][0] if found else None
    ref_nets: dict = defaultdict(list)
    for key, net in truth.items():
        ref_nets[net].append(key)
    ours_to_ref: dict = defaultdict(set)
    for key, net in truth.items():
        if got[key] is not None:
            ours_to_ref[got[key]].add(net)
    mismatches = []
    matched = 0
    for net, keys in sorted(ref_nets.items()):
        ours = {got[k] for k in keys}
        if None in ours:
            mismatches.append({"net": net, "why": "a pad is on no copper", "pads": len(keys)})
        elif len(ours) > 1:
            mismatches.append({"net": net, "why": "split", "into": sorted(ours), "pads": len(keys)})
        elif len(ours_to_ref[next(iter(ours))]) > 1:
            mismatches.append(
                {"net": net, "why": "merged", "with": sorted(ours_to_ref[next(iter(ours))] - {net}), "pads": len(keys)}
            )
        else:
            matched += 1
    n = len(ref_nets)
    return {"nets": n, "matched": matched, "pct": round(100 * matched / n, 2) if n else 100.0, "mismatches": mismatches}
