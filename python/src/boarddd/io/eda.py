"""
The component model the IPC-2581 and ODB++ readers build, and its conversion to `boarddd.model`.

`ipc2581.read_components` and `odbpp.read_components` return a `Board` of `Component`s, each
with its `Footprint` and `Placement`, as magpie's readers do (the shapes here are the subset of
magpie's footprint model they use). `to_model` turns that into a `boarddd.model.Board`.

Conventions of this model:

* millimeters;
* the footprint's own frame, seen from the top: x right, **y up**, angles in degrees
  counterclockwise. A bottom-side footprint is stored unmirrored, as it would sit on the top;
* a pad's `size` is its width and height before its `rotation`; `center` is in the footprint
  frame;
* paste and mask openings are given explicitly per pad when the source states or implies them:
  `paste=None` means unknown (a source with no paste layer), `paste=[]` means none.

`Placement` is the board model's transform exactly (a footprint point f lands at
`xy + R(rotation) * M(f)`, M mirroring y on the bottom), so a placement converts as it is;
footprints flip to `boarddd.model`'s KiCad semantics (y down; a pad's position is its hole).

Source: `magpie/footprint/model.py` (the classes `Drill`, `Aperture`, `Pad`, `Graphic`,
`FootprintSource`, `Footprint`, `Placement`, `Component`, `Board`) at magpie commit 3a0374d3,
without the JSON round trip and the pin-1 and version types; `to_model` is boarddd's own.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from boarddd import model as m

__all__ = [
    "PAD_KINDS",
    "PAD_SHAPES",
    "Drill",
    "Aperture",
    "Pad",
    "Graphic",
    "FootprintSource",
    "Footprint",
    "Placement",
    "Component",
    "Board",
    "to_model",
]

PAD_KINDS = ("smd", "tht", "npth")
PAD_SHAPES = ("rect", "roundrect", "chamfered", "circle", "oval", "trapezoid", "polygon")

Point = tuple[float, float]


@dataclass(frozen=True)
class Drill:
    """
    A hole. `size` is (w, h): equal for a round hole; for a slot, w is the length along the
    slot's axis, which is turned `rotation` degrees CCW from the pad's x axis.
    """

    size: Point
    shape: str = "circle"  # circle | oblong
    offset: Point = (0.0, 0.0)  # of the hole from the pad's center, pad frame
    plated: bool = True
    rotation: float = 0.0


@dataclass(frozen=True)
class Aperture:
    """
    A paste or mask opening in the footprint frame.

    Same shape vocabulary as a pad. `polygon` (for shape 'polygon') is in the aperture's own
    frame: around `center`, before `rotation`.
    """

    shape: str
    size: Point
    center: Point = (0.0, 0.0)
    rotation: float = 0.0
    roundrect_ratio: float = 0.0
    polygon: tuple[Point, ...] = ()
    #: Holes in a 'polygon' opening (a ring's inside), in the polygon's frame.
    holes: tuple[tuple[Point, ...], ...] = ()

    @property
    def area(self) -> float:
        return _area(self.shape, self.size, self.roundrect_ratio, self.polygon) - _holes_area(self.holes)

    @property
    def perimeter(self) -> float:
        return _perimeter(self.shape, self.size, self.roundrect_ratio, self.polygon)


@dataclass(frozen=True)
class Pad:
    """
    One pad.

    `number` is the pad's name as the footprint numbers it ('1', 'A1', 'EP'), or None when the
    source does not say (gerbers without X2, or KiCad's unnumbered paste and thermal pads).
    """

    number: str | None
    kind: str  # PAD_KINDS
    shape: str  # PAD_SHAPES
    size: Point
    center: Point
    rotation: float = 0.0
    roundrect_ratio: float = 0.0  # corner radius / min(size), KiCad's convention
    chamfer_ratio: float = 0.0
    #: Chamfered corners, of 'top_left', 'top_right', 'bottom_left', 'bottom_right' (pad frame).
    chamfered: tuple[str, ...] = ()
    #: Trapezoid: KiCad's `rect_delta` as the file has it (pad frame, y down).
    delta: Point = (0.0, 0.0)
    #: Polygon and trapezoid pads: the outline around `center`, in the footprint frame's
    #: orientation (their `rotation` is 0; readers bake it into the points). `size` is the
    #: outline's extent.
    polygon: tuple[Point, ...] = ()
    drill: Drill | None = None
    #: Polygon pads: holes in the outline (a custom pad drawn as a ring around a mounting
    #: hole), in the outline's frame. The outline is the exterior; the copper is between.
    holes: tuple[tuple[Point, ...], ...] = ()
    paste: tuple[Aperture, ...] | None = None
    mask: Aperture | None = None
    #: Layers in KiCad's names, top-side view: 'F.Cu', 'F.Paste', 'F.Mask', '*.Cu' ...
    layers: tuple[str, ...] = ()
    #: Extra: what the source called the pad's function ('SMDPad,CuDef', 'HeatsinkPad').
    function: str = ""
    #: The source's own label for a pad that has no terminal number: KiCad's IPC-2581 and
    #: ODB++ exporters invent 'PAD3' for an unnumbered pad. `number` stays None for such a
    #: pad, so a board file and its export hash the same. Never part of the identity.
    name: str = ""

    @property
    def area(self) -> float:
        return _area(self.shape, self.size, self.roundrect_ratio, self.polygon) - _holes_area(self.holes)

    @property
    def perimeter(self) -> float:
        return _perimeter(self.shape, self.size, self.roundrect_ratio, self.polygon)

    def corners(self) -> list[Point]:
        """The pad's rectangle (or polygon) in the footprint frame. Rounding ignored."""
        if self.shape in ("polygon", "trapezoid") and self.polygon:
            local = list(self.polygon)
        else:
            w, h = self.size[0] / 2, self.size[1] / 2
            local = [(-w, -h), (w, -h), (w, h), (-w, h)]
        c, s = math.cos(math.radians(self.rotation)), math.sin(math.radians(self.rotation))
        return [(self.center[0] + x * c - y * s, self.center[1] + x * s + y * c) for x, y in local]

    def extent(self) -> tuple[float, float, float, float]:
        """(xmin, ymin, xmax, ymax) of the copper in the footprint frame."""
        if self.shape in ("circle",):
            r = self.size[0] / 2
            return (self.center[0] - r, self.center[1] - r, self.center[0] + r, self.center[1] + r)
        xs, ys = zip(*self.corners(), strict=False)
        return (min(xs), min(ys), max(xs), max(ys))


@dataclass(frozen=True)
class Graphic:
    """
    A drawn item from the courtyard, fab or silkscreen layer.

    `kind` is 'line' (a polyline through `points`), 'polygon' (closed), 'circle' (`points` is
    [center, a point on the circle]), 'arc' ([start, mid, end]) or 'rect' ([corner, corner]).
    """

    layer: str  # courtyard | fab | silk
    kind: str
    points: tuple[Point, ...]
    width: float = 0.0
    filled: bool = False


@dataclass(frozen=True)
class FootprintSource:
    """Where a footprint came from. Every field optional; say what is known."""

    kind: str = ""  # board | gerbers | ipc2581 | library | reference | file
    board: str = ""  # board or package name
    file: str = ""
    reference: str = ""  # designator it was read at, 'U3'
    library_id: str = ""  # 'Package_SO:SOIC-8_3.9x4.9mm_P1.27mm'
    side: str = ""  # top | bottom, of the placement it was read at
    #: Which layers the pads were read from, for a gerber read.
    layers: tuple[str, ...] = ()
    #: The coordinate step the source writes in, mm (0.01 for KiCad's default ODB++ export,
    #: 1e-6 for a .kicad_pcb or a 4.6 gerber); 0.0 when unknown. Version matching allows
    #: for it (`identity.match_version`).
    resolution: float = 0.0
    #: Where `resolution` comes from: 'ODB++ eda/data, features: 2 decimals, MM'.
    resolution_basis: str = ""


@dataclass(frozen=True)
class Footprint:
    name: str
    pads: tuple[Pad, ...]
    courtyard: tuple[Graphic, ...] = ()
    body: tuple[Graphic, ...] = ()  # fab layer outline
    silk: tuple[Graphic, ...] = ()
    models: tuple[str, ...] = ()  # 3D model paths as the source names them
    fields: dict = field(default_factory=dict)
    source: FootprintSource = FootprintSource()
    #: 'smd', 'tht' or '' as the source declares it.
    mount: str = ""
    #: Package height above the board as the source declares it (IPC-2581 `Package@height`),
    #: mm, or None. Not part of the identity: it describes the part, not the copper.
    height: float | None = None

    def numbered(self) -> list[Pad]:
        return [p for p in self.pads if p.number]


@dataclass(frozen=True)
class Placement:
    """
    Where a footprint sits on a board, in the pick-and-place frame: mm, x right, y up,
    rotation CCW degrees as KiCad's pos file states it.

    A footprint-frame point f lands on the board at `xy + R(rotation) · M(f)`, where M mirrors
    y on the bottom side and is the identity on the top.
    """

    reference: str
    x: float
    y: float
    rotation: float
    side: str = "top"
    value: str = ""
    package: str = ""

    def to_board(self, point: Point) -> Point:
        x, y = point
        if self.side == "bottom":
            y = -y
        c, s = math.cos(math.radians(self.rotation)), math.sin(math.radians(self.rotation))
        return (self.x + x * c - y * s, self.y + x * s + y * c)

    def to_footprint(self, point: Point) -> Point:
        dx, dy = point[0] - self.x, point[1] - self.y
        c, s = math.cos(math.radians(self.rotation)), math.sin(math.radians(self.rotation))
        x, y = dx * c + dy * s, -dx * s + dy * c
        return (x, -y) if self.side == "bottom" else (x, y)


@dataclass
class Component:
    """
    One placed part as a board-level reader returns it: every board reader (`.kicad_pcb`,
    IPC-2581, ODB++) returns a `Board` of these. Fields after `footprint` are optional.
    """

    reference: str
    part_number: str  # the file's part name (IPC `Component@part`, ODB CMP part)
    package_name: str  # footprint / package name as the file gives it
    placement: Placement
    footprint: Footprint
    #: Manufacturer part numbers the file names (BOM characteristic `MPN`, AVL entries).
    mpns: tuple[str, ...] = ()
    #: Everything else the file says about the part: Value, Manufacturer, Datasheet, KiCad's
    #: exclude_from_pos_files / exclude_from_bom ...
    properties: dict = field(default_factory=dict)
    mount: str = ""  # smd | tht | '' as the file declares it
    populate: bool = True  # False for a DNP part, when the file says so
    #: Placed height above the board and the gap under the body (IPC-2581 Component@height,
    #: @standoff), mm, or None.
    height: float | None = None
    standoff: float | None = None


@dataclass
class Board:
    """What a board-level reader returns."""

    name: str
    components: list[Component]
    units: str = ""
    source: str = ""  # 'kicad_pcb' | 'ipc2581' | 'odbpp'
    revision: str = ""  # file format version or revision
    warnings: list[str] = field(default_factory=list)
    #: The drill/place origin in the file's own board coordinates, when the file has one.
    #: Placements are already relative to it.
    aux_origin: Point | None = None
    #: Board-wide settings a reader keeps: KiCad's mask and paste margins ...
    properties: dict = field(default_factory=dict)

    def by_reference(self) -> dict[str, Component]:
        return {c.reference: c for c in self.components}


def _area(shape: str, size: Point, ratio: float, polygon) -> float:
    w, h = size
    if shape == "circle":
        return math.pi * w * w / 4
    if shape == "oval":
        d = min(w, h)
        return (max(w, h) - d) * d + math.pi * d * d / 4
    if shape == "roundrect":
        r = ratio * min(w, h)
        return w * h - (4 - math.pi) * r * r
    if shape in ("polygon", "trapezoid") and polygon:
        return (
            abs(sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(polygon, polygon[1:] + polygon[:1], strict=False)))
            / 2
        )
    return w * h


