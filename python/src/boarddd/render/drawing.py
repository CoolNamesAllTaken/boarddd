"""Review drawings of footprints: one footprint, or two versions overlaid.

SVG, y up in the drawing (flipped once in the outer group), sized by `width` with the footprint scaled to fit.
Every colour is a CSS variable with a fallback, so the page sets the theme:

    --fp-pad, --fp-pad-stroke, --fp-paste, --fp-text, --fp-courtyard, --fp-body, --fp-pin1,
    --fp-origin, --fp-dim, --fp-old, --fp-new, --fp-changed, --fp-bg

Text is drawn upright and sized in pixels of the final drawing, so labels stay readable at ~400 px whatever
the footprint's size. Input is boarddd's ``model.Footprint`` (KiCad footprint frame, y down; see docs/model.md),
e.g. from ``boarddd.io.kicad.read_kicad_mod`` or ``Board.footprints``.

Source: magpie ``footprint/svg.py`` at internal ``claud/magpie`` ``3a0374d3`` (boarddd phase F5), ported from
magpie's footprint model to boarddd's. magpie's version identity (``footprint.identity``: canonical frames,
turn detection, pin-1 evidence) stays in magpie; here pads are paired by number, then by position, and pin 1
is whatever the caller names. Compared with magpie's own drawings in python/tests/render/test_render_drawing.py.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from html import escape

from .. import model as m
from ..io.kicad.geom import pad_copper, pad_offset, rotate

__all__ = ["footprint_svg", "diff_svg", "footprint_diff", "FootprintDiff", "STYLE", "scaled_style"]

STYLE = """
.fp-bg{fill:var(--fp-bg,transparent)}
.fp-pad{fill:var(--fp-pad,#c8a14a);stroke:var(--fp-pad-stroke,#8a6d2a);stroke-width:1px;vector-effect:non-scaling-stroke}
.fp-paste{fill:none;stroke:var(--fp-paste,#7a7a7a);stroke-width:1px;stroke-dasharray:3 2;vector-effect:non-scaling-stroke}
.fp-drill{fill:var(--fp-bg,#fff);stroke:var(--fp-pad-stroke,#8a6d2a);stroke-width:1px;vector-effect:non-scaling-stroke}
.fp-court{fill:none;stroke:var(--fp-courtyard,#d040d0);stroke-width:1px;vector-effect:non-scaling-stroke}
.fp-body{fill:none;stroke:var(--fp-body,#6080a0);stroke-width:1px;vector-effect:non-scaling-stroke}
.fp-pin1{fill:var(--fp-pin1,#e03030)}
.fp-origin{stroke:var(--fp-origin,#2a8a2a);stroke-width:1.5px;vector-effect:non-scaling-stroke}
.fp-dim{stroke:var(--fp-dim,#3070c0);fill:none;stroke-width:1px;vector-effect:non-scaling-stroke}
.fp-text{fill:var(--fp-text,#222);font:600 11px system-ui,sans-serif;text-anchor:middle;dominant-baseline:central}
.fp-dimtext{fill:var(--fp-dim,#3070c0);font:11px system-ui,sans-serif;text-anchor:middle}
.fp-old{fill:none;stroke:var(--fp-old,#d04040);stroke-width:2px;stroke-dasharray:4 3;vector-effect:non-scaling-stroke}
.fp-new{fill:var(--fp-new,#3a8ad0);fill-opacity:.35;stroke:var(--fp-new,#3a8ad0);stroke-width:1.5px;vector-effect:non-scaling-stroke}
.fp-same{fill:var(--fp-pad,#c8a14a);fill-opacity:.55;stroke:var(--fp-pad-stroke,#8a6d2a);stroke-width:1px;vector-effect:non-scaling-stroke}
.fp-changed{fill:none;stroke:var(--fp-changed,#e09000);stroke-width:3px;vector-effect:non-scaling-stroke}
.fp-legend{fill:var(--fp-text,#222);font:11px system-ui,sans-serif}
"""


def _fmt(v: float) -> str:
    return f"{v:.4f}".rstrip("0").rstrip(".") or "0"


_RULE = re.compile(r"^\.(fp-[\w-]+)\{(.*)\}$")
_PX = re.compile(r"(stroke-width):([\d.]+)px")
_DASH = re.compile(r"(stroke-dasharray):([\d. ]+)")


def scaled_style(scale: float, style: str = STYLE) -> str:
    """Rules for the mm-scaled group (class ``fp-mm``): every pixel stroke width and dash divided by `scale` and
    ``vector-effect`` off, so the strokes look the same in a browser and in renderers that do not implement
    ``non-scaling-stroke`` (cairosvg)."""
    out = []
    for line in style.strip().splitlines():
        match = _RULE.match(line)
        if not match or "non-scaling-stroke" not in line:
            continue
        body = _PX.sub(lambda m_: f"{m_.group(1)}:{_fmt(float(m_.group(2)) / scale)}", match.group(2))
        body = _DASH.sub(
            lambda m_: f"{m_.group(1)}:" + " ".join(_fmt(float(v) / scale) for v in m_.group(2).split()), body
        )
        body = body.replace("vector-effect:non-scaling-stroke", "vector-effect:none")
        out.append(f".fp-mm .{match.group(1)}{{{body}}}")
    return "\n".join(out)


# ---------------------------------------------------------------------------------------------------------------------
# the footprint, y up (magpie's drawing frame)


@dataclass
class _Pad:
    """A copper pad in the drawing frame (footprint mm, y up)."""

    number: str
    shape: str  # circle | rect | oval | roundrect | chamfered | polygon
    center: tuple[float, float]  # the copper's centre (shape offset applied)
    size: tuple[float, float]
    rotation: float  # degrees, counter-clockwise
    radius: float = 0.0  # roundrect/oval corner radius
    chamfer: float = 0.0
    chamfered: tuple[str, ...] = ()
    polygons: list[list[tuple[float, float]]] = field(default_factory=list)  # absolute, for polygon shapes
    hole: tuple[float, float, float, float, float] | None = None  # cx, cy, w, h, rotation
    paste: list[list[tuple[float, float]]] = field(default_factory=list)
    #: On a paste layer with no opening of its own: the stencil opening is the copper (drawn dashed).
    paste_as_copper: bool = False

    def corners(self) -> list[tuple[float, float]]:
        if self.polygons:
            return [p for loop in self.polygons for p in loop]
        w, h = self.size[0] / 2, self.size[1] / 2
        c, s = math.cos(math.radians(self.rotation)), math.sin(math.radians(self.rotation))
        return [
            (self.center[0] + x * c - y * s, self.center[1] + x * s + y * c)
            for x, y in ((-w, -h), (w, -h), (w, h), (-w, h))
        ]

    def extent(self) -> tuple[float, float, float, float]:
        if self.shape == "circle":
            r = self.size[0] / 2
            return (self.center[0] - r, self.center[1] - r, self.center[0] + r, self.center[1] + r)
        xs, ys = zip(*self.corners(), strict=True)
        return (min(xs), min(ys), max(xs), max(ys))


def _up(p) -> tuple[float, float]:
    return (p[0] + 0.0, -p[1] + 0.0)


def _copper(pad: m.Pad) -> bool:
    return pad.type != "np_thru_hole" and (not pad.layers or any(layer.endswith(".Cu") for layer in pad.layers))


def _view_pad(pad: m.Pad) -> _Pad:
    x, y, a = pad.at
    ox, oy = rotate(*pad_offset(pad), a)
    center = _up((x + ox, y + oy))
    w, h = pad.size
    shape = pad.shape
    view = _Pad(number=pad.number, shape=shape, center=center, size=(w, h), rotation=a)
    if shape == "roundrect" and pad.chamfer:
        shape = "chamfered_rect"
    if shape == "roundrect":
        view.radius = (pad.roundrect_rratio if pad.roundrect_rratio is not None else 0.25) * min(w, h)
    elif shape == "oval":
        view.radius = min(w, h) / 2
    elif shape == "chamfered_rect":
        view.shape = "chamfered"
        view.chamfer = (pad.chamfer_ratio if pad.chamfer_ratio is not None else 0.2) * min(w, h)
        view.chamfered = tuple(pad.chamfer or ())
    if shape in ("trapezoid", "custom"):
        view.shape = "polygon"
        view.polygons = [[_up(p) for p in loop] for loop in pad_copper(pad, 8)]
    if pad.drill is not None:
        dw, dh = pad.drill.size
        view.hole = (*_up((x, y)), dw, dh, a)
    view.paste_as_copper = pad.paste is None and any(layer.endswith(".Paste") for layer in pad.layers)
    for ap in pad.paste or []:
        cx, cy = ap.center
        loop = (
            [(cx + px, cy + py) for px, py in ap.polygon]
            if ap.polygon
            else [
                (cx + sx * ap.size[0] / 2, cy + sy * ap.size[1] / 2) for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1))
            ]
        )
        view.paste.append([_up((x + dx, y + dy)) for dx, dy in (rotate(px, py, a) for px, py in loop)])
    return view


def _graphics(fp: m.Footprint, suffixes: tuple[str, ...]) -> list[list[tuple[float, float]]]:
    out = []
    for g in fp.graphics:
        if g.layer.startswith("F.") and g.layer.split(".", 1)[1] in suffixes:
            pts = [_up(p) for p in g.pts]
            out.append(pts + [pts[0]] if g.closed and pts else pts)
    return out


def _pad_path(pad: _Pad, cls: str) -> str:
    """The pad's outline as an SVG element in footprint mm (y up)."""
    cx, cy = pad.center
    if pad.shape == "circle":
        return f'<circle class="{cls}" cx="{_fmt(cx)}" cy="{_fmt(cy)}" r="{_fmt(pad.size[0] / 2)}"/>'
    if pad.shape == "polygon" and pad.polygons:
        return "".join(
            f'<polygon class="{cls}" points="{" ".join(f"{_fmt(x)},{_fmt(y)}" for x, y in loop)}"/>'
            for loop in pad.polygons
        )
    w, h = pad.size
    if pad.shape == "chamfered" and pad.chamfered:
        c = pad.chamfer
        pts = []
        for corner, (sx, sy) in (
            ("bottom_left", (-1, -1)),
            ("bottom_right", (1, -1)),
            ("top_right", (1, 1)),
            ("top_left", (-1, 1)),
        ):
            x, y = sx * w / 2, sy * h / 2
            if corner in pad.chamfered:
                pts += [(x, y - sy * c), (x - sx * c, y)] if sx * sy > 0 else [(x - sx * c, y), (x, y - sy * c)]
            else:
                pts.append((x, y))
        a = math.radians(pad.rotation)
        pts = [(cx + x * math.cos(a) - y * math.sin(a), cy + x * math.sin(a) + y * math.cos(a)) for x, y in pts]
        return f'<polygon class="{cls}" points="{" ".join(f"{_fmt(x)},{_fmt(y)}" for x, y in pts)}"/>'
    transform = f' transform="rotate({_fmt(pad.rotation)} {_fmt(cx)} {_fmt(cy)})"' if pad.rotation else ""
    return (
        f'<rect class="{cls}" x="{_fmt(cx - w / 2)}" y="{_fmt(cy - h / 2)}" width="{_fmt(w)}" '
        f'height="{_fmt(h)}" rx="{_fmt(pad.radius)}"{transform}/>'
    )


def _bounds(pads: list[_Pad], courtyards: list[list[tuple[float, float]]]):
    xs, ys = [], []
    for p in pads:
        x0, y0, x1, y1 = p.extent()
        xs += [x0, x1]
        ys += [y0, y1]
    for loop in courtyards:
        for x, y in loop:
            xs.append(x)
            ys.append(y)
    if not xs:
        return -1, -1, 1, 1
    return min(xs), min(ys), max(xs), max(ys)


class _Canvas:
    """Footprint mm to pixels: y flipped, uniform scale, a margin for labels."""

    def __init__(self, bounds, width: int, margin: int = 28, extra_bottom: int = 0):
        x0, y0, x1, y1 = bounds
        span = max(x1 - x0, y1 - y0, 0.5)
        self.scale = (width - 2 * margin) / span
        self.width = width
        self.height = int((y1 - y0) * self.scale + 2 * margin + extra_bottom)
        self.x0, self.y1, self.margin = x0, y1, margin
        inner_w = (x1 - x0) * self.scale
        self.dx = margin + (width - 2 * margin - inner_w) / 2

    def px(self, x: float, y: float):
        return self.dx + (x - self.x0) * self.scale, self.margin + (self.y1 - y) * self.scale

    def group(self) -> str:
        """Open a group mapping footprint mm onto the canvas (y up)."""
        return (
            f'<g class="fp-mm" transform="translate({_fmt(self.dx - self.x0 * self.scale)} '
            f'{_fmt(self.margin + self.y1 * self.scale)}) scale({_fmt(self.scale)} {_fmt(-self.scale)})">'
        )


def _pitch(pads: list[_Pad]) -> float | None:
    """Commonest nearest-neighbour distance between pads of one size (rounded to 1 um); a neighbour off to the
    side counts by its step along the row (magpie ``footprint/facts._pitch``)."""
    by_size: dict = {}
    for p in pads:
        by_size.setdefault(tuple(sorted(round(v, 3) for v in p.size)), []).append(p)
    found: Counter = Counter()
    for group in by_size.values():
        if len(group) < 2:
            continue
        xs, ys = [p.center[0] for p in group], [p.center[1] for p in group]
        wide, tall = max(xs) - min(xs), max(ys) - min(ys)
        for p in group:
            q = min((q for q in group if q is not p), key=lambda q, p=p: math.dist(p.center, q.center))
            dx, dy = abs(q.center[0] - p.center[0]), abs(q.center[1] - p.center[1])
            if dx > 1e-3 and dy > 1e-3:
                d = dy if tall > wide + 1e-3 else dx if wide > tall + 1e-3 else min(dx, dy)
            else:
                d = math.hypot(dx, dy)
            if d > 1e-6:
                found[round(d, 3)] += 1
    if not found:
        return None
    top = max(found.values())
    return min(d for d, n in found.items() if n == top)


def _pitch_pair(pads: list[_Pad], pitch: float):
    for i, a in enumerate(pads):
        for b in pads[i + 1 :]:
            if abs(math.dist(a.center, b.center) - pitch) < 1e-3 and tuple(sorted(a.size)) == tuple(sorted(b.size)):
                return a.center, b.center
    return None


def footprint_svg(fp: m.Footprint, *, width: int = 400, title: str = "", pin1: str | None = None) -> str:
    """One footprint: pads with numbers, paste openings, courtyard, fab body, origin, pitch; `pin1` (a pad
    number) gets a dot."""
    pads = [_view_pad(p) for p in fp.pads if _copper(p)]
    courtyards = _graphics(fp, ("CrtYd", "Courtyard"))
    canvas = _Canvas(_bounds(pads, courtyards), width, extra_bottom=18)
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {canvas.height}" '
        f'width="{width}" height="{canvas.height}" role="img" aria-label="{escape(title or fp.name)}">',
        f"<style>{STYLE}{scaled_style(canvas.scale)}\n</style>",
        f'<rect class="fp-bg" width="{width}" height="{canvas.height}"/>',
        canvas.group(),
    ]
    for loop in courtyards:
        out.append(f'<polyline class="fp-court" points="{" ".join(f"{_fmt(x)},{_fmt(y)}" for x, y in loop)}"/>')
    for loop in _graphics(fp, ("Fab",)):
        out.append(f'<polyline class="fp-body" points="{" ".join(f"{_fmt(x)},{_fmt(y)}" for x, y in loop)}"/>')
    for p in pads:
        out.append(_pad_path(p, "fp-pad"))
        if p.hole is not None:
            hx, hy, hw, hh, ha = p.hole
            turn = f' transform="rotate({_fmt(ha)} {_fmt(hx)} {_fmt(hy)})"' if ha else ""
            out.append(
                f'<ellipse class="fp-drill" cx="{_fmt(hx)}" cy="{_fmt(hy)}" rx="{_fmt(hw / 2)}" ry="{_fmt(hh / 2)}"{turn}/>'
            )
        if p.paste_as_copper:
            out.append(_pad_path(p, "fp-paste"))
        for loop in p.paste:
            out.append(f'<polygon class="fp-paste" points="{" ".join(f"{_fmt(x)},{_fmt(y)}" for x, y in loop)}"/>')
    arm = 8 / canvas.scale  # origin cross, a fixed 16 px
    out.append(f'<path class="fp-origin" d="M{_fmt(-arm)} 0H{_fmt(arm)}M0 {_fmt(-arm)}V{_fmt(arm)}"/>')
    out.append("</g>")
    # Labels in pixel space so they stay upright and legible.
    smallest = min((min(p.size) for p in pads), default=1.0) * canvas.scale
    for p in pads:
        if p.number and smallest >= 9 and len(pads) <= 120:
            x, y = canvas.px(*p.center)
            size = max(8, min(11, smallest * 0.7))
            out.append(
                f'<text class="fp-text" x="{_fmt(x)}" y="{_fmt(y)}" style="font-size:{_fmt(size)}px">{escape(p.number)}</text>'
            )
    marked = next((p for p in pads if pin1 is not None and p.number == pin1), None)
    if marked is not None:
        x0, _y0, _x1, y1 = marked.extent()
        x, y = canvas.px(x0, y1)
        out.append(f'<circle class="fp-pin1" cx="{_fmt(x - 5)}" cy="{_fmt(y - 5)}" r="4"><title>pin 1</title></circle>')
    pitch = _pitch(pads) if len(pads) > 2 else None
    pair = _pitch_pair(pads, pitch) if pitch else None
    if pair:
        (ax, ay), (bx, by) = canvas.px(*pair[0]), canvas.px(*pair[1])
        off = 14
        if abs(ay - by) < abs(ax - bx):
            yy = min(ay, by) - off if min(ay, by) - off > 12 else max(ay, by) + off
            out.append(
                f'<path class="fp-dim" d="M{_fmt(ax)} {_fmt(yy)}H{_fmt(bx)}M{_fmt(ax)} {_fmt(yy - 4)}v8M{_fmt(bx)} {_fmt(yy - 4)}v8"/>'
            )
            out.append(f'<text class="fp-dimtext" x="{_fmt((ax + bx) / 2)}" y="{_fmt(yy - 5)}">{_fmt(pitch)} mm</text>')
        else:
            xx = max(ax, bx) + off if max(ax, bx) + off < width - 30 else min(ax, bx) - off
            out.append(
                f'<path class="fp-dim" d="M{_fmt(xx)} {_fmt(ay)}V{_fmt(by)}M{_fmt(xx - 4)} {_fmt(ay)}h8M{_fmt(xx - 4)} {_fmt(by)}h8"/>'
            )
            out.append(
                f'<text class="fp-dimtext" x="{_fmt(xx + 22)}" y="{_fmt((ay + by) / 2 + 4)}">{_fmt(pitch)}</text>'
            )
    out.append(f'<text class="fp-legend" x="8" y="{canvas.height - 6}">{escape(title or fp.name)}</text>')
    out.append("</svg>")
    return "\n".join(out)


# ---------------------------------------------------------------------------------------------------------------------
# two versions


#: Pads closer than this, of the same shape, size and angle, are the same pad.
SAME_MM = 0.001
SAME_DEG = 0.01


@dataclass
class FootprintDiff:
    """What changed between two versions of a footprint's copper."""

    pairs: list[tuple[int, int]] = field(default_factory=list)  # (index in a, index in b), copper pads
    changed: list[tuple[int, int]] = field(default_factory=list)  # paired, but moved, resized or reshaped
    only_a: list[int] = field(default_factory=list)
    only_b: list[int] = field(default_factory=list)

    @property
    def same(self) -> bool:
        return not (self.changed or self.only_a or self.only_b)

    def summary(self) -> str:
        if self.same:
            return f"copper identical ({len(self.pairs)} pads)"
        parts = []
        if self.changed:
            parts.append(f"{len(self.changed)} changed")
        if self.only_a:
            parts.append(f"{len(self.only_a)} removed")
        if self.only_b:
            parts.append(f"{len(self.only_b)} added")
        return ", ".join(parts) + f" of {len(self.pairs) + len(self.only_a)} pads"


def _same_pad(a: _Pad, b: _Pad) -> bool:
    return (
        a.shape == b.shape
        and math.dist(a.center, b.center) <= SAME_MM
        and all(abs(x - y) <= SAME_MM for x, y in zip(a.size, b.size, strict=True))
        and abs((a.rotation - b.rotation + 180) % 360 - 180) <= SAME_DEG
        and abs(a.radius - b.radius) <= SAME_MM
        and (a.hole is None) == (b.hole is None)
        and (
            not a.polygons
            or [[(round(x, 3), round(y, 3)) for x, y in lp] for lp in a.polygons]
            == [[(round(x, 3), round(y, 3)) for x, y in lp] for lp in b.polygons]
        )
    )


def _diff_views(a: list[_Pad], b: list[_Pad]) -> FootprintDiff:
    d = FootprintDiff()
    left, right = set(range(len(a))), set(range(len(b)))
    # by number first (unique numbers only), then the nearest unnumbered pad of the same shape and size
    numbers_a = Counter(p.number for p in a if p.number)
    numbers_b = Counter(p.number for p in b if p.number)
    for i, p in enumerate(a):
        if p.number and numbers_a[p.number] == 1 and numbers_b[p.number] == 1:
            j = next(j for j, q in enumerate(b) if q.number == p.number)
            d.pairs.append((i, j))
            left.discard(i)
            right.discard(j)
    for i in sorted(left):
        candidates = [j for j in right if b[j].number == a[i].number and _same_pad(a[i], b[j])]
        if candidates:
            j = min(candidates, key=lambda j: math.dist(a[i].center, b[j].center))
            d.pairs.append((i, j))
            left.discard(i)
            right.discard(j)
    d.pairs.sort()
    d.changed = [(i, j) for i, j in d.pairs if not _same_pad(a[i], b[j])]
    d.only_a, d.only_b = sorted(left), sorted(right)
    return d


def footprint_diff(old: m.Footprint, new: m.Footprint) -> FootprintDiff:
    """Pair two versions' copper pads (by number, then position) and say which changed."""
    return _diff_views([_view_pad(p) for p in old.pads if _copper(p)], [_view_pad(p) for p in new.pads if _copper(p)])


def diff_svg(
    old: m.Footprint,
    new: m.Footprint,
    d: FootprintDiff | None = None,
    *,
    width: int = 400,
    labels: tuple[str, str] = ("old", "new"),
) -> str:
    """Two versions overlaid: old dashed, new filled, changed pads ringed, with a legend."""
    a = [_view_pad(p) for p in old.pads if _copper(p)]
    b = [_view_pad(p) for p in new.pads if _copper(p)]
    d = d or _diff_views(a, b)
    canvas = _Canvas(
        _bounds(a + b, _graphics(old, ("CrtYd", "Courtyard")) + _graphics(new, ("CrtYd", "Courtyard"))),
        width,
        extra_bottom=44,
    )
    changed_a = {i for i, _ in d.changed}
    changed_b = {j for _, j in d.changed}
    paired_b = {j for _, j in d.pairs}
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {canvas.height}" '
        f'width="{width}" height="{canvas.height}" role="img" aria-label="footprint diff">',
        f"<style>{STYLE}{scaled_style(canvas.scale)}\n</style>",
        f'<rect class="fp-bg" width="{width}" height="{canvas.height}"/>',
        canvas.group(),
    ]
    for j, p in enumerate(b):
        out.append(_pad_path(p, "fp-same" if j in paired_b and j not in changed_b else "fp-new"))
    for i, p in enumerate(a):
        if i in changed_a or i in d.only_a:
            out.append(_pad_path(p, "fp-old"))
    for j, p in enumerate(b):
        if j in changed_b:
            x0, y0, x1, y1 = p.extent()
            pad = 2.5 / canvas.scale
            out.append(
                f'<rect class="fp-changed" x="{_fmt(x0 - pad)}" y="{_fmt(y0 - pad)}" '
                f'width="{_fmt(x1 - x0 + 2 * pad)}" height="{_fmt(y1 - y0 + 2 * pad)}"/>'
            )
    arm = 8 / canvas.scale
    out.append(f'<path class="fp-origin" d="M{_fmt(-arm)} 0H{_fmt(arm)}M0 {_fmt(-arm)}V{_fmt(arm)}"/>')
    out.append("</g>")
    y = canvas.height - 30
    x = 8
    for cls, text in (
        ("fp-same", "unchanged"),
        ("fp-old", labels[0]),
        ("fp-new", labels[1]),
        ("fp-changed", "changed"),
    ):
        out.append(f'<rect class="{cls}" x="{x}" y="{y - 9}" width="14" height="10"/>')
        out.append(f'<text class="fp-legend" x="{x + 18}" y="{y}">{escape(text)}</text>')
        x += 24 + 7 * len(text)
    out.append(f'<text class="fp-legend" x="8" y="{canvas.height - 8}">{escape(d.summary())}</text>')
    out.append("</svg>")
    return "\n".join(out)
