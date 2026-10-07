"""Build the golden board.json for KiCad's royalblue54L_feather demo (temporary, hand-written).

    python fixtures/royalblue54L_feather/make_board.py          # rewrites board.json
    python fixtures/royalblue54L_feather/make_board.py --check  # fails if board.json differs

Phase C stand-in for the real readers (phase F: boarddd.io.kicad, io.gerber, io.excellon, io.gbrjob):
reads kicad/RoyalBlue54L-Feather.kicad_pcb for components, footprints, outline, stackup and origins,
fab/*.gbrjob for the layer files, fab/*.drl for the drills, and cross-checks the placements against
kicad-cli's fab/pos.csv. Stdlib + boarddd.model only. Delete it once phase F reproduces board.json.

Conventions it follows (docs/model.md): board frame = KiCad's absolute page coordinates with y negated
(what kicad-cli's Gerbers, drills and pos file use); footprints in KiCad footprint frame, library
orientation (bottom-side instances flipped back, pad angles made relative to the footprint).
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "python" / "src"))

from boarddd import model as m  # noqa: E402

PCB = HERE / "kicad" / "RoyalBlue54L-Feather.kicad_pcb"
FAB = HERE / "fab"
OUT = HERE / "board.json"
NDIGITS = 6


def r(v: float) -> float:
    v = round(v, NDIGITS)
    return 0.0 if v == 0 else v


def rp(p) -> tuple[float, float]:
    return (r(p[0]), r(p[1]))


# ---------------------------------------------------------------------------------------------------------------------
# s-expressions


class Str(str):
    """A quoted string atom."""


def parse_sexpr(text: str):
    tok = re.compile(r'\s*(?:(\()|(\))|"((?:[^"\\]|\\.)*)"|([^\s()"]+))')
    stack: list[list] = [[]]
    pos = 0
    while pos < len(text):
        mt = tok.match(text, pos)
        if not mt:
            break
        pos = mt.end()
        if mt.group(1):
            stack.append([])
        elif mt.group(2):
            node = stack.pop()
            stack[-1].append(node)
        elif mt.group(3) is not None:
            stack[-1].append(Str(re.sub(r"\\(.)", r"\1", mt.group(3))))
        elif mt.group(4):
            stack[-1].append(mt.group(4))
    return stack[0][0]


def head(n):
    return n[0] if isinstance(n, list) and n else None


def kids(n, name):
    return [c for c in n if isinstance(c, list) and head(c) == name] if isinstance(n, list) else []


def kid(n, name):
    k = kids(n, name)
    return k[0] if k else None


def num(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def xy(n, d=(0.0, 0.0)):
    return (num(n[1]), num(n[2])) if n else d


# ---------------------------------------------------------------------------------------------------------------------
# geometry, ported from src/geom/loops.js and src/footprint/kicad_mod.js so footprints match boarddd's JS parser


def segments_for(radius: float) -> int:
    if not radius > 0:
        return 10
    ratio = min(1.0, 0.01 / radius)
    needed = math.ceil(math.pi / math.acos(max(-1.0, 1 - ratio)))
    return max(10, min(48, needed))


def ring_points(cx, cy, radius, segments=None):
    segments = segments or segments_for(radius)
    return [
        (cx + radius * math.cos(-2 * math.pi * i / segments), cy + radius * math.sin(-2 * math.pi * i / segments))
        for i in range(segments)
    ]


def slot_points(x1, y1, x2, y2, radius, segments=None):
    segments = segments or segments_for(radius)
    if math.hypot(x2 - x1, y2 - y1) < 1e-9:
        return list(reversed(ring_points(x1, y1, radius, segments)))
    along = math.atan2(y2 - y1, x2 - x1)
    half = max(2, round(segments / 2))
    pts = []
    for cx, cy, frm in ((x2, y2, along - math.pi / 2), (x1, y1, along + math.pi / 2)):
        for i in range(half + 1):
            a = frm + math.pi * i / half
            pts.append((cx + radius * math.cos(a), cy + radius * math.sin(a)))
    return pts


def stroke_loops(pts, width, closed=False):
    n = len(pts) if closed else len(pts) - 1
    return [slot_points(*pts[i], *pts[(i + 1) % len(pts)], width / 2, 16) for i in range(n)]


def arc_through(p1, pm, p2, segments=24):
    (x1, y1), (xm, ym), (x2, y2) = p1, pm, p2
    d = 2 * (x1 * (ym - y2) + xm * (y2 - y1) + x2 * (y1 - ym))
    if abs(d) < 1e-12:
        return [p1, p2]
    s1, sm, s2 = x1 * x1 + y1 * y1, xm * xm + ym * ym, x2 * x2 + y2 * y2
    cx = (s1 * (ym - y2) + sm * (y2 - y1) + s2 * (y1 - ym)) / d
    cy = (s1 * (x2 - xm) + sm * (x1 - x2) + s2 * (xm - x1)) / d
    rad = math.hypot(x1 - cx, y1 - cy)
    a1, am, a2 = (math.atan2(y - cy, x - cx) for x, y in (p1, pm, p2))

    def norm(a):
        return ((a % (2 * math.pi)) + 2 * math.pi) % (2 * math.pi)

    sweep = norm(a2 - a1)
    if norm(am - a1) > sweep:
        sweep -= 2 * math.pi
    n = max(2, math.ceil(segments * abs(sweep) / (2 * math.pi)))
    return [(cx + rad * math.cos(a1 + sweep * i / n), cy + rad * math.sin(a1 + sweep * i / n)) for i in range(n + 1)]


def stroke_width(n, d=0.12):
    w = kid(kid(n, "stroke"), "width") or kid(n, "width")
    return num(w[1], d) if w else d


def is_filled(n):
    f = kid(n, "fill")
    if not f:
        return False
    if isinstance(f[1], list):
        t = kid(f, "type")
        return (t[1] if t else None) != "none"
    return f[1] in ("solid", "yes")


def poly_pts(n):
    out = []
    for c in (kid(n, "pts") or [])[1:]:
        if head(c) == "xy":
            out.append(xy(c))
        elif head(c) == "arc":
            pts = arc_through(xy(kid(c, "start")), xy(kid(c, "mid")), xy(kid(c, "end")))
            out.extend(pts[1:] if out else pts)
    return out


def graphic(n):
    kind = re.sub(r"^(fp|gr)_", "", head(n))
    width, filled = stroke_width(n), is_filled(n)
    if kind == "line":
        return dict(kind=kind, pts=[xy(kid(n, "start")), xy(kid(n, "end"))], width=width, closed=False, filled=False)
    if kind == "rect":
        (x0, y0), (x1, y1) = xy(kid(n, "start")), xy(kid(n, "end"))
        return dict(kind=kind, pts=[(x0, y0), (x1, y0), (x1, y1), (x0, y1)], width=width, closed=True, filled=filled)
    if kind == "circle":
        (cx, cy), (ex, ey) = xy(kid(n, "center")), xy(kid(n, "end"))
        pts = list(reversed(ring_points(cx, cy, math.hypot(ex - cx, ey - cy), 48)))
        return dict(kind=kind, pts=pts, width=width, closed=True, filled=filled)
    if kind == "arc":
        pts = arc_through(xy(kid(n, "start")), xy(kid(n, "mid")), xy(kid(n, "end")))
        return dict(kind=kind, pts=pts, width=width, closed=False, filled=False)
    if kind == "poly":
        return dict(
            kind=kind,
            pts=poly_pts(n),
            width=width,
            closed=True,
            filled=filled or (head(n) == "gr_poly" and not kid(n, "fill")),
        )
    return None


def primitive_loops(g):
    if not g or len(g["pts"]) < 2:
        return []
    if g["closed"] and (g["filled"] or g["kind"] == "poly"):
        loops = [g["pts"]]
        if g["width"] > 0:
            loops += stroke_loops(g["pts"], g["width"], True)
        return loops
    return stroke_loops(g["pts"], g["width"] or 0.01, g["closed"])


# ---------------------------------------------------------------------------------------------------------------------
# footprints


def norm_angle(a: float) -> float:
    a = (a + 180.0) % 360.0 - 180.0
    return 180.0 if a == -180.0 else r(a) + 0.0


FLIP_LAYER = {"F": "B", "B": "F"}


def unflip_layer(name: str) -> str:
    return re.sub(r"^([FB])\.", lambda mt: FLIP_LAYER[mt.group(1)] + ".", name)


def read_pad(n, fp_angle: float, bottom: bool) -> m.Pad:
    """A pad from a .kicad_pcb footprint -> library form (angle relative, bottom flipped back)."""
    number, ptype, shape = (str(v) for v in n[1:4])
    at, size = kid(n, "at"), kid(n, "size")
    x, y, a = num(at[1]), num(at[2]), num(at[3]) if len(at) > 3 else 0.0
    sy = -1.0 if bottom else 1.0  # KiCad's flip mirrors the footprint across its x axis
    rel = (fp_angle - a) if bottom else (a - fp_angle)
    pad = m.Pad(
        number=number,
        type=ptype,
        shape=shape,
        at=(r(x), r(sy * y), norm_angle(rel)),
        size=(r(num(size[1])), r(num(size[2]))),
        layers=[unflip_layer(str(v)) if bottom else str(v) for v in (kid(n, "layers") or [])[1:]],
    )
    d = kid(n, "drill")
    if d:
        vals = [v for v in d[1:] if not isinstance(v, list)]
        oval = vals and vals[0] == "oval"
        nums = [float(v) for v in vals if v != "oval"]
        off = kid(d, "offset")
        if nums and nums[0] > 0:
            pad.drill = m.PadDrill(
                shape="oval" if oval else "circle",
                size=(r(nums[0]), r(nums[1] if oval and len(nums) > 1 else nums[0])),
                offset=(r(num(off[1])), r(sy * num(off[2]))) if off else (0.0, 0.0),
            )
    if (v := kid(n, "roundrect_rratio")) is not None:
        pad.roundrect_rratio = r(num(v[1]))
    if (v := kid(n, "chamfer_ratio")) is not None:
        pad.chamfer_ratio = r(num(v[1]))
    if (v := kid(n, "chamfer")) is not None:
        corners = [str(c) for c in v[1:]]
        if bottom:  # mirrored across x: top <-> bottom corners
            corners = [c.replace("top", "@").replace("bottom", "top").replace("@", "bottom") for c in corners]
        pad.chamfer = corners
    if (v := kid(n, "rect_delta")) is not None:
        pad.rect_delta = (r(num(v[1])), r(sy * num(v[2])))
    if (opts := kid(n, "options")) is not None:
        anchor = kid(opts, "anchor")
        pad.anchor = str(anchor[1]) if anchor else "rect"
    if (prims := kid(n, "primitives")) is not None:
        loops = [lp for g in prims[1:] if isinstance(g, list) for lp in primitive_loops(graphic(g))]
        # a mirror reverses winding: flip the point order back for bottom-side pads
        pad.primitives = [
            m.Primitive(pts=[(r(px), r(sy * py)) for px, py in (lp[::-1] if bottom else lp)]) for lp in loops
        ]
    if (v := kid(n, "solder_mask_margin")) is not None:
        pad.solder_mask_margin = r(num(v[1]))
    if (v := kid(n, "solder_paste_margin")) is not None:
        pad.solder_paste_margin = r(num(v[1]))
    return pad


def read_footprint_def(fp, bottom: bool) -> m.Footprint:
    angle = num(kid(fp, "at")[3]) if len(kid(fp, "at")) > 3 else 0.0
    sy = -1.0 if bottom else 1.0
    out = m.Footprint(
        name=str(fp[1]), attr=[str(a) for a in (kid(fp, "attr") or [])[1:] if a in ("smd", "through_hole")]
    )
    for c in fp:
        h = head(c)
        if h == "pad":
            out.pads.append(read_pad(c, angle, bottom))
        elif h and re.fullmatch(r"fp_(line|rect|circle|arc|poly|curve)", h):
            layer = str(kid(c, "layer")[1])
            g = graphic(c)
            if g and len(g["pts"]) >= 2 and re.search(r"\.(SilkS|Fab|CrtYd)$", layer):
                out.graphics.append(
                    m.Graphic(
                        layer=unflip_layer(layer) if bottom else layer,
                        kind=g["kind"],
                        pts=[
                            (r(px), r(sy * py)) for px, py in (g["pts"][::-1] if bottom and g["closed"] else g["pts"])
                        ],
                        width=max(0.0, r(g["width"])),
                        closed=g["closed"],
                        filled=g["filled"],
                    )
                )  # -1 = no stroke
    return out


# ---------------------------------------------------------------------------------------------------------------------
# board pieces


def to_board(p) -> tuple[float, float]:
    return (r(p[0]), r(-p[1]))


def read_outline(root) -> m.Outline:
    segs = []
    for c in root:
        if head(c) in ("gr_line", "gr_arc") and str(kid(c, "layer")[1]) == "Edge.Cuts":
            if head(c) == "gr_line":
                segs.append([xy(kid(c, "start")), xy(kid(c, "end"))])
            else:
                s, md, e = xy(kid(c, "start")), xy(kid(c, "mid")), xy(kid(c, "end"))
                rad = math.dist(s, md)  # chord-based segment count: ~5 degrees per segment
                pts = arc_through(s, md, e, segments=72)
                segs.append(pts if rad else [s, e])
    # chain into one loop
    loop = list(segs.pop(0))
    while segs:
        end = loop[-1]
        i = next((i for i, sg in enumerate(segs) if min(math.dist(sg[0], end), math.dist(sg[-1], end)) < 1e-6), None)
        if i is None:
            raise SystemExit("Edge.Cuts does not close")
        sg = segs.pop(i)
        loop += sg[1:] if math.dist(sg[0], end) < 1e-6 else list(reversed(sg))[1:]
    assert math.dist(loop[0], loop[-1]) < 1e-6
    pts = [to_board(p) for p in loop[:-1]]
    area = sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(pts, pts[1:] + pts[:1], strict=True))
    if area < 0:
        pts.reverse()
    return m.Outline(board=pts)


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


def read_stackup(root) -> m.Stackup:
    st = kid(kid(root, "setup"), "stackup")
    thickness = num(kid(kid(root, "general"), "thickness")[1])
    layers = []
    for lay in kids(st, "layer"):
        name, kind = str(lay[1]), STACKUP_KIND[str(kid(lay, "type")[1])]
        side = "top" if name.startswith("F.") else "bottom" if name.startswith("B.") else "inner"
        val = {
            k: (kid(lay, k)[1] if kid(lay, k) else None)
            for k in ("thickness", "material", "color", "epsilon_r", "loss_tangent")
        }
        layers.append(
            m.StackupLayer(
                name=name,
                kind=kind,
                side=side,
                thickness=r(num(val["thickness"])) if val["thickness"] else None,
                material=str(val["material"]) if val["material"] else None,
                color=str(val["color"]) if val["color"] else None,
                epsilon_r=num(val["epsilon_r"]) if val["epsilon_r"] else None,
                loss_tangent=num(val["loss_tangent"]) if val["loss_tangent"] else None,
                layer=name if kind == "copper" else STACKUP_LAYER.get(name),
            )
        )
    color = {lay.name: lay.color for lay in layers}
    return m.Stackup(
        thickness=thickness,
        copper_layers=sum(lay.kind == "copper" for lay in layers),
        finish=str(kid(st, "copper_finish")[1]),
        mask_color=m.SideValues(top=color.get("F.Mask"), bottom=color.get("B.Mask")),
        silk_color=m.SideValues(top=color.get("F.SilkS"), bottom=color.get("B.SilkS")),
        layers=layers,
    )


ROLE = {"Copper": "copper", "SolderMask": "mask", "SolderPaste": "paste", "Legend": "silk", "Profile": "outline"}
DRAW_ORDER = ["outline", "paste", "silk", "mask", "copper", "drill"]


def layer_id(path: str) -> str:
    stem = path.rsplit("-", 1)[1].rsplit(".", 1)[0]  # RoyalBlue54L-Feather-F_Cu.gbr -> F_Cu
    return stem.replace("_", ".", 1) if stem not in ("PTH", "NPTH") else stem


def read_layers(job: dict) -> list[m.Layer]:
    layers = []
    n_copper = job["GeneralSpecs"]["LayerNumber"]
    for f in job["FilesAttributes"]:
        func = f["FileFunction"].split(",")
        role = ROLE[func[0]]
        if role == "copper":
            side = {"Top": "top", "Bot": "bottom", "Inr": "inner"}[func[2]]
            order = int(func[1][1:])
        else:
            side = {"Top": "top", "Bot": "bottom"}.get(func[-1], "none")
            order = n_copper + 1 + DRAW_ORDER.index(role)
        layers.append(
            m.Layer(
                id=layer_id(f["Path"]),
                role=role,
                side=side,
                order=order,
                files=[f"fab/{f['Path']}"],
                format="gerber",
                polarity=f["FilePolarity"].lower(),
                function=f["FileFunction"],
            )
        )
    for name, plated in (("PTH", True), ("NPTH", False)):
        path = next(FAB.glob(f"*-{name}.drl"))
        func = re.search(r"TF\.FileFunction,(.*)", path.read_text()).group(1).strip()
        layers.append(
            m.Layer(
                id=name,
                role="drill",
                side="none",
                order=n_copper + 1 + len(DRAW_ORDER) + (not plated),
                files=[f"fab/{path.name}"],
                format="excellon",
                function=func,
                plated=plated,
            )
        )
    return sorted(layers, key=lambda la: (la.order, la.side != "top", la.id))


DRILL_FUNCTION = {"ViaDrill": "via", "ComponentDrill": "component", "MechanicalDrill": "mechanical"}


def read_drills(layer_id_: str, plated: bool) -> list[m.Drill]:
    text = next(FAB.glob(f"*-{layer_id_}.drl")).read_text()
    assert "METRIC" in text
    tools, funcs, func, out, tool = {}, {}, None, [], None
    for line in text.splitlines():
        if mt := re.match(r"; #@! TA\.AperFunction,.*,(\w+)$", line):
            func = DRILL_FUNCTION.get(mt.group(1))
        elif mt := re.fullmatch(r"(T\d+)C([\d.]+)", line):
            tools[mt.group(1)], funcs[mt.group(1)] = float(mt.group(2)), func
        elif re.fullmatch(r"T\d+", line):
            tool = line
        elif mt := re.fullmatch(r"X(-?[\d.]+)Y(-?[\d.]+)(?:G85X(-?[\d.]+)Y(-?[\d.]+))?", line):
            x, y, x2, y2 = (float(v) if v is not None else None for v in mt.groups())
            out.append(
                m.Drill(
                    x=r(x),
                    y=r(y),
                    diameter=r(tools[tool]),
                    plated=plated,
                    x2=r(x2) if x2 is not None else None,
                    y2=r(y2) if y2 is not None else None,
                    tool=tool,
                    function=funcs[tool],
                    layer=layer_id_,
                )
            )
    return out


def sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


ROLE_OF_FILE = {".kicad_pcb": "pcb", ".gbrjob": "job", ".drl": "drill", ".csv": "placement"}


def read_components(root, footprints: dict[str, m.Footprint]) -> list[m.Component]:
    comps = []
    defs_bottom: dict[str, list] = {}
    for fp in kids(root, "footprint"):
        name = str(fp[1])
        bottom = str(kid(fp, "layer")[1]) == "B.Cu"
        props = {str(p[1]): str(p[2]) for p in kids(fp, "property")}
        at = kid(fp, "at")
        attr = [str(a) for a in (kid(fp, "attr") or [])[1:]]
        if name not in footprints and not bottom:
            footprints[name] = read_footprint_def(fp, bottom=False)
        elif bottom:
            defs_bottom.setdefault(name, []).append(fp)
        models = []
        for md in kids(fp, "model"):

            def vec(k, d, md=md):
                v = kid(kid(md, k), "xyz")
                return (r(num(v[1])), r(num(v[2])), r(num(v[3]))) if v else d

            hide = "hide" in md or (kid(md, "hide") is not None and kid(md, "hide")[1] == "yes")
            models.append(
                m.ModelRef(
                    path=str(md[1]),
                    offset=vec("offset", (0.0, 0.0, 0.0)),
                    rotate=vec("rotate", (0.0, 0.0, 0.0)),
                    scale=vec("scale", (1.0, 1.0, 1.0)),
                    hide=hide,
                )
            )
        x, y = to_board(xy(at))
        mpn = props.get("MPN") or props.get("Manufacturer Part Number")
        comps.append(
            m.Component(
                ref=props["Reference"],
                side="bottom" if bottom else "top",
                x=x,
                y=y,
                rotation=r(num(at[3])) if len(at) > 3 else 0.0,
                value=props.get("Value"),
                footprint=name,
                mount="smd" if "smd" in attr else "tht" if "through_hole" in attr else "other",
                populate="dnp" not in attr,
                in_bom="exclude_from_bom" not in attr,
                in_pos="exclude_from_pos_files" not in attr,
                mpn=[m.PartNumber(mpn=mpn, manufacturer=props.get("Manufacturer"))] if mpn else [],
                models=models,
                attributes={
                    k: v for k, v in sorted(props.items()) if k not in ("Reference", "Value", "Footprint") and v
                },
            )
        )
    for name, fps in defs_bottom.items():
        if name not in footprints:
            footprints[name] = read_footprint_def(fps[0], bottom=True)
    return sorted(comps, key=lambda c: (re.sub(r"\d+", "", c.ref), int(re.sub(r"\D", "", c.ref) or 0), c.ref))


def check_pos(comps: list[m.Component]) -> int:
    """kicad-cli's pos file (board frame) must agree with what we read from the .kicad_pcb."""
    by_ref = {c.ref: c for c in comps}
    rows = list(csv.DictReader(open(FAB / "pos.csv", newline="")))
    for row in rows:
        c = by_ref[row["Ref"]]
        assert c.in_pos and c.side == row["Side"], row
        assert abs(c.x - float(row["PosX"])) < 1e-6 and abs(c.y - float(row["PosY"])) < 1e-6, (row, c.x, c.y)
        assert abs(norm_angle(c.rotation - float(row["Rot"]))) < 1e-6, (row, c.rotation)
    assert len(rows) == sum(c.in_pos for c in comps), "pos file and in_pos disagree"
    return len(rows)


