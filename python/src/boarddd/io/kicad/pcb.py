"""KiCad boards (``.kicad_pcb``, KiCad 5 to 10) -> :class:`boarddd.model.Board`, without pcbnew.

``read_kicad_pcb(path)`` reads what the board file itself holds: outline (Edge.Cuts, footprint cut-outs
included), stackup (Er/Df, prepreg/core, sublayers, locked thickness, impedance control), origins,
footprints (library form) and components, drills (pad holes and vias), nets, and with the project file next
to it (``.kicad_pro``) the net classes and their impedance targets (``project.py``). Layers stay empty: the
drawable layers are fab outputs (Gerbers, drill files), which ``boarddd.io`` reads from a fab package.

Frames (docs/model.md): KiCad stores page coordinates, y down; the board frame negates y. Footprints are
stored in library form (see ``footprint.py``).

``read_kicad_copper(path)`` reads the routed copper into a ``boarddd/copper@1`` document (tracks, arcs, vias, zone
fills, keepouts, pads as copper, net ties; see ``boarddd.copper`` and docs/copper.md).

``load(text)`` is the other half: kipr's item view of a board for diffing (tracks, vias, zones, graphics with
canonical keys and bounding boxes, KiCad frame).

Sources (boarddd phase F2): the model half from ``fixtures/royalblue54L_feather/make_board.py`` (boarddd
``2599fed``) and magpie ``pcb/kicad_pcb.py`` ``read_board`` (internal ``claud/magpie`` ``3a0374d3``); the
item view from kipr ``kipr/project/pcb.py`` + ``geom.py`` (kipr main ``f632b3f``); the stackup grammar from
KiCad's ``board_stackup.cpp`` (see the t0272 stackup notes).
"""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass, field
from pathlib import Path

from ... import __version__
from ... import copper as cu
from ... import model as m
from .footprint import footprint_angle, graphic_points, read_footprint, read_pad
from .geom import arc_from_center, arc_through, pad_offset, pad_outline, r, ring_points, rotate, signed_area
from .project import KicadProject, assign_nets, read_kicad_pro
from .sexpr import Atom, Node, dumps, parse

__all__ = [
    "read_kicad_pcb",
    "read_kicad_copper",
    "read_stackup",
    "read_nets",
    "load",
    "PcbFile",
    "PcbFootprint",
    "Item",
    "Zone",
]


def to_board(p) -> tuple[float, float]:
    """KiCad page point (y down) -> board frame (y up), 1 nm rounding."""
    return (r(p[0]), r(-p[1]))


def _xy(n: Node | None, d=(0.0, 0.0)) -> tuple[float, float]:
    if n is None:
        return d
    v = n.nums()
    return (v[0], v[1]) if len(v) >= 2 else d


def _sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


# ---------------------------------------------------------------------------------------------------------------------
# entry point


def read_kicad_pcb(
    source: str | Path,
    project: str | Path | KicadProject | None = None,
    *,
    name: str | None = None,
    root: str | Path | None = None,
) -> m.Board:
    """Read a ``.kicad_pcb`` (a path, or the file's text).

    ``project``: the ``.kicad_pro`` (path or :class:`KicadProject`); by default the one next to the board.
    ``name``: the board name (default: the file stem). ``root``: the folder ``source.files`` paths are relative
    to (default: the board's folder).
    """
    path = None if isinstance(source, str) and source.lstrip().startswith("(") else Path(source)
    text = path.read_text(encoding="utf-8") if path else str(source)
    tree = parse(text)
    if tree.name != "kicad_pcb":
        raise ValueError("not a .kicad_pcb file")
    if project is None and path is not None and path.with_suffix(".kicad_pro").exists():
        project = path.with_suffix(".kicad_pro")
    pro_path = None
    if project is not None and not isinstance(project, KicadProject):
        pro_path = Path(project)
        project = read_kicad_pro(pro_path)

    warnings: list[str] = []
    files = []
    if path is not None:
        base = Path(root) if root is not None else path.parent
        for p, role in ((path, "pcb"), (pro_path, "other")):
            if p is not None:
                files.append(m.SourceFile(path=_rel(p, base), role=role, sha256=_sha256(p)))
    setup = tree.child("setup")
    tb = tree.child("title_block")
    footprints, components = read_components(tree, warnings)
    nets = read_nets(tree)
    net_classes: list[m.NetClass] = []
    if project is not None:
        net_classes = assign_nets(nets, project, warnings)
    else:
        net_classes = _legacy_net_classes(tree, nets)
    gv = tree.value("generator_version")
    return m.Board(
        name=name or (path.stem if path else "board"),
        revision=str(tb.value("rev")) if tb is not None and tb.value("rev") is not None else None,
        source=m.Source(
            kind="kicad_pcb",
            files=files,
            generator=f"{tree.value('generator', 'pcbnew')} {gv}" if gv else str(tree.value("generator", "pcbnew")),
            reader=f"boarddd {__version__} io.kicad",
            created=str(tb.value("date")) if tb is not None and tb.value("date") is not None else None,
        ),
        origin=m.Origin(
            aux=to_board(_xy(setup.child("aux_axis_origin")))
            if setup is not None and setup.child("aux_axis_origin")
            else None,
            grid=to_board(_xy(setup.child("grid_origin")))
            if setup is not None and setup.child("grid_origin")
            else None,
        ),
        outline=read_outline(tree, warnings),
        stackup=read_stackup(tree),
        drills=read_drills(tree),
        footprints=footprints,
        components=components,
        nets=nets,
        net_classes=net_classes,
        warnings=warnings,
    )


def _rel(p: Path, base: Path) -> str:
    try:
        return p.resolve().relative_to(base.resolve()).as_posix()
    except ValueError:
        return p.name


# ---------------------------------------------------------------------------------------------------------------------
# outline


def _footprint_to_page(fp: Node):
    x0, y0 = _xy(fp.child("at"))
    a = footprint_angle(fp)

    def tf(p):
        dx, dy = rotate(p[0], p[1], a)
        return (x0 + dx, y0 + dy)

    return tf


def _edge_shapes(tree: Node):
    """Edge.Cuts drawings in page coordinates: (open polylines, closed loops)."""
    opened, closed = [], []

    def add(n: Node, tf=None):
        if n.name in ("gr_line", "fp_line"):
            pts = [_xy(n.child("start")), _xy(n.child("end"))]
        elif n.name in ("gr_arc", "fp_arc"):
            g = graphic_points(n)
            s = _xy(n.child("start"))
            if n.child("mid") is not None:
                # ~5 degrees per segment (72 per turn), the outline's own resolution
                pts = arc_through(s, _xy(n.child("mid")), _xy(n.child("end")), segments=72)
            else:
                pts = g["pts"]
        elif n.name in ("gr_circle", "fp_circle"):
            (cx, cy), (ex, ey) = _xy(n.child("center")), _xy(n.child("end"))
            closed.append([tf(p) if tf else p for p in ring_points(cx, cy, math.hypot(ex - cx, ey - cy), 72)])
            return
        elif n.name in ("gr_rect", "fp_rect", "gr_poly", "fp_poly"):
            g = graphic_points(n)
            closed.append([tf(p) if tf else p for p in g["pts"]])
            return
        else:
            return
        opened.append([tf(p) if tf else p for p in pts])

    for c in tree.children():
        if c.name.startswith("gr_") and str(c.value("layer", "")) == "Edge.Cuts":
            add(c)
        elif c.name in ("footprint", "module"):
            tf = _footprint_to_page(c)
            for g in c.children():
                if g.name.startswith("fp_") and str(g.value("layer", "")) == "Edge.Cuts":
                    add(g, tf)
    return opened, closed


