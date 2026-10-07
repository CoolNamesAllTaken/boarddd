"""
The board's shape, from the Edge_Cuts gerber.

A board outline is not one shape in the file. KiCad emits it as a scatter of separate strokes
-- each rounded corner its own arc, each castellation its own pair of segments -- in no
particular order, and on an export with the drawing sheet plotted the sheet border and title
block are in there too. On one real 14.55 x 23.55 mm board's package that is 2155 separate
strokes.

So the strokes are stitched back into closed loops by matching their endpoints, and the loops
are ranked by area. The largest is the board; anything enclosed by it is a slot or a cutout;
anything else (the drawing sheet, the title block) is bigger than the board and is offered to
the user rather than guessed at, because "the largest loop" is exactly the wrong answer when
somebody plotted a sheet border.

Pure: no settings, no filesystem.

Source: `magpie/pcb/outline.py` at magpie commit 3a0374d3 (copied for boarddd phase F1; magpie's later
changes are re-diffed before magpie switches to this module).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

__all__ = ["Contour", "contours", "TOLERANCE_MM"]

#: Endpoints closer than this are the same point. Gerber coordinates here are integers of
#: 1e-6 mm, so exact matching nearly works; a micron of slack absorbs the arcs whose computed
#: end lands a rounding step from the next stroke's start.
TOLERANCE_MM = 0.002
#: Points per full circle when an arc is flattened. A rounded corner is a quarter of one, so
#: this is a dozen points across it -- enough that the board's silhouette reads as curved both
#: in the flat view and in the 3D extrusion built from the same outline.
_ARC_SEGMENTS = 48

_FORMAT = re.compile(r"%FSLAX(\d)(\d)Y(\d)(\d)\*%")
_UNITS = re.compile(r"%MO(MM|IN)\*%")
#: One drawing operation. G01 line, G02/G03 arc (I/J give the center offset), D01 draw,
#: D02 move. Coordinates are modal: a missing X or Y means "unchanged".
#:
#: At most twelve digits, which is a metre at six decimals with room to spare. A coordinate of
#: four hundred digits is an infinite float; an arc drawn from it has a NaN sweep, and the
#: int() of that was an exception on every read of the board's outline. A line naming one
#: is not an operation this reads.
_OP = re.compile(
    r"^(?:G0([123]))?"
    r"(?:X(-?\d{1,12}))?(?:Y(-?\d{1,12}))?"
    r"(?:I(-?\d{1,12}))?(?:J(-?\d{1,12}))?"
    r"D0([123])\*$"
)

#: The interpolation mode on a line of its own, which is how KiCad writes it: `G03*` and then
#: the coordinates on the next line. Skipping these left the mode at "line" for the whole file,
#: so every arc was drawn as the chord across it -- which is exactly a rounded corner coming
#: out mitred. The reference board is a plain rectangle, so nothing here ever noticed.
_MODE = re.compile(r"^G0([123])\*$")

#: Single-quadrant arcs, where I and J are unsigned and the arc may not exceed a quarter turn.
#: KiCad emits G75 and multi-quadrant offsets, but it is one line to honor the older form and
#: a board from another tool is otherwise silently wrong rather than visibly unsupported.
_QUADRANT = re.compile(r"^G7([45])\*$")


@dataclass(frozen=True)
class Contour:
    """One closed loop, as a list of (x, y) points in millimeters."""

    points: tuple[tuple[float, float], ...]
    area: float
    min_x: float
    min_y: float
    max_x: float
    max_y: float

    @property
    def width(self) -> float:
        return self.max_x - self.min_x

    @property
    def height(self) -> float:
        return self.max_y - self.min_y

    def contains(self, other: Contour) -> bool:
        """Whether this loop's bounding box encloses another's."""
        return (
            self.min_x <= other.min_x
            and self.max_x >= other.max_x
            and self.min_y <= other.min_y
            and self.max_y >= other.max_y
        )

    def as_dict(self) -> dict:
        return {
            "points": [[round(x, 4), round(y, 4)] for x, y in self.points],
            "area": round(self.area, 4),
            "width": round(self.width, 4),
            "height": round(self.height, 4),
            "min_x": round(self.min_x, 4),
            "min_y": round(self.min_y, 4),
            "max_x": round(self.max_x, 4),
            "max_y": round(self.max_y, 4),
        }


def _scale(text: str) -> float:
    match = _FORMAT.search(text)
    decimals = int(match.group(2)) if match else 6
    units = _UNITS.search(text)
    per_unit = 25.4 if units and units.group(1) == "IN" else 1.0
    return per_unit / (10**decimals)


def _arc_points(start, end, center, clockwise) -> list[tuple[float, float]]:
    """An arc as a short chain of points. Sampled, not preserved: this is a silhouette."""
    sx, sy = start
    ex, ey = end
    cx, cy = center
    radius = math.hypot(sx - cx, sy - cy)
    if radius <= 0:
        return [end]

    start_angle = math.atan2(sy - cy, sx - cx)
    end_angle = math.atan2(ey - cy, ex - cx)
    sweep = end_angle - start_angle
    if clockwise:
        while sweep > 0:
            sweep -= 2 * math.pi
        # A full circle arrives as start == end, which reads as a zero sweep.
        if abs(sweep) < 1e-9:
            sweep = -2 * math.pi
    else:
        while sweep < 0:
            sweep += 2 * math.pi
        if abs(sweep) < 1e-9:
            sweep = 2 * math.pi

    steps = max(2, min(_ARC_SEGMENTS, int(abs(sweep) / (2 * math.pi) * _ARC_SEGMENTS) + 2))
    return [
        (
            cx + radius * math.cos(start_angle + sweep * i / steps),
            cy + radius * math.sin(start_angle + sweep * i / steps),
        )
        for i in range(1, steps + 1)
    ]


def _quadrant_center(start, end, i, j, clockwise) -> tuple[float, float]:
    """
    Where the center is when the offsets came without signs.

    In single-quadrant mode I and J are magnitudes, so there are four candidate centers and the
    file does not say which. Only one of them is both equidistant from the two ends -- it is a
    circle, so it has to be -- and reached by a sweep of no more than a quarter turn, which is
    the whole rule that makes the mode work. Ties are impossible in practice and harmless if
    they were: the first is as good as the second.
    """
    sx, sy = start
    ex, ey = end
    best, best_error = (sx + i, sy + j), float("inf")

    for di in (i, -i):
        for dj in (j, -j):
            cx, cy = sx + di, sy + dj
            r_start = math.hypot(sx - cx, sy - cy)
            r_end = math.hypot(ex - cx, ey - cy)
            error = abs(r_start - r_end)
            if error > max(r_start, 1.0) * 1e-3:
                continue

            sweep = math.atan2(ey - cy, ex - cx) - math.atan2(sy - cy, sx - cx)
            if clockwise:
                while sweep > 0:
                    sweep -= 2 * math.pi
            else:
                while sweep < 0:
                    sweep += 2 * math.pi
            # A quarter turn, with room for the rounding that a file written in integers brings.
            if abs(sweep) > math.pi / 2 + 1e-6:
                continue
            if error < best_error:
                best, best_error = (cx, cy), error
    return best


def _strokes(text: str) -> list[list[tuple[float, float]]]:
    """Every drawn path in the file, each as a list of points."""
    scale = _scale(text)
    strokes: list[list[tuple[float, float]]] = []
    current: list[tuple[float, float]] = []
    x = y = 0.0
    mode = 1
    single_quadrant = False

    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith(("%", "G04", "M")):
            continue

        alone = _MODE.match(line)
        if alone:
            mode = int(alone.group(1))
            continue
        quadrant = _QUADRANT.match(line)
        if quadrant:
            single_quadrant = quadrant.group(1) == "4"
            continue

        match = _OP.match(line)
        if not match:
            continue

        g, raw_x, raw_y, raw_i, raw_j, d = match.groups()
        if g:
            mode = int(g)
        new_x = float(raw_x) * scale if raw_x is not None else x
        new_y = float(raw_y) * scale if raw_y is not None else y

        if d == "2":  # pen up: the previous stroke ends
            if len(current) > 1:
                strokes.append(current)
            current = [(new_x, new_y)]
        elif d == "1":  # draw
            if not current:
                current = [(x, y)]
            if mode == 1 or raw_i is None and raw_j is None:
                current.append((new_x, new_y))
            else:
                i = float(raw_i) * scale if raw_i else 0.0
                j = float(raw_j) * scale if raw_j else 0.0
                center = (
                    _quadrant_center((x, y), (new_x, new_y), i, j, mode == 2) if single_quadrant else (x + i, y + j)
                )
                current.extend(_arc_points((x, y), (new_x, new_y), center, mode == 2))
        x, y = new_x, new_y

    if len(current) > 1:
        strokes.append(current)
    return strokes


def _key(point, tolerance) -> tuple[int, int]:
    return (round(point[0] / tolerance), round(point[1] / tolerance))


def _stitch(strokes, tolerance) -> list[list[tuple[float, float]]]:
    """
    Join strokes end to end into closed loops.

    Greedy: take an unused stroke, then keep attaching whichever unused stroke starts or ends
    where the chain currently ends, until it closes or runs out. Good enough for board
    outlines, which are made of strokes that meet exactly.
    """
    ends: dict[tuple[int, int], list[int]] = {}
    for index, stroke in enumerate(strokes):
        for point in (stroke[0], stroke[-1]):
            ends.setdefault(_key(point, tolerance), []).append(index)

    used = [False] * len(strokes)
    loops = []

    for index in range(len(strokes)):
        if used[index]:
            continue
        used[index] = True
        chain = list(strokes[index])

        extended = True
        while extended:
            # A chain that has come back to where it started is a finished loop. Without this
            # it goes on swallowing strokes, and an outline drawn twice in the same layer --
            # which is ordinary, and what real exports do -- closes and then sets off around
            # itself again. The result is one ring of 59 points delivered as 118, enclosing
            # twice its own bounding box, which an even-odd fill reads as inside-out and clips
            # away to nothing.
            if len(chain) > 2 and _key(chain[0], tolerance) == _key(chain[-1], tolerance):
                break

            extended = False
            for candidate in ends.get(_key(chain[-1], tolerance), []):
                if used[candidate]:
                    continue
                stroke = strokes[candidate]
                if _key(stroke[0], tolerance) == _key(chain[-1], tolerance):
                    chain.extend(stroke[1:])
                elif _key(stroke[-1], tolerance) == _key(chain[-1], tolerance):
                    chain.extend(reversed(stroke[:-1]))
                else:
                    continue
                used[candidate] = True
                extended = True
                break

        if len(chain) > 2 and _key(chain[0], tolerance) == _key(chain[-1], tolerance):
            loops.append(chain)
    return loops


def _area(points) -> float:
    total = 0.0
    for (x1, y1), (x2, y2) in zip(points, points[1:] + points[:1], strict=False):
        total += x1 * y2 - x2 * y1
    return abs(total) / 2


def extents(text: str) -> tuple[float, float, float, float] | None:
    """
    The bounding box of everything drawn in a gerber, as (min_x, min_y, max_x, max_y) mm.

    Not a silhouette and not a board: on an export with the drawing sheet plotted this is
    the sheet. It is what the viewer frames when nothing better is known -- a board with no
    outline picked and no placement file is still a board somebody wants to look at.
    """
    # Every coordinate the file names, flashes included: a paste or copper layer is mostly
    # pads, which are D03 flashes and never a stroke. Modal coordinates, as in _strokes.
    scale = _scale(text)
    x = y = 0.0
    xs: list[float] = []
    ys: list[float] = []
    for line in text.splitlines():
        match = _OP.match(line.strip())
        if not match:
            continue
        _, raw_x, raw_y, _, _, _ = match.groups()
        if raw_x is not None:
            x = float(raw_x) * scale
        if raw_y is not None:
            y = float(raw_y) * scale
        xs.append(x)
        ys.append(y)
    if not xs:
        return None
    return (min(xs), min(ys), max(xs), max(ys))


def contours(text: str) -> list[Contour]:
    """
    Every closed loop in the file, largest first.

    The caller decides which one is the board. That is deliberate: on an export with the
    drawing sheet plotted, the largest loop is the sheet border, and picking it automatically
    would frame a page with the board lost in the middle of it.
    """
    loops = _stitch(_strokes(text), TOLERANCE_MM)
    found = []
    for loop in loops:
        points = tuple(loop[:-1])  # drop the repeated closing point
        if len(points) < 3:
            continue
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        found.append(
            Contour(points=points, area=_area(list(points)), min_x=min(xs), min_y=min(ys), max_x=max(xs), max_y=max(ys))
        )
    found.sort(key=lambda c: c.area, reverse=True)
    return found


def pick_board(found: list[Contour], width: float | None = None, height: float | None = None) -> Contour | None:
    """
    The loop that is the board, given its size from the job file if we have it.

    With a stated size, the loop whose bounding box matches it wins -- that is what makes this
    reliable on an export that also plotted a sheet border. Without one, the largest loop is
    the best guess available, and the UI lets somebody say otherwise.
    """
    if not found:
        return None
    if width and height:

        def error(contour: Contour) -> float:
            return abs(contour.width - width) + abs(contour.height - height)

        best = min(found, key=error)
        # Within a millimeter on both sides: the outline is drawn on the board edge, so it
        # matches the stated size closely or it is not the board.
        if error(best) <= 2.0:
            return best
    return found[0]


#: A loop this close in area to the board is the board, drawn again -- not a hole in it.
_SAME_SHAPE = 0.98


def cutouts(found: list[Contour], board: Contour) -> list[Contour]:
    """
    The loops inside the board: slots, windows, and the holes a router makes.

    A loop the same size as the board is excluded, because it is the board. Plenty of exports
    draw the outline twice in the same layer -- real exports do -- and the second copy is
    inside the first by any containment test you care to write. Punched as a hole under an
    even-odd fill it cancels the board exactly, and the viewer clips away the whole thing:
    a board that renders as nothing at all, with no error to explain it.
    """
    inside = [
        c
        for c in found
        if c is not board and board.contains(c) and (board.area <= 0 or c.area / board.area < _SAME_SHAPE)
    ]
    # And each of those once. The same doubling that puts the board's outline in the file
    # twice puts every slot of a KiKit panel in twice -- the panel is the board's Edge_Cuts
    # stamped per copy -- and two coincident holes are worse than none: an even-odd fill
    # cancels the pair and the slot disappears from the flat view, and the triangulator lays
    # overlapping faces across them and the 3D board grows a black wedge from the nearest
    # corner. On one real 6-up panel that was forty cutouts for twenty slots.
    kept: list[Contour] = []
    for contour in inside:
        if any(_same_loop(contour, other) for other in kept):
            continue
        kept.append(contour)
    return kept


def _same_loop(a: Contour, b: Contour) -> bool:
    """The same hole drawn again: the same box and the same area, to the stitching tolerance."""
    return (
        abs(a.min_x - b.min_x) <= TOLERANCE_MM
        and abs(a.max_x - b.max_x) <= TOLERANCE_MM
        and abs(a.min_y - b.min_y) <= TOLERANCE_MM
        and abs(a.max_y - b.max_y) <= TOLERANCE_MM
        and (max(a.area, b.area) <= 0 or min(a.area, b.area) / max(a.area, b.area) >= _SAME_SHAPE)
    )
