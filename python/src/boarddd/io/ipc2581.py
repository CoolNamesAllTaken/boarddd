"""
IPC-2581 (revisions B and C) -> components with their footprints.

IPC-2581 carries what gerbers lose: each component's package with numbered pins and pad
shapes, its placement, side and rotation, and usually its part number. This reader turns a
file into `Board(components=[Component(...)])` (`read_components`), each with a `boarddd.io.eda.Footprint`
in the footprint's own frame (mm, x right, y up, CCW degrees, top view, unmirrored) and a
`Placement` in the file's board coordinates.

What it reads:

* `Content/Dictionary*` -- standard primitives (Circle, RectCenter, RectRound, RectCham,
  Oval, Contour, Octagon, Diamond, RectCorner) and user primitives (`EntryUser`), each with
  its own `units`.
* `Ecad/CadHeader@units` and `Ecad/CadData/Layer` (layer function and side).
* `Ecad/CadData/Step/Package` -- pins (`Pin@number`, `Location`, `Xform`, a primitive by
  reference or inline), the outline (courtyard), the assembly drawing (body) and silkscreen.
* `Step/PadStackDef` -- per-layer pad, mask and paste primitives and the hole.
* `Step/Component` -- reference, package, part, layer, mount type, `Xform` and `Location`.
* `Step/LayerFeature/Set/Pad` with `PinRef` -- which padstack each component pin uses.
* `Bom/BomItem` -- `RefDes@populate`, `Characteristics/Textual` properties (MPN,
  Manufacturer, Value ...), and `Avl` manufacturer part numbers.

Conventions settled against KiCad 10's exporter and synthetic boards with known ground truth
(magpie's corpus; in boarddd, python/tests/io/test_ipc2581.py cross-checks KiCad's demo boards):

* Package pins are in the package frame, y up, unmirrored. A package is shared by top and
  bottom instances.
* A component's `Xform@rotation` is CCW. With `mirror="true"` (bottom side) the part is
  rotated and then mirrored about the board's y axis, so in the model's convention (mirror y
  first, then rotate) the rotation is 180 - rotation.
* `Location` is the component's origin in the file's board coordinates (y up). The origin is
  the exporter's choice. KiCad writes its page coordinates with y negated; it is not the
  pick-and-place origin.

Other exporters may read the standard's transform order differently; every component's
bottom-side rotation is therefore also checked against its pads' board positions in
`LayerFeature` when they are present, and a disagreement is a warning.
"""

from __future__ import annotations

import gzip
import hashlib
import math
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, replace
from pathlib import Path

from boarddd import model as m
from boarddd.io import eda
from boarddd.io.eda import Aperture, Board, Component, Drill, Footprint, FootprintSource, Graphic, Pad, Placement
from boarddd.io.layers import layer_id as eda_layer_id
from boarddd.io.layers import layer_order, sort_key

__all__ = ["Board", "Component", "read_components", "read_ipc2581", "UNITS"]

UNITS = {"MILLIMETER": 1.0, "MM": 1.0, "INCH": 25.4, "MICRON": 0.001, "MILS": 0.0254, "MIL": 0.0254}

Point = tuple[float, float]


def _r2(size) -> tuple[float, float]:
    """A size rounded to 6 decimals (1 nm), as the model serializes it."""
    return (round(size[0], 6), round(size[1], 6))


# ─── XML helpers ─────────────────────────────────────────────────────────────


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _strip_namespaces(root: ET.Element) -> None:
    for el in root.iter():
        el.tag = _local(el.tag)


def _f(el: ET.Element | None, name: str, default: float = 0.0) -> float:
    if el is None:
        return default
    value = el.get(name)
    try:
        return float(value) if value not in (None, "") else default
    except ValueError:
        return default


def _true(value: str | None) -> bool:
    return str(value or "").strip().lower() in ("true", "1", "yes")


def _load(source) -> ET.Element:
    if isinstance(source, (bytes, bytearray)):
        data = bytes(source)
    else:
        data = Path(source).read_bytes()
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    root = ET.fromstring(data)
    _strip_namespaces(root)
    return root


def _rot(point: Point, degrees: float) -> Point:
    if not degrees:
        return point
    c, s = math.cos(math.radians(degrees)), math.sin(math.radians(degrees))
    return (point[0] * c - point[1] * s, point[0] * s + point[1] * c)


def _norm(angle: float) -> float:
    a = round(angle % 360.0, 6)
    return 0.0 if a >= 360.0 else a


# ─── Primitives ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Shape:
    """A pad-like primitive in its own frame (mm): what a Pad/Aperture needs."""

    shape: str  # model PAD_SHAPES
    size: Point
    roundrect_ratio: float = 0.0
    chamfer_ratio: float = 0.0
    chamfered: tuple[str, ...] = ()
    polygon: tuple[Point, ...] = ()  # around (0, 0), unrotated
    offset: Point = (0.0, 0.0)  # of the shape's center from the reference point


_CORNERS = (
    ("upperLeft", "top_left"),
    ("upperRight", "top_right"),
    ("lowerLeft", "bottom_left"),
    ("lowerRight", "bottom_right"),
)


def _polygon_points(poly: ET.Element, scale: float) -> list[Point]:
    """PolyBegin / PolyStepSegment / PolyStepCurve outline; curves are kept as their end points
    plus the midpoint of the arc, which is enough for an extent and an area."""
    pts: list[Point] = []
    for el in poly:
        if el.tag == "PolyBegin" or el.tag == "PolyStepSegment":
            pts.append((_f(el, "x") * scale, _f(el, "y") * scale))
        elif el.tag == "PolyStepCurve" and pts:
            end = (_f(el, "x") * scale, _f(el, "y") * scale)
            center = (_f(el, "centerX") * scale, _f(el, "centerY") * scale)
            pts.extend(_arc(pts[-1], end, center, _true(el.get("clockwise"))))
    if len(pts) > 1 and math.dist(pts[0], pts[-1]) < 1e-9:
        pts.pop()
    return pts


def _arc(start: Point, end: Point, center: Point, clockwise: bool, steps: int = 8) -> list[Point]:
    a0 = math.atan2(start[1] - center[1], start[0] - center[0])
    a1 = math.atan2(end[1] - center[1], end[0] - center[0])
    r = math.dist(start, center)
    if clockwise:
        while a1 >= a0:
            a1 -= 2 * math.pi
    else:
        while a1 <= a0:
            a1 += 2 * math.pi
    return [
        (center[0] + r * math.cos(a0 + (a1 - a0) * k / steps), center[1] + r * math.sin(a0 + (a1 - a0) * k / steps))
        for k in range(1, steps + 1)
    ]


