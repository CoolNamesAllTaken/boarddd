"""Geometry the KiCad readers share: arc flattening, stroke loops, pad outlines. Pure, stdlib.

Ported from boarddd's JS (``src/geom/loops.js``, ``src/footprint/kicad_mod.js``, ``src/geom/pads.js``) so that
the Python readers and the JS ``.kicad_mod`` parser flatten arcs, strokes and pads to the same points (the
royalblue54L golden checks this). All coordinates here are KiCad's: mm, y down.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

Pt = tuple[float, float]

NDIGITS = 6


def r(v: float) -> float:
    """Round to 1 nm (KiCad's internal unit) and drop -0."""
    v = round(v, NDIGITS)
    return 0.0 if v == 0 else v


def rp(p: Sequence[float]) -> Pt:
    return (r(p[0]), r(p[1]))


def norm_angle(a: float) -> float:
    """Degrees into (-180, 180]."""
    a = (a + 180.0) % 360.0 - 180.0
    return 180.0 if a == -180.0 else r(a) + 0.0


def rotate(x: float, y: float, deg: float) -> Pt:
    """Rotate a point as KiCad does on screen (y down, positive = counter-clockwise as seen)."""
    if not deg:
        return x, y
    a = math.radians(deg)
    c, s = math.cos(a), math.sin(a)
    return x * c + y * s, -x * s + y * c


# ---------------------------------------------------------------------------------------------------------------------
# loops (src/geom/loops.js)


def segments_for(radius: float) -> int:
    if not radius > 0:
        return 10
    ratio = min(1.0, 0.01 / radius)
    needed = math.ceil(math.pi / math.acos(max(-1.0, 1 - ratio)))
    return max(10, min(48, needed))


def ring_points(cx: float, cy: float, radius: float, segments: int | None = None) -> list[Pt]:
    segments = segments or segments_for(radius)
    return [
        (cx + radius * math.cos(-2 * math.pi * i / segments), cy + radius * math.sin(-2 * math.pi * i / segments))
        for i in range(segments)
    ]


def slot_points(x1: float, y1: float, x2: float, y2: float, radius: float, segments: int | None = None) -> list[Pt]:
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


def stroke_loops(pts: Sequence[Pt], width: float, closed: bool = False) -> list[list[Pt]]:
    n = len(pts) if closed else len(pts) - 1
    return [slot_points(*pts[i], *pts[(i + 1) % len(pts)], width / 2, 16) for i in range(n)]


def arc_through(p1: Pt, pm: Pt, p2: Pt, segments: int = 24) -> list[Pt]:
    """Points along the arc start -> mid -> end; ``segments`` per full turn."""
    (x1, y1), (xm, ym), (x2, y2) = p1, pm, p2
    d = 2 * (x1 * (ym - y2) + xm * (y2 - y1) + x2 * (y1 - ym))
    if abs(d) < 1e-12:
        return [p1, p2]
    s1, sm, s2 = x1 * x1 + y1 * y1, xm * xm + ym * ym, x2 * x2 + y2 * y2
    cx = (s1 * (ym - y2) + sm * (y2 - y1) + s2 * (y1 - ym)) / d
    cy = (s1 * (x2 - xm) + sm * (x1 - x2) + s2 * (xm - x1)) / d
    rad = math.hypot(x1 - cx, y1 - cy)
    a1, am, a2 = (math.atan2(y - cy, x - cx) for x, y in (p1, pm, p2))

    def norm(a: float) -> float:
        return ((a % (2 * math.pi)) + 2 * math.pi) % (2 * math.pi)

    sweep = norm(a2 - a1)
    if norm(am - a1) > sweep:
        sweep -= 2 * math.pi
    n = max(2, math.ceil(segments * abs(sweep) / (2 * math.pi)))
    return [(cx + rad * math.cos(a1 + sweep * i / n), cy + rad * math.sin(a1 + sweep * i / n)) for i in range(n + 1)]


def arc_from_center(center: Pt, start: Pt, angle_deg: float) -> tuple[Pt, Pt, Pt]:
    """KiCad 5 arcs (centre, start, angle clockwise on screen) as (start, mid, end)."""
    cx, cy = center
    sx, sy = start
    rad = math.hypot(sx - cx, sy - cy)
    a0 = math.atan2(sy - cy, sx - cx)
    a = math.radians(angle_deg)
    mid = (cx + rad * math.cos(a0 + a / 2), cy + rad * math.sin(a0 + a / 2))
    end = (cx + rad * math.cos(a0 + a), cy + rad * math.sin(a0 + a))
    return (sx, sy), mid, end


def signed_area(pts: Sequence[Pt]) -> float:
    """Shoelace area: > 0 counter-clockwise in a y-up frame."""
    return sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(pts, [*pts[1:], pts[0]], strict=True)) / 2


