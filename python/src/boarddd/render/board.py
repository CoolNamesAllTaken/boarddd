"""A board drawn from its model: the outline, every placed footprint's copper pads, and the drills, one side at a
time, as SVG. For thumbnails and pages without a browser renderer, and as the board map of a review page: each
component is an ``<a>`` (class ``bm-pl st-<state>``, ``data-ref``) that a page can colour by state, link and
highlight.

Frames: the board model's (mm, y up; docs/model.md). Footprint pads are placed with the model's placement
transform. The bottom side is drawn as seen from below, mirrored left to right. The SVG's user unit is the
millimetre (viewBox in mm, origin at the drawing's top-left corner, margin ``MARGIN_MM``).

Colours are CSS variables with fallbacks (``--bm-board``, ``--bm-edge``, ``--bm-pad``, ``--bm-drill``,
``--bm-state-<state>``, ``--bm-highlight``); ``board.stackup.mask_color`` picks the default board colour.

Source: the layout half of magpie ``django/boardmap.py`` (``geometry``, ``board_svg``) at internal
``claud/magpie`` ``3a0374d3`` (boarddd phase F5), reworked onto boarddd's Board: magpie read placements and
pad outlines out of its review database; here they come from the model, and the request-time state filling
is plain arguments.
"""

from __future__ import annotations

import html
import math

from .. import model as m
from ..io.kicad.geom import pad_copper

__all__ = ["board_svg", "board_bounds", "STYLE", "MASK_COLOURS", "MARGIN_MM"]

#: Margin around the board, mm.
MARGIN_MM = 2.0
#: A component with no copper pads in the model shows as a square this size, mm.
NO_PADS_MM = 1.0

#: Solder mask colour names (KiCad's stackup names) -> board fill.
MASK_COLOURS = {
    "green": "#1d5a2c",
    "red": "#8a1c1c",
    "blue": "#1b3a78",
    "black": "#161616",
    "white": "#e6e6e6",
    "yellow": "#b8a21c",
    "purple": "#4b2b6b",
    "matte black": "#161616",
    "matte green": "#1d5a2c",
}

STYLE = """
.bm-board{fill:var(--bm-board,%(board)s);stroke:var(--bm-edge,#d8d8d8);stroke-width:0.15;fill-rule:evenodd}
.bm-pl{fill:var(--bm-pad,#c8a14a)}
.bm-hit{fill:transparent}
.bm-drill{fill:var(--bm-drill,#111)}
.bm-pl.st-approved{fill:var(--bm-state-approved,#3fa34d)}
.bm-pl.st-candidate{fill:var(--bm-state-candidate,#3a8ad0)}
.bm-pl.st-open{fill:var(--bm-state-open,#e09000)}
.bm-pl.st-rejected{fill:var(--bm-state-rejected,#d04040)}
.bm-pl.st-unresolved{fill:var(--bm-state-unresolved,#9a9a9a)}
.bm-pl.hl{fill:var(--bm-highlight,#ff2d95)}
"""


def _f(v: float) -> str:
    return f"{v:.3f}".rstrip("0").rstrip(".") or "0"


def _place(c: m.Component, q) -> tuple[float, float]:
    """docs/model.md: footprint point (KiCad footprint frame, y down) -> board frame."""
    qy = q[1] if c.side == "bottom" else -q[1]
    a = math.radians(c.rotation)
    return (c.x + math.cos(a) * q[0] - math.sin(a) * qy, c.y + math.sin(a) * q[0] + math.cos(a) * qy)


def board_bounds(board: m.Board) -> tuple[float, float, float, float]:
    """(min_x, min_y, max_x, max_y): the outline's, else the placements' with a margin."""
    if board.outline is not None and board.outline.board:
        xs = [p[0] for p in board.outline.board]
        ys = [p[1] for p in board.outline.board]
        return min(xs), min(ys), max(xs), max(ys)
    if not board.components:
        return 0.0, 0.0, 10.0, 10.0
    xs, ys = [c.x for c in board.components], [c.y for c in board.components]
    return min(xs) - MARGIN_MM, min(ys) - MARGIN_MM, max(xs) + MARGIN_MM, max(ys) + MARGIN_MM


def _copper(pad: m.Pad) -> bool:
    return pad.type != "np_thru_hole" and any(layer.endswith(".Cu") for layer in pad.layers)


