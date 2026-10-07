"""Copper geometry with nets (``boarddd/copper@1``): one JSON document per board revision, next to ``board.json``.

``board.json`` (``boarddd/board@1``) says what the board is; ``copper.json`` holds its routed copper: tracks
(segments and arcs), vias, zone fills (polygons with holes, per layer), keepouts, pads as copper shapes, net ties
and a per-layer plane summary. It is separate because it is big (a pour is thousands of points) and only some
consumers need it (impedance, net highlighting, DRC-like checks); see docs/copper.md.

Conventions are board@1's: millimetres, degrees, the **board frame** (x right, y up, seen from the top, origin =
the source's file origin). Layer ids are board@1 ``Layer.id`` / ``StackupLayer.layer`` values ('F.Cu', 'In1.Cu',
'B.Cu'); net names are board@1 ``Net.name`` values, '' for copper on no net.

Readers: ``boarddd.io.kicad.read_kicad_copper`` (``.kicad_pcb``) and ``boarddd.io.gerber_copper`` (Gerber X2
copper layers with ``%TO.N`` net attributes, plus the drill files); in the browser ``boarddd/copper``'s
``copperFromGerbers``. The dataclasses own the format: ``schema/copper.schema.json`` and ``src/copper/copper.d.ts``
are generated from them (``python -m boarddd.model --write``; CI runs ``--check``).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal

from .model import Source, Vec2, _f, from_dict, to_dict

__all__ = [
    "SCHEMA_ID",
    "Copper",
    "Track",
    "Via",
    "ViaPad",
    "FillPolygon",
    "Zone",
    "Keepout",
    "KeepoutRules",
    "CopperPad",
    "NetTie",
    "Plane",
    "PLANE_COVERAGE",
]

SCHEMA_ID = "boarddd/copper@1"

#: A pour covering at least this share of the board's area is a solid plane (``Plane.solid``).
PLANE_COVERAGE = 0.5


@dataclass(kw_only=True)
class Track:
    """A routed track: a straight segment, or an arc through ``mid``; round ends of diameter ``width``."""

    layer: str = _f("Copper layer id ('F.Cu', 'In1.Cu').")
    net: str = _f("Net name ('' = no net).")
    width: float = _f("Track width, mm.", minimum=0)
    start: Vec2 = _f("First end, board frame.")
    end: Vec2 = _f("Second end, board frame.")
    mid: Vec2 | None = _f(
        "Arcs: the point on the arc halfway between start and end (KiCad's (mid)); null for a straight segment.",
        default=None,
    )
    id: str | None = _f("Source id (KiCad uuid), when the source has one.", default=None)


@dataclass(kw_only=True)
class ViaPad:
    """A via's annular ring on one layer, when it differs from the via's default (KiCad 10 padstacks, Gerber)."""

    layer: str = _f("Copper layer id.")
    diameter: float = _f("Pad diameter on this layer, mm.", minimum=0)


@dataclass(kw_only=True)
class Via:
    """A plated via joining copper layers ``span[0]`` to ``span[1]`` (top to bottom)."""

    at: Vec2 = _f("Centre, board frame.")
    net: str = _f("Net name ('' = no net).")
    diameter: float = _f("Pad (annular ring) diameter, mm: the largest when it differs per layer.", minimum=0)
    drill: float = _f(
        "Finished hole diameter, mm; 0 when the source has none (Gerber without drill files) or writes a placeholder.",
        minimum=0,
    )
    span: tuple[str, str] = _f("First and last copper layer the hole connects, top to bottom.")
    type: Literal["through", "blind", "buried", "micro"] = _f(
        "From the span ('through': outer to outer) or the source's own type (KiCad blind/micro)."
    )
    pad_layers: list[str] | None = _f(
        "Layers in the span with an annular ring, when not all (KiCad 'remove unused layers', Gerber); null = all.",
        default=None,
    )
    padstack: list[ViaPad] | None = _f("Per-layer pad diameters, when they differ from ``diameter``.", default=None)
    id: str | None = _f("Source id (KiCad uuid).", default=None)


@dataclass(kw_only=True)
class FillPolygon:
    """One filled area: an outer loop (counter-clockwise) minus holes (clockwise); loops implicitly closed."""

    outline: list[Vec2] = _f("Outer boundary.", minItems=3)
    holes: list[list[Vec2]] = _f("Holes inside it.", factory=list, items={"minItems": 3})


@dataclass(kw_only=True)
class Zone:
    """Copper of one zone (pour) on one layer: a multi-layer zone has one entry per layer, sharing ``id``."""

    layer: str = _f("Copper layer id.")
    net: str = _f("Net name ('' = no net).")
    kind: Literal["pour", "teardrop", "shape", "region"] = _f(
        "'pour': a KiCad zone; 'teardrop': a KiCad teardrop zone; 'shape': a filled copper drawing (KiCad gr_/fp_ "
        "polygons, rectangles, circles on copper; Gerber EtchedComponent regions); 'region': other filled copper "
        "from a Gerber/ODB++ region, which does not say what drew it."
    )
    fill: list[FillPolygon] = _f("The filled copper as the source stores it (may be stale: see ``stale``).")
    area: float = _f("Filled area, mm^2 (outlines minus holes).", minimum=0)
    outline: list[Vec2] | None = _f(
        "The zone's own boundary as drawn (KiCad (polygon)); null for regions.", default=None
    )
    priority: int | None = _f("KiCad zone priority (higher fills first).", default=None)
    name: str | None = _f("Zone name, when set.", default=None)
    filled: bool = _f(
        "False when the zone has no fill at all on this layer (never filled, or filled before it was moved).",
        default=True,
    )
    stale: bool | None = _f(
        "True when a fill check found the fill out of date (copper of another net inside it); null = not checked.",
        default=None,
    )
    id: str | None = _f("Source id (KiCad zone uuid), shared by a multi-layer zone's entries.", default=None)


@dataclass(kw_only=True)
class KeepoutRules:
    """What a keepout (KiCad rule area) forbids: true = not allowed."""

    tracks: bool = _f("No tracks.", default=False)
    vias: bool = _f("No vias.", default=False)
    pads: bool = _f("No pads.", default=False)
    pours: bool = _f("No copper pours.", default=False)
    footprints: bool = _f("No footprints.", default=False)


@dataclass(kw_only=True)
class Keepout:
    """A keepout (KiCad rule area) on one or more copper layers."""

    layers: list[str] = _f("Copper layer ids it applies to.")
    outline: list[Vec2] = _f("Boundary, board frame.", minItems=3)
    rules: KeepoutRules = _f("What it forbids.", factory=KeepoutRules)
    name: str | None = _f("Rule area name, when set.", default=None)
    ref: str | None = _f("The footprint (reference) it belongs to, for footprint keepouts.", default=None)
    id: str | None = _f("Source id (KiCad uuid).", default=None)


@dataclass(kw_only=True)
class CopperPad:
    """A pad as copper: where it is and its shape on the board, plus the footprint pad it is (``ref``/``number``)."""

    ref: str | None = _f(
        "Component reference (board@1 Component.ref); null when the source does not say (Gerber: inner-layer flashes "
        "take it from an outer-layer pad flashed at the same point on the same net)."
    )
    number: str = _f("Pad number ('' for unnumbered pads).")
    net: str = _f("Net name ('' = no net).")
    layers: list[str] = _f("Copper layers it has copper on.")
    at: Vec2 = _f(
        "Centre of the copper shape, board frame: KiCad's pad position plus its shape offset (they differ for pads "
        "with a (drill (offset))); Gerber: the flash point."
    )
    rotation: float = _f("Degrees counter-clockwise, board frame (KiCad: the absolute pad angle).", default=0.0)
    shape: str = _f(
        "KiCad pad shape ('circle', 'rect', 'roundrect', 'oval', 'trapezoid', 'chamfered_rect', 'custom'), or the "
        "Gerber aperture's ('circle', 'rect', 'oval', 'polygon', or the macro name)."
    )
    size: Vec2 | None = _f("Width, height before rotation, mm (Gerber macros: the bounding box).", default=None)
    polygons: list[list[Vec2]] = _f(
        "Filled loops, board frame, whose union is the pad's copper (circles and arcs flattened).", factory=list
    )
    drill: float | None = _f("Hole diameter (oval: the smaller size), mm, for pads with a hole.", default=None)
    type: Literal["smd", "thru_hole", "np_thru_hole", "connect"] | None = _f(
        "KiCad pad type, when the source says.", default=None
    )
    function: str | None = _f(
        "Gerber X2 aperture function ('SMDPad,CuDef', 'ComponentPad', 'HeatsinkPad'...), when read from Gerber.",
        default=None,
    )


@dataclass(kw_only=True)
class NetTie:
    """A net tie: a footprint whose pads short different nets on purpose (KiCad net_tie_pad_groups)."""

    ref: str = _f("Component reference.")
    groups: list[list[str]] = _f("Pad numbers shorted together, per group.")
    nets: list[str] = _f("The nets the tie joins.", factory=list)


@dataclass(kw_only=True)
class Plane:
    """Pour copper of one net on one layer (teardrops excluded), for reference-plane detection."""

    layer: str = _f("Copper layer id.")
    net: str = _f("Net name.")
    area: float = _f("Filled area, mm^2.", minimum=0)
    coverage: float | None = _f(
        "Share of the board's area (outline minus cutouts) it covers, 0..1; null without an outline.",
        default=None,
        minimum=0,
    )
    solid: bool = _f(f"coverage >= {PLANE_COVERAGE}: a solid plane, as reference planes are.", default=False)


@dataclass(kw_only=True)
class Copper:
    """The copper of one board revision."""

    schema: Literal["boarddd/copper@1"] = _f("Format id.", default=SCHEMA_ID, required=True)
    board: str = _f("The board@1 document's name this copper belongs to.")
    source: Source = _f("Provenance.")
    units: Literal["mm"] = _f("Length unit.", default="mm", required=True)
    frame: Literal["board"] = _f("Coordinate frame: x right, y up, seen from the top.", default="board", required=True)
    layers: list[str] = _f("Copper layer ids, top to bottom.")
    nets: list[str] = _f("Net names the copper uses ('' first when some copper has no net).", factory=list)
    tracks: list[Track] = _f("Tracks.", factory=list)
    vias: list[Via] = _f("Vias.", factory=list)
    zones: list[Zone] = _f("Zone fills per layer (pours, teardrops, Gerber regions).", factory=list)
    keepouts: list[Keepout] = _f("Keepouts (rule areas).", factory=list)
    pads: list[CopperPad] = _f("Pads as copper.", factory=list)
    net_ties: list[NetTie] = _f("Net ties.", factory=list)
    planes: list[Plane] = _f("Pour area per layer and net, for reference-plane detection.", factory=list)
    warnings: list[str] = _f("What the reader could not represent, or found suspicious (stale fills).", factory=list)
    meta: dict[str, Any] = _f("Free-form application data (not interpreted by boarddd).", factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return to_dict(self)

    def to_json(self, indent: int | None = None) -> str:
        """Compact JSON, one top-level list item per line (diffable, about half the size of indented JSON)."""
        if indent is not None:
            return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False) + "\n"
        return dumps(self.to_dict())

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Copper:
        return from_dict(cls, data)

    @classmethod
    def from_json(cls, text: str) -> Copper:
        return cls.from_dict(json.loads(text))


def dumps(doc: dict[str, Any]) -> str:
    """copper.json's layout: top-level keys one per line, list items one per line, each item compact."""

    def compact(v: Any) -> str:
        return json.dumps(v, ensure_ascii=False, separators=(",", ":"))

    lines = ["{"]
    keys = list(doc)
    for i, k in enumerate(keys):
        v = doc[k]
        comma = "," if i < len(keys) - 1 else ""
        if isinstance(v, list) and v:
            lines.append(f"{json.dumps(k)}: [")
            lines.extend(f"  {compact(x)}{',' if j < len(v) - 1 else ''}" for j, x in enumerate(v))
            lines.append(f"]{comma}")
        else:
            lines.append(f"{json.dumps(k)}: {compact(v)}{comma}")
    lines.append("}")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------------------------------------------------