# ---------------------------------------------------------------------------------------------------------------------
# pads (src/geom/pads.js): outlines of a boarddd model Pad, KiCad footprint frame


def _corner_rect(w: float, h: float, rad: float = 0.0, cut: float = 0.0, chamfered=(), segments: int = 8) -> list[Pt]:
    rad = max(0.0, min(rad, w / 2, h / 2))
    cut = max(0.0, min(cut, w / 2, h / 2))
    ring: list[Pt] = []
    corners = (
        (-w / 2, -h / 2, "top_left", 180),
        (w / 2, -h / 2, "top_right", 270),
        (w / 2, h / 2, "bottom_right", 0),
        (-w / 2, h / 2, "bottom_left", 90),
    )
    for x, y, name, a0 in corners:
        sx, sy = math.copysign(1, x), math.copysign(1, y)
        if name in chamfered and cut > 1e-9:
            pts = [(x, y - sy * cut), (x - sx * cut, y)]
            ring += pts if name in ("top_left", "bottom_right") else pts[::-1]
        elif rad > 1e-9:
            cx, cy = x - sx * rad, y - sy * rad
            for i in range(segments + 1):
                a = math.radians(a0 + 90 * i / segments)
                ring.append((cx + rad * math.cos(a), cy + rad * math.sin(a)))
        else:
            ring.append((x, y))
    return ring


def trapezoid_corners(w: float, h: float, delta: Sequence[float]) -> list[Pt]:
    """KiCad's trapezoid (pcbnew's effective polygon): dx > 0 makes the left edge dx taller, dy > 0 the bottom
    (y+) edge dy wider."""
    hx, hy, ddx, ddy = w / 2, h / 2, delta[0] / 2, delta[1] / 2
    return [(-hx - ddy, hy + ddx), (-hx + ddy, -hy - ddx), (hx - ddy, -hy + ddx), (hx + ddy, hy - ddx)]


def pad_offset(pad) -> Pt:
    """The copper shape offset, pad-local (KiCad ``(drill (offset))``): ``drill.offset``, or ``offset`` for pads
    without a hole (castellated SMD pads)."""
    if pad.drill is not None:
        return tuple(pad.drill.offset)
    return tuple(pad.offset) if pad.offset is not None else (0.0, 0.0)


def pad_outline(pad, segments: int = 8) -> tuple[list[Pt], list[list[Pt]]]:
    """A Pad's copper in its own frame (before rotation, shape offset applied): (outer, custom primitives)."""
    w, h = pad.size
    shape = pad.shape
    if shape == "custom":
        shape = "circle" if pad.anchor == "circle" else "rect"
    if shape == "circle":
        n = segments * 4
        outer = [(w / 2 * math.cos(2 * math.pi * i / n), w / 2 * math.sin(2 * math.pi * i / n)) for i in range(n)]
    elif shape == "oval":
        outer = _corner_rect(w, h, min(w, h) / 2, segments=segments)
    elif shape in ("roundrect", "chamfered_rect"):
        rr = pad.roundrect_rratio if pad.roundrect_rratio is not None else (0.25 if shape == "roundrect" else 0.0)
        cr = pad.chamfer_ratio if pad.chamfer_ratio is not None else 0.2
        outer = _corner_rect(w, h, min(w, h) * rr, min(w, h) * cr, tuple(pad.chamfer or ()), segments)
    elif shape == "trapezoid":
        outer = trapezoid_corners(w, h, pad.rect_delta or (0.0, 0.0))
    else:
        outer = _corner_rect(w, h, segments=segments)
    extra = [list(p.pts) for p in (pad.primitives or []) if len(p.pts) >= 3]
    ox, oy = pad_offset(pad)
    return [(x + ox, y + oy) for x, y in outer], [[(x + ox, y + oy) for x, y in ring] for ring in extra]


def pad_to_footprint(pad, p: Pt) -> Pt:
    """Pad-local point -> footprint frame, honouring the pad's (at x y angle)."""
    x, y, a = pad.at
    dx, dy = rotate(p[0], p[1], a)
    return (x + dx, y + dy)


def pad_copper(pad, segments: int = 8) -> list[list[Pt]]:
    """The pad's copper loops in the footprint frame (KiCad, y down): [outer, *primitives]."""
    outer, extra = pad_outline(pad, segments)
    return [[pad_to_footprint(pad, q) for q in ring] for ring in (outer, *extra)]