def _outline_shape(points: list[Point]) -> Shape:
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    local = tuple((round(x - cx, 6), round(y - cy, 6)) for x, y in points)
    return Shape("polygon", (max(xs) - min(xs), max(ys) - min(ys)), polygon=local, offset=(cx, cy))


def primitive(el: ET.Element, scale: float) -> Shape | None:
    """One standard primitive element (Circle, RectRound, Contour ...) as a Shape."""
    tag = el.tag
    if tag == "Circle":
        d = _f(el, "diameter") * scale
        return Shape("circle", (d, d))
    if tag in ("RectCenter", "RectRound", "RectCham"):
        w, h = _f(el, "width") * scale, _f(el, "height") * scale
        if tag == "RectCenter":
            return Shape("rect", (w, h))
        corners = tuple(name for attr, name in _CORNERS if _true(el.get(attr)))
        if tag == "RectRound":
            r = _f(el, "radius") * scale
            if len(corners) in (0, 4) and min(w, h) > 0:
                return Shape("roundrect", (w, h), roundrect_ratio=round(r / min(w, h), 6))
            return Shape("roundrect", (w, h), roundrect_ratio=round(r / min(w, h), 6) if min(w, h) > 0 else 0.0)
        c = _f(el, "chamfer") * scale
        return Shape(
            "chamfered",
            (w, h),
            chamfer_ratio=round(c / min(w, h), 6) if min(w, h) else 0,
            chamfered=corners or tuple(n for _, n in _CORNERS),
        )
    if tag == "Oval":
        return Shape("oval", (_f(el, "width") * scale, _f(el, "height") * scale))
    if tag == "RectCorner":
        x0, y0 = _f(el, "lowerLeftX") * scale, _f(el, "lowerLeftY") * scale
        x1, y1 = _f(el, "upperRightX") * scale, _f(el, "upperRightY") * scale
        return Shape("rect", (abs(x1 - x0), abs(y1 - y0)), offset=((x0 + x1) / 2, (y0 + y1) / 2))
    if tag in ("Diamond", "Octagon", "Hexagon", "Triangle"):
        w, h = _f(el, "width") * scale, _f(el, "height") * scale
        if tag == "Octagon" or tag == "Hexagon":
            w = h = w or _f(el, "length") * scale
        n = {"Diamond": 4, "Octagon": 8, "Hexagon": 6, "Triangle": 3}[tag]
        start = 0.0 if tag == "Diamond" else math.pi / n
        pts = tuple(
            (
                round(w / 2 * math.cos(start + 2 * math.pi * k / n), 6),
                round(h / 2 * math.sin(start + 2 * math.pi * k / n), 6),
            )
            for k in range(n)
        )
        return Shape("polygon", (w, h), polygon=pts)
    if tag == "Ellipse":
        return Shape("oval", (_f(el, "width") * scale, _f(el, "height") * scale))
    if tag == "Contour":
        poly = el.find("Polygon")
        if poly is None:
            return None
        pts = _polygon_points(poly, scale)
        return _outline_shape(pts) if len(pts) >= 3 else None
    if tag == "Polygon":
        pts = _polygon_points(el, scale)
        return _outline_shape(pts) if len(pts) >= 3 else None
    return None


class Dictionaries:
    """Standard and user primitives by id, already in mm."""

    def __init__(self, root: ET.Element):
        self.standard: dict[str, Shape] = {}
        self.user: dict[str, Shape] = {}
        for dic in root.iter("DictionaryStandard"):
            scale = UNITS.get(str(dic.get("units") or "MILLIMETER").upper(), 1.0)
            for entry in dic.findall("EntryStandard"):
                for child in entry:
                    shape = primitive(child, scale)
                    if shape is not None:
                        self.standard[entry.get("id", "")] = shape
                        break
        for dic in root.iter("DictionaryUser"):
            scale = UNITS.get(str(dic.get("units") or "MILLIMETER").upper(), 1.0)
            for entry in dic.findall("EntryUser"):
                shape = self._user(entry, scale)
                if shape is not None:
                    self.user[entry.get("id", "")] = shape

    def _user(self, entry: ET.Element, scale: float) -> Shape | None:
        special = entry.find("UserSpecial")
        if special is None:
            return None
        shapes = [primitive(child, scale) for child in special]
        shapes = [s for s in shapes if s is not None]
        if len(shapes) == 1:
            return shapes[0]
        if shapes:  # several features: their joint outline, as a polygon of the extents
            pts = []
            for s in shapes:
                w, h = s.size
                ox, oy = s.offset
                pts += [
                    (ox - w / 2, oy - h / 2),
                    (ox + w / 2, oy - h / 2),
                    (ox + w / 2, oy + h / 2),
                    (ox - w / 2, oy + h / 2),
                ]
            xs, ys = [p[0] for p in pts], [p[1] for p in pts]
            return _outline_shape([(min(xs), min(ys)), (max(xs), min(ys)), (max(xs), max(ys)), (min(xs), max(ys))])
        return None

    def shape_of(self, el: ET.Element, scale: float) -> Shape | None:
        """The primitive an element references or contains."""
        for child in el:
            if child.tag == "StandardPrimitiveRef":
                return self.standard.get(child.get("id", ""))
            if child.tag == "UserPrimitiveRef":
                return self.user.get(child.get("id", ""))
        for child in el:
            shape = primitive(child, scale)
            if shape is not None:
                return shape
        return None


# ─── Reading ─────────────────────────────────────────────────────────────────


@dataclass
class _Padstack:
    pads: dict[str, tuple[Shape, Point, float]]  # layer -> (shape, location, rotation)
    hole: Drill | None


