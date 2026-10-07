"""
The pick-and-place file, read and written.

KiCad's CSV: `Ref,Val,Package,PosX,PosY,Rot,Side`, millimeters, one row per placed component.
Read once at upload into rows that can be edited, and written back out on demand -- so this
module is the round trip, and the test that matters is that an untouched file survives it.

Every other tool writes the same seven facts and spells them differently:

    Altium    Designator, Comment, Layer, Footprint, Center-X(mm), Center-Y(mm), Rotation
              -- a title block above the table, and a fixed-width .txt as well as the CSV;
                 "(mil)" in the header when it was exported in mils
    JLC/EasyEDA  Designator, Mid X, Mid Y, Layer, Rotation   -- values like "12.7mm"
    DipTrace  RefDes, Name, X (mm), Y (mm), Side, Rotate, Value
    Allegro   REFDES, SYM_X, SYM_Y, SYM_ROTATE, SYM_MIRROR
    gEDA      # refdes, footprint, value, x, y, rotation, side
    Eagle     C1 15.24 12.70 180 100nF C0603   -- mountsmd.ulp, no header, one file per side

Columns are found by header name, not by position: a positional reader would put Y in the
rotation column and be believed. The header is looked for rather than assumed to be first,
the delimiter is whatever the header uses, and coordinates are brought to millimeters.

Pure: no settings, no filesystem.

Source: `magpie/pcb/posfile.py` at magpie commit 3a0374d3 (copied for boarddd phase F1; magpie's later
changes are re-diffed before magpie switches to this module).
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

__all__ = [
    "PosRow",
    "parse",
    "render",
    "HEADER",
    "looks_like_placements",
    "frame_offset",
    "FRAME_MARGIN_MM",
    "FRAME_FRACTION",
]

HEADER = ["Ref", "Val", "Package", "PosX", "PosY", "Rot", "Side"]

#: Header spellings seen in the wild, mapped to (field, preference). Normalized first:
#: lowercased, units in brackets dropped, everything but letters and digits removed -- so
#: `Center-X(mm)`, `Mid X` and `SYM_X` arrive as `centerx`, `midx` and `symx`.
#:
#: The preference settles a file that offers two spellings of the same thing: Altium writes
#: `Mid X`, `Ref X` and `Pad X` side by side, and the middle is the one a machine wants.
_ALIASES: dict[str, tuple[str, int]] = {
    "ref": ("ref", 0),
    "reference": ("ref", 0),
    "references": ("ref", 0),
    "designator": ("ref", 0),
    "designators": ("ref", 0),
    "refdes": ("ref", 0),
    "referencedesignator": ("ref", 0),
    "refdesignator": ("ref", 0),
    "part": ("ref", 2),
    "component": ("ref", 2),
    "name": ("ref", 3),
    "val": ("value", 0),
    "value": ("value", 0),
    "comment": ("value", 1),
    "compvalue": ("value", 0),
    "partnumber": ("value", 2),
    "mpn": ("value", 2),
    "package": ("footprint", 0),
    "footprint": ("footprint", 0),
    "pattern": ("footprint", 0),
    "symname": ("footprint", 0),
    "device": ("footprint", 1),
    "partshape": ("footprint", 1),
    "posx": ("x", 0),
    "x": ("x", 0),
    "midx": ("x", 0),
    "centerx": ("x", 0),
    "symx": ("x", 0),
    "locationx": ("x", 0),
    "xcenter": ("x", 0),
    "xmm": ("x", 0),
    "xmil": ("x", 0),
    "refx": ("x", 1),
    "padx": ("x", 2),
    "xpos": ("x", 0),
    "posy": ("y", 0),
    "y": ("y", 0),
    "midy": ("y", 0),
    "centery": ("y", 0),
    "symy": ("y", 0),
    "locationy": ("y", 0),
    "ycenter": ("y", 0),
    "ymm": ("y", 0),
    "ymil": ("y", 0),
    "refy": ("y", 1),
    "pady": ("y", 2),
    "ypos": ("y", 0),
    "rot": ("rotation", 0),
    "rotation": ("rotation", 0),
    "rotate": ("rotation", 0),
    "symrotate": ("rotation", 0),
    "angle": ("rotation", 0),
    "orientation": ("rotation", 0),
    "rotationdeg": ("rotation", 0),
    "side": ("side", 0),
    "layer": ("side", 0),
    "tb": ("side", 0),
    "topbottom": ("side", 0),
    "symmirror": ("mirror", 0),
    "mirror": ("mirror", 0),
    "mirrored": ("mirror", 0),
}
_BRACKETED = re.compile(r"[\(\[].*?[\)\]]")
_UNIT_IN_HEADER = re.compile(r"[\(\[\s_-](mils?|inch|inches|in|mm)[\)\]\s_-]*$", re.I)
_UNITS_LINE = re.compile(r"(units?\s*(used)?\s*[:=]\s*|\bin\s+)(?P<unit>mils?|inch|inches|mm)\b", re.I)
#: A number, optionally followed by a unit: `12.7`, `12.7mm`, `500mil`, `-0.25 in`.
_NUMBER = re.compile(r"^\s*(?P<number>[-+]?\d*\.?\d+(?:e[-+]?\d+)?)\s*(?P<unit>mils?|mm|in(?:ch(?:es)?)?)?\s*$", re.I)
_TO_MM = {
    "mm": Decimal(1),
    "mil": Decimal("0.0254"),
    "mils": Decimal("0.0254"),
    "in": Decimal("25.4"),
    "inch": Decimal("25.4"),
    "inches": Decimal("25.4"),
}
_HEADER_SEARCH_LINES = 60
_DELIMITERS = (",", ";", "\t")


@dataclass
class PosRow:
    reference: str
    value: str
    footprint: str
    x: Decimal
    y: Decimal
    rotation: Decimal
    side: str


def _normalize(header: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", _BRACKETED.sub("", (header or "").strip().lower()))


def _unit_of(header: str) -> Decimal | None:
    """The scale a column header states, `Center-X(mil)` or `X (mm)`; None when it doesn't."""
    match = _UNIT_IN_HEADER.search((header or "").strip())
    return _TO_MM[match.group(1).lower()] if match else None