def _holes_area(holes) -> float:
    return sum(_area("polygon", (0.0, 0.0), 0.0, hole) for hole in holes)


def _perimeter(shape: str, size: Point, ratio: float, polygon) -> float:
    w, h = size
    if shape == "circle":
        return math.pi * w
    if shape == "oval":
        d = min(w, h)
        return 2 * (max(w, h) - d) + math.pi * d
    if shape == "roundrect":
        r = ratio * min(w, h)
        return 2 * (w + h) - 8 * r + 2 * math.pi * r
    if shape in ("polygon", "trapezoid") and polygon:
        return sum(math.dist(a, b) for a, b in zip(polygon, polygon[1:] + polygon[:1], strict=False))
    return 2 * (w + h)


# ─── To boarddd.model ────────────────────────────────────────────────────────

NDIGITS = 6
_PAD_TYPE = {"smd": "smd", "tht": "thru_hole", "npth": "np_thru_hole"}
_PAD_SHAPE = {
    "rect": "rect",
    "roundrect": "roundrect",
    "chamfered": "roundrect",  # KiCad writes a chamfered pad as a roundrect with (chamfer ...)
    "circle": "circle",
    "oval": "oval",
    "trapezoid": "trapezoid",
    "polygon": "custom",
}
_GRAPHIC_LAYER = {"courtyard": "CrtYd", "fab": "Fab", "silk": "SilkS"}
_GRAPHIC_KIND = {"line": "line", "polygon": "poly", "circle": "circle", "arc": "arc", "rect": "rect"}
_DEFAULT_LAYERS = {
    "smd": ["F.Cu", "F.Paste", "F.Mask"],
    "thru_hole": ["*.Cu", "*.Mask"],
    "np_thru_hole": ["*.Cu", "*.Mask"],
}