def read_components(source) -> Board:
    """An IPC-2581 file (path, bytes; gzip accepted) -> Board."""
    root = _load(source)
    if _local(root.tag) != "IPC-2581":
        raise ValueError(f"not an IPC-2581 document (root {root.tag!r})")
    revision = root.get("revision", "")
    dicts = Dictionaries(root)
    ecad = root.find("Ecad")
    if ecad is None:
        raise ValueError("IPC-2581 document has no Ecad section")
    header = ecad.find("CadHeader")
    units = str(header.get("units") if header is not None else "MILLIMETER").upper()
    scale = UNITS.get(units, 1.0)
    cad = ecad.find("CadData")
    warnings: list[str] = []
    if units not in UNITS:
        warnings.append(f"unknown units {units!r}; read as millimeters")

    layer_function = {}
    for layer in cad.findall("Layer"):
        layer_function[layer.get("name", "")] = (
            str(layer.get("layerFunction", "")).upper(),
            str(layer.get("side", "")).upper(),
        )
    steps = cad.findall("Step")
    if not steps:
        raise ValueError("IPC-2581 document has no Step")
    step = steps[0]
    if len(steps) > 1:
        warnings.append(f"{len(steps)} steps; read the first, {step.get('name')!r}")

    bom = _bom(root)
    kicad = _from_kicad(root)
    resolution, basis = _resolution(step, scale)
    padstacks = _padstacks(step, dicts, scale)
    pin_stacks, pin_features, pin_holes = _pin_features(step, dicts, scale)
    packages = {p.get("name", ""): p for p in step.findall("Package")}
    slots = _slots(step, scale)

    components = []
    for comp in step.findall("Component"):
        ref = comp.get("refDes", "")
        pkg_name = comp.get("packageRef", "")
        package = packages.get(pkg_name)
        if package is None:
            warnings.append(f"{ref}: package {pkg_name!r} not found")
            continue
        xform = comp.find("Xform")
        rotation = _f(xform, "rotation")
        mirror = _true(xform.get("mirror")) if xform is not None else False
        layer = comp.get("layerRef", "")
        function, side_attr = layer_function.get(layer, ("", ""))
        bottom = mirror or side_attr == "BOTTOM"
        loc = comp.find("Location")
        x, y = _f(loc, "x") * scale, _f(loc, "y") * scale
        model_rotation = _norm(180.0 - rotation) if bottom else _norm(rotation)
        placement = Placement(
            reference=ref,
            x=round(x, 6),
            y=round(y, 6),
            rotation=model_rotation,
            side="bottom" if bottom else "top",
            package=pkg_name,
        )
        pads = _pads(
            package,
            ref,
            dicts,
            scale,
            padstacks,
            pin_stacks,
            pin_features,
            layer_function,
            warnings,
            placement,
            slots,
            pin_holes,
        )
        _check_rotation(ref, placement, pads, pin_features, layer_function, warnings)
        mount = {"SMT": "smd", "THMT": "tht", "PRESSFIT": "tht"}.get(str(comp.get("mountType", "")).upper(), "")
        item = bom.get(ref, {})
        if kicad:
            pads = [_unnumbered(p) for p in pads]
        package_height = _f(package, "height", -1.0)
        footprint = Footprint(
            name=pkg_name,
            pads=tuple(pads),
            height=round(package_height * scale, 6) if package_height >= 0 else None,
            courtyard=tuple(_outlines(package.find("Outline"), "courtyard", scale)),
            body=tuple(_outlines(package.find("AssemblyDrawing/Outline"), "fab", scale)),
            silk=tuple(_silk(package, scale)),
            fields=dict(item.get("properties", {})),
            source=FootprintSource(
                kind="ipc2581",
                board=step.get("name", ""),
                reference=ref,
                side=placement.side,
                resolution=resolution,
                resolution_basis=basis,
            ),
            mount=mount,
        )
        height, standoff = _f(comp, "height", -1.0), _f(comp, "standoff", -1.0)
        components.append(
            Component(
                reference=ref,
                part_number=comp.get("part", "") or item.get("part", ""),
                package_name=pkg_name,
                placement=placement,
                footprint=footprint,
                mpns=tuple(item.get("mpns", ())),
                properties=dict(item.get("properties", {})),
                mount=mount,
                populate=item.get("populate", True),
                height=round(height * scale, 6) if height >= 0 else None,
                standoff=round(standoff * scale, 6) if standoff >= 0 else None,
            )
        )
    return Board(
        name=step.get("name", ""),
        components=components,
        units=units,
        source="ipc2581",
        revision=revision,
        warnings=warnings,
    )


#: KiCad's exporters name a pad that has no number 'PAD<index>' ('NPTH<index>' for a plain
#: hole).
_INVENTED = re.compile(r"^(PAD|NPTH)\d+$")


def _unnumbered(pad: Pad) -> Pad:
    """A KiCad pin name invented for an unnumbered pad: number None, the name kept."""
    if pad.number and _INVENTED.match(pad.number):
        return replace(pad, number=None, name=pad.number)
    return pad


def _from_kicad(root: ET.Element) -> bool:
    for el in root.iter():
        tag = _local(el.tag)
        if tag in ("SoftwarePackage", "HistoryRecord"):
            text = el.get("name", "") + " " + el.get("software", "") + " " + el.get("vendor", "")
            if "kicad" in text.lower():
                return True
        if tag == "Ecad":
            break
    return False


def _resolution(step: ET.Element, scale: float) -> tuple[float, str]:
    """
    The coordinate step of the geometry a footprint is built from: the most decimals any
    package pin or pad-stack location is written with (IPC-2581 states no precision of its
    own; an exporter's precision setting shows in the numbers).
    """
    decimals = 0
    for tag in ("Package", "PadStackDef"):
        for el in step.iter(tag):
            for sub_el in el.iter():
                for key in ("x", "y", "diameter", "width", "height", "radius"):
                    value = sub_el.get(key)
                    if value and "." in value:
                        decimals = max(decimals, len(value.split(".", 1)[1].rstrip()))
            if decimals >= 6:
                break
    decimals = min(decimals, 9)
    step_mm = round(10.0**-decimals * scale, 12)
    return step_mm, f"IPC-2581 package and pad-stack numbers: {decimals} decimals"


def _bom(root: ET.Element) -> dict[str, dict]:
    """refdes -> {part, populate, properties, mpns} from every BomItem, plus AVL MPNs."""
    avl: dict[str, list[str]] = {}
    for item in root.iter("AvlItem"):
        key = item.get("OEMDesignNumber", "")
        for mpn in item.iter("AvlMpn"):
            if mpn.get("name"):
                avl.setdefault(key, []).append(mpn.get("name"))
    out: dict[str, dict] = {}
    for item in root.iter("BomItem"):
        part = item.get("OEMDesignNumberRef", "")
        props = {}
        for t in item.iter("Textual"):
            name = t.get("textualCharacteristicName")
            if name:
                props[name] = t.get("textualCharacteristicValue", "")
        mpns = [props["MPN"]] if props.get("MPN") else []
        mpns += [m for m in avl.get(part, []) if m not in mpns]
        for refdes in item.findall("RefDes"):
            out[refdes.get("name", "")] = {
                "part": part,
                "properties": props,
                "mpns": mpns,
                "populate": not (refdes.get("populate") is not None and not _true(refdes.get("populate"))),
            }
    return out


