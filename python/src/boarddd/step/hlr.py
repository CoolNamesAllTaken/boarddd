"""
Line drawings of component models, and overlay diffs of two models of one part, as SVG.

A reviewer approves a new standard model, or a disagreement between models, from a picture.
The review screens have live 3D views; these drawings are for everywhere WebGL is not (the
queue, emails, a PDF) and for making a difference obvious at a glance.

Four orthographic views, all at one scale in the component frame: top (looking down -Z,
X right, Y up), front (looking along +Y, X right, Z up), right side (looking along -X, Y right,
Z up) in third-angle layout, and the bottom (looking up +Z, flipped left-right) beside the
top view, because the contacts and an exposed pad are only seen from underneath. Visible edges are solid, hidden edges dashed. The views
come from OpenCascade's exact hidden-line removal (HLRBRep_Algo); a model with more than
EXACT_FACES faces uses the mesh-based variant, which is faster and a little coarser.

Colors are CSS variables with defaults for light and dark pages:

    --boarddd-step-line   visible edges          --boarddd-step-old   the first model's edges
    --boarddd-step-hidden hidden edges           --boarddd-step-new   the second model's edges
    --boarddd-step-dim    dimensions, board line  --boarddd-step-same  edges both models share
    --boarddd-step-text   labels                 --boarddd-step-bg    panel background

A page that sets them on an ancestor of an inline SVG themes it; an SVG shown through <img>
uses its own defaults, which follow prefers-color-scheme.

Projections are cached by the model's fingerprint (frame and B-rep keys), so the same model
seen on thirty boards is projected once; `cache_dir` also keeps finished SVGs on disk.

Source: magpie `step/render.py` at internal `claud/magpie` `3a0374d3` (boarddd phase F4), renamed
`hlr`; its CSS class and variables are `boarddd-step` (magpie's were `magpie-step`). Needs shapely.
"""

from __future__ import annotations

import hashlib
import html
import math
import os
from dataclasses import dataclass, field
from pathlib import Path

from ._extra import require

require(__name__)

from . import occ  # noqa: E402

__all__ = ["View", "project", "views", "drawing", "overlay", "EXACT_FACES", "VIEWS"]

#: Above this many faces, use mesh-based hidden-line removal.
EXACT_FACES = 4000
#: Target size of the largest view, in px.
VIEW_PX = 380
#: Name -> (direction toward the viewer, the view's x axis, which model axes the view shows).
VIEWS = {
    "top": ((0.0, 0.0, 1.0), (1.0, 0.0, 0.0), ("x", "y")),
    "front": ((0.0, -1.0, 0.0), (1.0, 0.0, 0.0), ("x", "z")),
    "side": ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), ("y", "z")),
    "bottom": ((0.0, 0.0, -1.0), (-1.0, 0.0, 0.0), ("-x", "y")),
}

Polyline = list[tuple[float, float]]


@dataclass
class View:
    """One projection: polylines in the view plane, in mm."""

    name: str
    visible: list[Polyline] = field(default_factory=list)
    hidden: list[Polyline] = field(default_factory=list)

    def bounds(self) -> tuple[float, float, float, float] | None:
        points = [p for line in self.visible + self.hidden for p in line]
        if not points:
            return None
        xs, ys = zip(*points, strict=False)
        return min(xs), min(ys), max(xs), max(ys)


_PROJECTIONS: dict[tuple, dict[str, View]] = {}


def _faces(shape) -> int:
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer

    explorer, count = TopExp_Explorer(shape, TopAbs_FACE), 0
    while explorer.More():
        count += 1
        explorer.Next()
    return count


def _polylines(compound, deflection: float) -> list[Polyline]:
    """Every edge of a projected compound, discretized. The edges lie in the view plane."""
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.GCPnts import GCPnts_QuasiUniformDeflection
    from OCP.TopAbs import TopAbs_EDGE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    if compound is None or compound.IsNull():
        return []
    lines: list[Polyline] = []
    explorer = TopExp_Explorer(compound, TopAbs_EDGE)
    while explorer.More():
        edge = TopoDS.Edge(explorer.Current())
        explorer.Next()
        try:
            curve = BRepAdaptor_Curve(edge)
        except Exception:  # an edge with no 3D curve; nothing to draw
            continue
        sampler = GCPnts_QuasiUniformDeflection(curve, deflection)
        if not sampler.IsDone() or sampler.NbPoints() < 2:
            continue
        line = []
        for index in range(1, sampler.NbPoints() + 1):
            point = sampler.Value(index)
            line.append((round(point.X(), 5), round(point.Y(), 5)))
        lines.append(line)
    return lines