def _quantity(raw: str, scale: Decimal) -> Decimal:
    """A coordinate in millimeters, whatever unit the cell or the column put it in."""
    match = _NUMBER.match(raw or "")
    if not match:
        return Decimal(0)
    try:
        number = Decimal(match.group("number"))
    except InvalidOperation:
        return Decimal(0)
    unit = (match.group("unit") or "").lower()
    try:
        return number * (_TO_MM[unit] if unit else scale)
    except ArithmeticError:
        # `1e9999999`: past what Decimal's context will multiply. Not a coordinate, and the
        # row is left out for it (see _stored) rather than raising out of an upload.
        return Decimal("NaN")


def _decimal(raw: str) -> Decimal:
    try:
        return Decimal((raw or "0").strip() or "0")
    except InvalidOperation:
        return Decimal(0)


#: What a placement table typically holds: x/y as numeric(12,6) and rotation numeric(9,6), so
#: six and three digits before the point. A number at or past these is not a placement -- nobody
#: builds a board a kilometre wide or turns a part through 1000 degrees -- and handed to the
#: database it was a DataError after the file had been stored.
_LIMIT_MM = Decimal(10) ** 6
_LIMIT_DEG = Decimal(1000)
_PLACES = Decimal("0.000001")


def _stored(value: Decimal, limit: Decimal) -> Decimal | None:
    """
    `value` as the placement table stores it, six places, or None when it cannot be stored.

    `Decimal` reads `NaN`, `sNaN` and `Infinity` as numbers, and a database refuses them only
    at the insert -- an error out of the upload, and out of every re-read of that file after
    it. The six places are the column's own; an ORM hands the value over as it is, and
    Postgres refuses `1e-9999999` as overflowing rather than rounding it to 0.
    """
    # copy_abs, not abs(): abs() rounds in the context, and `1e9999999` overflows it.
    if not value.is_finite() or value.copy_abs() >= limit:
        return None
    return value.quantize(_PLACES)


#: `12,70` -- a decimal comma, which is what Excel writes where the comma is not a separator.
_DECIMAL_COMMA = re.compile(r"^\s*([-+]?\d+),(\d+)\s*$")