def _padstacks(step: ET.Element, dicts: Dictionaries, scale: float) -> dict[str, _Padstack]:
    out = {}
    for psd in step.findall("PadStackDef"):
        pads = {}
        for pad in psd.findall("PadstackPadDef"):
            shape = dicts.shape_of(pad, scale)
            if shape is None:
                continue
            loc = pad.find("Location")
            xf = pad.find("Xform")
            pads[pad.get("layerRef", "")] = (shape, (_f(loc, "x") * scale, _f(loc, "y") * scale), _f(xf, "rotation"))
        hole = None
        h = psd.find("PadstackHoleDef")
        if h is not None:
            d = _f(h, "diameter") * scale
            plated = str(h.get("platingStatus", "PLATED")).upper() != "NONPLATED"
            hole = Drill(size=(d, d), plated=plated, offset=(_f(h, "x") * scale, _f(h, "y") * scale))
        out[psd.get("name", "")] = _Padstack(pads, hole)
    return out


@dataclass(frozen=True)
class _Feature:
    """A pin's pad as drawn on one layer: board position, rotation and shape."""

    layer: str
    shape: Shape
    xy: Point
    rotation: float
    mirror: bool


def _pin_features(step: ET.Element, dicts: Dictionaries, scale: float):
    """(ref, pin) -> padstack name, and (ref, pin) -> its pads on every layer, from the
    LayerFeature pads that carry a PinRef."""
    stacks: dict[tuple[str, str], str] = {}
    features: dict[tuple[str, str], list[_Feature]] = {}
    holes: dict[tuple[str, str], Drill] = {}

    def add(pad: ET.Element, layer: str) -> tuple[str, str] | None:
        pin = pad.find("PinRef")
        if pin is None:
            return None
        key = (pin.get("componentRef", ""), pin.get("pin", ""))
        if pad.get("padstackDefRef"):
            stacks.setdefault(key, pad.get("padstackDefRef"))
        loc, xf = pad.find("Location"), pad.find("Xform")
        shape = dicts.shape_of(pad, scale)
        if loc is not None and shape is not None:
            features.setdefault(key, []).append(
                _Feature(
                    layer,
                    shape,
                    (_f(loc, "x") * scale, _f(loc, "y") * scale),
                    _f(xf, "rotation"),
                    _true(xf.get("mirror")) if xf is not None else False,
                )
            )
        return key

    # Revisions B and C: pads drawn per layer, each tied to its pin.
    for lf in step.findall("LayerFeature"):
        layer = lf.get("layerRef", "")
        for pad in lf.iter("Pad"):
            add(pad, layer)
    # Revision A: board-level padstacks, one per pin, with a LayerPad per layer and the hole.
    for ps in step.findall("PadStack"):
        keys = {k for lp in ps.findall("LayerPad") if (k := add(lp, lp.get("layerRef", "")))}
        hole = ps.find("LayerHole")
        if hole is not None and len(keys) == 1 and _f(hole, "diameter") > 0:
            d = _f(hole, "diameter") * scale
            plated = str(hole.get("platingStatus", "PLATED")).upper() != "NONPLATED"
            holes[next(iter(keys))] = Drill(size=(d, d), plated=plated)
    return stacks, features, holes


def _pads(
    package,
    ref,
    dicts,
    scale,
    padstacks,
    pin_stacks,
    pin_features,
    layer_function,
    warnings,
    placement: Placement,
    slots: list[tuple[Point, Shape, float]],
    pin_holes: dict | None = None,
):
    """
    The package's pins as pads. The file's layer features are authoritative where present:
    a pin number drawn as several copper pads (a switch's two '1' pads, mounting pads all
    named 'MP') becomes one pad per copper feature even when the package lists the number once.
    """
    bottom = placement.side == "bottom"
    pads = []
    by_number: dict[str, list[ET.Element]] = {}
    for pin in package.findall("Pin"):
        by_number.setdefault(pin.get("number", ""), []).append(pin)
    for number, pins in by_number.items():
        features = pin_features.get((ref, number), [])
        copper = _side_copper(features, layer_function, bottom)
        templates = [_template(pin, dicts, scale) for pin in pins]
        centers = [t[0] for t in templates]
        if len(copper) > len(pins):
            # Duplicated numbers collapsed in the package: one pad per copper feature, with
            # the feature's own shape (a SOT-223 tab is not the size of pin 2).
            centers = [placement.to_footprint(f.xy) for f in copper]
            templates = [
                (c, _feature_rotation(f, placement), f.shape, pins[0]) for c, f in zip(centers, copper, strict=False)
            ]
        for (_pin_center, rotation, shape, pin), center in zip(templates, centers, strict=False):
            stack = padstacks.get(pin_stacks.get((ref, number), ""))
            if shape is None and stack is not None:
                shape = next(
                    (
                        sh
                        for layer, (sh, _l, _r) in stack.pads.items()
                        if _FUNCTION_LAYER.get(layer_function.get(layer, ("",))[0]) == "Cu"
                    ),
                    None,
                )
            if shape is None:
                warnings.append(f"{ref} pin {number}: no pad shape")
                continue
            mine = _nearest_features(features, center, centers, placement)
            if mine:
                layers, paste, mask = _openings_from_features(mine, layer_function, placement, rotation)
            else:
                layers, paste, mask = _openings(stack, layer_function, center, rotation, bottom)
            drill = stack.hole if stack is not None else (pin_holes or {}).get((ref, number))
            if drill is not None and drill.size[0] <= 0:
                drill = None
            if drill is None and str(pin.get("type", "")).upper() == "THRU" or (drill is None and "*.Cu" in layers):
                drill = _slot_for(center, rotation, placement, slots)
            if drill is not None:
                kind = "tht" if drill.plated else "npth"
            else:
                kind = "tht" if str(pin.get("type", "")).upper() == "THRU" else "smd"
            offset = _rot(shape.offset, rotation)
            polygon, pad_rotation = shape.polygon, rotation
            if shape.shape == "polygon":
                polygon = tuple((round(q[0], 6), round(q[1], 6)) for q in (_rot(v, rotation) for v in shape.polygon))
                pad_rotation = 0.0
            pads.append(
                Pad(
                    number=number or None,
                    kind=kind,
                    shape=shape.shape,
                    size=(round(shape.size[0], 6), round(shape.size[1], 6)),
                    center=(round(center[0] + offset[0], 6), round(center[1] + offset[1], 6)),
                    rotation=_norm(pad_rotation),
                    roundrect_ratio=shape.roundrect_ratio,
                    chamfer_ratio=shape.chamfer_ratio,
                    chamfered=shape.chamfered,
                    polygon=polygon,
                    drill=drill,
                    paste=paste,
                    mask=mask,
                    layers=layers,
                    function=str(pin.get("electricalType", "")),
                )
            )
    pads += _pads_from_features_only(ref, by_number, pads, pin_features, layer_function, placement, warnings)
    return pads