def project(shape, name: str, *, hidden: bool = True, deflection: float | None = None) -> View:
    """One orthographic view of a shape placed in the component frame."""
    occ.load()
    from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt
    from OCP.HLRAlgo import HLRAlgo_Projector

    toward, x_axis, _axes = VIEWS[name]
    projector = HLRAlgo_Projector(gp_Ax2(gp_Pnt(0, 0, 0), gp_Dir(*toward), gp_Dir(*x_axis)))
    from .measure import bbox

    box = bbox(shape)
    size = max((h - lo for lo, h in zip(*box, strict=False)), default=1.0) if box else 1.0
    deflection = deflection or max(0.0005, size / 2000)

    if _faces(shape) <= EXACT_FACES:
        from OCP.HLRBRep import HLRBRep_Algo, HLRBRep_HLRToShape

        algo = HLRBRep_Algo()
        algo.Add(shape)
        algo.Projector(projector)
        algo.Update()
        algo.Hide()
        result = HLRBRep_HLRToShape(algo)
    else:
        from OCP.BRepMesh import BRepMesh_IncrementalMesh
        from OCP.HLRBRep import HLRBRep_PolyAlgo, HLRBRep_PolyHLRToShape

        BRepMesh_IncrementalMesh(shape, max(0.005, size / 500), False, 0.3, False)
        algo = HLRBRep_PolyAlgo()
        algo.Load(shape)
        algo.Projector(projector)
        algo.Update()
        result = HLRBRep_PolyHLRToShape()
        result.Update(algo)

    view = View(name)
    view.visible = _polylines(result.VCompound(), deflection) + _polylines(result.OutLineVCompound(), deflection)
    if hidden:
        view.hidden = _polylines(result.HCompound(), deflection) + _polylines(result.OutLineHCompound(), deflection)
    return view


def _key(model) -> tuple | None:
    fingerprint = getattr(model, "fingerprint", None)
    if fingerprint is None:
        return None
    return (fingerprint.frame_key, fingerprint.brep_key, fingerprint.seat)


def views(model, *, hidden: bool = True) -> dict[str, View]:
    """All three views of a model (a `split.Model` or `Component`), cached by fingerprint."""
    model = getattr(model, "model", None) or model
    key = _key(model)
    cache_key = (key, hidden) if key else None
    if cache_key and cache_key in _PROJECTIONS:
        return _PROJECTIONS[cache_key]
    found = {name: project(model.shape, name, hidden=hidden) for name in VIEWS}
    if cache_key:
        _PROJECTIONS[cache_key] = found
    return found


# ─── SVG ─────────────────────────────────────────────────────────────────────

_STYLE = """
<style>
svg.boarddd-step{--_line:#1f2328;--_hidden:#8c959f;--_dim:#57606a;--_text:#1f2328;--_bg:#ffffff;
 --_old:#cf222e;--_new:#0969da;--_same:#afb8c1;--_pad:#d4a72c;--_pad-fill:rgba(212,167,44,.25)}
@media (prefers-color-scheme: dark){svg.boarddd-step{--_line:#e6edf3;--_hidden:#6e7681;--_dim:#8b949e;
 --_text:#e6edf3;--_bg:#0d1117;--_old:#ff7b72;--_new:#79c0ff;--_same:#484f58;--_pad:#e3b341;
 --_pad-fill:rgba(227,179,65,.22)}}
.bg{fill:var(--boarddd-step-bg,var(--_bg))}
.vis{stroke:var(--boarddd-step-line,var(--_line));stroke-width:1.3;fill:none;stroke-linecap:round;stroke-linejoin:round}
.hid{stroke:var(--boarddd-step-hidden,var(--_hidden));stroke-width:.8;fill:none;stroke-dasharray:4 3}
.dim{stroke:var(--boarddd-step-dim,var(--_dim));stroke-width:.8;fill:none}
.board{stroke:var(--boarddd-step-dim,var(--_dim));stroke-width:.8;stroke-dasharray:8 3 2 3;fill:none}
.old{stroke:var(--boarddd-step-old,var(--_old));fill:none;stroke-linecap:round}
.new{stroke:var(--boarddd-step-new,var(--_new));fill:none;stroke-linecap:round}
.same{stroke:var(--boarddd-step-same,var(--_same));stroke-width:1;fill:none}
.only{stroke-width:2.6}
.pad{stroke:var(--boarddd-step-pad,var(--_pad));fill:var(--boarddd-step-pad-fill,var(--_pad-fill));stroke-width:1}
.contact{stroke:var(--boarddd-step-new,var(--_new));fill:none;stroke-width:1.6}
.fitted{stroke:var(--boarddd-step-old,var(--_old));fill:none;stroke-width:1.2;stroke-dasharray:5 3}
text{fill:var(--boarddd-step-text,var(--_text));font:12px system-ui,-apple-system,"Segoe UI",sans-serif}
text.small{font-size:11px}
text.title{font-size:14px;font-weight:600}
text.differs{fill:var(--boarddd-step-new,var(--_new));font-weight:600}
.swatch-old{fill:var(--boarddd-step-old,var(--_old))}.swatch-new{fill:var(--boarddd-step-new,var(--_new))}
.swatch-same{fill:var(--boarddd-step-same,var(--_same))}
</style>"""