def _side(raw: str, mirror: str = "") -> str:
    """
    Which face, read loosely: `bottom`, `B`, `Bot`, `BottomLayer`, `back` are all the same
    answer, and Allegro says it with SYM_MIRROR=YES instead.
    """
    word = (raw or "").strip().lower()
    if word.startswith(("b", "back")):
        return "bottom"
    if not word and (mirror or "").strip().lower() in ("yes", "y", "true", "1", "mirrored", "m"):
        return "bottom"
    return "top"


def _delimiter(line: str) -> str:
    """The delimiter a header line uses, or '' for whitespace-aligned columns."""
    counts = {d: line.count(d) for d in _DELIMITERS}
    best = max(counts, key=counts.get)
    return best if counts[best] else ""


def _split(line: str, delimiter: str) -> list[str]:
    if delimiter:
        return [cell.strip().strip('"').strip() for cell in next(csv.reader([line], delimiter=delimiter), [])]
    return [cell.strip('"') for cell in line.split()]


def _columns(cells: list[str]) -> dict[str, int]:
    """{field: column index} for a header row, or {} when it is not one."""
    chosen: dict[str, tuple[int, int]] = {}
    for index, cell in enumerate(cells):
        found = _ALIASES.get(_normalize(cell))
        if not found:
            continue
        field, preference = found
        if field not in chosen or preference < chosen[field][0]:
            chosen[field] = (preference, index)
    columns = {field: index for field, (_pref, index) in chosen.items()}
    if "ref" in columns and "x" in columns and "y" in columns:
        return columns
    return {}


def _find_header(lines: list[str]) -> tuple[int, str, dict[str, int]] | None:
    """
    (line index, delimiter, columns) of the row naming the columns, searched for rather than
    assumed: Altium writes a title block above its table, and KiCad's .pos comments its
    header out with a `#`.
    """
    for index, line in enumerate(lines[:_HEADER_SEARCH_LINES]):
        candidate = line.lstrip("﻿").strip()
        if not candidate:
            continue
        if candidate.startswith("#"):
            candidate = candidate.lstrip("#").strip()
        delimiter = _delimiter(candidate)
        columns = _columns(_split(candidate, delimiter))
        if columns:
            return index, delimiter, columns
    return None


def looks_like_placements(text: str) -> bool:
    """Whether a header row naming a designator and two coordinates is in here."""
    return _find_header(text.splitlines()) is not None


def _stated_scale(lines: list[str], header: str) -> Decimal:
    """
    Millimeters unless the file says otherwise: in the coordinate headers (`Center-X(mil)`),
    or in a title-block line (`Units used: mil`, `X,Y in mil.`). Per-cell units win over
    both, in `_quantity`.
    """
    for cell in _split(header.lstrip("#").strip(), _delimiter(header)):
        found = _ALIASES.get(_normalize(cell))
        if found and found[0] == "x":
            scale = _unit_of(cell)
            if scale is not None:
                return scale
    for line in lines[:_HEADER_SEARCH_LINES]:
        match = _UNITS_LINE.search(line)
        if match:
            return _TO_MM[match.group("unit").lower()]
    return Decimal(1)


def _fixed_width_cells(header: str, line: str) -> list[str]:
    """
    Cells of a space-aligned row, each token filed under the header label it sits under.

    Read by the header's column POSITIONS rather than by splitting on whitespace alone: a
    value like `100 nF` or a package with a space in it would otherwise shift every column
    after it, and this format has no quoting to fall back on. The header is the ruler, and
    two tokens in one column are one cell with a space in it.

    A column can be aligned either way under its label. Altium's .txt and KiCad's text
    columns are left-aligned, so a token starts under its label. KiCad's numbers are not:
    the exporter prints `%9.9s` for "PosX" and `%9.4f` for the value, so both are
    right-aligned and `44.0000` starts three characters left of `PosX` -- in the gap after
    the package. Filing by start alone put every number one column to the left (X in the
    package, Y in X, the rotation in Y and every rotation 0). So a token goes to the column
    it starts in when it also touches that column's label; failing that, to the first label
    it does touch (a right-aligned number reaching its label); and a token touching none --
    the second word of `100 nF` -- to the column it starts in. KiCad's header is commented
    -- `# Ref` -- so its first label starts two characters in while the data rows start at
    column zero; the first column is taken from zero.
    """
    spans = [(m.start(), m.end()) for m in re.finditer(r"\S+", header) if m.group() != "#"]
    if not spans:
        return []
    spans[0] = (0, spans[0][1])
    cells = [""] * len(spans)
    for token in re.finditer(r"\S+", line):
        start, end = token.span()
        touches = [i for i, (left, right) in enumerate(spans) if left < end and start < right]
        column = max((i for i, (left, _right) in enumerate(spans) if left <= start), default=0)
        if column not in touches and touches:
            column = touches[0]
        cells[column] = f"{cells[column]} {token.group()}".strip()
    return cells