def _pads_from_features_only(
    ref, by_number, pads, pin_features, layer_function, placement: Placement, warnings
) -> list[Pad]:
    """
    Copper the file draws for this component that its package does not list: a pin number
    missing from the package (KiCad 10 drops a TO-263's tab), or pads on the far side of the
    board (an edge-mount connector soldered on both faces).
    """
    bottom = placement.side == "bottom"
    extra = []
    for (cref, number), features in pin_features.items():
        if cref != ref:
            continue
        copper = [
            f
            for f in features
            if _FUNCTION_LAYER.get(layer_function.get(f.layer, ("",))[0]) == "Cu"
            and layer_function.get(f.layer, ("", ""))[1] in ("TOP", "BOTTOM")
        ]
        known = number in by_number
        for f in copper:
            at = placement.to_footprint(f.xy)
            own_side = (layer_function[f.layer][1] == "BOTTOM") == bottom
            if known and own_side:
                continue
            if any(math.dist(p.center, at) < 0.01 for p in pads + extra):
                continue
            rotation = _feature_rotation(f, placement)
            mine = [g for g in features if math.dist(placement.to_footprint(g.xy), at) < 0.01]
            layers, paste, mask = _openings_from_features(mine, layer_function, placement, rotation)
            shape = f.shape
            offset = _rot(shape.offset, rotation)
            polygon, pad_rotation = shape.polygon, rotation
            if shape.shape == "polygon":
                polygon = tuple((round(q[0], 6), round(q[1], 6)) for q in (_rot(v, rotation) for v in shape.polygon))
                pad_rotation = 0.0
            extra.append(
                Pad(
                    number=number or None,
                    kind="smd",
                    shape=shape.shape,
                    size=(round(shape.size[0], 6), round(shape.size[1], 6)),
                    center=(round(at[0] + offset[0], 6), round(at[1] + offset[1], 6)),
                    rotation=_norm(pad_rotation),
                    roundrect_ratio=shape.roundrect_ratio,
                    chamfer_ratio=shape.chamfer_ratio,
                    chamfered=shape.chamfered,
                    polygon=polygon,
                    paste=paste,
                    mask=mask,
                    layers=layers,
                    function="from layer features",
                )
            )
            if not known:
                warnings.append(f"{ref} pin {number}: not in the package; read from the layer features")
    return extra


def _feature_rotation(feat: _Feature, placement: Placement) -> float:
    """A copper feature's angle in the footprint frame, from its board angle."""
    if placement.side == "bottom":
        return _norm(feat.rotation - (180.0 - placement.rotation))
    return _norm(feat.rotation - placement.rotation)


def _template(pin: ET.Element, dicts: Dictionaries, scale: float):
    """(center, rotation, shape, pin) of a package pin, in the package frame."""
    loc, xf = pin.find("Location"), pin.find("Xform")
    return ((_f(loc, "x") * scale, _f(loc, "y") * scale), _f(xf, "rotation"), dicts.shape_of(pin, scale), pin)


def _side_copper(features, layer_function, bottom: bool):
    """The copper features of a pin on the component's own side (any copper when none)."""
    copper = [f for f in features if _FUNCTION_LAYER.get(layer_function.get(f.layer, ("",))[0]) == "Cu"]
    want = "BOTTOM" if bottom else "TOP"
    mine = [f for f in copper if layer_function.get(f.layer, ("", ""))[1] == want]
    return mine or copper


def _nearest_features(features, center: Point, centers: list[Point], placement: Placement):
    """The features of a pin number that belong to the pad at `center` (nearest pad wins)."""
    if len(centers) <= 1:
        return features
    out = []
    for f in features:
        at = placement.to_footprint(f.xy)
        nearest = min(centers, key=lambda c: math.dist(c, at))
        if nearest == center:
            out.append(f)
    return out


def _slot_for(center: Point, rotation: float, placement: Placement, slots) -> Drill | None:
    """A SlotCavity drawn at this pad's position (slots are not tied to pins)."""
    board = placement.to_board(center)
    for at, shape, slot_rotation in slots:
        if math.dist(at, board) < 0.01:
            w, h = shape.size
            if abs(w - h) <= 1e-9:
                return Drill(size=(w, h))
            # The slot's angle in the footprint frame, then from the pad's own x axis.
            if placement.side == "bottom":
                in_footprint = _norm(slot_rotation - (180.0 - placement.rotation))
            else:
                in_footprint = _norm(slot_rotation - placement.rotation)
            return Drill(size=(w, h), shape="oblong", rotation=_norm(in_footprint - rotation))
    return None


def _slots(step: ET.Element, scale: float) -> list[tuple[Point, Shape, float]]:
    out = []
    for slot in step.iter("SlotCavity"):
        loc, xf = slot.find("Location"), slot.find("Xform")
        shape = None
        for child in slot:
            shape = primitive(child, scale)
            if shape is not None:
                break
        if loc is not None and shape is not None:
            out.append(((_f(loc, "x") * scale, _f(loc, "y") * scale), shape, _f(xf, "rotation")))
    return out


#: Layer functions by what they hold. Revision A says PASTEMASK where B and C say SOLDERPASTE.
_FUNCTION_LAYER = {
    "SIGNAL": "Cu",
    "CONDUCTOR": "Cu",
    "PLANE": "Cu",
    "MIXED": "Cu",
    "CONDPOWER": "Cu",
    "CONDFOIL": "Cu",
    "SOLDERPASTE": "Paste",
    "PASTEMASK": "Paste",
    "SOLDERMASK": "Mask",
}