def r(value: float) -> float:
    value = round(float(value), NDIGITS)
    return 0.0 if value == 0 else value


def _rot(point: Point, degrees: float) -> Point:
    c, s = math.cos(math.radians(degrees)), math.sin(math.radians(degrees))
    return (point[0] * c - point[1] * s, point[0] * s + point[1] * c)


def _angle(a: float) -> float:
    a = (float(a) + 180.0) % 360.0 - 180.0
    return 180.0 if r(a) == -180.0 else r(a)


def _down(point: Point) -> tuple[float, float]:
    """y up -> y down (KiCad's footprint frame)."""
    return (r(point[0]), r(-point[1]))


def _aperture(ap: Aperture, pad: Pad, hole: Point) -> m.Aperture:
    """A footprint-frame opening -> pad-local (relative to the pad's hole position, y down)."""
    local = _rot((ap.center[0] - hole[0], ap.center[1] - hole[1]), -pad.rotation)
    shape = ap.shape if ap.shape in ("rect", "roundrect", "circle", "oval") else "polygon"
    return m.Aperture(
        shape=shape,
        size=(r(ap.size[0]), r(ap.size[1])),
        center=_down(local),
        rotation=_angle(ap.rotation - pad.rotation),
        roundrect_ratio=r(min(ap.roundrect_ratio, 0.5)) if shape == "roundrect" else None,
        polygon=[_down(p) for p in reversed(ap.polygon)] if shape == "polygon" and ap.polygon else None,
    )