# polygon helpers shared by the readers (stdlib only)

Pt = tuple[float, float]


def signed_area(pts: list[Pt]) -> float:
    """Shoelace area: > 0 counter-clockwise (board frame, y up)."""
    n = len(pts)
    return sum(pts[i][0] * pts[(i + 1) % n][1] - pts[(i + 1) % n][0] * pts[i][1] for i in range(n)) / 2


def _key(p: Pt) -> tuple[int, int]:
    return (round(p[0] * 1e6), round(p[1] * 1e6))


def unfracture(ring: list[Pt]) -> list[FillPolygon]:
    """A fractured polygon (KiCad's filled_polygon, a Gerber G36 region: holes joined to the outline by zero-width
    cuts that run there and back along the same edge) as polygons with holes.

    The cut edge pairs cancel; what is left chains into loops (at a vertex with several ways on, the original
    order wins), and loops nest by containment: even depth = outline (made counter-clockwise), odd = hole
    (clockwise) of the smallest loop around it. Points are matched at 1 nm. The area is the ring's either way.
    """
    pts: list[Pt] = []
    for p in ring:
        if not pts or _key(p) != _key(pts[-1]):
            pts.append(p)
    while len(pts) > 1 and _key(pts[0]) == _key(pts[-1]):
        pts.pop()
    n = len(pts)
    if n < 3:
        return []
    keys = [_key(p) for p in pts]
    edges = [(keys[i], keys[(i + 1) % n]) for i in range(n)]
    by_dir: dict[tuple, list[int]] = {}
    for i, e in enumerate(edges):
        by_dir.setdefault(e, []).append(i)
    alive = [True] * n
    for i, (a, b) in enumerate(edges):
        if not alive[i]:
            continue
        back = [j for j in by_dir.get((b, a), ()) if alive[j]]
        if back:
            alive[i] = alive[back[0]] = False
    out_edges: dict[tuple, list[int]] = {}
    for i, (a, _b) in enumerate(edges):
        if alive[i]:
            out_edges.setdefault(a, []).append(i)
    loops: list[list[Pt]] = []
    for start in range(n):
        if not alive[start]:
            continue
        loop: list[Pt] = []
        i = start
        while alive[i]:
            alive[i] = False
            loop.append(pts[i])
            v = edges[i][1]
            nxt = (i + 1) % n
            if alive[nxt] and edges[nxt][0] == v:
                i = nxt
                continue
            cands = [j for j in out_edges.get(v, ()) if alive[j]]
            if not cands:
                break
            i = cands[0]
        if len(loop) >= 3 and abs(signed_area(loop)) > 1e-12:
            loops.append(loop)
    loops.sort(key=lambda lp: -abs(signed_area(lp)))
    boxes = [bbox(lp) for lp in loops]
    parent: list[int | None] = []
    depth: list[int] = []
    for i, lp in enumerate(loops):
        p = None
        bx = boxes[i]
        for j in range(i - 1, -1, -1):  # smallest enclosing loop first
            bj = boxes[j]
            if bx[0] < bj[0] or bx[1] < bj[1] or bx[2] > bj[2] or bx[3] > bj[3]:
                continue
            if point_in_ring(lp[0], loops[j]) or point_in_ring(_inner_point(lp), loops[j]):
                p = j
                break
        parent.append(p)
        depth.append(0 if p is None else depth[p] + 1)
    polys: dict[int, FillPolygon] = {}
    for i, lp in enumerate(loops):
        a = signed_area(lp)
        if depth[i] % 2 == 0:
            polys[i] = FillPolygon(outline=lp if a > 0 else lp[::-1])
        else:
            polys[parent[i]].holes.append(lp if a < 0 else lp[::-1])
    return list(polys.values())