def _nice(value: float) -> float:
    """A round length near `value`: 1, 2 or 5 times a power of ten."""
    if value <= 0:
        return 1.0
    power = 10 ** math.floor(math.log10(value))
    for step in (1, 2, 5, 10):
        if step * power >= value:
            return step * power
    return 10 * power


def _fmt(value: float) -> str:
    return f"{value:.2f}" if abs(value) < 100 else f"{value:.1f}"


class _Canvas:
    """Panels laid out in px, with a mm -> px mapping per panel."""

    def __init__(self):
        self.parts: list[str] = []

    def add(self, text: str) -> None:
        self.parts.append(text)


@dataclass
class _Panel:
    name: str
    x: float  # px of the panel's left edge
    y: float  # px of its top edge
    lo: tuple[float, float]
    hi: tuple[float, float]
    k: float  # px per mm

    def to_px(self, point) -> tuple[float, float]:
        return (
            round(self.x + (point[0] - self.lo[0]) * self.k, 2),
            round(self.y + (self.hi[1] - point[1]) * self.k, 2),
        )

    @property
    def width(self) -> float:
        return (self.hi[0] - self.lo[0]) * self.k

    @property
    def height(self) -> float:
        return (self.hi[1] - self.lo[1]) * self.k


def _simplified(points: list, tolerance: float = 0.25) -> list:
    """Douglas-Peucker in px: curves sampled finely in mm need far fewer points on screen."""
    if len(points) <= 2:
        return points
    from shapely.geometry import LineString

    return list(LineString(points).simplify(tolerance).coords)


def _path(panel: _Panel, lines, css: str) -> str:
    if not lines:
        return ""
    data = []
    for line in lines:
        points = _simplified([panel.to_px(p) for p in line])
        data.append("M" + " L".join(f"{x},{y}" for x, y in points))
    return f'<path class="{css}" d="{" ".join(data)}"/>'


def _extent3(box) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    low, high = box
    return tuple(low), tuple(high)


def _layout(low, high, *, px: float = VIEW_PX, margin: float = 56.0, top_space: float = 62.0):
    """Top view above the front view, right side right of the front, bottom right of the top."""
    size = [max(h - lo, 1e-3) for lo, h in zip(low, high, strict=False)]
    k = px / max(size)
    x0, y0 = margin, top_space + 18
    top = _Panel("top", x0, y0, (low[0], low[1]), (high[0], high[1]), k)
    front = _Panel("front", x0, y0 + size[1] * k + margin, (low[0], low[2]), (high[0], high[2]), k)
    right = x0 + size[0] * k + margin
    side = _Panel("side", right, front.y, (low[1], low[2]), (high[1], high[2]), k)
    bottom = _Panel("bottom", right, y0, (-high[0], low[1]), (-low[0], high[1]), k)
    width = right + max(size[0], size[1]) * k + margin
    height = front.y + size[2] * k + margin
    return {"top": top, "bottom": bottom, "front": front, "side": side}, k, width, height