def _openings(stack: _Padstack | None, layer_function, center: Point, rotation: float, bottom: bool):
    """
    Layers (KiCad names, top-side view: a bottom part's B.Cu is its F.Cu), paste openings and
    the mask opening of a pin, from its padstack. Paste is None when the file has no paste
    layer at all, () when it has one and this pin has no opening on it.
    """
    if stack is None:
        return (), None, None
    sides: dict[str, set[str]] = {}
    paste, mask = [], None
    has_paste_layer = any(f[0] in ("SOLDERPASTE", "PASTEMASK") for f in layer_function.values())
    for layer, (shape, loc, rot) in stack.pads.items():
        function, side = layer_function.get(layer, ("", ""))
        kind = _FUNCTION_LAYER.get(function)
        if kind is None:
            continue
        front = (side != "BOTTOM") != bottom
        sides.setdefault(kind, set()).add("F" if front else "B")
        if kind == "Cu":
            continue
        offset = _rot((loc[0] + shape.offset[0], loc[1] + shape.offset[1]), rotation)
        ap = Aperture(
            shape=shape.shape,
            size=_r2(shape.size),
            center=(round(center[0] + offset[0], 6), round(center[1] + offset[1], 6)),
            rotation=_norm(rotation + rot),
            roundrect_ratio=shape.roundrect_ratio,
            polygon=shape.polygon,
        )
        if kind == "Paste" and front:
            paste.append(ap)
        elif kind == "Mask" and (mask is None or front):
            mask = ap
    layers = tuple(
        ("*" if len(v) > 1 else next(iter(v))) + "." + k for k in ("Cu", "Paste", "Mask") if (v := sides.get(k))
    )
    return layers, (tuple(paste) if has_paste_layer else None), mask


def _openings_from_features(features: list[_Feature], layer_function, placement: Placement, rotation: float):
    """Layers, paste and mask of a pin from what the file draws on each layer (exact, and
    independent of how the exporter shares padstack definitions between sides)."""
    bottom = placement.side == "bottom"
    sides: dict[str, set[str]] = {}
    paste, mask = [], None
    has_paste_layer = any(f[0] in ("SOLDERPASTE", "PASTEMASK") for f in layer_function.values())
    for feat in features:
        function, side = layer_function.get(feat.layer, ("", ""))
        kind = _FUNCTION_LAYER.get(function)
        if kind is None:
            continue
        front = (side != "BOTTOM") != bottom
        sides.setdefault(kind, set()).add("F" if front else "B")
        if kind == "Cu" or not front:
            continue
        center = placement.to_footprint(feat.xy)
        offset = _rot(feat.shape.offset, rotation)
        ap = Aperture(
            shape=feat.shape.shape,
            size=_r2(feat.shape.size),
            center=(round(center[0] + offset[0], 6), round(center[1] + offset[1], 6)),
            rotation=_norm(rotation),
            roundrect_ratio=feat.shape.roundrect_ratio,
            polygon=feat.shape.polygon,
        )
        if kind == "Paste":
            paste.append(ap)
        elif mask is None:
            mask = ap
    layers = tuple(
        ("*" if len(v) > 1 else next(iter(v))) + "." + k for k in ("Cu", "Paste", "Mask") if (v := sides.get(k))
    )
    return layers, (tuple(paste) if has_paste_layer else None), mask


def _check_rotation(ref, placement: Placement, pads: list[Pad], pin_features, layer_function, warnings) -> None:
    """The file's copper pad board positions against placement.to_board(pad.center)."""
    worst = 0.0
    for pad in pads:
        if pad.shape == "polygon":
            continue
        copper = [
            f
            for f in pin_features.get((ref, pad.number or ""), [])
            if _FUNCTION_LAYER.get(layer_function.get(f.layer, ("",))[0]) == "Cu"
        ]
        if copper:
            bx, by = placement.to_board(pad.center)
            worst = max(worst, min(math.dist((bx, by), f.xy) for f in copper))
    if worst > 0.01:
        warnings.append(
            f"{ref}: pads land {worst:.3f} mm from the file's pad positions; "
            "the transform convention may differ from KiCad's"
        )


def _outlines(outline: ET.Element | None, layer: str, scale: float) -> list[Graphic]:
    if outline is None:
        return []
    out = []
    for poly in outline.iter("Polygon"):
        pts = _polygon_points(poly, scale)
        if len(pts) >= 2:
            out.append(Graphic(layer=layer, kind="polygon", points=tuple(pts)))
    return out


def _silk(package: ET.Element, scale: float) -> list[Graphic]:
    out = []
    for silk in package.findall("SilkScreen"):
        for line in silk.iter("Line"):
            start = (_f(line, "startX") * scale, _f(line, "startY") * scale)
            end = (_f(line, "endX") * scale, _f(line, "endY") * scale)
            out.append(Graphic("silk", "line", (start, end)))
        for pl in silk.iter("Polyline"):
            pts = _polygon_points(pl, scale)
            if len(pts) >= 2:
                out.append(Graphic("silk", "line", tuple(pts)))
    return out


# ─── The whole board as boarddd.model (boarddd's own) ───────────────────────────────

#: IPC-2581 layerFunction -> Layer.role (dielectrics are stackup only, not drawable layers).
_LAYER_ROLE = {
    "SIGNAL": "copper",
    "CONDUCTOR": "copper",
    "PLANE": "copper",
    "MIXED": "copper",
    "CONDPOWER": "copper",
    "CONDFOIL": "copper",
    "SOLDERMASK": "mask",
    "SOLDERPASTE": "paste",
    "PASTEMASK": "paste",
    "SILKSCREEN": "silk",
    "LEGEND": "silk",
    "BOARD_OUTLINE": "outline",
    "DRILL": "drill",
    "ROUT": "drill",
    "ASSEMBLY": "fab",
    "COURTYARD": "courtyard",
    "GLUE": "adhesive",
    "DOCUMENT": "user",
}
_STACK_KIND = {"copper": "copper", "mask": "mask", "paste": "paste", "silk": "silk"}
_HOLE_FUNCTION = {"VIA": "via", "PLATED": "component"}


