"""
The pads a layer actually has, and the pattern they make around one placement.

A part's terminals make a pattern -- two ends, two rows, four rows, a grid -- and the board
already draws it: every solder-paste opening is a pad the part's leads will sit on. This reads
the openings around a placement and says what it found: "8 openings, 4 per side on two sides,
1.27 mm apart", with the pattern, pitch and pad size as data.

Two halves. The first is a small gerber reader that turns flashes (`D03`), aperture macros and
filled regions into boxes -- center, width, height, in millimeters -- with no interest in what
they look like beyond their extent. KiCad draws nearly every pad as a `RoundRect` macro and
Altium as a `ROUNDEDRECT` built from lines and circles; both come out as the rectangle they
occupy, which is all a pitch or a lead width needs. The second half takes the boxes near one
placement, puts them in the part's own frame, and sorts them into rows.

Deliberately loose where looseness is safe and strict where it is not. An arc in a region is
taken by its endpoints (the box is a hair small; nothing downstream cares), but a pattern that
does not resolve into rows is reported as `irregular` rather than forced into the nearest one
-- a wrong row count is worse than no answer.

Pure: no framework, no filesystem.

Source: `magpie/pcb/pads.py` at magpie commit 3a0374d3 (copied for boarddd phase F1; magpie's later
changes are re-diffed before magpie switches to this module).
"""

from __future__ import annotations

import ast
import math
import re
from dataclasses import dataclass, field
from statistics import median

from boarddd.io import gerber

__all__ = ["Pad", "Row", "Topology", "pads", "around", "topology", "describe", "PATTERNS", "IGNORED_FUNCTIONS"]

# ─── Reading a layer ─────────────────────────────────────────────────────────

_FORMAT = re.compile(r"%FS[LT]?[AI]?X(\d)(\d)Y(\d)(\d)\*%")
_UNITS = re.compile(r"%MO(MM|IN)\*%")
_APERTURE = re.compile(r"^%ADD(\d+)([A-Za-z_.$][A-Za-z0-9_.$-]*)(?:,([^*]*))?\*%$")
_APERTURE_FUNCTION = re.compile(r"^%TA\.AperFunction,([^,*]*)", re.I)
_CLEAR_ATTRIBUTE = re.compile(r"^%TD(\.AperFunction)?\*%", re.I)
#: `D10*` and up select an aperture; `D01`-`D03` are operations, and Altium writes those alone
#: on a line after the coordinates.
_SELECT = re.compile(r"^(?:G54)?D(\d{2,})\*$")
_COORD = re.compile(r"([XYIJ])([+-]?\d+)")
_OPERATION = re.compile(r"D0([123])\*$")
_POLARITY = re.compile(r"^%LP([CD])\*%")

#: Aperture functions that are not pads even when they are flashed: a via, a trace, a
#: fiducial. Everything else -- `SMDPad`, `HeatsinkPad`, an unlabelled paste opening -- is.
IGNORED_FUNCTIONS = frozenset(
    {"conductor", "nonconductor", "viapad", "profile", "fiducialpad", "testpad", "washerpad", "heatsinkvia"}
)


@dataclass(frozen=True)
class Pad:
    """One opening: where its box sits and how big it is, in millimeters, board frame."""

    x: float
    y: float
    w: float
    h: float
    #: The `.AperFunction` its aperture carried, lowercased, or ''.
    function: str = ""

    @property
    def area(self) -> float:
        return self.w * self.h


@dataclass(frozen=True)
class _Aperture:
    w: float
    h: float
    dx: float
    dy: float
    function: str


def _evaluate(expr: str, args: list[float]) -> float:
    """
    A macro parameter expression -- `$1+$1`, `0.0433`, `$2x2` -- as a number.

    The grammar is four operators over numbers and `$n`; parsed through `ast` so that only
    that grammar is accepted, never executed.
    """
    text = expr.strip().replace("x", "*").replace("X", "*")

    def variable(match):
        index = int(match.group(1)) - 1
        return repr(args[index]) if 0 <= index < len(args) else "0"

    text = re.sub(r"\$(\d+)", variable, text)
    try:
        node = ast.parse(text or "0", mode="eval").body
    except SyntaxError:
        return 0.0
    return _walk(node)