def build() -> m.Board:
    root = parse_sexpr(PCB.read_text(encoding="utf-8"))
    job = json.loads(next(FAB.glob("*.gbrjob")).read_text())
    gen = job["Header"]["GenerationSoftware"]
    setup = kid(root, "setup")
    footprints: dict[str, m.Footprint] = {}
    comps = read_components(root, footprints)
    check_pos(comps)
    drills = read_drills("PTH", True) + read_drills("NPTH", False)
    zero = sum(d.diameter == 0 for d in drills)
    files = sorted([PCB, *FAB.iterdir()], key=lambda p: (p.parent.name, p.name))
    layers = read_layers(job)
    side_of = {f: la.side for la in layers for f in la.files}
    role_of = {f: la.role for la in layers for f in la.files}
    src_files = []
    for p in files:
        rel = p.relative_to(HERE).as_posix()
        role = role_of.get(rel) or ROLE_OF_FILE.get(p.suffix, "other")
        side = side_of.get(rel)
        src_files.append(m.SourceFile(path=rel, role=role, side=side if side != "none" else None, sha256=sha256(p)))
    tb = kid(root, "title_block")
    return m.Board(
        name="RoyalBlue54L-Feather",
        revision=str(kid(tb, "rev")[1]) if kid(tb, "rev") else None,
        source=m.Source(
            kind="kicad_pcb",
            files=src_files,
            generator=f"{gen['Vendor']} {gen['Application']} {gen['Version']} (fab outputs); "
            f"pcbnew {kid(root, 'generator_version')[1]} (board file)",
            reader="boarddd fixtures/royalblue54L_feather/make_board.py (hand-written golden)",
            created=str(kid(tb, "date")[1]) if kid(tb, "date") else None,
        ),
        origin=m.Origin(aux=to_board(xy(kid(setup, "aux_axis_origin"))), grid=to_board(xy(kid(setup, "grid_origin")))),
        outline=read_outline(root),
        stackup=read_stackup(root),
        layers=layers,
        drills=drills,
        footprints=dict(sorted(footprints.items())),
        components=comps,
        warnings=[
            f"{zero} vias have a 0 mm drill in the drill file (the demo board's vias use a 0.00001 mm "
            "placeholder drill)"
        ]
        if zero
        else [],
    )


def main() -> int:
    text = build().to_json()
    if "--check" in sys.argv:
        if OUT.read_text("utf-8") != text:
            print(f"{OUT} is out of date: run {Path(__file__).name}", file=sys.stderr)
            return 1
        return 0
    OUT.write_text(text, encoding="utf-8")
    print(f"wrote {OUT} ({len(text) // 1024} KiB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