def _dimensions(canvas: _Canvas, panels: dict[str, _Panel], low, high) -> None:
    """Width under the top view, depth right of it, height left of the front view."""
    top, front = panels["top"], panels["front"]

    def horizontal(panel, y_px, label):
        x1, x2 = panel.x, panel.x + panel.width
        canvas.add(
            f'<path class="dim" d="M{x1},{y_px} L{x2},{y_px} M{x1},{y_px - 4} L{x1},{y_px + 4} '
            f'M{x2},{y_px - 4} L{x2},{y_px + 4}"/>'
        )
        canvas.add(f'<text x="{(x1 + x2) / 2}" y="{y_px - 5}" text-anchor="middle">{label}</text>')

    def vertical(x_px, y1, y2, label, anchor="end"):
        canvas.add(
            f'<path class="dim" d="M{x_px},{y1} L{x_px},{y2} M{x_px - 4},{y1} L{x_px + 4},{y1} '
            f'M{x_px - 4},{y2} L{x_px + 4},{y2}"/>'
        )
        dx = -6 if anchor == "end" else 6
        canvas.add(f'<text x="{x_px + dx}" y="{(y1 + y2) / 2 + 4}" text-anchor="{anchor}">{label}</text>')

    horizontal(top, top.y - 10, _fmt(high[0] - low[0]))
    vertical(top.x + top.width + 12, top.y, top.y + top.height, _fmt(high[1] - low[1]), "start")
    vertical(front.x - 12, front.y, front.y + front.height, _fmt(high[2] - low[2]))


def _board_line(canvas: _Canvas, panels: dict[str, _Panel], z: float = 0.0) -> None:
    """The board surface (z = 0 of the component frame) across the front and side views."""
    for name in ("front", "side"):
        panel = panels[name]
        if panel.lo[1] - 1e-9 <= z <= panel.hi[1] + 1e-9:
            _x, y = panel.to_px((panel.lo[0], z))
            canvas.add(
                f'<path class="board" d="M{panel.x - 8},{y} L{panel.x + panel.width + 8},{y}">'
                f"<title>board surface</title></path>"
            )


def _labels(canvas: _Canvas, panels: dict[str, _Panel]) -> None:
    names = {
        "top": ("TOP", "Top view, looking down"),
        "front": ("FRONT", "Front view, looking along +Y"),
        "side": ("RIGHT", "Right side view, looking along −X"),
        "bottom": ("BOTTOM ⇄", "Bottom view, flipped left-right"),
    }
    for name, panel in panels.items():
        label, tip = names[name]
        canvas.add(
            f'<text class="small" x="{panel.x}" y="{panel.y + panel.height + 18}"><title>{tip}</title>{label}</text>'
        )


def _scale_bar(canvas: _Canvas, k: float, x: float, y: float) -> None:
    length = _nice(60 / k)
    px = length * k
    canvas.add(
        f'<path class="dim" d="M{x},{y} L{x + px},{y} M{x},{y - 4} L{x},{y + 4} M{x + px},{y - 4} L{x + px},{y + 4}"/>'
    )
    label = f"{length:g} mm"
    canvas.add(f'<text class="small" x="{x + px + 6}" y="{y + 4}">{label}</text>')


def _svg(canvas: _Canvas, width: float, height: float, title: str) -> str:
    body = "\n".join(part for part in canvas.parts if part)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" class="boarddd-step" '
        f'viewBox="0 0 {round(width)} {round(height)}" width="{round(width)}" height="{round(height)}" '
        f'role="img" aria-label="{html.escape(title)}">{_STYLE}\n'
        f'<rect class="bg" width="100%" height="100%"/>\n{body}\n</svg>\n'
    )


def _box_of(found: dict[str, View]):
    """The model's 3D box, from the top and front views (they cover x, y and z)."""
    top, front = found["top"].bounds(), found["front"].bounds()
    if top is None or front is None:
        return None
    return (top[0], top[1], front[1]), (top[2], top[3], front[3])


def _cached(cache_dir, key_parts, model=None) -> tuple[Path | None, str | None]:
    if cache_dir is None and model is not None and getattr(model, "store", None) is not None:
        cache_dir = model.store.root / "drawings"  # the model cache keeps drawings too
    if cache_dir is None:
        return None, None
    digest = hashlib.sha256(repr(key_parts).encode()).hexdigest()[:20]
    path = Path(cache_dir) / f"{digest}.svg"
    return path, (path.read_text() if path.exists() else None)