def _headerless_columns(first: list[str]) -> dict[str, int]:
    """
    A file with no header row, in the one of two orders its first row fits.

    KiCad's own order is `ref, value, package, x, y, rot, side`; Eagle's mountsmd.ulp writes
    `ref, x, y, rot, value, package`. Whether the second and third cells are numbers tells
    them apart.
    """

    def numeric(cell: str) -> bool:
        return bool(_NUMBER.match(cell or ""))

    if len(first) >= 4 and numeric(first[1]) and numeric(first[2]):
        return {"ref": 0, "x": 1, "y": 2, "rotation": 3, "value": 4, "footprint": 5}
    return {name: i for i, name in enumerate(["ref", "value", "footprint", "x", "y", "rotation", "side"])}


def parse(text: str, skipped: list[str] | None = None) -> list[PosRow]:
    """
    Every placement in the file, in file order, coordinates in millimeters.

    Rows without a reference are skipped: a trailing blank line is not a component, and a
    nameless placement could not be matched to a BOM line or edited afterwards anyway. So
    are rows above the header -- a title block -- and comment lines. So is a row whose
    position or rotation is not a number a placement can have (see `_stored`); its reference
    is added to `skipped`, when given, so the caller can say which were left out.

    The header is found by looking, then everything below it is read the way the header is
    written: delimited by whatever it uses, or cut at its column positions when it is a
    space-aligned table (KiCad's `.pos`, Altium's `.txt`). A file with no header at all is
    read positionally, in KiCad's order or Eagle's, whichever its first row fits.
    """
    lines = text.lstrip("﻿").splitlines()
    found = _find_header(lines)
    rows: list[PosRow] = []

    if found is None:
        body = [line for line in lines if line.strip() and not line.startswith("#")]
        if not body:
            return rows
        delimiter = _delimiter(body[0])
        columns = _headerless_columns(_split(body[0], delimiter))
        scale = Decimal(1)
        # With no header to say which column is which, the numbers have to vouch for the
        # guess: a row whose x or y is not a number is not a placement. Without this, any
        # table at all reads as one -- a BOM dropped in the placement slot came back as
        # fourteen components at (Quantity, 0), its own header row among them, and the 3D
        # model was then "misaligned" against a board that was never described.
        records = (
            record
            for record in (_split(line, delimiter) for line in body)
            if all(index < len(record) and _NUMBER.match(record[index] or "") for index in (columns["x"], columns["y"]))
        )
    else:
        at, delimiter, columns = found
        header = lines[at]
        scale = _stated_scale(lines, header)
        body = [line for line in lines[at + 1 :] if line.strip() and not line.startswith("#")]
        if delimiter:
            records = (_split(line, delimiter) for line in body)
        else:
            records = (_fixed_width_cells(header.lstrip("﻿"), line) for line in body)

    for record in records:

        def cell(field: str, record=record) -> str:
            index = columns.get(field)
            return record[index].strip() if index is not None and index < len(record) else ""

        def number(field: str) -> str:
            # Only where the comma is not what separates the columns can it be a decimal
            # point; read as a number it was not one, and the coordinate came out 0.
            raw = cell(field)
            return _DECIMAL_COMMA.sub(r"\1.\2", raw) if delimiter != "," else raw

        reference = cell("ref")
        if not reference:
            continue
        x = _stored(_quantity(number("x"), scale), _LIMIT_MM)
        y = _stored(_quantity(number("y"), scale), _LIMIT_MM)
        rotation = _stored(_decimal(number("rotation")), _LIMIT_DEG)
        if x is None or y is None or rotation is None:
            if skipped is not None:
                skipped.append(reference)
            continue
        rows.append(
            PosRow(
                reference=reference,
                value=cell("value"),
                footprint=cell("footprint"),
                x=x,
                y=y,
                rotation=rotation,
                side=_side(cell("side"), cell("mirror")),
            )
        )
    return rows