def _chain(segs: list[list], tol: float = 0.01) -> tuple[list[list], int]:
    """Join open polylines end to end into closed loops (ends within ``tol`` mm meet: designs leave micron gaps,
    e.g. 3.5 um in KiCad's NFC antenna demo); returns (loops, number of segments left open)."""
    loops = []
    segs = [list(s) for s in segs]
    left = 0
    while segs:
        loop = segs.pop(0)
        while math.dist(loop[0], loop[-1]) > tol:
            end = loop[-1]
            i = next(
                (i for i, sg in enumerate(segs) if min(math.dist(sg[0], end), math.dist(sg[-1], end)) <= tol), None
            )
            if i is None:
                break
            sg = segs.pop(i)
            loop += sg[1:] if math.dist(sg[0], end) <= tol else list(reversed(sg))[1:]
        if math.dist(loop[0], loop[-1]) <= tol and len(loop) > 3:
            loops.append(loop[:-1])
        else:
            left += 1
    return loops, left


def read_outline(tree: Node, warnings: list[str] | None = None) -> m.Outline | None:
    """The largest closed Edge.Cuts loop is the board (counter-clockwise), the others cut-outs (clockwise)."""
    warnings = warnings if warnings is not None else []
    opened, closed = _edge_shapes(tree)
    loops, left = _chain(opened)
    loops = [[to_board(p) for p in lp] for lp in loops + closed if len(lp) >= 3]
    if left:
        warnings.append(f"Edge.Cuts: {left} drawing(s) do not close into a loop and were ignored")
    if not loops:
        pts = [to_board(p) for s in opened for p in s]
        if not pts:
            return None
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        warnings.append("Edge.Cuts has no closed outline: using the bounding box of its drawings")
        return m.Outline(
            board=[(min(xs), min(ys)), (max(xs), min(ys)), (max(xs), max(ys)), (min(xs), max(ys))], approximate=True
        )
    loops.sort(key=lambda lp: -abs(signed_area(lp)))
    board, *cutouts = loops
    if signed_area(board) < 0:
        board.reverse()
    for c in cutouts:
        if signed_area(c) > 0:
            c.reverse()
    return m.Outline(board=board, cutouts=cutouts)


# ---------------------------------------------------------------------------------------------------------------------
# stackup

STACKUP_KIND = {
    "Top Silk Screen": "silk",
    "Bottom Silk Screen": "silk",
    "Top Solder Paste": "paste",
    "Bottom Solder Paste": "paste",
    "Top Solder Mask": "mask",
    "Bottom Solder Mask": "mask",
    "copper": "copper",
    "core": "dielectric",
    "prepreg": "dielectric",
}
STACKUP_LAYER = {
    "F.SilkS": "F.Silkscreen",
    "B.SilkS": "B.Silkscreen",
    "F.Paste": "F.Paste",
    "B.Paste": "B.Paste",
    "F.Mask": "F.Mask",
    "B.Mask": "B.Mask",
}