def remember(model, svg: str) -> None:
    """Keep a model's default drawing (made elsewhere, e.g. in a worker) where `drawing` finds it."""
    model = getattr(model, "model", None) or model
    path, _ready = _cached(None, ("drawing", _key(model), "", True), model)
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            temporary = path.with_name(f".{path.name}.{os.getpid()}")
            temporary.write_text(svg)
            os.replace(temporary, path)
    else:
        model._drawing = svg


def drawing(model, *, title: str = "", hidden: bool = True, cache_dir=None) -> str:
    """Three views of one model, with its bounding-box dimensions and a scale bar."""
    model = getattr(model, "model", None) or model
    if not title and hidden and cache_dir is None and getattr(model, "_drawing", None):
        return model._drawing
    path, ready = _cached(cache_dir, ("drawing", _key(model), title, hidden), model)
    if ready:
        return ready
    found = views(model, hidden=hidden)
    box = _box_of(found)
    title = title or getattr(model, "product", "") or "model"
    if box is None:
        canvas = _Canvas()
        canvas.add(f'<text class="title" x="16" y="24">{html.escape(title)}: no geometry</text>')
        return _svg(canvas, 400, 60, title)
    low, high = box
    panels, k, width, height = _layout(low, high)
    canvas = _Canvas()
    canvas.add(f'<text class="title" x="16" y="22">{html.escape(title)}</text>')
    size = " × ".join(_fmt(h - lo) for lo, h in zip(low, high, strict=False))
    canvas.add(
        f'<text class="small" x="16" y="40"><title>W × D × H, component frame; '
        f"z = 0 is the board surface</title>{size} mm</text>"
    )
    for name, panel in panels.items():
        canvas.add(_path(panel, found[name].hidden, "hid"))
        canvas.add(_path(panel, found[name].visible, "vis"))
    _board_line(canvas, panels)
    _dimensions(canvas, panels, low, high)
    _labels(canvas, panels)
    _scale_bar(canvas, k, max(width, 420) - 150, 22)
    svg = _svg(canvas, max(width, 420), height, title)
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(svg)
    return svg


# ─── Overlay diff ────────────────────────────────────────────────────────────


def _split_lines(a: list[Polyline], b: list[Polyline], tolerance: float):
    """(a only, shared, b only) as polylines: where each model's edges are not near the other's."""
    from shapely.geometry import LineString, MultiLineString

    def lines(polylines):
        return MultiLineString([LineString(line) for line in polylines if len(line) >= 2])

    def out(geometry) -> list[Polyline]:
        if geometry.is_empty:
            return []
        parts = getattr(geometry, "geoms", [geometry])
        result = []
        for part in parts:
            if part.geom_type == "LineString" and part.length > tolerance:
                result.append(list(part.coords))
            elif hasattr(part, "geoms"):
                result.extend(out(part))
        return result

    first, second = lines(a), lines(b)
    if first.is_empty or second.is_empty:
        return a, [], b
    near_first, near_second = first.buffer(tolerance), second.buffer(tolerance)
    return (
        out(first.difference(near_second)),
        out(first.intersection(near_second)),
        out(second.difference(near_first)),
    )


def _row(label, old, new, unit="", tolerance=0.01):
    """A table row; `changed` when the values differ by more than `tolerance` (mm), or by more
    than `tolerance` as a share for unitless quantities."""
    if old is None or new is None:
        return label, old, new, None, old != new
    delta = new - old
    limit = tolerance if unit == "mm" else tolerance * max(abs(old), abs(new), 1e-12)
    return label, old, new, delta, abs(delta) > limit


def _table_rows(old, new) -> list[tuple]:
    """(label, tooltip, old, new, delta, changed) for the dimension table."""
    a, b = old.measurements, new.measurements
    rows = []

    def add(label, tip, *values, **options):
        name, first, second, delta, changed = _row(label, *values, **options)
        rows.append((name, tip, first, second, delta, changed))

    for index, (axis, word) in enumerate((("W", "width (X)"), ("D", "depth (Y)"), ("H", "height (Z)"))):
        add(f"{axis} mm", f"bounding box {word}", a.size[index], b.size[index], "mm")
    add("H↑ mm", "top above the substrate, including the model lift (H is the model itself)", a.height, b.height, "mm")
    add("V mm³", "volume", a.volume, b.volume, "", 0.005)
    add("A mm²", "surface area", a.area, b.area, "", 0.005)
    rows.append(("faces", "B-rep faces", a.faces, b.faces, None, a.faces != b.faces))
    return rows