#: How far outside the outline's box a placement may sit and still count as on the board: a
#: connector hanging over the edge, a fiducial on a rail. Generous, because the question is
#: "which corner is this file measured from", and every wrong corner misses by the width of
#: the board, not by two millimeters.
FRAME_MARGIN_MM = 3.0
#: The share of rows a corner has to land inside the outline to be the answer.
FRAME_FRACTION = 0.95


def frame_offset(rows, outline) -> tuple[float, float] | None:
    """
    The shift that carries a placement file's rows into the outline's frame, or None.

    Gerbers, drills and the placement file are supposed to share one frame, and from KiCad's
    board export they do. But a placement file can be written from the *aux origin* instead
    -- KiBot writes a panel's that way, from the panel outline's top-left corner, and KiCad
    will do the same for a board when the box is ticked -- while the gerbers stay in the
    sheet's frame. Then every row is off by exactly that corner, the picture puts the parts
    beside the board, and nothing in either file says so.

    So the file is asked which corner explains it. The rows as they are come first, and win
    whenever they already sit inside the outline; then each corner of the outline's box is
    tried as the origin the rows were measured from. A corner that puts nearly all of them
    on the board is the answer, as the shift to add. None when the rows fit as they are, when
    there is no outline to ask, or when no corner explains them -- a file that is off by
    something other than a corner is left alone rather than moved to a guess.

    `rows` need `x` and `y` (PosRow, a dict, a model row's `x_mm`/`y_mm` will not do);
    `outline` is the polygon as `[[x, y], ...]`.
    """
    points = [(float(p[0]), float(p[1])) for p in (outline or [])]
    rows = list(rows or [])
    if len(points) < 3 or not rows:
        return None
    min_x, max_x = min(p[0] for p in points), max(p[0] for p in points)
    min_y, max_y = min(p[1] for p in points), max(p[1] for p in points)
    xy = [(float(r.x), float(r.y)) for r in rows]

    def inside(dx: float, dy: float) -> float:
        hit = sum(
            1
            for x, y in xy
            if min_x - FRAME_MARGIN_MM <= x + dx <= max_x + FRAME_MARGIN_MM
            and min_y - FRAME_MARGIN_MM <= y + dy <= max_y + FRAME_MARGIN_MM
        )
        return hit / len(xy)

    if inside(0.0, 0.0) >= FRAME_FRACTION:
        return None
    # Top-left first because that is the corner KiKit uses; the order only matters when two
    # corners both explain the file, which takes a board narrower than the margin.
    for corner in ((min_x, max_y), (min_x, min_y), (max_x, max_y), (max_x, min_y)):
        if inside(corner[0], corner[1]) >= FRAME_FRACTION:
            return (round(corner[0], 4), round(corner[1], 4))
    return None


def _number(value: Decimal) -> str:
    return f"{Decimal(value):.6f}"


def render(rows) -> str:
    """
    The rows as a KiCad-format CSV.

    Quoting matches KiCad's exporter (text quoted, numbers bare) so a diff against the
    original shows the placements somebody changed and nothing else.
    """
    out = io.StringIO()
    out.write(",".join(HEADER) + "\n")
    for row in rows:
        reference = getattr(row, "reference", "")
        value = getattr(row, "value", "")
        footprint = getattr(row, "footprint", "")
        x = getattr(row, "x", None)
        x = x if x is not None else row.x_mm
        y = getattr(row, "y", None)
        y = y if y is not None else row.y_mm
        out.write(
            f'"{reference}","{value}","{footprint}",'
            f"{_number(x)},{_number(y)},{_number(row.rotation)},"
            f"{getattr(row, 'side', 'top')}\n"
        )
    return out.getvalue()