def read_ipc2581(source, *, name: str | None = None) -> m.Board:
    """
    An IPC-2581 file (path or bytes; gzip accepted) -> `boarddd.model.Board`: components and their
    packages as footprints (`read_components`), the profile as the outline, holes and slots of the
    drill layers, layers, stackup (thickness, materials, colours, Er, Df) and net names.
    """
    data = source if isinstance(source, (bytes, bytearray)) else Path(source).read_bytes()
    board = read_components(data)
    footprints, components, warnings = eda.to_model(board)
    root = _load(data)
    ecad = root.find("Ecad")
    cad = ecad.find("CadData")
    header = ecad.find("CadHeader")
    scale = UNITS.get(str(header.get("units") if header is not None else "MILLIMETER").upper(), 1.0)
    step = cad.findall("Step")[0]
    layers, copper_ids = _model_layers(cad)
    drills = _model_drills(step, cad, scale)
    for layer in layers:
        plating = {d.plated for d in drills if d.layer == layer.id}
        if layer.role == "drill" and len(plating) == 1:
            layer.plated = plating.pop()
    software = next((el for el in root.iter("SoftwarePackage")), None)
    generator = (
        " ".join(x for x in (software.get("name", ""), software.get("revision", "")) if x)
        if software is not None
        else ""
    )
    path = None if isinstance(source, (bytes, bytearray)) else Path(source)
    out = m.Board(
        name=name or step.get("name") or (path.stem if path else "board"),
        source=m.Source(
            kind="ipc2581",
            files=[
                m.SourceFile(
                    path=path.name if path else "board.xml", role="ipc2581", sha256=hashlib.sha256(data).hexdigest()
                )
            ],
            generator=generator or None,
            reader="boarddd.io.ipc2581",
        ),
        outline=_model_outline(step, scale, warnings),
        stackup=_model_stackup(cad, header, scale, copper_ids),
        layers=layers,
        drills=drills,
        footprints=footprints,
        components=components,
        nets=[m.Net(name=n) for n in _net_names(step)],
        warnings=board.warnings + warnings,
    )
    if out.stackup.copper_layers is None and copper_ids:
        out.stackup.copper_layers = len(copper_ids)
    return out


def _model_layers(cad: ET.Element) -> tuple[list[m.Layer], list[str]]:
    rows = [
        (
            el.get("name", ""),
            str(el.get("layerFunction", "")).upper(),
            str(el.get("side", "")).upper(),
            str(el.get("polarity", "POSITIVE")).upper(),
        )
        for el in cad.findall("Layer")
    ]
    sequence = {}
    for i, sl in enumerate(cad.iter("StackupLayer")):
        sequence[sl.get("layerOrGroupRef", "")] = _f(sl, "sequence", float(i))
    copper = [r[0] for r in rows if _LAYER_ROLE.get(r[1]) == "copper"]
    written = {n: i for i, n in enumerate(copper)}
    copper.sort(key=lambda n: sequence.get(n, written[n]))
    count = len(copper)
    layers, seen = [], set()
    for lname, function, side_attr, polarity in rows:
        role = _LAYER_ROLE.get(function)
        if role is None:
            continue
        side = {"TOP": "top", "BOTTOM": "bottom", "INTERNAL": "inner"}.get(side_attr, "none")
        index = copper.index(lname) + 1 if role == "copper" else None
        if role == "copper":
            side = "top" if index == 1 else "bottom" if index == count else "inner"
        plated = None
        if role == "drill":
            plated = None if "NON" not in lname.upper() else False
        lid = lname if role in ("user",) else eda_layer_id(role, side, index, count, plated=plated)
        if role in ("fab", "courtyard", "adhesive", "silk", "mask", "paste") and side not in ("top", "bottom"):
            lid = lname
        if role == "drill":
            lid = lname
        base, k = lid, 2
        while lid in seen:
            lid, k = f"{base}-{k}", k + 1
        seen.add(lid)
        layers.append(
            m.Layer(
                id=lid,
                role=role,
                side=side if side in ("top", "bottom", "inner") else "none",
                order=layer_order(role, side, index, count, plated=plated),
                files=[],
                format="ipc2581",
                polarity="negative" if polarity == "NEGATIVE" else "positive",
                function=function,
                plated=plated,
            )
        )
    layers.sort(key=sort_key)
    copper_ids = [la.id for la in layers if la.role == "copper"]
    return layers, copper_ids


def _model_outline(step: ET.Element, scale: float, warnings: list[str]) -> m.Outline | None:
    profile = step.find("Profile")
    if profile is None:
        return None
    board = None
    cutouts = []
    for poly in profile.findall("Polygon"):
        pts = _polygon_points_arcs(poly, scale)
        if len(pts) >= 3:
            board = pts if board is None or abs(_signed(pts)) > abs(_signed(board)) else board
    for cut in profile.findall("Cutout"):
        pts = _polygon_points_arcs(cut, scale)
        if len(pts) >= 3:
            cutouts.append(pts)
    if board is None:
        warnings.append("the Profile has no closed polygon")
        return None
    return m.Outline(board=_wind(board, True), cutouts=[_wind(c, False) for c in cutouts])


def _polygon_points_arcs(poly: ET.Element, scale: float) -> list[Point]:
    """A Polygon/Cutout's points, PolyStepCurve arcs flattened at 48 points per turn (as io.outline)."""
    pts: list[Point] = []
    for el in poly:
        tag = _local(el.tag)
        x, y = _f(el, "x") * scale, _f(el, "y") * scale
        if tag == "PolyBegin":
            pts = [(x, y)]
        elif tag == "PolyStepSegment":
            pts.append((x, y))
        elif tag == "PolyStepCurve" and pts:
            cx, cy = _f(el, "centerX") * scale, _f(el, "centerY") * scale
            sx, sy = pts[-1]
            radius = math.hypot(sx - cx, sy - cy)
            a0, a1 = math.atan2(sy - cy, sx - cx), math.atan2(y - cy, x - cx)
            sweep = a1 - a0
            if _true(el.get("clockwise")):
                while sweep >= 0:
                    sweep -= 2 * math.pi
            else:
                while sweep <= 0:
                    sweep += 2 * math.pi
            steps = max(2, int(abs(sweep) / (2 * math.pi) * 48) + 2)
            pts += [
                (cx + radius * math.cos(a0 + sweep * i / steps), cy + radius * math.sin(a0 + sweep * i / steps))
                for i in range(1, steps)
            ]
            pts.append((x, y))
    if len(pts) > 1 and math.dist(pts[0], pts[-1]) < 1e-9:
        pts.pop()
    return pts