def _num_or_none(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _stackup_parts(lay: Node) -> list[dict]:
    """A (layer ...) entry split at its ``addsublayer`` atoms: one dict of values per sublayer."""
    parts: list[dict] = [{}]
    for c in lay[2:]:
        if isinstance(c, Atom) and c == "addsublayer":
            parts.append({})
        elif isinstance(c, Node) and c.name in ("thickness", "material", "color", "epsilon_r", "loss_tangent"):
            parts[-1][c.name] = c.arg(0)
            if c.name == "thickness" and "locked" in [str(a) for a in c.atoms()[1:]]:
                parts[-1]["locked"] = True
    return parts


def read_stackup(tree: Node) -> m.Stackup:
    """``(general (thickness))`` and ``(setup (stackup ...))`` as a model Stackup."""
    setup = tree.child("setup")
    st = setup.child("stackup") if setup is not None else None
    gen = tree.child("general")
    thickness = gen.num("thickness", None) if gen is not None else None
    layers = []
    for lay in st.children("layer") if st is not None else []:
        name = str(lay.arg(0, ""))
        ltype = str(lay.value("type", ""))
        kind = STACKUP_KIND.get(ltype, "copper" if name.endswith(".Cu") else "dielectric" if ltype else "other")
        side = "top" if name.startswith("F.") else "bottom" if name.startswith("B.") else "inner"
        parts = _stackup_parts(lay)
        first = parts[0]
        sl = m.StackupLayer(
            name=name,
            kind=kind,
            side=side,
            thickness=r(float(first["thickness"])) if _num_or_none(first.get("thickness")) else None,
            material=str(first["material"]) if first.get("material") else None,
            color=str(first["color"]) if first.get("color") else None,
            epsilon_r=_num_or_none(first.get("epsilon_r")) or None,
            loss_tangent=_num_or_none(first.get("loss_tangent")) if first.get("loss_tangent") else None,
            layer=name if kind == "copper" else STACKUP_LAYER.get(name),
            dielectric=ltype if ltype in ("prepreg", "core") else None,
            locked=True if any(p.get("locked") for p in parts) else None,
            frequency=lay.num("spec_frequency", None),
            dielectric_model=str(lay.value("dielectric_model")) if lay.value("dielectric_model") else None,
        )
        if len(parts) > 1:
            _combine_sublayers(sl, parts)
        layers.append(sl)
    color = {la.name: la.color for la in layers}
    copper = sum(la.kind == "copper" for la in layers) or _copper_count(tree)
    constraints = st.value("dielectric_constraints") if st is not None else None
    return m.Stackup(
        thickness=thickness,
        copper_layers=copper or None,
        finish=str(st.value("copper_finish")) if st is not None and st.value("copper_finish") is not None else None,
        mask_color=m.SideValues(top=color.get("F.Mask"), bottom=color.get("B.Mask")),
        silk_color=m.SideValues(top=color.get("F.SilkS"), bottom=color.get("B.SilkS")),
        layers=layers,
        impedance_controlled=None if constraints is None else str(constraints) == "yes",
    )


def _combine_sublayers(sl: m.StackupLayer, parts: list[dict]) -> None:
    """The layer's own values become the whole: total thickness, series epsilon_r, thickness-weighted loss."""
    subs = [
        m.StackupSublayer(
            thickness=r(float(p["thickness"])) if _num_or_none(p.get("thickness")) else None,
            material=str(p["material"]) if p.get("material") else None,
            color=str(p["color"]) if p.get("color") else None,
            epsilon_r=_num_or_none(p.get("epsilon_r")) or None,
            loss_tangent=_num_or_none(p.get("loss_tangent")) if p.get("loss_tangent") else None,
        )
        for p in parts
    ]
    sl.sublayers = subs
    ts = [s.thickness for s in subs]
    if all(t is not None for t in ts):
        total = sum(ts)
        sl.thickness = r(total)
        if total > 0 and all(s.epsilon_r for s in subs):
            sl.epsilon_r = r(total / sum(s.thickness / s.epsilon_r for s in subs))
        if total > 0 and all(s.loss_tangent is not None for s in subs):
            sl.loss_tangent = r(sum(s.thickness * s.loss_tangent for s in subs) / total)
    materials = list(dict.fromkeys(s.material for s in subs if s.material))
    sl.material = " + ".join(materials) if materials else None


def _copper_count(tree: Node) -> int:
    layers = tree.child("layers")
    return sum(1 for c in (layers.children() if layers is not None else []) if str(c.arg(0, "")).endswith(".Cu"))


# ---------------------------------------------------------------------------------------------------------------------
# footprints and components


def _natural(ref: str):
    return (re.sub(r"\d+", "", ref), int(re.sub(r"\D", "", ref) or 0), ref)


def _properties(fp: Node) -> dict[str, str]:
    props = {str(p.arg(0)): str(p.arg(1, "")) for p in fp.children("property") if p.arg(0) is not None}
    for t in fp.children("fp_text"):  # KiCad 5-7
        kind = str(t.arg(0, ""))
        if kind in ("reference", "value"):
            props.setdefault(kind.capitalize(), str(t.arg(1, "")))
    return props


def _model_ref(md: Node) -> m.ModelRef:
    def vec(k, d):
        c = md.child(k)
        v = c.child("xyz") if c is not None else None
        if v is None:
            return d
        n = (v.nums() + list(d))[:3]
        return (r(n[0]), r(n[1]), r(n[2]))

    offset = vec("offset", (0.0, 0.0, 0.0))
    if md.child("offset") is None and md.child("at") is not None:  # KiCad 5: inches
        offset = tuple(r(v * 25.4) for v in vec("at", (0.0, 0.0, 0.0)))
    return m.ModelRef(
        path=str(md.arg(0, "")),
        offset=offset,
        rotate=vec("rotate", (0.0, 0.0, 0.0)),
        scale=vec("scale", (1.0, 1.0, 1.0)),
        hide=md.flag("hide"),
    )


def _same_footprint(a: m.Footprint, b: m.Footprint) -> bool:
    """Same pads and attributes, and the same drawings in any order or direction (a flipped instance lists its
    lines in another order)."""

    def drawings(fp):
        return sorted(
            (g.layer, g.kind, g.width, g.closed, g.filled, tuple(sorted((round(x, 4), round(y, 4)) for x, y in g.pts)))
            for g in fp.graphics
        )

    return m.to_dict(a.pads) == m.to_dict(b.pads) and a.attr == b.attr and drawings(a) == drawings(b)


def read_components(tree: Node, warnings: list[str] | None = None) -> tuple[dict[str, m.Footprint], list[m.Component]]:
    """Footprint definitions (library form, keyed by library id) and the placed components.

    A definition comes from the first top-side instance of a library id, else the first bottom one. An instance
    that differs from it (a footprint edited on the board) gets its own definition, keyed 'lib_id#ref'.
    """
    warnings = warnings if warnings is not None else []
    defs: dict[str, m.Footprint] = {}
    comps: list[m.Component] = []
    nodes = [c for c in tree.children() if c.name in ("footprint", "module")]
    # top-side instances first, so the definition is the library (unflipped) one when there is one
    order = sorted(range(len(nodes)), key=lambda i: str(nodes[i].value("layer", "F.Cu")).startswith("B."))
    # logos, graphics and unannotated parts share references ('', 'G***', 'REF**'): number the repeats
    refs, seen, renamed = [], {}, []
    for fp in nodes:
        ref = _properties(fp).get("Reference", "")
        n = seen[ref] = seen.get(ref, 0) + 1
        refs.append(f"{ref}#{n}" if n > 1 else ref)
        if n > 1:
            renamed.append(ref)
    if renamed:
        warnings.append(
            f"{len(renamed)} component(s) repeat a reference and got a '#n' suffix (the original is in "
            f"attributes.Reference): {', '.join(sorted(set(renamed), key=_natural))}"
        )
    key_of: dict[int, str] = {}
    edited = []
    for i in order:
        fp = nodes[i]
        name = str(fp.arg(0, ""))
        lib = read_footprint(fp, name=name, board=True)
        if name not in defs:
            defs[name] = lib
            key_of[i] = name
        elif _same_footprint(lib, defs[name]):
            key_of[i] = name
        else:
            key = f"{name}#{refs[i]}"
            lib.name = key
            defs[key] = lib
            key_of[i] = key
            edited.append(refs[i])
    if edited:
        warnings.append(
            f"{len(edited)} footprint(s) differ from the first instance of their library id and got their own "
            f"definition ('lib_id#ref'): {', '.join(sorted(edited, key=_natural))}"
        )
    for i, fp in enumerate(nodes):
        props = _properties(fp)
        at = fp.child("at")
        av = at.nums() if at is not None else []
        attr_node = fp.child("attr")
        attr = [str(a) for a in attr_node.atoms()] if attr_node is not None else []
        x, y = to_board(av[:2] if len(av) >= 2 else (0.0, 0.0))
        mpn = props.get("MPN") or props.get("Manufacturer Part Number")
        attributes = {k: v for k, v in sorted(props.items()) if k not in ("Reference", "Value", "Footprint") and v}
        if refs[i] != props.get("Reference", ""):
            attributes["Reference"] = props.get("Reference", "")
        comps.append(
            m.Component(
                ref=refs[i],
                side="bottom" if str(fp.value("layer", "F.Cu")).startswith("B.") else "top",
                x=x,
                y=y,
                rotation=r(av[2]) if len(av) > 2 else 0.0,
                value=props.get("Value"),
                footprint=key_of[i],
                mount="smd" if "smd" in attr else "tht" if "through_hole" in attr else "other",
                populate=not ("dnp" in attr or fp.flag("dnp")),
                in_bom="exclude_from_bom" not in attr,
                in_pos="exclude_from_pos_files" not in attr,
                mpn=[m.PartNumber(mpn=mpn, manufacturer=props.get("Manufacturer"))] if mpn else [],
                models=[_model_ref(md) for md in fp.children("model")],
                attributes=attributes,
            )
        )
    return dict(sorted(defs.items())), sorted(comps, key=lambda c: _natural(c.ref))


# ---------------------------------------------------------------------------------------------------------------------
# drills


def read_drills(tree: Node) -> list[m.Drill]:
    """Pad holes (in footprint order) and vias, board frame. Oval holes are slots end to end along their long
    axis, as the drill files write them."""
    out: list[m.Drill] = []
    for fp in tree.children():
        if fp.name not in ("footprint", "module"):
            continue
        tf = _footprint_to_page(fp)
        for pad in fp.children("pad"):
            ptype = str(pad.arg(1, ""))
            d = pad.child("drill")
            if ptype not in ("thru_hole", "np_thru_hole") or d is None:
                continue
            dv = [str(v) for v in d.atoms()]
            oval = "oval" in dv
            nums = [float(v) for v in dv if v != "oval" and _num_or_none(v) is not None]
            if not nums:
                continue
            w = nums[0]
            h = nums[1] if oval and len(nums) > 1 else w
            av = pad.child("at").nums() + [0.0, 0.0, 0.0]
            cx, cy = tf(av[:2])
            drill = m.Drill(x=0.0, y=0.0, diameter=r(min(w, h)), plated=ptype == "thru_hole", function="component")
            if oval and abs(w - h) > 1e-9:
                half = abs(w - h) / 2
                # the pad's angle in the board file is absolute (KiCad y down, CCW as seen)
                dx, dy = rotate(half, 0.0, av[2]) if w > h else rotate(0.0, half, av[2])
                drill.x, drill.y = to_board((cx - dx, cy - dy))
                drill.x2, drill.y2 = to_board((cx + dx, cy + dy))
            else:
                drill.x, drill.y = to_board((cx, cy))
            out.append(drill)
    for v in tree.children("via"):
        x, y = to_board(_xy(v.child("at")))
        out.append(m.Drill(x=x, y=y, diameter=r(v.num("drill")), plated=True, function="via"))
    return out


# ---------------------------------------------------------------------------------------------------------------------
# nets

_PAIR = re.compile(r"^(.*?)(\+|-|_P|_N|P|N)$")
_PARTNER = {"+": "-", "-": "+", "_P": "_N", "_N": "_P", "P": "N", "N": "P"}


def _net_names(tree: Node) -> list[str]:
    """Net names in file order: the (net N "name") table (KiCad <= 9), else every name items use (KiCad 10)."""
    table = [str(n.arg(1)) for n in tree.children("net") if len(n.atoms()) >= 2]
    if table:
        return [n for n in table if n]
    seen: dict[str, None] = {}
    for node in tree.walk():
        if node.name == "net" and len(node.atoms()) == 1 and not isinstance(node.arg(0), Atom):
            seen.setdefault(str(node.arg(0)), None)
    return [n for n in seen if n]


def read_nets(tree: Node) -> list[m.Net]:
    """Nets with their differential-pair partners (KiCad's rule: same name but a '+'/'-' or 'P'/'N' suffix)."""
    names = _net_names(tree)
    have = set(names)
    nets = []
    for n in names:
        pair = None
        mt = _PAIR.match(n)
        if mt and mt.group(1):
            partner = mt.group(1) + _PARTNER[mt.group(2)]
            pair = partner if partner in have else None
        nets.append(m.Net(name=n, pair=pair))
    return nets


def _legacy_net_classes(tree: Node, nets: list[m.Net]) -> list[m.NetClass]:
    """KiCad 5 boards keep the classes in the board file: (net_class name "descr" (clearance) (add_net ...))."""
    from .project import impedance_from_name

    out = []
    by_name = {n.name: n for n in nets}
    for nc in tree.children("net_class"):
        name = str(nc.arg(0, ""))
        members = [str(a.arg(0)) for a in nc.children("add_net")]
        for n in members:
            if n in by_name:
                by_name[n].net_class = name
        out.append(
            m.NetClass(
                name=name,
                nets=members,
                track_width=nc.num("trace_width", None),
                clearance=nc.num("clearance", None),
                diff_pair_width=nc.num("diff_pair_width", None),
                diff_pair_gap=nc.num("diff_pair_gap", None),
                via_diameter=nc.num("via_dia", None),
                via_drill=nc.num("via_drill", None),
                impedance=impedance_from_name(name),
            )
        )
    if out:
        for n in nets:
            n.net_class = n.net_class or "Default"
    return out


# ---------------------------------------------------------------------------------------------------------------------
# copper (boarddd/copper@1): tracks, arcs, vias, zone fills, keepouts, pads, net ties

_COPPER_TYPES = ("signal", "power", "mixed", "jumper")
_KEEPOUT_RULES = {"tracks": "tracks", "vias": "vias", "pads": "pads", "copperpour": "pours", "footprints": "footprints"}


def read_kicad_copper(
    source: str | Path,
    *,
    name: str | None = None,
    root: str | Path | None = None,
    check_fills: bool = True,
    segments: int = 8,
) -> cu.Copper:
    """The copper of a ``.kicad_pcb`` (a path, or the file's text) as a ``boarddd/copper@1`` document.

    Zone fills are taken as the file stores them (KiCad saves its last fill): zones with no fill get
    ``filled: false`` and a warning, and with ``check_fills`` a fill holding copper of another net is marked
    ``stale`` (with a warning). ``segments``: points per quarter turn when pad corners and circles are flattened.
    """
    path = None if isinstance(source, str) and source.lstrip().startswith("(") else Path(source)
    text = path.read_text(encoding="utf-8") if path else str(source)
    tree = parse(text)
    if tree.name != "kicad_pcb":
        raise ValueError("not a .kicad_pcb file")
    files = []
    if path is not None:
        base = Path(root) if root is not None else path.parent
        files.append(m.SourceFile(path=_rel(path, base), role="pcb", sha256=_sha256(path)))
    warnings: list[str] = []
    copper = copper_layers(tree)
    nets = {str(n.arg(0)): str(n.arg(1)) for n in tree.children("net") if len(n.atoms()) >= 2}
    doc = cu.Copper(
        board=name or (path.stem if path else "board"),
        source=m.Source(
            kind="kicad_pcb",
            files=files,
            generator=_generator(tree),
            reader=f"boarddd {__version__} io.kicad copper",
        ),
        layers=copper,
        warnings=warnings,
    )
    pruned: list[cu.Via] = []  # 'remove unused layers' vias: their rings depend on what connects to them
    for n in tree.children():
        if n.name in ("segment", "arc"):
            doc.tracks.append(_copper_track(n, nets))
        elif n.name == "via":
            doc.vias.append(_copper_via(n, nets, copper))
            if n.flag("remove_unused_layers"):
                pruned.append(doc.vias[-1])
    refs: dict[int, str] = {}
    for fp in tree.children():
        if fp.name in ("footprint", "module"):
            ref = _fp_ref(fp)
            refs[id(fp)] = ref
            doc.pads += _copper_pads(fp, ref, nets, copper, warnings, segments)
            tie = _net_tie(fp, ref, nets)
            if tie is not None:
                doc.net_ties.append(tie)
    zone_nodes = [(z, None) for z in tree.children("zone")]
    for fp in tree.children():
        if fp.name in ("footprint", "module"):
            zone_nodes += [(z, refs[id(fp)]) for z in fp.children("zone")]
    shapes, strokes = _copper_shapes(tree, copper, nets, warnings)
    doc.zones += shapes
    doc.tracks += strokes
    unfilled = 0
    for z, ref in zone_nodes:
        layers = _zone_layers(z, copper)
        if not layers:
            continue
        if z.child("keepout") is not None:
            doc.keepouts.append(_keepout(z, layers, ref))
            continue
        zones = _copper_zones(z, layers, nets)
        unfilled += sum(not zz.filled for zz in zones)
        doc.zones += zones
    if unfilled:
        warnings.append(
            f"{unfilled} zone layer(s) have no fill (never filled, or the fill was not saved): fill the zones "
            "(KiCad: Edit > Fill All Zones) for complete copper"
        )
    _via_pad_layers(doc, pruned)
    doc.nets = _used_nets(doc, [n.name for n in read_nets(tree)])
    doc.planes = cu.planes(doc.zones, cu.outline_area(read_outline(tree, [])), copper)
    if check_fills:
        cu.check_fills(doc)
    return doc


def _generator(tree: Node) -> str:
    gv = tree.value("generator_version")
    return f"{tree.value('generator', 'pcbnew')} {gv}" if gv else str(tree.value("generator", "pcbnew"))


def copper_layers(tree: Node) -> list[str]:
    """Copper layer names from the board's (layers ...) table, top to bottom."""
    names = []
    lnode = tree.child("layers")
    for c in lnode.children() if lnode is not None else []:
        a = c.atoms()
        if len(a) >= 2 and str(a[0]).endswith(".Cu") and str(a[1]) in _COPPER_TYPES:
            names.append(str(a[0]))
    return stack_order(names)


def _fp_ref(fp: Node) -> str:
    for p in fp.children("property"):
        if str(p.arg(0, "")) == "Reference":
            return str(p.arg(1, ""))
    for t in fp.children("fp_text"):
        if str(t.arg(0, "")) == "reference":
            return str(t.arg(1, ""))
    return ""


def _copper_track(n: Node, nets) -> cu.Track:
    mid = n.child("mid")
    return cu.Track(
        layer=str(n.value("layer", "")),
        net=net_of(n, nets),
        width=r(n.num("width")),
        start=to_board(_xy(n.child("start"))),
        end=to_board(_xy(n.child("end"))),
        mid=to_board(_xy(mid)) if n.name == "arc" and mid is not None else None,
        id=str(n.value("uuid", n.value("tstamp", ""))) or None,
    )


def _span_type(span: tuple[str, str], copper: list[str]) -> str:
    if span == (copper[0], copper[-1]):
        return "through"
    return "blind" if copper[0] in span or copper[-1] in span else "buried"


def _copper_via(n: Node, nets, copper: list[str]) -> cu.Via:
    order = {c: i for i, c in enumerate(copper)}
    named = [str(a) for a in (n.child("layers").atoms() if n.child("layers") is not None else [])]
    named = [c for c in named if c in order] or [copper[0], copper[-1]]
    a, b = sorted(named, key=order.__getitem__)[0], sorted(named, key=order.__getitem__)[-1]
    span = (a, b)
    flags = {str(x) for x in n.atoms()}
    kind = "micro" if "micro" in flags else _span_type(span, copper)
    size = r(n.num("size"))
    via = cu.Via(
        at=to_board(_xy(n.child("at"))),
        net=net_of(n, nets),
        diameter=size,
        drill=r(n.num("drill")),
        span=span,
        type=kind,
        id=str(n.value("uuid", n.value("tstamp", ""))) or None,
    )
    ps = n.child("padstack")
    if ps is not None and str(ps.value("mode", "normal")) != "normal":
        inside = copper[order[a] : order[b] + 1]
        sizes = {c: size for c in inside}
        for lay in ps.children("layer"):
            lname = str(lay.arg(0, ""))
            d = r((lay.nums("size") or [size])[0])
            targets = [c for c in inside if c not in (copper[0], copper[-1])] if lname == "Inner" else [lname]
            for c in targets:
                if c in sizes:
                    sizes[c] = d
        if len(set(sizes.values())) > 1:
            via.diameter = max(sizes.values())
            via.padstack = [cu.ViaPad(layer=c, diameter=d) for c, d in sizes.items()]
    if n.flag("remove_unused_layers"):
        via.pad_layers = [a, b] if n.flag("keep_end_layers") else []  # completed by _via_pad_layers
    return via


def _via_pad_layers(doc: cu.Copper, vias: list[cu.Via]) -> None:
    """Vias with 'remove unused layers': a ring where a same-net track ends on the via or a same-net fill covers it
    (KiCad's rule, without pads), plus the end layers when kept."""
    order = {c: i for i, c in enumerate(doc.layers)}
    for v in vias:
        keep = set(v.pad_layers or [])
        rad = v.diameter / 2
        for t in doc.tracks:
            if t.net == v.net and any(math.dist(p, v.at) <= rad for p in (t.start, t.end)):
                keep.add(t.layer)
        for z in doc.zones:
            if z.net == v.net and any(cu.in_fill(v.at, p) for p in z.fill):
                keep.add(z.layer)
        lo, hi = order[v.span[0]], order[v.span[1]]
        v.pad_layers = [c for c in doc.layers[lo : hi + 1] if c in keep]


def _pad_copper_layers(names: list[str], copper: list[str]) -> list[str]:
    out: set[str] = set()
    for nm in names:
        if nm in ("*.Cu", "F&B.Cu"):
            out.update(copper if nm == "*.Cu" else (copper[0], copper[-1]))
        elif nm in copper:
            out.add(nm)
    return [c for c in copper if c in out]


def _copper_pads(fp: Node, ref: str, nets, copper, warnings, segments) -> list[cu.CopperPad]:
    x0, y0 = _xy(fp.child("at"))
    fa = footprint_angle(fp)
    out = []
    for n in fp.children("pad"):
        layers = _pad_copper_layers([str(a) for a in (n.child("layers") or Node()).atoms()], copper)
        pad = read_pad(n)  # board form: (at) footprint-local, angle absolute; geometry as stored (flipped)
        if pad.type == "np_thru_hole" and pad.drill is not None and min(pad.size) <= min(pad.drill.size) + 1e-9:
            layers = []  # a bare hole: no copper
        if not layers:
            continue
        if n.child("padstack") is not None and str(n.child("padstack").value("mode", "normal")) != "normal":
            warnings.append(f"pad {ref}.{pad.number}: per-layer padstack: the front shape is used on every layer")
        dx, dy = rotate(pad.at[0], pad.at[1], fa)
        cx, cy = x0 + dx, y0 + dy
        outer, extra = pad_outline(pad, segments)  # pad-local, shape offset applied
        loops = []
        for ring in (outer, *extra):
            pts = [to_board((cx + q[0], cy + q[1])) for q in (rotate(p[0], p[1], pad.at[2]) for p in ring)]
            loops.append(pts if signed_area(pts) > 0 else pts[::-1])
        ox, oy = rotate(*pad_offset(pad), pad.at[2])
        out.append(
            cu.CopperPad(
                ref=ref,
                number=pad.number,
                net=net_of(n, nets),
                layers=layers,
                at=to_board((cx + ox, cy + oy)),
                rotation=r(pad.at[2] % 360),
                shape=pad.shape,
                size=pad.size,
                polygons=loops,
                drill=r(min(pad.drill.size)) if pad.drill is not None else None,
                type=pad.type,
            )
        )
    return out


def _net_tie(fp: Node, ref: str, nets) -> cu.NetTie | None:
    g = fp.child("net_tie_pad_groups")
    if g is None:
        return None
    groups = [[x for x in re.split(r"[\s,]+", str(a)) if x] for a in g.atoms()]
    groups = [grp for grp in groups if grp]
    pad_net = {str(p.arg(0, "")): net_of(p, nets) for p in fp.children("pad")}
    tied = [pad_net.get(num, "") for grp in groups for num in grp]
    return cu.NetTie(ref=ref, groups=groups, nets=[n for n in dict.fromkeys(tied) if n])


def _zone_layers(z: Node, copper: list[str]) -> list[str]:
    return _pad_copper_layers(item_layers(z), copper)


def _zone_outline(z: Node) -> list[tuple[float, float]]:
    pts = []
    for p in z.children("polygon"):
        pts.extend(shape_points(p))
    return [to_board(p) for p in pts]


def _keepout(z: Node, layers: list[str], ref: str | None) -> cu.Keepout:
    k = z.child("keepout")
    rules = cu.KeepoutRules(**{v: str(k.value(key, "allowed")) == "not_allowed" for key, v in _KEEPOUT_RULES.items()})
    outline = _zone_outline(z)
    if signed_area(outline) < 0:
        outline.reverse()
    return cu.Keepout(
        layers=layers,
        outline=outline,
        rules=rules,
        name=str(z.value("name")) if z.value("name") is not None else None,
        ref=ref,
        id=str(z.value("uuid", z.value("tstamp", ""))) or None,
    )


def _copper_zones(z: Node, layers: list[str], nets) -> list[cu.Zone]:
    name = z.value("name")
    attr = z.child("attr")
    teardrop = (attr is not None and attr.child("teardrop") is not None) or str(name or "").startswith("$teardrop")
    outline = _zone_outline(z)
    if len(outline) >= 3 and signed_area(outline) < 0:
        outline.reverse()
    pri = z.value("priority")
    fills: dict[str, list[cu.FillPolygon]] = {c: [] for c in layers}
    for fp in z.children("filled_polygon"):
        lay = str(fp.value("layer", layers[0]))
        if lay in fills:
            ring = [to_board(p) for p in shape_points(fp)]
            fills[lay] += cu.unfracture(ring)
    out = []
    for lay in layers:
        fill = fills[lay]
        out.append(
            cu.Zone(
                layer=lay,
                net=net_of(z, nets),
                kind="teardrop" if teardrop else "pour",
                fill=fill,
                area=round(cu.fill_area(fill), 6),
                outline=outline if len(outline) >= 3 else None,
                priority=int(float(pri)) if pri is not None else None,
                name=str(name) if name is not None and not teardrop else None,
                filled=bool(fill),
                id=str(z.value("uuid", z.value("tstamp", ""))) or None,
            )
        )
    return out


def _copper_shapes(tree: Node, copper: list[str], nets, warnings: list[str]) -> tuple[list[cu.Zone], list[cu.Track]]:
    """Drawings on copper layers (board gr_* and footprint fp_* shapes) as copper, the way a Gerber plot has them:
    a filled polygon, rectangle or circle is a zone of kind 'shape', a stroke (lines, arcs, outlines; a filled
    shape's border when it has a width) becomes tracks of the stroke's width, a full circle two half arcs.
    Text and curves on copper are counted in a warning, not included."""
    zones: list[cu.Zone] = []
    tracks: list[cu.Track] = []
    skipped = 0

    def add(n: Node, tf=None):
        nonlocal skipped
        lay = str(n.value("layer", ""))
        if lay not in copper:
            return
        g = graphic_points(n)
        if g is None or len(g["pts"]) < 2:
            skipped += 1
            return
        net = net_of(n, nets)
        uid = str(n.value("uuid", n.value("tstamp", ""))) or None

        def b(p):
            return to_board(tf(p) if tf else p)

        if g["closed"] and g["filled"] and len(g["pts"]) >= 3:
            fill = cu.unfracture([b(p) for p in g["pts"]])
            if fill:
                zones.append(
                    cu.Zone(layer=lay, net=net, kind="shape", fill=fill, area=round(cu.fill_area(fill), 6), id=uid)
                )
        w = r(g["width"])
        if w <= 0:
            return

        def track(s, e, mid=None):
            tracks.append(cu.Track(layer=lay, net=net, width=w, start=s, end=e, mid=mid, id=uid))

        kind = g["kind"]
        if kind == "line":
            track(b(g["pts"][0]), b(g["pts"][1]))
        elif kind == "arc":
            if n.child("mid") is not None:
                s, mid, e = _xy(n.child("start")), _xy(n.child("mid")), _xy(n.child("end"))
            else:
                s, mid, e = arc_from_center(_xy(n.child("start")), _xy(n.child("end")), n.num("angle", 90.0))
            track(b(s), b(e), b(mid))
        elif kind == "circle":
            c = b(_xy(n.child("center")))
            for s, e, mid in cu.circle_halves(c, b(_xy(n.child("end")))):
                track(s, e, mid)
        else:  # rect, poly outlines (arcs in polygons flattened)
            ring = [b(p) for p in g["pts"]]
            for i, p in enumerate(ring):
                track(p, ring[(i + 1) % len(ring)])

    for c in tree.children():
        if c.name.startswith("gr_"):
            add(c)
        elif c.name in ("footprint", "module"):
            tf = _footprint_to_page(c)
            for g in c.children():
                if g.name.startswith("fp_"):
                    add(g, tf)
    if skipped:
        warnings.append(f"{skipped} copper drawing(s) (text, curves) are not included")
    return zones, tracks


def _used_nets(doc: cu.Copper, order: list[str]) -> list[str]:
    used = {t.net for t in doc.tracks} | {v.net for v in doc.vias} | {z.net for z in doc.zones}
    used |= {p.net for p in doc.pads}
    head = [""] if "" in used else []
    known = [n for n in order if n in used]
    rest = sorted(used - set(known) - {""})
    return head + known + rest


# ---------------------------------------------------------------------------------------------------------------------
# kipr's item view (diffing): KiCad frame, y down, canonical keys

R = 4  # rounding for geometric keys (0.1 um)

SHAPE_NAMES = ("line", "rect", "circle", "arc", "poly", "curve", "bbox")
TEXT_NAMES = ("text", "text_box")


def rnd(v: float) -> float:
    v = round(float(v), R)
    return 0.0 if v == 0 else v


def pt(node: Node | None, default=(0.0, 0.0)):
    if node is None:
        return default
    a = node.nums()
    return (a[0], a[1]) if len(a) >= 2 else default


def box_of(points) -> list[float] | None:
    xs, ys = [], []
    for x, y in points:
        xs.append(x)
        ys.append(y)
    if not xs:
        return None
    return [min(xs), min(ys), max(xs), max(ys)]


def union(*boxes) -> list[float] | None:
    bs = [b for b in boxes if b]
    if not bs:
        return None
    return [min(b[0] for b in bs), min(b[1] for b in bs), max(b[2] for b in bs), max(b[3] for b in bs)]


def grow(b, d: float):
    return None if b is None else [b[0] - d, b[1] - d, b[2] + d, b[3] + d]


def circle_box(cx, cy, rad):
    return [cx - rad, cy - rad, cx + rad, cy + rad]


def arc_points(start, mid, end, n: int = 16):
    """Points along a 3-point arc (for bboxes; kipr's resolution)."""
    (x1, y1), (x2, y2), (x3, y3) = start, mid, end
    d = 2 * (x1 * (y2 - y3) + x2 * (y3 - y1) + x3 * (y1 - y2))
    if abs(d) < 1e-12:
        return [start, mid, end]
    ux = ((x1 * x1 + y1 * y1) * (y2 - y3) + (x2 * x2 + y2 * y2) * (y3 - y1) + (x3 * x3 + y3 * y3) * (y1 - y2)) / d
    uy = ((x1 * x1 + y1 * y1) * (x3 - x2) + (x2 * x2 + y2 * y2) * (x1 - x3) + (x3 * x3 + y3 * y3) * (x2 - x1)) / d
    rad = math.hypot(x1 - ux, y1 - uy)
    a1, a2, a3 = (math.atan2(p[1] - uy, p[0] - ux) for p in (start, mid, end))

    def ccw(a, b):
        return (b - a) % (2 * math.pi)

    span = ccw(a1, a3)
    if ccw(a1, a2) > span:  # mid not on the ccw path: go the other way
        span -= 2 * math.pi
    return [(ux + rad * math.cos(a1 + span * i / n), uy + rad * math.sin(a1 + span * i / n)) for i in range(n + 1)]


def net_of(node: Node, nets: dict[str, str]) -> str:
    """Net name of an item: `(net 3)`, `(net 3 "GND")` (pads) or `(net "GND")` (KiCad 10)."""
    nn = node.value("net_name")
    if nn is not None:
        return str(nn)
    c = node.child("net")
    if c is None:
        return ""
    args = c.atoms()
    if len(args) >= 2:
        return str(args[1])
    if len(args) == 1:
        a = args[0]
        return nets.get(str(a), "") if isinstance(a, Atom) else str(a)
    return ""


def item_layers(node: Node) -> list[str]:
    ls = node.child("layers")
    if ls is not None:
        return [str(a) for a in ls.atoms()]
    lay = node.value("layer")
    return [str(lay)] if lay is not None else []


def shape_points(node: Node) -> list[tuple[float, float]]:
    """Outline points of a gr_*/fp_* shape in its own frame."""
    kind = node.name.split("_", 1)[-1]
    if kind in ("line", "rect", "bbox"):
        s, e = pt(node.child("start")), pt(node.child("end"))
        if kind == "line":
            return [s, e]
        return [s, (e[0], s[1]), e, (s[0], e[1])]
    if kind == "circle":
        c, e = pt(node.child("center")), pt(node.child("end"))
        rad = math.hypot(e[0] - c[0], e[1] - c[1])
        return [(c[0] - rad, c[1] - rad), (c[0] + rad, c[1] + rad)]
    if kind == "arc":
        s, mid, e = pt(node.child("start")), node.child("mid"), pt(node.child("end"))
        if mid is None:  # KiCad 5 style (start=centre, end=point, angle)
            return [s, e]
        return arc_points(s, pt(mid), e)
    pts = node.child("pts")
    out = []
    if pts is not None:
        for c in pts.children():
            if c.name == "xy":
                out.append(pt(c))
            elif c.name == "arc":
                out.extend(arc_points(pt(c.child("start")), pt(c.child("mid")), pt(c.child("end"))))
    return out


def text_box(node: Node, text: str) -> list[float] | None:
    x, y = pt(node.child("at"))
    font = node.child("effects")
    size = 1.0
    if font is not None and font.child("font") is not None and font.child("font").child("size") is not None:
        size = (font.child("font").nums("size") or [1.0])[0]
    lines = str(text).split("\n")
    w = max((len(ln) for ln in lines), default=1) * size * 0.8 / 2
    h = len(lines) * size * 1.4 / 2
    if node.name.endswith("text_box") and node.child("start") is not None:
        return box_of(shape_points(node) if node.child("pts") else [pt(node.child("start")), pt(node.child("end"))])
    rot = (node.child("at").nums() + [0, 0, 0])[2] if node.child("at") is not None else 0
    corners = [rotate(dx, dy, rot) for dx, dy in ((-w, -h), (w, -h), (w, h), (-w, h))]
    return box_of([(x + dx, y + dy) for dx, dy in corners])


@dataclass
class Item:
    """A board-level item that is compared as a whole (track, via, graphic, text, outline)."""

    kind: str
    layers: list[str]
    key: str
    box: list[float] | None
    net: str = ""
    uuid: str = ""
    text: str = ""
    length: float = 0.0


@dataclass
class Zone:
    uuid: str
    name: str
    net: str
    layers: list[str]
    keepout: bool
    outline: str
    settings: str
    fill: str
    box: list[float] | None


@dataclass
class PcbFootprint:
    uuid: str
    ref: str
    value: str
    lib_id: str
    x: float
    y: float
    rot: float
    side: str
    fields: dict[str, str]
    attrs: list[str]
    dnp: bool
    locked: bool
    models: list[dict]
    pads_key: str
    graphics_key: str
    box: list[float] | None
    layers: set[str] = field(default_factory=set)
    path: str = ""
    pad_nets: dict[str, str] = field(default_factory=dict)
    edge_pts: list = field(default_factory=list)
    holes: set[str] = field(default_factory=set)  # {"PTH", "NPTH"} if it has drilled pads


@dataclass
class PcbFile:
    layers: list[dict]
    copper: list[str]
    nets: dict[str, str]
    footprints: list[PcbFootprint]
    items: list[Item]
    zones: list[Zone]
    setup_key: dict[str, str]
    thickness: float | None
    stackup: dict
    edge_box: list[float] | None
    title: dict


def layer_kind(name: str) -> str:
    if name.endswith(".Cu"):
        return "copper"
    suffix = name.split(".", 1)[-1]
    return {
        "Mask": "mask",
        "Paste": "paste",
        "SilkS": "silk",
        "Silkscreen": "silk",
        "Fab": "fab",
        "CrtYd": "courtyard",
        "Courtyard": "courtyard",
        "Adhes": "adhesive",
        "Adhesive": "adhesive",
        "Cuts": "outline",
    }.get(suffix, "user")


# Documentation layers (fab notes, drawings, comments, User.N, Margin): drawn anywhere on the page, so
# viewers frame them by their own extents instead of the board outline
DOC_KINDS = ("fab", "user")


def layer_side(name: str) -> str:
    if name.startswith("F."):
        return "top"
    if name.startswith("B."):
        return "bottom"
    if name.startswith("In") and name.endswith(".Cu"):
        return "inner"
    return "none"


def copper_index(name: str) -> int | None:
    """Physical position of a copper layer, top -> bottom: F.Cu 0, In<n>.Cu n, B.Cu 1000; None otherwise."""
    if name == "F.Cu":
        return 0
    if name == "B.Cu":
        return 1000
    mt = re.fullmatch(r"In(\d+)\.Cu", name)
    return int(mt.group(1)) if mt else None


def stack_order(names: list[str]) -> list[str]:
    """Copper first in physical order (F.Cu, In1.Cu, In2.Cu, ..., In10.Cu, B.Cu), then the rest as given."""
    return sorted(names, key=lambda n: (0, copper_index(n)) if copper_index(n) is not None else (1, 0))


def expand_layers(names, copper: list[str], all_layers: list[str]) -> set[str]:
    out = set()
    for n in names:
        if n.startswith("*."):
            suf = n[1:]
            out.update(x for x in (copper if suf == ".Cu" else all_layers) if x.endswith(suf))
        elif n.startswith("F&B."):
            suf = n[3:]
            out.update(("F" + suf, "B" + suf))
        else:
            out.add(n)
    return out


def _fp_transform(x0, y0, rot):
    def tf(p):
        dx, dy = rotate(p[0], p[1], rot)
        return (x0 + dx, y0 + dy)

    return tf


def _pad_box(pad: Node, tf, fprot: float):
    px, py = pt(pad.child("at"))
    a = (pad.child("at").nums() + [0, 0, 0])[2] if pad.child("at") is not None else 0.0
    sw, sh = ((pad.nums("size") or []) + [0.0, 0.0])[:2]
    cx, cy = tf((px, py))
    corners = [
        rotate(dx, dy, a) for dx, dy in ((-sw / 2, -sh / 2), (sw / 2, -sh / 2), (sw / 2, sh / 2), (-sw / 2, sh / 2))
    ]
    return box_of([(cx + dx, cy + dy) for dx, dy in corners])


def _pad_key(pad: Node, fprot: float) -> str:
    """Pad canonical text, footprint-relative (pad angles in files include the footprint's)."""
    at = pad.child("at")
    drop = ("uuid", "tstamp", "net", "pinfunction", "pintype")
    parts = [dumps(c, drop=drop) for c in pad[1:] if not (isinstance(c, Node) and (c.name == "at" or c.name in drop))]
    if at is not None:
        n = at.nums() + [0, 0, 0]
        parts.insert(0, f"(at {rnd(n[0])} {rnd(n[1])} {rnd((n[2] - fprot) % 360)})")
    return "(pad " + " ".join(parts) + ")"


def _parse_footprint(fp: Node, nets, copper, all_layers) -> PcbFootprint:
    at = fp.child("at")
    xyz = (at.nums() + [0, 0, 0]) if at is not None else [0, 0, 0]
    x, y, rot = xyz[0], xyz[1], xyz[2]
    tf = _fp_transform(x, y, rot)
    layer = str(fp.value("layer", "F.Cu"))
    props: dict[str, str] = {}
    for p in fp.children("property"):
        if p.arg(0) is not None:
            props[str(p.arg(0))] = str(p.arg(1, ""))
    for t in fp.children("fp_text"):  # KiCad <= 7
        kind = str(t.arg(0, ""))
        if kind == "reference":
            props.setdefault("Reference", str(t.arg(1, "")))
        elif kind == "value":
            props.setdefault("Value", str(t.arg(1, "")))
    ref, value = props.pop("Reference", ""), props.pop("Value", "")
    props.pop("Footprint", None)
    attr = fp.child("attr")
    attrs = sorted(str(a) for a in attr.atoms()) if attr is not None else []
    dnp = "dnp" in attrs or fp.flag("dnp")
    models = []
    for md_node in fp.children("model"):
        md = {"path": str(md_node.arg(0, ""))}
        for k in ("offset", "scale", "rotate"):
            c = md_node.child(k)
            if c is not None:
                md[k] = [rnd(v) for v in (c.nums("xyz") or [])]
        if md_node.flag("hide"):
            md["hide"] = True
        models.append(md)
    pads = list(fp.children("pad"))
    pad_keys = sorted(_pad_key(p, rot) for p in pads)
    pad_nets = {}
    for p in pads:
        num = str(p.arg(0, ""))
        if num:
            n = net_of(p, nets)
            if n or num not in pad_nets:
                pad_nets[num] = n
    gfx_keys, crt_pts, edge_pts, all_boxes, layers = [], [], [], [], set()
    for c in fp.children():
        nm = c.name
        if nm.startswith("fp_") and nm[3:] in SHAPE_NAMES:
            gfx_keys.append(dumps(c, drop=("uuid", "tstamp")))
            pts = [tf(p) for p in shape_points(c)]
            lay = str(c.value("layer", ""))
            layers.add(lay)
            if lay.endswith("CrtYd"):
                crt_pts.extend(pts)
            elif lay == "Edge.Cuts":
                edge_pts.extend(pts)
            b = box_of(pts)
            if b:
                all_boxes.append(b)
        elif nm in ("fp_text", "fp_text_box"):
            gfx_keys.append(f"(text {str(c.arg(0, ''))!r} {str(c.arg(1, ''))!r} {c.value('layer', '')})")
            layers.add(str(c.value("layer", "")))
        elif nm == "property":
            lay = c.value("layer")
            if lay is not None and not c.flag("hide"):
                layers.add(str(lay))
    for p in pads:
        layers |= expand_layers(
            [str(a) for a in (p.child("layers").atoms() if p.child("layers") else [])], copper, all_layers
        )
        b = _pad_box(p, tf, rot)
        if b:
            all_boxes.append(b)
    box = box_of(crt_pts) or union(*all_boxes)
    if box is None:
        box = [x - 0.5, y - 0.5, x + 0.5, y + 0.5]
    layers.discard("")
    return PcbFootprint(
        uuid=str(fp.value("uuid", fp.value("tstamp", "")) or ""),
        ref=ref,
        value=value,
        lib_id=str(fp.arg(0, "")),
        x=rnd(x),
        y=rnd(y),
        rot=rnd(rot % 360),
        side="bottom" if layer.startswith("B.") else "top",
        fields=props,
        attrs=attrs,
        dnp=dnp,
        locked=fp.flag("locked"),
        models=models,
        pads_key="\n".join(pad_keys),
        graphics_key="\n".join(sorted(gfx_keys)),
        box=box,
        layers=layers,
        path=str(fp.value("path", "") or ""),
        pad_nets=pad_nets,
        edge_pts=edge_pts,
        holes={
            {"thru_hole": "PTH", "np_thru_hole": "NPTH"}[str(p.arg(1))]
            for p in pads
            if str(p.arg(1, "")) in ("thru_hole", "np_thru_hole")
        },
    )


def _track_item(n: Node, nets) -> Item:
    layers = item_layers(n)
    net = net_of(n, nets)
    w = rnd(n.num("width"))
    if n.name == "via":
        x, y = pt(n.child("at"))
        size = n.num("size")
        extra = list(n.atoms())  # blind / micro
        key = (
            f"via {rnd(x)} {rnd(y)} {rnd(size)} {rnd(n.num('drill'))} {'/'.join(layers)} {net} "
            f"{' '.join(map(str, extra))}"
        )
        return Item("via", layers, key, circle_box(x, y, size / 2), net, str(n.value("uuid", "")))
    s, e = pt(n.child("start")), pt(n.child("end"))
    if n.name == "arc":
        mid = pt(n.child("mid"))
        pts = arc_points(s, mid, e)
        ends = sorted([(rnd(s[0]), rnd(s[1])), (rnd(e[0]), rnd(e[1]))])
        key = f"arc {ends} {rnd(mid[0])} {rnd(mid[1])} {w} {'/'.join(layers)} {net}"
        length = sum(math.dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1))
    else:
        pts = [s, e]
        ends = sorted([(rnd(s[0]), rnd(s[1])), (rnd(e[0]), rnd(e[1]))])
        key = f"seg {ends} {w} {'/'.join(layers)} {net}"
        length = math.dist(s, e)
    return Item("track", layers, key, grow(box_of(pts), w / 2), net, str(n.value("uuid", "")), length=length)


