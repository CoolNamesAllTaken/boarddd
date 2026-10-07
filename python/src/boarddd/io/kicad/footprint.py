"""KiCad footprints (``.kicad_mod``, and the footprints inside a ``.kicad_pcb``) -> :class:`boarddd.model.Footprint`.

The model keeps KiCad's footprint semantics (footprint frame, mm, y down, library orientation; see
docs/model.md), so reading is mostly copying, with three things pcbnew does for us made explicit:

* a board footprint's pad ``(at x y a)`` holds the pad's *board* angle (the footprint's rotation is added in):
  the model stores it relative to the footprint;
* a footprint on the back is stored flipped (mirrored across its x axis, layers swapped F <-> B, angles
  negated): the model stores the library (top-side) form, so it is flipped back;
* arcs, circles and custom-pad primitives are flattened to polylines the way boarddd's JS ``.kicad_mod``
  parser (``src/footprint/kicad_mod.js``) does, so both give the same points.

``KicadFootprint`` is the other half: kipr's raw parse of a footprint (texts, every graphic, zones, embedded
files, pad dicts) that renderers (kipr's library viewer, ``render/svg.py`` in phase F5) work from.

Sources (boarddd phase F2): the model half from ``fixtures/royalblue54L_feather/make_board.py`` (boarddd
``2599fed``, itself a port of the JS parser) and magpie ``pcb/kicad_pcb.py`` (internal ``claud/magpie``
``3a0374d3``: KiCad 5 forms, pad-angle semantics, castellated shape offsets, drawn paste openings);
``KicadFootprint`` from kipr ``kipr/library/render/fp.py`` (kipr main ``f632b3f``), parse half only.
"""

from __future__ import annotations

import math
import re
from pathlib import Path

from ... import model as m
from .geom import (
    arc_from_center,
    arc_through,
    norm_angle,
    r,
    ring_points,
    rotate,
    stroke_loops,
)
from .sexpr import Node, load

__all__ = ["read_kicad_mod", "read_footprint", "KicadFootprint", "expand_layers", "decode_embedded"]

GRAPHIC_LAYER = re.compile(r"\.(SilkS|Silkscreen|Fab|CrtYd|Courtyard)$")
FLIP_SIDE = {"F": "B", "B": "F"}


def read_kicad_mod(source: str | Path, name: str | None = None) -> m.Footprint:
    """A library footprint file (path or text). ``name`` overrides the footprint's own (e.g. 'Lib:Name')."""
    root = load(source)
    if root.name not in ("footprint", "module"):
        raise ValueError("not a .kicad_mod file")
    fp = read_footprint(root)
    if name:
        fp.name = name
    return fp


# ---------------------------------------------------------------------------------------------------------------------
# small readers


def _xy(n: Node | None, d=(0.0, 0.0)) -> tuple[float, float]:
    if n is None:
        return d
    v = n.nums()
    return (v[0], v[1]) if len(v) >= 2 else d