def board_svg(
    board: m.Board,
    side: str = "top",
    *,
    states: dict[str, str] | None = None,
    titles: dict[str, str] | None = None,
    links: dict[str, str] | None = None,
    highlight: str = "",
    px_per_mm: float | None = None,
    drills: bool = True,
) -> str:
    """The board seen from `side` ('top' or 'bottom').

    `states`: ref -> state (class ``st-<state>``); `titles`: ref -> tooltip (default: ref, value, footprint);
    `links`: ref -> href; `highlight`: a ref drawn in the highlight colour. `px_per_mm` sets width/height
    attributes (default: none, the SVG scales to its container). `drills`: draw the holes.
    """
    if side not in ("top", "bottom"):
        raise ValueError("side is 'top' or 'bottom'")
    min_x, min_y, max_x, max_y = board_bounds(board)
    width, height = max_x - min_x, max_y - min_y
    mirror = side == "bottom"  # seen from below

    def sx(x: float) -> float:
        return (max_x - x) if mirror else (x - min_x)

    def pt(p) -> str:
        return f"{_f(sx(p[0]))},{_f(max_y - p[1])}"

    mask = (getattr(board.stackup.mask_color, side) or "").strip().lower()
    style = STYLE % {"board": MASK_COLOURS.get(mask, mask if mask.startswith("#") else "#1d5a2c")}
    parts = [f"<style>{style}</style>"]
    if board.outline is not None and board.outline.board:
        loops = [board.outline.board, *board.outline.cutouts]
        d = "".join("M" + "L".join(pt(p).replace(",", " ") for p in loop) + "Z" for loop in loops)
        parts.append(f'<path class="bm-board" d="{d}"/>')
    else:
        parts.append(
            f'<rect class="bm-board bm-guess" x="0" y="0" width="{_f(width)}" height="{_f(height)}">'
            "<title>outline unknown: placements box</title></rect>"
        )
    for c in sorted(board.components, key=lambda c: c.ref):
        if c.side != side:
            continue
        fp = board.footprints.get(c.footprint or "")
        shapes, xs, ys = [], [], []
        for pad in fp.pads if fp is not None else []:
            if not _copper(pad):
                continue
            for loop in pad_copper(pad, 6):
                placed = [_place(c, q) for q in loop]
                xs += [sx(x) for x, _y in placed]
                ys += [max_y - y for _x, y in placed]
                shapes.append(f'<polygon points="{" ".join(pt(p) for p in placed)}"/>')
        if shapes:
            # the whole part answers the pointer, not only its pads (a chip's middle is bare)
            shapes.insert(
                0,
                f'<rect class="bm-hit" x="{_f(min(xs))}" y="{_f(min(ys))}" '
                f'width="{_f(max(xs) - min(xs))}" height="{_f(max(ys) - min(ys))}"/>',
            )
        else:
            half = NO_PADS_MM / 2
            shapes.append(
                f'<rect x="{_f(sx(c.x) - half)}" y="{_f(max_y - c.y - half)}" width="{_f(NO_PADS_MM)}" '
                f'height="{_f(NO_PADS_MM)}" rx="0.15"/>'
            )
        state = (states or {}).get(c.ref)
        classes = (
            "bm-pl" + (f" st-{html.escape(state, quote=True)}" if state else "") + (" hl" if c.ref == highlight else "")
        )
        title = (titles or {}).get(c.ref) or " · ".join(v for v in (c.ref, c.value, c.footprint) if v)
        href = (links or {}).get(c.ref)
        attrs = f' href="{html.escape(href, quote=True)}"' if href else ""
        parts.append(
            f'<a class="{classes}" data-ref="{html.escape(c.ref, quote=True)}"'
            + (f' data-state="{html.escape(state, quote=True)}"' if state else "")
            + f"{attrs}><title>{html.escape(title)}</title><g>{''.join(shapes)}</g></a>"
        )
    if drills:
        holes = []
        for d in board.drills:
            r = max(d.diameter, 0.1) / 2
            if d.x2 is None or d.y2 is None:
                holes.append(f'<circle cx="{_f(sx(d.x))}" cy="{_f(max_y - d.y)}" r="{_f(r)}"/>')
            else:
                holes.append(
                    f'<path d="M{pt((d.x, d.y)).replace(",", " ")}L{pt((d.x2, d.y2)).replace(",", " ")}" '
                    f'stroke="var(--bm-drill,#111)" stroke-width="{_f(2 * r)}" stroke-linecap="round" fill="none"/>'
                )
        if holes:
            parts.append(f'<g class="bm-drill">{"".join(holes)}</g>')
    size = (
        f' width="{_f((width + 2 * MARGIN_MM) * px_per_mm)}" height="{_f((height + 2 * MARGIN_MM) * px_per_mm)}"'
        if px_per_mm
        else ""
    )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" class="bm-svg" data-side="{side}" '
        f'viewBox="{_f(-MARGIN_MM)} {_f(-MARGIN_MM)} {_f(width + 2 * MARGIN_MM)} {_f(height + 2 * MARGIN_MM)}"{size} '
        f'preserveAspectRatio="xMidYMid meet" role="img" aria-label="{html.escape(board.name)}, {side} side">'
        f"{''.join(parts)}</svg>"
    )