def _inner_point(lp: list[Pt]) -> Pt:
    """A point just off the first edge's midpoint (for loops that touch their parent at their first vertex)."""
    (x0, y0), (x1, y1) = lp[0], lp[1]
    return ((x0 + x1) / 2, (y0 + y1) / 2)


def point_in_ring(p: Pt, ring: list[Pt]) -> bool:
    """Even-odd ray cast."""
    x, y = p
    inside = False
    x1, y1 = ring[-1]
    for x2, y2 in ring:
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            inside = not inside
        x1, y1 = x2, y2
    return inside


def fill_area(fill: list[FillPolygon]) -> float:
    return sum(abs(signed_area(p.outline)) - sum(abs(signed_area(h)) for h in p.holes) for p in fill)


def circle_halves(c: Pt, start: Pt) -> list[tuple[Pt, Pt, Pt]]:
    """A full circle as two half arcs (start, end, mid): start -> opposite with its mid a quarter turn
    counter-clockwise from start, then back. Tracks have no full circles; both readers write them this way."""
    dx, dy = start[0] - c[0], start[1] - c[1]
    o = (round(c[0] - dx, 6), round(c[1] - dy, 6))
    m1 = (round(c[0] - dy, 6), round(c[1] + dx, 6))
    m2 = (round(c[0] + dy, 6), round(c[1] - dx, 6))
    s = (round(start[0], 6), round(start[1], 6))
    return [(s, o, m1), (o, s, m2)]