def _num(v, d: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def _stroke_width(n: Node, d: float = 0.12) -> float:
    st = n.child("stroke")
    w = (st.child("width") if st is not None else None) or n.child("width")
    return _num(w.arg(0), d) if w is not None else d


def _is_filled(n: Node) -> bool:
    f = n.child("fill")
    if f is None:
        return False
    if f.child("type") is not None:
        return str(f.value("type")) != "none"
    return str(f.arg(0, "")) in ("solid", "yes")


def _poly_pts(n: Node) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for c in (n.child("pts") or Node()).children():
        if c.name == "xy":
            out.append(_xy(c))
        elif c.name == "arc":
            pts = arc_through(_xy(c.child("start")), _xy(c.child("mid")), _xy(c.child("end")))
            out.extend(pts[1:] if out else pts)
    return out


def graphic_points(n: Node) -> dict | None:
    """An fp_*/gr_* drawing as {kind, pts, width, closed, filled} (footprint frame), arcs flattened; None for
    kinds the model doesn't keep (curves, text)."""
    kind = re.sub(r"^(fp|gr)_", "", n.name)
    width, filled = _stroke_width(n), _is_filled(n)
    if kind == "line":
        return dict(
            kind=kind, pts=[_xy(n.child("start")), _xy(n.child("end"))], width=width, closed=False, filled=False
        )
    if kind == "rect":
        (x0, y0), (x1, y1) = _xy(n.child("start")), _xy(n.child("end"))
        return dict(kind=kind, pts=[(x0, y0), (x1, y0), (x1, y1), (x0, y1)], width=width, closed=True, filled=filled)
    if kind == "circle":
        (cx, cy), (ex, ey) = _xy(n.child("center")), _xy(n.child("end"))
        pts = list(reversed(ring_points(cx, cy, math.hypot(ex - cx, ey - cy), 48)))
        return dict(kind=kind, pts=pts, width=width, closed=True, filled=filled)
    if kind == "arc":
        if n.child("mid") is not None:
            s, mid, e = _xy(n.child("start")), _xy(n.child("mid")), _xy(n.child("end"))
        else:  # KiCad 5: start = centre, end = the arc's start, angle = sweep
            s, mid, e = arc_from_center(_xy(n.child("start")), _xy(n.child("end")), n.num("angle", 90.0))
        return dict(kind=kind, pts=arc_through(s, mid, e), width=width, closed=False, filled=False)
    if kind == "poly":
        return dict(
            kind=kind,
            pts=_poly_pts(n),
            width=width,
            closed=True,
            filled=filled or (n.name == "gr_poly" and not n.child("fill")),
        )
    return None


def _primitive_loops(g: dict | None) -> list[list[tuple[float, float]]]:
    if not g or len(g["pts"]) < 2:
        return []
    if g["closed"] and (g["filled"] or g["kind"] == "poly"):
        loops = [g["pts"]]
        if g["width"] > 0:
            loops += stroke_loops(g["pts"], g["width"], True)
        return loops
    return stroke_loops(g["pts"], g["width"] or 0.01, g["closed"])


def _unflip_layer(name: str) -> str:
    return re.sub(r"^([FB])\.", lambda mt: FLIP_SIDE[mt.group(1)] + ".", name)


def _swap_corners(corners: list[str]) -> list[str]:
    return [c.replace("top", "@").replace("bottom", "top").replace("@", "bottom") for c in corners]


# ---------------------------------------------------------------------------------------------------------------------
# footprints -> model


def read_pad(n: Node, fp_angle: float = 0.0, bottom: bool = False, *, board: bool = False) -> m.Pad:
    """A pad in library form: angle relative to the footprint (board files), bottom side flipped back.

    ``board``: the pad comes from a ``.kicad_pcb`` (its angle includes the footprint's); library files hold
    relative angles already.
    """
    vals = [str(v) for v in n.atoms()]
    number = vals[0] if vals else ""
    ptype = vals[1] if len(vals) > 1 else "smd"
    shape = vals[2] if len(vals) > 2 else "rect"
    at = n.child("at")
    av = at.nums() if at is not None else []
    x, y, a = (av + [0.0, 0.0, 0.0])[:3]
    size = n.nums("size") or [0.0, 0.0]
    if len(size) == 1:
        size = [size[0], size[0]]
    sy = -1.0 if bottom else 1.0  # KiCad's flip mirrors the footprint across its x axis
    rel = a - fp_angle if board else a
    if bottom:
        rel = -rel
    pad = m.Pad(
        number=number,
        type=ptype,
        shape=shape,
        at=(r(x), r(sy * y), norm_angle(rel)),
        size=(r(size[0]), r(size[1])),
        layers=[_unflip_layer(str(v)) if bottom else str(v) for v in (n.child("layers") or Node()).atoms()],
    )
    d = n.child("drill")
    if d is not None:
        dv = [str(v) for v in d.atoms()]
        oval = "oval" in dv
        nums = [_num(v) for v in dv if v != "oval"]
        off = d.child("offset")
        offset = (r(_num(off.arg(0))), r(sy * _num(off.arg(1)))) if off is not None else (0.0, 0.0)
        if nums and nums[0] > 0:
            pad.drill = m.PadDrill(
                shape="oval" if oval else "circle",
                size=(r(nums[0]), r(nums[1] if oval and len(nums) > 1 else nums[0])),
                offset=offset,
            )
        elif offset != (0.0, 0.0):
            pad.offset = offset  # an SMD pad's shape offset (castellated pads): KiCad keeps it in (drill (offset))
    if n.child("roundrect_rratio") is not None:
        pad.roundrect_rratio = r(n.num("roundrect_rratio"))
    if n.child("chamfer_ratio") is not None:
        pad.chamfer_ratio = r(n.num("chamfer_ratio"))
    if (v := n.child("chamfer")) is not None:
        corners = [str(c) for c in v.atoms()]
        pad.chamfer = _swap_corners(corners) if bottom else corners
    if (v := n.child("rect_delta")) is not None:
        dv = v.nums() + [0.0, 0.0]
        pad.rect_delta = (r(dv[0]), r(sy * dv[1]))
    if (opts := n.child("options")) is not None:
        pad.anchor = str(opts.value("anchor", "rect"))
    if (prims := n.child("primitives")) is not None:
        loops = [lp for g in prims.children() for lp in _primitive_loops(graphic_points(g))]
        # a mirror reverses winding: flip the point order back for bottom-side pads
        pad.primitives = [
            m.Primitive(pts=[(r(px), r(sy * py)) for px, py in (lp[::-1] if bottom else lp)]) for lp in loops
        ]
    if n.child("solder_mask_margin") is not None:
        pad.solder_mask_margin = r(n.num("solder_mask_margin"))
    if n.child("solder_paste_margin") is not None:
        pad.solder_paste_margin = r(n.num("solder_paste_margin"))
    if (pf := n.value("pinfunction")) is not None and str(pf).lower() in ("ground", "heatsink", "fiducial"):
        pad.function = str(pf).lower()
    if (prop := n.value("property")) is not None:  # (property pad_prop_heatsink), pad_prop_fiducial_loc...
        mt = re.fullmatch(r"pad_prop_(heatsink|fiducial)(?:_\w+)?", str(prop))
        if mt:
            pad.function = mt.group(1)
    return pad


def footprint_angle(fp: Node) -> float:
    at = fp.child("at")
    v = at.nums() if at is not None else []
    return v[2] if len(v) > 2 else 0.0


def read_footprint(fp: Node, *, name: str | None = None, board: bool = False) -> m.Footprint:
    """A ``(footprint ...)`` / ``(module ...)`` node as a model Footprint in library form.

    ``board``: the node comes from a ``.kicad_pcb`` (pad angles absolute, bottom footprints flipped).
    """
    bottom = board and str(fp.value("layer", "F.Cu")).startswith("B.")
    angle = footprint_angle(fp) if board else 0.0
    sy = -1.0 if bottom else 1.0
    attr = fp.child("attr")
    out = m.Footprint(
        name=name or str(fp.arg(0, "")),
        attr=[str(a) for a in (attr.atoms() if attr is not None else []) if a in ("smd", "through_hole")],
    )
    paste_drawings = []
    for c in fp.children():
        if c.name == "pad":
            out.pads.append(read_pad(c, angle, bottom, board=board))
        elif re.fullmatch(r"fp_(line|rect|circle|arc|poly|curve)", c.name):
            layer = str(c.value("layer", ""))
            g = graphic_points(c)
            if not g or len(g["pts"]) < 2:
                continue
            if layer.endswith(".Paste") and (g["filled"] or g["kind"] == "poly"):
                paste_drawings.append([(px, sy * py) for px, py in g["pts"]])
            if GRAPHIC_LAYER.search(layer):
                pts = g["pts"][::-1] if bottom and g["closed"] else g["pts"]
                out.graphics.append(
                    m.Graphic(
                        layer=_unflip_layer(layer) if bottom else layer,
                        kind=g["kind"],
                        pts=[(r(px), r(sy * py)) for px, py in pts],
                        width=max(0.0, r(g["width"])),  # -1 = no stroke
                        closed=g["closed"],
                        filled=g["filled"],
                    )
                )
    for loop in paste_drawings:
        _attach_paste(out.pads, loop)
    return out


def _to_pad_local(pad: m.Pad, p) -> tuple[float, float]:
    x, y, a = pad.at
    return rotate(p[0] - x, p[1] - y, -a)


def _attach_paste(pads: list[m.Pad], loop) -> None:
    """A filled shape drawn on a paste layer is a stencil opening (magpie ``_with_drawn_paste``): put it on the
    largest copper pad under its centre, pad-local, as a polygon aperture."""
    xs, ys = [p[0] for p in loop], [p[1] for p in loop]
    centre = ((max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2)
    hosts = []
    for pad in pads:
        if pad.type == "np_thru_hole":
            continue
        lx, ly = _to_pad_local(pad, centre)
        if abs(lx) <= pad.size[0] / 2 + 1e-6 and abs(ly) <= pad.size[1] / 2 + 1e-6:
            hosts.append(pad)
    if not hosts:
        return
    pad = max(hosts, key=lambda p: p.size[0] * p.size[1])
    cx, cy = _to_pad_local(pad, centre)
    local = [_to_pad_local(pad, p) for p in loop]
    pad.paste = [
        *(pad.paste or []),
        m.Aperture(
            shape="polygon",
            size=(r(max(xs) - min(xs)), r(max(ys) - min(ys))),
            center=(r(cx), r(cy)),
            polygon=[(r(px - cx), r(py - cy)) for px, py in local],
        ),
    ]


# ---------------------------------------------------------------------------------------------------------------------
# kipr's raw footprint parse (renderers work from this)


def expand_layers(names) -> list[str]:
    """'*.Cu' / 'F&B.Cu' -> ['F.Cu', 'B.Cu'] (outer layers only), order kept, duplicates dropped."""
    out = []
    for n in names:
        n = str(n)
        if n.startswith("*.") or n.startswith("F&B."):
            suffix = n.split(".", 1)[1]
            out += [f"F.{suffix}", f"B.{suffix}"]
        else:
            out.append(n)
    return list(dict.fromkeys(out))


def _at(node: Node):
    at = node.child("at")
    if at is None:
        return 0.0, 0.0, 0.0
    v = at.nums()
    return (v[0] if v else 0.0), (v[1] if len(v) > 1 else 0.0), (v[2] if len(v) > 2 else 0.0)


def _kipr_stroke_width(node: Node, default=0.12):
    st = node.child("stroke")
    if st is not None:
        w = st.num("width", default)
        return w if w > 0 else default
    w = node.num("width", default)
    return w if w > 0 else default


def _kipr_filled(node: Node) -> bool:
    fill = node.child("fill")
    if fill is None:
        return False
    v = str(fill.arg(0, "") or fill.value("type", ""))
    return v in ("yes", "solid", "true")


def _layer(node: Node) -> str:
    return str(node.value("layer", "F.SilkS"))


def _isnum(v) -> bool:
    try:
        float(v)
        return True
    except (TypeError, ValueError):
        return False


class KicadFootprint:
    """A ``.kicad_mod`` as kipr's renderers see it: properties, texts, graphics, pads, zones, models and embedded
    files as plain dicts in the file's own frame (mm, y down). ``KicadFootprint(load(path))``."""

    def __init__(self, root: Node):
        self.root = root
        self.name = str(root.arg(0, ""))
        self.version = root.value("version")
        self.generator_version = root.value("generator_version")
        self.descr = str(root.value("descr", "") or "")
        self.tags = str(root.value("tags", "") or "")
        attr = root.child("attr")
        self.attr = [str(a) for a in attr.atoms()] if attr is not None else []
        self.properties: dict[str, str] = {}
        self.texts = []  # dicts: text, x, y, angle, layer, size, hidden, justify, mirror
        self.graphics = []  # dicts: kind, layer, width, filled, geometry
        self.pads = []
        self.zones = []
        self.models = []
        self.embedded: dict[str, dict] = {}
        self._parse()

    def _text(self, node: Node, txt: str, hidden: bool):
        x, y, a = _at(node)
        eff = node.child("effects")
        size = (1.0, 1.0)
        thick = 0.15
        justify = []
        bold = False
        if eff is not None:
            font = eff.child("font")
            if font is not None:
                s = font.nums("size") or [1.0, 1.0]
                size = (s[0], s[1] if len(s) > 1 else s[0])
                thick = font.num("thickness", 0.15)
                bold = font.flag("bold")
            j = eff.child("justify")
            if j is not None:
                justify = [str(v) for v in j.atoms()]
            hidden = hidden or eff.flag("hide")
        hidden = hidden or node.flag("hide")
        self.texts.append(
            dict(
                text=txt,
                x=x,
                y=y,
                angle=a,
                layer=_layer(node),
                size=size,
                thickness=thick,
                hidden=hidden,
                justify=justify,
                bold=bold,
                unlocked=node.flag("unlocked"),
            )
        )

    def _parse(self):
        for c in self.root.children():
            n = c.name
            if n == "property":
                key, val = str(c.arg(0, "")), str(c.arg(1, ""))
                self.properties[key] = val
                if c.child("layer") is not None:
                    self._text(c, val, hidden=c.flag("hide") or key not in ("Reference", "Value"))
            elif n == "fp_text":
                kind = str(c.arg(0, "user"))
                txt = str(c.arg(1, ""))
                if kind == "reference":
                    self.properties.setdefault("Reference", txt)
                elif kind == "value":
                    self.properties.setdefault("Value", txt)
                self._text(c, txt, hidden=c.flag("hide"))
            elif n in ("fp_line", "fp_arc", "fp_circle", "fp_rect", "fp_poly", "fp_curve"):
                g = self._graphic(c)
                if g:
                    self.graphics.append(g)
            elif n == "fp_text_box":
                self._text(c, str(c.arg(0, "")), hidden=False)
            elif n == "pad":
                self.pads.append(self._pad(c))
            elif n == "zone":
                self.zones.append(self._zone(c))
            elif n == "model":
                self.models.append(self._model(c))
            elif n == "embedded_files":
                for fl in c.children("file"):
                    self.embedded[str(fl.value("name", ""))] = {
                        "type": str(fl.value("type", "")),
                        "data": str(fl.value("data", "") or ""),
                        "checksum": str(fl.value("checksum", "") or ""),
                    }
        if self.descr and "descr" not in self.properties:
            self.properties.setdefault("Description", self.properties.get("Description") or "")

    def _graphic(self, c: Node):
        n = c.name
        g = dict(kind=n[3:], layer=_layer(c), width=_kipr_stroke_width(c), filled=_kipr_filled(c))
        if n == "fp_line":
            g["pts"] = [_xy(c.child("start")), _xy(c.child("end"))]
        elif n == "fp_rect":
            (x0, y0), (x1, y1) = _xy(c.child("start")), _xy(c.child("end"))
            g["pts"] = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
            g["closed"] = True
            g["kind"] = "poly"
        elif n == "fp_circle":
            cx, cy = _xy(c.child("center"))
            ex, ey = _xy(c.child("end"))
            g["center"], g["r"] = (cx, cy), math.hypot(ex - cx, ey - cy)
        elif n == "fp_arc":
            if c.child("mid") is not None:
                g["arc"] = (_xy(c.child("start")), _xy(c.child("mid")), _xy(c.child("end")))
            else:  # legacy: start = centre, end = arc start, angle
                g["arc"] = arc_from_center(_xy(c.child("start")), _xy(c.child("end")), c.num("angle", 90))
        elif n == "fp_poly":
            g["pts"] = [tuple(p.nums()[:2]) for p in (c.child("pts") or Node()).children("xy")]
            g["closed"] = True
            g["kind"] = "poly"
        elif n == "fp_curve":
            g["pts"] = [tuple(p.nums()[:2]) for p in (c.child("pts") or Node()).children("xy")]
            g["kind"] = "bezier"
        return g

    def _pad(self, c: Node):
        x, y, a = _at(c)
        size = c.nums("size") or [0.0, 0.0]
        if len(size) == 1:
            size = [size[0], size[0]]
        drill = None
        offset = (0.0, 0.0)
        d = c.child("drill")
        if d is not None:
            # (drill ... (offset x y)) is KiCad's pad *shape* offset: the copper sits at at+offset (in the
            # pad's rotated frame), the hole stays at `at`. SMD pads carry it too, with no drill size.
            offset = _xy(d.child("offset"))
            oval = "oval" in [str(v) for v in d.atoms()]
            nums = [float(v) for v in d.atoms() if _isnum(v)]
            if nums:
                dx = nums[0]
                dy = nums[1] if (oval and len(nums) > 1) else dx
                drill = dict(w=dx, h=dy, oval=oval)
        layers = expand_layers((c.child("layers") or Node()).atoms())
        prims = []
        pr = c.child("primitives")
        if pr is not None:
            for p in pr.children():
                g = dict(
                    kind=p.name[3:],
                    width=p.num("width", 0.0) or _kipr_stroke_width(p, 0.0),
                    filled=_kipr_filled(p) or p.name == "gr_poly",
                )
                if p.name == "gr_poly":
                    g["pts"] = [tuple(q.nums()[:2]) for q in (p.child("pts") or Node()).children("xy")]
                    g["kind"] = "poly"
                elif p.name == "gr_line":
                    g["pts"] = [_xy(p.child("start")), _xy(p.child("end"))]
                elif p.name == "gr_rect":
                    (x0, y0), (x1, y1) = _xy(p.child("start")), _xy(p.child("end"))
                    g["pts"] = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
                    g["kind"] = "poly"
                elif p.name == "gr_circle":
                    cx, cy = _xy(p.child("center"))
                    ex, ey = _xy(p.child("end"))
                    g["center"], g["r"] = (cx, cy), math.hypot(ex - cx, ey - cy)
                elif p.name == "gr_arc":
                    if p.child("mid") is not None:
                        g["arc"] = (_xy(p.child("start")), _xy(p.child("mid")), _xy(p.child("end")))
                    else:
                        g["arc"] = arc_from_center(_xy(p.child("start")), _xy(p.child("end")), p.num("angle", 90))
                else:
                    continue
                prims.append(g)
        opts = c.child("options")
        anchor = str(opts.value("anchor", "circle")) if opts is not None else "circle"
        chamfer = c.child("chamfer")
        return dict(
            number=str(c.arg(0, "")),
            type=str(c.arg(1, "")),
            shape=str(c.arg(2, "")),
            x=x,
            y=y,
            angle=a,
            w=size[0],
            h=size[1],
            offset=offset,
            drill=drill,
            layers=layers,
            rratio=c.num("roundrect_rratio", 0.25),
            delta=c.nums("rect_delta") or [0.0, 0.0],
            chamfer_ratio=c.num("chamfer_ratio", 0.0),
            chamfer=[str(v) for v in chamfer.atoms()] if chamfer is not None else [],
            primitives=prims,
            anchor=anchor,
            mask_margin=c.num("solder_mask_margin", 0.0),
            paste_margin=c.num("solder_paste_margin", 0.0),
            paste_ratio=c.num("solder_paste_margin_ratio", 0.0),
            line=c.line_start,
        )

    def _zone(self, c: Node):
        layers = []
        if c.child("layer") is not None:
            layers = [str(c.value("layer"))]
        if c.child("layers") is not None:
            layers = [str(v) for v in c.child("layers").atoms()]
        polys = []
        for p in c.children("polygon"):
            polys.append([tuple(q.nums()[:2]) for q in (p.child("pts") or Node()).children("xy")])
        return dict(
            layers=expand_layers(layers),
            polys=polys,
            keepout=c.child("keepout") is not None,
            name=str(c.value("name", "") or ""),
        )

    def _model(self, c: Node):
        def vec(name, default):
            n = c.child(name)
            if n is None:
                return list(default)
            xyz = n.child("xyz")
            v = xyz.nums() if xyz is not None else []
            return (v + list(default))[:3] if len(v) < 3 else v[:3]

        off = vec("offset", (0, 0, 0))
        if c.child("offset") is None and c.child("at") is not None:  # KiCad 5: inches
            off = [v * 25.4 for v in vec("at", (0, 0, 0))]

        def z(v):
            return [x + 0.0 for x in v]  # -0.0 -> 0.0

        return dict(
            path=str(c.arg(0, "")),
            offset=z(off),
            scale=z(vec("scale", (1, 1, 1))),
            rotate=z(vec("rotate", (0, 0, 0))),
            hidden=c.flag("hide"),
            opacity=c.num("opacity", 1.0),
        )

    def substitute(self, txt: str) -> str:
        """Expand ${REFERENCE}, ${VALUE}, ${FOOTPRINT_NAME} and property variables."""

        def rep(mt):
            key = mt.group(1)
            if key == "REFERENCE":
                return self.properties.get("Reference", "REF**")
            if key == "VALUE":
                return self.properties.get("Value", "")
            if key == "FOOTPRINT_NAME":
                return self.name
            return self.properties.get(key, self.properties.get(key.title(), mt.group(0)))

        return re.sub(r"\$\{([A-Za-z0-9_]+)\}", rep, txt)


def decode_embedded(data: str) -> bytes:
    """KiCad 10 embedded file payload: |base64(zstd(content))| (zstd needs the ``zstandard`` package)."""
    import base64

    raw = base64.b64decode(data.strip("|"))
    if raw[:4] == b"\x28\xb5\x2f\xfd":
        import zstandard

        return zstandard.ZstdDecompressor().decompressobj().decompress(raw)
    return raw