def _zone(z: Node, nets) -> Zone:
    layers = item_layers(z)
    poly_pts = []
    for p in z.children("polygon"):
        poly_pts.extend(shape_points(p))
    outline = " ".join(f"{rnd(a)},{rnd(b)}" for a, b in poly_pts)
    settings = " ".join(
        dumps(c)
        for c in z.children()
        if c.name
        not in (
            "polygon",
            "filled_polygon",
            "uuid",
            "tstamp",
            "fill_segments",
            "net",
            "net_name",
            "layer",
            "layers",
            "name",
        )
    )
    fill = str(hash(" ".join(dumps(c) for c in z.children("filled_polygon"))))
    return Zone(
        uuid=str(z.value("uuid", z.value("tstamp", "")) or ""),
        name=str(z.value("name", "") or ""),
        net=net_of(z, nets),
        layers=layers,
        keepout=z.child("keepout") is not None,
        outline=outline,
        settings=settings,
        fill=fill,
        box=box_of(poly_pts),
    )


def load(text: str) -> PcbFile:
    """kipr's item view of a board (KiCad frame, y down), for diffing two revisions."""
    root = parse(text)
    layers, all_names = [], []
    lnode = root.child("layers")
    if lnode is not None:
        for c in lnode.children():
            a = c.atoms()
            # (0 "F.Cu" signal): the head is the ordinal
            if len(a) >= 2:
                layers.append(
                    {
                        "ordinal": str(c[0]),
                        "name": str(a[0]),
                        "type": str(a[1]),
                        "user_name": str(a[2]) if len(a) > 2 else None,
                    }
                )
                all_names.append(str(a[0]))
    copper = [ly["name"] for ly in layers if ly["name"].endswith(".Cu")]
    nets = {}
    for n in root.children("net"):
        a = n.atoms()
        if len(a) >= 2:
            nets[str(a[0])] = str(a[1])
    footprints = [_parse_footprint(f, nets, copper, all_names) for f in root.children("footprint")]
    items: list[Item] = []
    zones: list[Zone] = []
    edge_pts = []
    for c in root.children():
        nm = c.name
        if nm in ("segment", "arc", "via"):
            items.append(_track_item(c, nets))
        elif nm == "zone":
            zones.append(_zone(c, nets))
        elif nm.startswith("gr_") and nm[3:] in SHAPE_NAMES:
            pts = shape_points(c)
            lay = item_layers(c)
            kind = "outline" if "Edge.Cuts" in lay else "graphic"
            if kind == "outline":
                edge_pts.extend(pts)
            items.append(
                Item(
                    kind,
                    lay,
                    dumps(c, drop=("uuid", "tstamp")),
                    box_of(pts),
                    net_of(c, nets),
                    str(c.value("uuid", "") or ""),
                )
            )
        elif nm in ("gr_text", "gr_text_box"):
            txt = str(c.arg(0, ""))
            items.append(
                Item(
                    "text",
                    item_layers(c),
                    dumps(c, drop=("uuid", "tstamp")),
                    text_box(c, txt),
                    "",
                    str(c.value("uuid", "") or ""),
                    text=txt,
                )
            )
        elif nm == "dimension":
            pts = [pt(x) for x in (c.child("pts").children("xy") if c.child("pts") else [])]
            items.append(
                Item(
                    "graphic",
                    item_layers(c),
                    dumps(c, drop=("uuid", "tstamp")),
                    box_of(pts),
                    "",
                    str(c.value("uuid", "") or ""),
                )
            )
    for fp in footprints:  # footprint-embedded Edge.Cuts (slots, cut-outs) count for the size
        edge_pts.extend(fp.edge_pts)
    setup = root.child("setup")
    setup_key = {}
    stackup = {}
    if setup is not None:
        for c in setup.children():
            if c.name == "stackup":
                for ly in c.children("layer"):
                    stackup[str(ly.arg(0, ""))] = {
                        k: str(ly.value(k))
                        for k in ("type", "color", "material", "thickness")
                        if ly.value(k) is not None
                    }
                for k in (
                    "copper_finish",
                    "dielectric_constraints",
                    "edge_connector",
                    "castellated_pads",
                    "edge_plating",
                ):
                    if c.value(k) is not None:
                        stackup[k] = str(c.value(k))
                setup_key["stackup"] = dumps(c)
            elif c.name != "pcbplotparams":
                setup_key[c.name] = dumps(c)
    gen = root.child("general")
    thickness = gen.num("thickness", None) if gen is not None else None
    tb = root.child("title_block")
    title = {}
    if tb is not None:
        for c in tb.children():
            title[c.name if c.name != "comment" else f"comment{c.arg(0)}"] = str(
                c.arg(1 if c.name == "comment" else 0, "")
            )
    return PcbFile(
        layers=layers,
        copper=copper,
        nets=nets,
        footprints=footprints,
        items=items,
        zones=zones,
        setup_key=setup_key,
        thickness=thickness,
        stackup=stackup,
        edge_box=box_of(edge_pts),
        title=title,
    )