def in_fill(p: Pt, poly: FillPolygon) -> bool:
    return point_in_ring(p, poly.outline) and not any(_in_box(p, bbox(h)) and point_in_ring(p, h) for h in poly.holes)


def _in_box(p: Pt, b: tuple[float, float, float, float]) -> bool:
    return b[0] <= p[0] <= b[2] and b[1] <= p[1] <= b[3]


def bbox(pts: list[Pt]) -> tuple[float, float, float, float]:
    xs = [q[0] for q in pts]
    ys = [q[1] for q in pts]
    return (min(xs), min(ys), max(xs), max(ys))


def outline_area(outline) -> float | None:
    """Area of a board@1 Outline (board minus cutouts), or None."""
    if outline is None or len(outline.board) < 3:
        return None
    return abs(signed_area(list(outline.board))) - sum(abs(signed_area(list(c))) for c in outline.cutouts)


def planes(zones: list[Zone], board_area: float | None, layers: list[str] | None = None) -> list[Plane]:
    """Pour area per (layer, net), teardrops and no-net copper excluded; by ``layers`` order, then largest first."""
    acc: dict[tuple[str, str], float] = {}
    for z in zones:
        if z.kind != "teardrop" and z.net:
            acc[(z.layer, z.net)] = acc.get((z.layer, z.net), 0.0) + z.area
    rank = {lid: i for i, lid in enumerate(layers or [])}
    out = []
    for (layer, net), area in sorted(acc.items(), key=lambda kv: (rank.get(kv[0][0], len(rank)), -kv[1])):
        cov = round(min(area / board_area, 1.0), 4) if board_area else None
        out.append(
            Plane(layer=layer, net=net, area=round(area, 4), coverage=cov, solid=bool(cov and cov >= PLANE_COVERAGE))
        )
    return out