def _signed(pts: list[Point]) -> float:
    return sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(pts, pts[1:] + pts[:1], strict=True)) / 2


def _wind(pts: list[Point], ccw: bool) -> list[tuple[float, float]]:
    out = [(eda.r(x), eda.r(y)) for x, y in pts]
    return out if (_signed(out) > 0) == ccw else out[::-1]


def _model_drills(step: ET.Element, cad: ET.Element, scale: float) -> list[m.Drill]:
    drill_layers = {
        el.get("name", "")
        for el in cad.findall("Layer")
        if str(el.get("layerFunction", "")).upper() in ("DRILL", "ROUT")
    }
    out = []
    for feature in step.findall("LayerFeature"):
        layer = feature.get("layerRef", "")
        if layer not in drill_layers:
            continue
        for hole in feature.iter("Hole"):
            status = str(hole.get("platingStatus", "PLATED")).upper()
            out.append(
                m.Drill(
                    x=eda.r(_f(hole, "x") * scale),
                    y=eda.r(_f(hole, "y") * scale),
                    diameter=eda.r(max(_f(hole, "diameter") * scale, 0.0)),
                    plated=status != "NONPLATED",
                    function=_HOLE_FUNCTION.get(status),
                    layer=layer,
                )
            )
        for slot in feature.iter("SlotCavity"):
            loc, xf = slot.find("Location"), slot.find("Xform")
            shape = next((s for s in (primitive(c, scale) for c in slot) if s is not None), None)
            if loc is None or shape is None:
                continue
            w, h = shape.size
            rotation = _f(xf, "rotation")
            if h > w:
                w, h = h, w
                rotation += 90.0
            half = (w - h) / 2
            dx, dy = _rot((half, 0.0), rotation)
            cx, cy = _f(loc, "x") * scale, _f(loc, "y") * scale
            status = str(slot.get("platingStatus", "PLATED")).upper()
            out.append(
                m.Drill(
                    x=eda.r(cx - dx),
                    y=eda.r(cy - dy),
                    x2=eda.r(cx + dx),
                    y2=eda.r(cy + dy),
                    diameter=eda.r(h),
                    plated=status != "NONPLATED",
                    function=_HOLE_FUNCTION.get(status),
                    layer=layer,
                )
            )
    return out


def _spec_values(header: ET.Element | None) -> dict[str, dict]:
    """Spec name -> {material, color, er, df} from CadHeader/Spec (KiCad writes the stackup's there)."""
    out: dict[str, dict] = {}
    for spec in header.iter("Spec") if header is not None else []:
        values: dict = {}
        texts = []
        for el in spec:
            kind = str(el.get("type", "")).upper()
            for prop in el.findall("Property"):
                text, value = (prop.get("text") or "").strip(), prop.get("value")
                if _local(el.tag) == "General" and text:
                    if ":" in text:
                        key, _, val = (t.strip() for t in text.partition(":"))
                        if key.lower() == "color":
                            values["color"] = val
                    else:
                        texts.append(text)
                elif value is not None and kind in ("DIELECTRIC_CONSTANT", "LOSS_TANGENT"):
                    try:
                        values["er" if kind == "DIELECTRIC_CONSTANT" else "df"] = float(value)
                    except ValueError:
                        pass
        if texts:
            values["material"] = texts[0]
        out[spec.get("name", "")] = values
    return out


def _model_stackup(cad: ET.Element, header: ET.Element | None, scale: float, copper_ids: list[str]) -> m.Stackup:
    stackup = cad.find("Stackup")
    if stackup is None:
        return m.Stackup(copper_layers=len(copper_ids) or None)
    functions = {
        el.get("name", ""): (str(el.get("layerFunction", "")).upper(), str(el.get("side", "")).upper())
        for el in cad.findall("Layer")
    }
    specs = _spec_values(header)
    rows = sorted(stackup.iter("StackupLayer"), key=lambda sl: _f(sl, "sequence"))
    out = []
    copper_index = 0
    for sl in rows:
        ref = sl.get("layerOrGroupRef", "")
        function, side_attr = functions.get(ref, ("", ""))
        role = _LAYER_ROLE.get(function)
        kind = _STACK_KIND.get(role or "", "dielectric" if function.startswith("DIEL") else "other")
        side = {"TOP": "top", "BOTTOM": "bottom"}.get(side_attr, "inner")
        layer = None
        if kind == "copper":
            layer = copper_ids[copper_index] if copper_index < len(copper_ids) else None
            side = "top" if copper_index == 0 else "bottom" if copper_index == len(copper_ids) - 1 else "inner"
            copper_index += 1
        elif kind in ("mask", "paste", "silk") and side in ("top", "bottom"):
            layer = eda_layer_id(kind, side, None, None)
        spec = {}
        for ref_el in sl.findall("SpecRef"):
            spec.update(specs.get(ref_el.get("id", ""), {}))
        thickness = _f(sl, "thickness", -1.0)
        out.append(
            m.StackupLayer(
                name=ref,
                kind=kind,
                side=side,
                thickness=eda.r(thickness * scale) if thickness > 0 else None,
                material=spec.get("material") if kind == "dielectric" else None,
                color=spec.get("color"),
                epsilon_r=spec.get("er"),
                loss_tangent=spec.get("df"),
                layer=layer,
            )
        )

    def color(kind: str, side: str) -> str | None:
        return next((la.color for la in out if la.kind == kind and la.side == side and la.color), None)

    total = _f(stackup, "overallThickness", -1.0)
    return m.Stackup(
        thickness=eda.r(total * scale) if total > 0 else None,
        copper_layers=len(copper_ids) or None,
        mask_color=m.SideValues(top=color("mask", "top"), bottom=color("mask", "bottom")),
        silk_color=m.SideValues(top=color("silk", "top"), bottom=color("silk", "bottom")),
        layers=out,
    )


def _net_names(step: ET.Element) -> list[str]:
    names = {el.get("net", "") for el in step.iter("Set")}
    names |= {el.get("name", "") for el in step.iter("LogicalNet")}
    names |= {el.get("name", "") for el in step.iter("PhyNet")}
    return sorted(n for n in names if n and n.lower() not in ("no_net", "nonet", "$none$"))