def _walk(node) -> float:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return float(node.value)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        value = _walk(node.operand)
        return -value if isinstance(node.op, ast.USub) else value
    if isinstance(node, ast.BinOp):
        left, right = _walk(node.left), _walk(node.right)
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        if isinstance(node.op, ast.Div):
            return left / right if right else 0.0
    return 0.0


def _rotated(x: float, y: float, degrees: float) -> tuple[float, float]:
    if not degrees:
        return x, y
    angle = math.radians(degrees)
    c, s = math.cos(angle), math.sin(angle)
    return x * c - y * s, x * s + y * c


def _macro_extent(primitives: list[str], args: list[float]) -> tuple[float, float, float, float] | None:
    """
    The box a macro aperture paints, as (w, h, dx, dy): size and the offset of its center
    from the flash point. None when nothing in it is exposed.

    Only the primitives that add material count; a primitive with exposure 0 is a hole, and a
    hole does not make a pad bigger. Rotation is applied where the primitive has one, so an
    Altium pad rotated 90 degrees comes out tall rather than wide.
    """
    points: list[tuple[float, float]] = []
    for raw in primitives:
        raw = raw.strip()
        if not raw or raw.startswith("0"):
            continue
        parts = [p for p in raw.split(",")]
        try:
            code = int(parts[0])
        except ValueError:
            continue
        values = [_evaluate(p, args) for p in parts[1:]]
        if code == 1 and len(values) >= 4:
            exposure, diameter, cx, cy = values[:4]
            rot = values[4] if len(values) > 4 else 0.0
            if exposure < 0.5:
                continue
            cx, cy = _rotated(cx, cy, rot)
            r = diameter / 2
            points += [(cx - r, cy - r), (cx + r, cy + r)]
        elif code == 4 and len(values) >= 4:
            exposure, count = values[0], int(round(values[1]))
            if exposure < 0.5:
                continue
            coords = values[2 : 2 + 2 * (count + 1)]
            rot = values[2 + 2 * (count + 1)] if len(values) > 2 + 2 * (count + 1) else 0.0
            for i in range(0, len(coords) - 1, 2):
                points.append(_rotated(coords[i], coords[i + 1], rot))
        elif code == 20 and len(values) >= 6:
            exposure, width, x1, y1, x2, y2 = values[:6]
            rot = values[6] if len(values) > 6 else 0.0
            if exposure < 0.5:
                continue
            half = width / 2
            for x, y in ((x1, y1), (x2, y2)):
                rx, ry = _rotated(x, y, rot)
                points += [(rx - half, ry - half), (rx + half, ry + half)]
        elif code == 21 and len(values) >= 5:
            exposure, width, height, cx, cy = values[:5]
            rot = values[5] if len(values) > 5 else 0.0
            if exposure < 0.5:
                continue
            for sx in (-1, 1):
                for sy in (-1, 1):
                    points.append(_rotated(cx + sx * width / 2, cy + sy * height / 2, rot))
        elif code == 5 and len(values) >= 5:
            exposure, _sides, cx, cy, diameter = values[:5]
            rot = values[5] if len(values) > 5 else 0.0
            if exposure < 0.5:
                continue
            cx, cy = _rotated(cx, cy, rot)
            r = diameter / 2
            points += [(cx - r, cy - r), (cx + r, cy + r)]
        elif code == 7 and len(values) >= 4:
            cx, cy, outer = values[:3]
            rot = values[5] if len(values) > 5 else 0.0
            cx, cy = _rotated(cx, cy, rot)
            r = outer / 2
            points += [(cx - r, cy - r), (cx + r, cy + r)]
    if not points:
        return None
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    w, h = max(xs) - min(xs), max(ys) - min(ys)
    return w, h, (max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2


def _standard_extent(template: str, args: list[float]) -> tuple[float, float] | None:
    if template == "C" and args:
        return args[0], args[0]
    if template in ("R", "O") and len(args) >= 2:
        return args[0], args[1]
    if template == "P" and args:
        return args[0], args[0]
    return None


def pads(text: str, ignored=IGNORED_FUNCTIONS) -> list[Pad]:
    """
    Every pad a gerber layer draws, as boxes in millimeters.

    Flashes of standard and macro apertures, and filled regions (a custom pad is a region).
    Strokes with an aperture are taken too -- an oblong pad is sometimes drawn as one short
    line -- and a run of overlapping strokes is merged into one box. Apertures whose function
    says they are not pads (`ignored`) are skipped, as is anything painted in clear polarity
    and the board outline KiCad plots onto every layer.
    """
    text = gerber.without_profile(text or "")
    scale = 1.0
    decimals = 5
    apertures: dict[int, _Aperture] = {}
    macros: dict[str, list[str]] = {}
    function = ""
    current: _Aperture | None = None
    dark = True
    x = y = 0.0
    out: list[Pad] = []
    strokes: list[Pad] = []
    region: list[tuple[float, float]] | None = None
    pending_macro: tuple[str, list[str]] | None = None

    def coordinate(raw: str) -> float:
        return int(raw) / (10**decimals) * scale

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue

        if pending_macro is not None:
            name, body = pending_macro
            chunk = line.rstrip("%").strip()
            if chunk:
                body += [p for p in chunk.split("*") if p.strip()]
            pending_macro = (name, body)
            if line.endswith("%"):
                macros[name] = body
                pending_macro = None
            continue

        if line.startswith("%AM"):
            head, _, rest = line[3:].partition("*")
            body = [p for p in rest.rstrip("%").split("*") if p.strip()]
            if line.endswith("%"):
                macros[head] = body
            else:
                pending_macro = (head, body)
            continue

        match = _FORMAT.match(line)
        if match:
            decimals = int(match.group(2))
            continue
        match = _UNITS.match(line)
        if match:
            scale = 25.4 if match.group(1) == "IN" else 1.0
            continue
        match = _APERTURE_FUNCTION.match(line)
        if match:
            function = match.group(1).strip().lower()
            continue
        if _CLEAR_ATTRIBUTE.match(line):
            function = ""
            continue
        match = _POLARITY.match(line)
        if match:
            dark = match.group(1) == "D"
            continue
        match = _APERTURE.match(line)
        if match:
            number, template, raw_args = int(match.group(1)), match.group(2), match.group(3)
            args = []
            for piece in (raw_args or "").split("X"):
                piece = piece.strip()
                if piece:
                    try:
                        args.append(float(piece))
                    except ValueError:
                        args.append(0.0)
            # Scaled once, at the end. A macro's body carries literal lengths in the file's
            # own units beside its `$n` parameters -- Altium's ROUNDEDRECT is all literals,
            # KiCad's RoundRect all parameters -- so scaling the parameters on the way in
            # would leave the two halves of one aperture in different units.
            extent = (
                _standard_extent(template, args)
                if template in ("C", "R", "O", "P")
                else _macro_extent(macros[template], args)
                if template in macros
                else None
            )
            if extent:
                w, h = extent[0] * scale, extent[1] * scale
                dx, dy = (extent[2] * scale, extent[3] * scale) if len(extent) > 2 else (0.0, 0.0)
                apertures[number] = _Aperture(w, h, dx, dy, function)
            continue
        if line.startswith("%"):
            continue
        if line.startswith("G04") or line.startswith("M02"):
            continue
        if line.startswith("G36"):
            region = []
            continue
        if line.startswith("G37"):
            if region and dark and len(region) >= 3:
                xs = [p[0] for p in region]
                ys = [p[1] for p in region]
                w, h = max(xs) - min(xs), max(ys) - min(ys)
                if w > 0 and h > 0:
                    out.append(Pad((max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2, w, h, "region"))
            region = None
            continue
        match = _SELECT.match(line)
        if match and int(match.group(1)) >= 10:
            current = apertures.get(int(match.group(1)))
            continue

        operation = _OPERATION.search(line)
        if operation is None:
            continue
        previous = (x, y)
        for axis, raw in _COORD.findall(line):
            if axis == "X":
                x = coordinate(raw)
            elif axis == "Y":
                y = coordinate(raw)
        code = operation.group(1)
        if region is not None:
            if code in ("1", "2"):
                region.append((x, y))
            continue
        if not dark or current is None or current.function in ignored:
            continue
        if code == "3":
            out.append(Pad(x + current.dx, y + current.dy, current.w, current.h, current.function))
        elif code == "1":
            (x0, y0) = previous
            strokes.append(
                Pad(
                    (x0 + x) / 2 + current.dx,
                    (y0 + y) / 2 + current.dy,
                    abs(x - x0) + current.w,
                    abs(y - y0) + current.h,
                    current.function,
                )
            )

    out.extend(_merged(strokes))
    return out


def _merged(boxes: list[Pad]) -> list[Pad]:
    """Overlapping stroke boxes as one: an oblong drawn as two strokes is one pad."""
    merged: list[Pad] = []
    for box in boxes:
        for i, other in enumerate(merged):
            if abs(box.x - other.x) * 2 < box.w + other.w and abs(box.y - other.y) * 2 < box.h + other.h:
                x0 = min(box.x - box.w / 2, other.x - other.w / 2)
                x1 = max(box.x + box.w / 2, other.x + other.w / 2)
                y0 = min(box.y - box.h / 2, other.y - other.h / 2)
                y1 = max(box.y + box.h / 2, other.y + other.h / 2)
                merged[i] = Pad((x0 + x1) / 2, (y0 + y1) / 2, x1 - x0, y1 - y0, box.function)
                break
        else:
            merged.append(box)
    return merged


# ─── The pads around one placement ───────────────────────────────────────────

#: How far past the body a lead can reach, as a share of the body's long side, bounded. A
#: gullwing foot is about a millimeter; a 0402's neighbour is half a millimeter away, so the
#: margin has to shrink with the part or it sweeps the next part's pads in.
_MARGIN_SHARE = 0.35
_MARGIN_MIN_MM = 0.15
_MARGIN_MAX_MM = 1.8
#: With no body size to go on: a window this wide, and the symmetry and size tests below to
#: pick the part's own pads out of it.
_BLIND_HALF_MM = 4.0


def around(
    found: list[Pad],
    x: float,
    y: float,
    rotation: float = 0.0,
    body: tuple[float, float] | None = None,
    side: str = "top",
) -> list[Pad]:
    """
    The pads near a placement, moved into the part's own frame: the placement at the origin,
    the part unrotated, and -- on the underside -- unmirrored, so the pattern reads the same
    way up as its twin on top.

    `body` is (short, long) in mm when the footprint's name says; the window is the long side
    plus a lead's reach, square, because the name does not say which way the body lies. Without
    it the window is wide and the answer is the cluster of pads joined to the nearest one.
    """
    local: list[Pad] = []
    angle = math.radians(-rotation)
    c, s = math.cos(angle), math.sin(angle)
    for pad in found:
        dx, dy = pad.x - x, pad.y - y
        if side == "bottom":
            dx = -dx
        lx, ly = dx * c - dy * s, dx * s + dy * c
        w, h = pad.w, pad.h
        # A quarter turn swaps a box's sides; anything in between keeps them, which is a
        # slight overstatement of a rotated pad's box and harmless for counting.
        if abs(round(rotation / 90) * 90 - rotation) < 1e-6 and round(rotation / 90) % 2:
            w, h = h, w
        local.append(Pad(lx, ly, w, h, pad.function))

    if body is not None and body[1] > 0:
        long_side = max(body)
        margin = min(_MARGIN_MAX_MM, max(_MARGIN_MIN_MM, long_side * _MARGIN_SHARE))
        half = long_side / 2 + margin
    else:
        half = _BLIND_HALF_MM
    near = [p for p in local if abs(p.x) <= half and abs(p.y) <= half]
    return _uniform(_mirrored(near))


#: How far apart two pads can be and still count as each other's mirror image.
_MIRROR_MM = 0.15


def _mirrored(local: list[Pad]) -> list[Pad]:
    """
    Only the pads that belong to the part: the ones with a twin across the part's own x or y
    axis, or sitting on one.

    A footprint is symmetric about its center on at least one axis in nearly every package --
    a chip's two ends, a SOT-23's pair and single, a QFN's four rows, a crystal's corners. A
    neighbour's pad that strayed into the window has no twin here. When the test would throw
    out most of the pads the footprint is anchored off-center, and the window is believed as
    it is rather than emptied.
    """
    if len(local) < 2:
        return local

    def near(a: float, b: float) -> bool:
        return abs(a - b) <= _MIRROR_MM

    kept = []
    for pad in local:
        on_axis = near(pad.x, 0.0) or near(pad.y, 0.0)
        twin = any(
            other is not pad
            and ((near(other.x, -pad.x) and near(other.y, pad.y)) or (near(other.x, pad.x) and near(other.y, -pad.y)))
            for other in local
        )
        if on_axis or twin:
            kept.append(pad)
    return kept if len(kept) * 2 >= len(local) else local


#: Pads within this much of one another's area are "the same size".
_SAME_AREA = 0.3
#: Everything within the lead set's box, grown by this much, belongs to the part.
_INSIDE_MM = 0.5
#: A lone pad this close to the placement point is the part (a test point).
_LONE_MM = 0.3


def _uniform(local: list[Pad]) -> list[Pad]:
    """
    The part's own pads, told from a neighbour's that survived the symmetry test.

    Pads are grouped by size. The group that spans the placement point and reaches furthest is
    the lead set -- leads are the outermost thing on a footprint -- and everything inside its
    box is the part too: the end pads a footprint draws wider, a DPAK's tab, a thermal pad or
    the windows KiCad cuts one into. A neighbour's pads are outside that box. When no group of
    two or more spans the origin the nearest single pad is taken if it sits on the placement
    (a test point), and otherwise nothing is dropped: a footprint anchored off its pads is a
    real thing, and an empty answer would hide it.
    """
    if len(local) < 2:
        return local
    ordered = sorted(local, key=lambda p: p.area)
    clusters: list[list[Pad]] = []
    for pad in ordered:
        if clusters and abs(pad.area - median(p.area for p in clusters[-1])) <= _SAME_AREA * pad.area:
            clusters[-1].append(pad)
        else:
            clusters.append([pad])

    def box(cluster):
        return (
            min(p.x - p.w / 2 for p in cluster) - _INSIDE_MM,
            min(p.y - p.h / 2 for p in cluster) - _INSIDE_MM,
            max(p.x + p.w / 2 for p in cluster) + _INSIDE_MM,
            max(p.y + p.h / 2 for p in cluster) + _INSIDE_MM,
        )

    spanning = [(box(c), c) for c in clusters if len(c) >= 2]
    spanning = [(b, c) for b, c in spanning if b[0] <= 0 <= b[2] and b[1] <= 0 <= b[3]]
    if spanning:
        (x0, y0, x1, y1), _ = max(spanning, key=lambda item: (item[0][2] - item[0][0]) * (item[0][3] - item[0][1]))
        return [p for p in local if x0 <= p.x <= x1 and y0 <= p.y <= y1]
    nearest = min(local, key=lambda p: p.x * p.x + p.y * p.y)
    if abs(nearest.x) <= _LONE_MM and abs(nearest.y) <= _LONE_MM:
        return [nearest]
    return local


# ─── What pattern they make ──────────────────────────────────────────────────

PATTERNS = ("none", "one", "two_ends", "two_rows", "four_rows", "one_row", "grid", "irregular")


@dataclass(frozen=True)
class Row:
    """One line of pads along one side, in the normalised frame."""

    count: int
    #: Center-to-center along the row, or None for a single pad.
    pitch: float | None
    #: A pad's size along the row and across it.
    width: float
    length: float
    pads: tuple[Pad, ...] = ()


@dataclass(frozen=True)
class Topology:
    """
    The pattern around one placement, in a frame turned so rows run along Y and sit at E and
    W, so every two-row pattern is described the same way.
    """

    pattern: str
    #: Every pad that was looked at, in the normalised frame.
    pads: tuple[Pad, ...] = ()
    #: Rows by side: E/W for two rows, N/S/E/W for four, E alone for one.
    rows: dict = field(default_factory=dict)
    #: Pads under the body -- a thermal pad, or the windows KiCad cuts one into.
    center: tuple[Pad, ...] = ()
    #: The box every pad fits in, (width, height).
    extent: tuple[float, float] = (0.0, 0.0)
    #: Whether opposite rows hold the same count.
    equal: bool = True
    #: Grid pitch, for a ball grid.
    pitch: float | None = None

    @property
    def count(self) -> int:
        return len(self.pads)

    @property
    def decisive(self) -> bool:
        """Whether this pattern says which alignment type to use, rather than only how many pads."""
        return self.pattern in ("two_ends", "two_rows", "four_rows", "one_row", "grid")


def _lines(values: list[float], tolerance: float) -> list[list[int]]:
    """Indices grouped by value, consecutive values within `tolerance` sharing a group."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    groups: list[list[int]] = []
    for i in order:
        if groups and values[i] - values[groups[-1][-1]] <= tolerance:
            groups[-1].append(i)
        else:
            groups.append([i])
    return groups


#: A row's middle may sit this far off the part's axis, as a share of the pattern's extent
#: that way, and still be a row of this part. Real rows are centered; a row of one stray pad
#: at a corner is not.
_OFF_AXIS = 0.3


def _centered(members, groups, axis: str, extent: float) -> bool:
    """Whether each row's middle lies on the part's axis. `groups` are index lists into
    `members`, or None for one row."""
    rows = [members] if groups is None else [[members[i] for i in g] for g in groups]
    limit = max(_OFF_AXIS * extent, 0.2)
    for row in rows:
        values = [getattr(p, axis) for p in row]
        middle = (max(values) + min(values)) / 2
        if abs(middle) > limit:
            return False
    return True


def _row(members: list[Pad], along: str) -> Row:
    ordered = sorted(members, key=lambda p: p.x if along == "x" else p.y)
    positions = [p.x if along == "x" else p.y for p in ordered]
    gaps = [b - a for a, b in zip(positions, positions[1:], strict=False)]
    pitch = round(median(gaps), 3) if gaps else None
    width = round(median(p.w if along == "x" else p.h for p in ordered), 3)
    length = round(median(p.h if along == "x" else p.w for p in ordered), 3)
    return Row(len(ordered), pitch, width, length, tuple(ordered))


def _turn(pad: Pad) -> Pad:
    """A quarter turn, so a row that ran along X runs along Y."""
    return Pad(-pad.y, pad.x, pad.h, pad.w, pad.function)


def _extent(found) -> tuple[float, float]:
    if not found:
        return 0.0, 0.0
    x0 = min(p.x - p.w / 2 for p in found)
    x1 = max(p.x + p.w / 2 for p in found)
    y0 = min(p.y - p.h / 2 for p in found)
    y1 = max(p.y + p.h / 2 for p in found)
    return round(x1 - x0, 3), round(y1 - y0, 3)


#: Opposite rows further apart from the center than this, one against the other, are not a
#: pair: one of them belongs to the next part over.
_LOPSIDED = 1.3


def _lopsided(found: Topology) -> bool:
    def offset(side: str, axis: str) -> float:
        return abs(median(getattr(p, axis) for p in found.rows[side].pads))

    pairs = []
    if found.pattern in ("two_rows", "four_rows"):
        pairs.append((offset("W", "x"), offset("E", "x")))
    if found.pattern == "four_rows":
        pairs.append((offset("N", "y"), offset("S", "y")))
    return any(max(a, b) > _LOPSIDED * max(min(a, b), 1e-6) for a, b in pairs)


def topology(local: list[Pad]) -> Topology:
    """
    Sort the pads around a placement into a pattern: two ends, rows, a grid, or irregular.

    Tried in the order that is safest to be wrong in: a grid (uniform pads on three or more
    lines each way) before anything else, because a ball grid's interior would otherwise be
    thrown away as a thermal pad; then two ends; then two rows on one axis or the other; then
    four; then one. What is left is `irregular`, on purpose.

    A pattern that comes out irregular or lopsided -- rows at unequal distances from the
    center -- is retried without its outermost line of pads, a few times: the pads a window
    catches off the next part over lie further out than the part's own. The first pattern
    that is both decisive and symmetric wins; failing that, the first answer stands.
    """
    found = list(local)
    first = _classify(found)
    if first.decisive and not _lopsided(first):
        return first
    for _ in range(3):
        if len(found) < 3:
            break
        farthest = max(max(abs(p.x) for p in found), max(abs(p.y) for p in found))
        keep = [p for p in found if abs(farthest - abs(p.x)) > 0.12 and abs(farthest - abs(p.y)) > 0.12]
        # Peeling is for a stray line or two; a pattern that only resolves once most of it
        # is gone was never this part's pattern.
        if len(keep) == len(found) or len(keep) * 2 < len(local):
            break
        found = keep
        again = _classify(found)
        if again.decisive and not _lopsided(again):
            return again
    return first


def _classify(local: list[Pad]) -> Topology:
    found = list(local)
    n = len(found)
    if n == 0:
        return Topology("none")
    if n == 1:
        return Topology("one", tuple(found), extent=_extent(found))

    tolerance = max(0.12, 0.25 * median(min(p.w, p.h) for p in found))
    xs = _lines([p.x for p in found], tolerance)
    ys = _lines([p.y for p in found], tolerance)

    # A grid: pads of one size on a lattice, most of it filled.
    if len(xs) >= 3 and len(ys) >= 3 and n >= 9:
        areas = [p.area for p in found]
        rim = {i for g in (xs[0], xs[-1], ys[0], ys[-1]) for i in g}
        inner = n - len(rim)
        # A leadless QFN also has three lines each way; what it lacks is anything inside them.
        if (
            max(areas) <= 2.0 * min(areas)
            and n >= 0.5 * len(xs) * len(ys)
            and inner >= 0.5 * (len(xs) - 2) * (len(ys) - 2)
        ):
            x_at = [median(found[i].x for i in g) for g in xs]
            y_at = [median(found[i].y for i in g) for g in ys]
            gaps = [b - a for a, b in zip(x_at, x_at[1:], strict=False)] + [
                b - a for a, b in zip(y_at, y_at[1:], strict=False)
            ]
            return Topology(
                "grid", tuple(found), extent=_extent(found), pitch=round(median(gaps), 3), center=tuple(found)
            )

    if n == 2:
        a, b = found
        # Two ends are each other's mirror about the placement; two pads that are not are two
        # strays, or one pad of ours and one of the next part's.
        if abs(a.x + b.x) > _MIRROR_MM * 2 or abs(a.y + b.y) > _MIRROR_MM * 2:
            return Topology("irregular", tuple(found), extent=_extent(found))
        if abs(a.y - b.y) > abs(a.x - b.x):
            found = [_turn(p) for p in found]
            a, b = found
        west, east = sorted((a, b), key=lambda p: p.x)
        return Topology(
            "two_ends", tuple(found), rows={"W": _row([west], "y"), "E": _row([east], "y")}, extent=_extent(found)
        )

    # Pads under the body do not belong to a row. Only when there are enough pads for rows to
    # exist around them, and only the ones clear of every outer line.
    center: list[Pad] = []
    outer = found
    if n >= 5:
        edge_x = {i for g in (xs[0], xs[-1]) for i in g}
        edge_y = {i for g in (ys[0], ys[-1]) for i in g}
        inside = [i for i in range(n) if i not in edge_x and i not in edge_y]
        if inside and len(inside) < n - 2:
            center = [found[i] for i in inside]
            outer = [p for i, p in enumerate(found) if i not in set(inside)]
            xs = _lines([p.x for p in outer], tolerance)
            ys = _lines([p.y for p in outer], tolerance)

    width, height = _extent(outer)
    along_x = len(ys) == 2  # two lines of constant y: rows run along x
    along_y = len(xs) == 2
    if along_x and along_y:
        # A 2 x 2 -- a four-pad crystal -- reads either way. By convention the pitch is the
        # one along the long side, so the rows run that way.
        along_x = width >= height
        along_y = not along_x
    # Two lines of constant y are two rows running along x, each of which must be centered
    # on the part's y axis -- and the other way round.
    if (along_x or along_y) and _centered(
        outer, ys if along_x else xs, "x" if along_x else "y", width if along_x else height
    ):
        groups = ys if along_x else xs
        first = [outer[i] for i in groups[0]]
        second = [outer[i] for i in groups[1]]
        turned = along_x
        if turned:
            first, second = [_turn(p) for p in first], [_turn(p) for p in second]
            center = [_turn(p) for p in center]
        low, high = sorted((first, second), key=lambda g: median(p.x for p in g))
        rows = {"W": _row(low, "y"), "E": _row(high, "y")}
        all_pads = tuple(rows["W"].pads + rows["E"].pads + tuple(center))
        return Topology(
            "two_rows",
            all_pads,
            rows=rows,
            center=tuple(center),
            extent=_extent(all_pads),
            equal=rows["W"].count == rows["E"].count,
        )

    if len(ys) >= 3 and len(xs) >= 3:
        north = [outer[i] for i in ys[-1]]
        south = [outer[i] for i in ys[0]]
        taken = set(ys[-1]) | set(ys[0])
        rest = [i for i in range(len(outer)) if i not in taken]
        rest_xs = _lines([outer[i].x for i in rest], tolerance) if rest else []
        if (
            rest
            and len(rest_xs) == 2
            and _centered(north, None, "x", width)
            and _centered(south, None, "x", width)
            and _centered([outer[rest[i]] for i in rest_xs[0]], None, "y", height)
            and _centered([outer[rest[i]] for i in rest_xs[1]], None, "y", height)
        ):
            west = [outer[rest[i]] for i in rest_xs[0]]
            east = [outer[rest[i]] for i in rest_xs[1]]
            rows = {"N": _row(north, "x"), "S": _row(south, "x"), "W": _row(west, "y"), "E": _row(east, "y")}
            all_pads = tuple(p for r in rows.values() for p in r.pads) + tuple(center)
            return Topology(
                "four_rows",
                all_pads,
                rows=rows,
                center=tuple(center),
                extent=_extent(all_pads),
                equal=(rows["N"].count == rows["S"].count and rows["E"].count == rows["W"].count),
            )

    if not center and (len(ys) == 1 or len(xs) == 1) and n >= 3:
        members = outer
        if len(ys) == 1:
            members = [_turn(p) for p in outer]
        return Topology("one_row", tuple(members), rows={"E": _row(members, "y")}, extent=_extent(members))

    return Topology("irregular", tuple(found), extent=_extent(found))


def _mm(value: float | None) -> str:
    return f"{value:g} mm" if value else ""


def describe(found: Topology, noun: str = "paste opening") -> str:
    """The pattern as a sentence for the line under a field."""
    n = found.count
    plural = f"{n} {noun}{'' if n == 1 else 's'}"
    if found.pattern == "none":
        return f"no {noun}s found at this placement"
    if found.pattern == "one":
        return plural
    if found.pattern == "two_ends":
        return f"{plural}, one at each end"
    under = f", plus {len(found.center)} under the body" if found.center else ""
    if found.pattern == "two_rows":
        west, east = found.rows["W"], found.rows["E"]
        if west.count == east.count:
            pitch = f", {_mm(east.pitch)} apart" if east.pitch else ""
            return f"{plural}: {east.count} per side on two sides{pitch}{under}"
        big, small = max(west.count, east.count), min(west.count, east.count)
        return f"{plural}: {big} on one side, {small} on the other{under}"
    if found.pattern == "four_rows":
        counts = {found.rows[s].count for s in "NSEW"}
        pitch = found.rows["E"].pitch or found.rows["N"].pitch
        apart = f", {_mm(pitch)} apart" if pitch else ""
        if len(counts) == 1:
            return f"{plural}: {counts.pop()} per side on four sides{apart}{under}"
        return (
            f"{plural}: {found.rows['N'].count}/{found.rows['S'].count} on the ends, "
            f"{found.rows['E'].count}/{found.rows['W'].count} on the sides{apart}{under}"
        )
    if found.pattern == "one_row":
        pitch = found.rows["E"].pitch
        return f"{plural} in one row{f', {_mm(pitch)} apart' if pitch else ''}"
    if found.pattern == "grid":
        return f"{plural} in a grid{f', {_mm(found.pitch)} apart' if found.pitch else ''}"
    return f"{plural} in no pattern this recognises"