def _cell(value) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return _fmt(value) if abs(value) >= 0.01 or value == 0 else f"{value:.4f}"
    return html.escape(str(value))


def overlay(old, new, *, labels: tuple[str, str] = ("standard", "new"), title: str = "", cache_dir=None) -> str:
    """
    Two models of one part in one frame: shared edges muted, each model's own edges in its
    color and drawn heavier, and a table of the dimensions that matter with differences marked.
    """
    old = getattr(old, "model", None) or old
    new = getattr(new, "model", None) or new
    path, ready = _cached(cache_dir, ("overlay", _key(old), _key(new), labels, title), new)
    if ready:
        return ready
    first, second = views(old, hidden=False), views(new, hidden=False)
    boxes = [box for box in (_box_of(first), _box_of(second)) if box]
    title = title or getattr(new, "product", "") or "models"
    if not boxes:
        canvas = _Canvas()
        canvas.add(f'<text class="title" x="16" y="24">{html.escape(title)}: no geometry</text>')
        return _svg(canvas, 400, 60, title)
    low = tuple(min(box[0][i] for box in boxes) for i in range(3))
    high = tuple(max(box[1][i] for box in boxes) for i in range(3))
    panels, k, width, height = _layout(low, high, top_space=72)
    canvas = _Canvas()
    canvas.add(f'<text class="title" x="16" y="22">{html.escape(title)}</text>')
    legend_y = 42
    for x, css, label, tip in (
        (16, "old", labels[0], f"edges only {labels[0]} has"),
        (150, "new", labels[1], f"edges only {labels[1]} has"),
        (284, "same", "=", "edges both have"),
    ):
        canvas.add(
            f'<g><title>{html.escape(tip)}</title><rect class="swatch-{css}" x="{x}" '
            f'y="{legend_y - 9}" width="14" height="6" rx="2"/><text class="small" x="{x + 20}" '
            f'y="{legend_y}">{html.escape(label)}</text></g>'
        )
    tolerance = max(0.01, 1.5 / k)
    differs = False
    for name, panel in panels.items():
        only_old, shared, only_new = _split_lines(first[name].visible, second[name].visible, tolerance)
        differs = differs or bool(only_old or only_new)
        canvas.add(_path(panel, shared, "same"))
        canvas.add(_path(panel, only_old, "old only"))
        canvas.add(_path(panel, only_new, "new only"))
    _board_line(canvas, panels)
    _dimensions(canvas, panels, low, high)
    _labels(canvas, panels)
    _scale_bar(canvas, k, max(width, 440) - 150, 22)
    if not differs:
        canvas.add(
            f'<text class="small" x="16" y="{legend_y + 16}"><title>no visible difference at '
            f"this scale</title>✓ Δ 0</text>"
        )

    # The dimension table, under the views.
    rows = _table_rows(old, new) if old.measurements and new.measurements else []
    y = height
    columns = (16, 130, 230, 330)
    canvas.add(
        f'<text class="small" x="{columns[1]}" y="{y}">{html.escape(labels[0])}</text>'
        f'<text class="small" x="{columns[2]}" y="{y}">{html.escape(labels[1])}</text>'
        f'<text class="small" x="{columns[3]}" y="{y}">Δ</text>'
    )
    for label, tip, a, b, delta, changed in rows:
        y += 18
        css = ' class="differs"' if changed else ""
        mark = "● " if changed else ""
        canvas.add(
            f'<text{css} x="{columns[0]}" y="{y}"><title>{html.escape(tip)}</title>'
            f"{mark}{html.escape(label)}</text>"
            f'<text{css} x="{columns[1]}" y="{y}">{_cell(a)}</text>'
            f'<text{css} x="{columns[2]}" y="{y}">{_cell(b)}</text>'
            f'<text{css} x="{columns[3]}" y="{y}">{_cell(delta) if delta is not None else ""}</text>'
        )
    svg = _svg(canvas, max(width, 440), y + 24, title)
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(svg)
    return svg