def check_fills(cu: Copper) -> int:
    """Mark pours (and Gerber regions) whose fill holds copper of another net (a track end or middle, a via or pad
    centre on its layer) as ``stale``, with a warning each; return how many. KiCad never fills over other nets, so
    this catches fills left from before the copper moved. Unfilled zones, teardrops and copper drawings (which are
    not filled around other copper) are not checked."""
    probes: dict[str, list[tuple[Pt, str, str]]] = {}
    for t in cu.tracks:
        m = t.mid or ((t.start[0] + t.end[0]) / 2, (t.start[1] + t.end[1]) / 2)
        for p in (t.start, t.end, m):
            probes.setdefault(t.layer, []).append((p, t.net, "track"))
    order = {lid: i for i, lid in enumerate(cu.layers)}
    for v in cu.vias:
        a, b = order.get(v.span[0], 0), order.get(v.span[1], len(cu.layers) - 1)
        for lid in v.pad_layers if v.pad_layers is not None else cu.layers[a : b + 1]:
            probes.setdefault(lid, []).append((v.at, v.net, "via"))
    for pd in cu.pads:
        for lid in pd.layers:
            probes.setdefault(lid, []).append((pd.at, pd.net, f"pad {pd.ref}.{pd.number}" if pd.ref else "pad"))
    stale = 0
    for z in cu.zones:
        if not z.fill or z.kind not in ("pour", "region"):  # drawings and teardrops are not filled around copper
            continue
        hit = None
        for poly in z.fill:
            box = bbox(poly.outline)
            holes = [(bbox(h), h) for h in poly.holes]
            for p, net, what in probes.get(z.layer, ()):
                if (
                    net != z.net
                    and _in_box(p, box)
                    and point_in_ring(p, poly.outline)
                    and not any(_in_box(p, hb) and point_in_ring(p, h) for hb, h in holes)
                ):
                    hit = (p, net, what)
                    break
            if hit:
                break
        z.stale = hit is not None
        if hit:
            stale += 1
            p, net, what = hit
            label = f"zone {z.name!r}" if z.name else "zone"
            cu.warnings.append(
                f"{label} ({z.net or 'no net'}) on {z.layer}: fill is stale: {what} of net {net or '(none)'!r} "
                f"at ({p[0]:.3f}, {p[1]:.3f}) is inside it; refill the zones"
            )
    return stale