def _pad(pad: Pad) -> m.Pad:
    ptype = _PAD_TYPE.get(pad.kind, "smd")
    shape = _PAD_SHAPE.get(pad.shape, "custom")
    # KiCad's pad position is its hole; the copper sits at (drill (offset)) from it.
    offset = pad.drill.offset if pad.drill is not None else (0.0, 0.0)
    hole = (pad.center[0] + _rot(offset, pad.rotation)[0], pad.center[1] + _rot(offset, pad.rotation)[1])
    at_x, at_y = _down(hole)
    out = m.Pad(
        number=pad.number or "",
        type=ptype,
        shape=shape,
        at=(at_x, at_y, _angle(pad.rotation)),
        size=(r(pad.size[0]), r(pad.size[1])),
        layers=list(pad.layers) or list(_DEFAULT_LAYERS[ptype]),
        function=pad.function or None,
    )
    if pad.drill is not None and min(pad.drill.size) > 0:
        w, h = pad.drill.size
        if round(pad.drill.rotation) % 180 == 90:  # a slot across the pad: KiCad's oval runs along its size
            w, h = h, w
        oval = pad.drill.shape == "oblong" and abs(w - h) > 1e-9
        out.drill = m.PadDrill(
            shape="oval" if oval else "circle",
            size=(r(w), r(h if oval else w)),
            offset=(r(-offset[0]), r(offset[1])),
        )
    if shape == "roundrect":
        out.roundrect_rratio = r(min(pad.roundrect_ratio, 0.5))
    if pad.shape == "chamfered":
        out.chamfer_ratio = r(min(pad.chamfer_ratio, 0.5))
        out.chamfer = [c for c in pad.chamfered if c in ("top_left", "top_right", "bottom_left", "bottom_right")]
    if shape == "trapezoid":
        out.rect_delta = (r(pad.delta[0]), r(pad.delta[1]))
    if shape == "custom":
        # The outline is around the copper centre, unrotated (readers bake the rotation in);
        # a primitive is pad-local, relative to the hole, before the pad's rotation (0 here).
        out.at = (at_x, at_y, 0.0)
        dx, dy = pad.center[0] - hole[0], pad.center[1] - hole[1]
        pts = [_down((x + dx, y + dy)) for x, y in reversed(pad.polygon)]
        out.anchor = "circle" if pad.drill is not None else "rect"
        if len(pts) >= 3:
            out.primitives = [m.Primitive(pts=pts)]
        small = r(min(pad.drill.size) if pad.drill is not None and min(pad.drill.size) > 0 else 0.01)
        out.size = (small, small)
    if pad.paste is not None:
        out.paste = [_aperture(ap, pad, hole) for ap in pad.paste]
    if pad.mask is not None:
        out.mask = _aperture(pad.mask, pad, hole)
    return out


def _graphic(g: Graphic) -> m.Graphic | None:
    kind = _GRAPHIC_KIND.get(g.kind, "poly")
    pts = list(g.points)
    if g.kind == "rect" and len(pts) == 2:
        (x0, y0), (x1, y1) = pts
        pts = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    elif g.kind == "circle" and len(pts) == 2:
        (cx, cy), (px, py) = pts
        radius = math.hypot(px - cx, py - cy)
        pts = [
            (cx + radius * math.cos(2 * math.pi * i / 48), cy + radius * math.sin(2 * math.pi * i / 48))
            for i in range(48)
        ]
    closed = kind in ("poly", "rect", "circle") and g.kind != "line"
    if closed and len(pts) > 2 and math.dist(pts[0], pts[-1]) < 1e-9:
        pts = pts[:-1]
    if len(pts) < 2:
        return None
    return m.Graphic(
        layer=f"F.{_GRAPHIC_LAYER.get(g.layer, 'Fab')}",
        kind=kind,
        pts=[_down(p) for p in (reversed(pts) if closed else pts)],
        width=max(0.0, r(g.width)),
        closed=closed,
        filled=bool(g.filled),
    )


def footprint_to_model(fp: Footprint, name: str | None = None) -> m.Footprint:
    """A footprint of this model -> `boarddd.model.Footprint` (KiCad frame, y down)."""
    graphics = [g for g in (_graphic(x) for x in (*fp.silk, *fp.body, *fp.courtyard)) if g is not None]
    return m.Footprint(
        name=name or fp.name,
        pads=[_pad(p) for p in fp.pads],
        graphics=graphics,
        attr=["smd"] if fp.mount == "smd" else ["through_hole"] if fp.mount == "tht" else [],
    )


_MPN_KEYS = ("mpn", "mfr_pn", "manufacturer_part_number", "manufacturer part number", "part_number")
_MANUFACTURER_KEYS = ("manufacturer", "mfr", "mfg", "manufacturer_name")
_VALUE_KEYS = ("value", "val", "comment")


def _natural(ref: str):
    return (re.sub(r"\d+", "", ref), int(re.sub(r"\D", "", ref)[:18] or 0), ref)


def to_model(board: Board) -> tuple[dict[str, m.Footprint], list[m.Component], list[str]]:
    """(footprints by name, components, warnings) for `boarddd.model.Board`.

    A package shared by several components becomes one footprint. When two instances of one
    package name read differently (an exporter's per-instance copy that disagrees), the later
    one is kept as `<name>#2`, `<name>#3` ... and named in a warning.
    """
    footprints: dict[str, m.Footprint] = {}
    variants: dict[str, list[str]] = {}
    components: list[m.Component] = []
    warnings: list[str] = []
    for comp in board.components:
        fp = footprint_to_model(comp.footprint, comp.package_name or comp.footprint.name)
        key = fp.name
        tol = max(2 * comp.footprint.source.resolution, 1e-6)
        for candidate in variants.setdefault(fp.name, [fp.name]):
            known = footprints.get(candidate)
            if known is None or _same(known, fp, tol):
                key = candidate
                break
        else:
            key = f"{fp.name}#{len(variants[fp.name]) + 1}"
            variants[fp.name].append(key)
            warnings.append(f"{comp.reference}: package {fp.name!r} differs from its other instances; kept as {key!r}")
        if key not in footprints:
            fp.name = key
            footprints[key] = fp
        props = {str(k): str(v) for k, v in comp.properties.items() if v not in (None, "")}
        lower = {k.lower(): v for k, v in props.items()}
        value = next((lower[k] for k in _VALUE_KEYS if lower.get(k)), None)
        manufacturer = next((lower[k] for k in _MANUFACTURER_KEYS if lower.get(k)), None)
        mpns = list(dict.fromkeys(comp.mpns)) or [v for k in _MPN_KEYS if (v := lower.get(k))][:1]
        mount = comp.mount or comp.footprint.mount
        pl = comp.placement
        components.append(
            m.Component(
                ref=comp.reference,
                side="bottom" if pl.side == "bottom" else "top",
                x=r(pl.x),
                y=r(pl.y),
                rotation=_angle(pl.rotation),
                value=value or pl.value or None,
                footprint=key,
                mount=mount if mount in ("smd", "tht") else None,
                populate=bool(comp.populate),
                mpn=[m.PartNumber(mpn=v, manufacturer=manufacturer) for v in mpns],
                height=r(comp.height) if comp.height is not None and comp.height >= 0 else None,
                attributes={k: v for k, v in sorted(props.items()) if k.lower() not in _VALUE_KEYS},
            )
        )
    components.sort(key=lambda c: _natural(c.ref))
    return dict(sorted(footprints.items())), components, warnings


def _close(a, b, tol: float) -> bool:
    """Equal, numbers within `tol` (the source's coordinate step: ODB++ from KiCad has 0.01 mm)."""
    if isinstance(a, float) or isinstance(b, float):
        return isinstance(a, (int, float)) and isinstance(b, (int, float)) and abs(a - b) <= tol
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(_close(x, y, tol) for x, y in zip(a, b, strict=True))
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_close(a[k], b[k], tol) for k in a)
    return a == b


def _same(a: m.Footprint, b: m.Footprint, tol: float) -> bool:
    """The same footprint, up to the source's rounding. An angle compares within 0.5 degrees."""
    da, db = m.to_dict(a), m.to_dict(b)
    for pa, pb in zip(da["pads"], db["pads"], strict=False):
        if abs((pa["at"][2] - pb["at"][2] + 180) % 360 - 180) <= 0.5:
            pb["at"][2] = pa["at"][2]
    return _close(
        {k: da[k] for k in ("pads", "graphics", "attr")}, {k: db[k] for k in ("pads", "graphics", "attr")}, tol
    )
