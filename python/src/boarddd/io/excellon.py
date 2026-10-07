"""
Reading a drill file: where the holes are, how big, and whether they are plated.

Plated and non-plated matter to different people for different reasons -- a plated hole is a
via or a connected pad, a non-plated one is a mounting hole a screw goes through -- so the
viewer draws them differently, and that means knowing which is which per hole rather than per
file. KiCad writes both into one file and marks each tool with an aperture-function comment,
so the tool table is where the answer is.

Only what the viewer needs: hole centers, diameters, plating, and slots. Not a general
Excellon reader -- no repeat codes, no tool compensation, and of the canned cycles only the
one that makes a slot.

**A slot arrives in one of two forms, and a reader that knows only one loses every slot on
the board.** KiCad's drill export has a "use route command" switch, and both settings ship in
the same package: the main drill file is written the way the switch was left, and a
fab-house-specific PTH/NPTH pair beside it may differ. The two forms are

    X36.9Y-43.18G85X36.1Y-43.18        the G85 canned slot: both ends on one line
    G00X36.9Y-43.18 / M15 / G01X36.1Y-43.18 / M16     rout mode: position, plunge, cut, lift

and they describe the same hole. This read only the first for a long time, so a board whose
export used route commands showed no slots at all -- not in the flat view, not in three
dimensions -- with no error anywhere, because a drill file full of holes had parsed fine. One real
board was exactly that: 442 holes and none of its four slots. `distinct` is what keeps a package
carrying both forms from drawing every slot twice.

**Coordinates without a decimal point have implied decimals** (Excellon's original form, and
Altium's default: `INCH,LZ` with `;FILE_FORMAT=2:5`, so `X0250906` is 2.50906 in). The digit
split comes from the header (`;FILE_FORMAT=2:5`, a `METRIC,TZ,000.000` units line, KiCad's
`; FORMAT={3:3/ ...}`), else Excellon's defaults (inch 2:4, metric 3:3). `LZ` keeps leading
zeros, so the digits are read from the left; `TZ` keeps trailing zeros, so they are read from
the right, which is also the reading when the file does not say. A number with a point is
taken as written, and so is every number in a file that declares decimal coordinates
(KiCad's `FORMAT={-:-/ absolute / metric / decimal}`) or writes its coordinates with points.
Altium marks plating per section (`;TYPE=PLATED`, `;TYPE=NON_PLATED`) instead of per tool.

Pure: no settings, no filesystem.

Source: `magpie/pcb/excellon.py` at magpie commit 3a0374d3 (copied for boarddd phase F1; magpie's later
changes are re-diffed before magpie switches to this module).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

__all__ = ["Hole", "parse", "distinct"]

#: KiCad writes gerber attributes into Excellon as comments:
#:   ; #@! TA.AperFunction,Plated,PTH,ViaDrill
#:   ; #@! TA.AperFunction,NonPlated,NPTH,ComponentDrill
_APER_FUNCTION = re.compile(r"TA\.AperFunction,([^,\s]+)", re.I)
#: The drill's purpose, wherever it sits among the attribute's fields (blind/buried vias add
#: layer numbers before it).
_DRILL_FUNCTION = re.compile(r",(\w*Drill)\b", re.I)
#: A number as a drill file writes one: digits with at most one point, and at least one digit.
#: `[\d.]+` also matched `.`, `1..2` and `-.`, which float() refuses -- and a ValueError here
#: was an exception on every page that draws the board's holes.
_SIZE = r"(?:\d+\.?\d*|\.\d+)"
_NUM = r"-?" + _SIZE
_TOOL_DEF = re.compile(rf"^T(\d+)(?:[A-Z][-\d.]*)*C({_SIZE})", re.I)
_TOOL_SELECT = re.compile(r"^T(\d+)\s*$", re.I)
#: A hole, or a slot in the canned form: X…Y… optionally followed by G85X…Y….
#: Each half of the G85 end is optional on its own -- a tool that writes only the axis that
#: moved (`…G85X36.1`) still means a slot, and taking it for a plain hole loses the cut.
_HOLE = re.compile(rf"^X({_NUM})Y({_NUM})(?:G85(?:X({_NUM}))?(?:Y({_NUM}))?)?", re.I)
#: Rout-mode motion: G00 positions, G01 cuts, G02/G03 cut an arc. Each axis is optional --
#: an omitted one holds its last value, which is how a straight cut along X is written.
_ROUT = re.compile(rf"^G0([0-3])(?:X({_NUM}))?(?:Y({_NUM}))?", re.I)
#: Tool down (the cut starts here) and tool up (it ends). M17 is M16 under another name.
_PLUNGE, _LIFT = "M15", ("M16", "M17")


@dataclass(frozen=True)
class Hole:
    x: float
    y: float
    diameter: float
    plated: bool
    #: The far end of a routed slot, or None for a round hole.
    x2: float | None = None
    y2: float | None = None
    #: The tool's drill function from KiCad's attribute comment (`ViaDrill`, `ComponentDrill`,
    #: `MechanicalDrill`), or '' when the file does not say.
    function: str = ""
    #: The Excellon tool that drilled it, as the file numbers it (`T3`). Added in boarddd.
    tool: str = ""


#: Altium: `;FILE_FORMAT=2:5`. KiCad: `; FORMAT={3:3/ absolute / metric / suppress trailing zeros}`.
_FILE_FORMAT = re.compile(r"FILE_FORMAT\s*=\s*(\d)\s*:\s*(\d)", re.I)
_KICAD_FORMAT = re.compile(r"FORMAT=\{\s*([-\d]):([-\d])\s*/[^}]*\}", re.I)
#: The units line: `METRIC`, `INCH,LZ`, `METRIC,TZ,000.000`.
_UNITS = re.compile(r"^(METRIC|INCH)\s*(?:,\s*(LZ|TZ))?\s*(?:,\s*(0+)\.(0+))?\s*$", re.I)
_COORD = re.compile(r"[XY](-?[\d.]+)", re.I)


@dataclass(frozen=True)
class _Format:
    """How the file writes numbers: units, and for numbers without a point, the digit split."""

    metric: bool = True
    decimal: bool = True  # every number as written
    integer: int = 3
    places: int = 3
    leading: bool = False  # LZ: leading zeros kept, digits read from the left

    def number(self, text: str) -> float:
        if self.decimal or "." in text:
            return float(text)
        sign = -1.0 if text.startswith("-") else 1.0
        digits = text.lstrip("+-")
        if self.leading:
            digits = digits.ljust(self.integer + self.places, "0")
        return sign * int(digits or "0") / 10**self.places


def _numbers(text: str) -> tuple[bool, int]:
    """(is_metric, decimal places) from the header: the places are 0 when coordinates carry their point."""
    fmt = _format(text)
    return fmt.metric, 0 if fmt.decimal else fmt.places


def _format(text: str) -> _Format:
    header = text.split("\n%", 1)[0] if "\n%" in text else text[:4000]
    metric, leading, integer, places, said = True, None, None, None, False
    for raw in header.splitlines():
        line = raw.strip()
        units = _UNITS.match(line)
        if units:
            metric = units.group(1).upper() == "METRIC"
            if units.group(2):
                leading, said = units.group(2).upper() == "LZ", True
            if units.group(3):
                integer, places, said = len(units.group(3)), len(units.group(4)), True
            continue
        found = _FILE_FORMAT.search(line)
        if found:
            integer, places, said = int(found.group(1)), int(found.group(2)), True
            continue
        kicad = _KICAD_FORMAT.search(line)
        if kicad:
            lowered = line.lower()
            if "decimal" in lowered:
                return _Format(metric="inch" not in lowered and metric)
            if kicad.group(1).isdigit() and kicad.group(2).isdigit():
                integer, places, said = int(kicad.group(1)), int(kicad.group(2)), True
            if "trailing" in lowered:  # trailing zeros suppressed: leading ones kept
                leading = True
            elif "leading" in lowered:
                leading = False
            if "inch" in lowered:
                metric = False
    if not said:
        # Nothing about implied digits: a file writing its coordinates with points is decimal.
        body = text.split("\n%", 1)[1] if "\n%" in text else text
        coords = _COORD.findall(body[:200_000])
        if not coords or any("." in c for c in coords):
            return _Format(metric=metric)
    if integer is None:
        integer, places = (3, 3) if metric else (2, 4)
    return _Format(metric=metric, decimal=False, integer=integer, places=places, leading=bool(leading))


def parse(text: str, *, keep_empty: bool = False) -> list[Hole]:
    """
    Every hole in the file, round ones and slots alike.

    Coordinates are read in the file's own format (see the module docstring), in millimeters.

    Hits of a tool without a diameter (KiCad 10 writes a 0.00001 mm placeholder via drill as
    `T1C0.000`) are left out, as `boarddd/gerber`'s `parseExcellon` leaves them out; with
    `keep_empty` (a boarddd addition, for the board model, which keeps them with diameter 0)
    they are kept. A tool that was never defined is left out either way.
    """
    fmt = _format(text)
    scale = 1.0 if fmt.metric else 25.4
    number = fmt.number

    diameters: dict[int, float] = {}
    plating: dict[int, bool] = {}
    pending_plated: bool | None = None
    #: Altium's `;TYPE=PLATED` / `;TYPE=NON_PLATED`: the plating of every tool after it.
    section_plated: bool | None = None
    functions: dict[int, str] = {}
    pending_function = ""
    current: int | None = None
    holes: list[Hole] = []
    in_body = False
    #: Where the router is and whether it is in the board. Both only mean anything in rout
    #: mode; `at` survives a G00 so that the G01 after it knows where the cut began.
    at: tuple[float, float] | None = None
    down = False

    def add(x, y, x2=None, y2=None):
        """One hole from the current tool, or nothing if the tool is not one we know."""
        if current is None or current not in diameters:
            return
        diameter = diameters[current]
        if diameter <= 0 and not (keep_empty and diameter == 0):
            return
        # Four hundred digits is a well-formed number and an infinite float, and no hole.
        if not all(math.isfinite(v) for v in (x, y, diameter, *(e for e in (x2, y2) if e is not None))):
            return
        holes.append(
            Hole(
                x=x,
                y=y,
                diameter=diameter,
                plated=plating.get(current, True),
                x2=x2,
                y2=y2,
                function=functions.get(current, ""),
                tool=f"T{current}",
            )
        )

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue

        if line.startswith(";"):
            kind = line[1:].replace(" ", "").upper()
            if kind.startswith("TYPE="):
                section_plated = kind != "TYPE=NON_PLATED"
                continue
            found = _APER_FUNCTION.search(line)
            if found:
                pending_plated = found.group(1).strip().lower() != "nonplated"
                purpose = _DRILL_FUNCTION.search(line)
                pending_function = purpose.group(1) if purpose else ""
            continue

        if line == "%":
            in_body = True
            continue

        # The tool comes up between one slot and the next, and G05 puts the machine back in
        # drill mode. Forgetting the position at each is what stops the first cut of one slot
        # from being drawn from the last cut of the previous one, clear across the board.
        if line.startswith(_PLUNGE):
            down = True
            continue
        if line.startswith(_LIFT):
            down = False
            continue
        if line in ("M71", "M72"):  # metric / inch from here on
            scale = 1.0 if line == "M71" else 25.4
            continue
        if line in ("M48", "M30", "FMAT,2", "G90", "G05", "METRIC", "INCH"):
            if line == "G05":
                at, down = None, False
            continue

        definition = _TOOL_DEF.match(line)
        if definition and not in_body:
            tool = int(definition.group(1))
            diameters[tool] = float(definition.group(2)) * scale
            # A tool with no attribute comment is plated: PTH is overwhelmingly the common
            # case, and drawing a via as a mounting hole is the more misleading mistake.
            plating[tool] = (
                pending_plated if pending_plated is not None else section_plated if section_plated is not None else True
            )
            pending_plated = None
            functions[tool], pending_function = pending_function, ""
            continue

        select = _TOOL_SELECT.match(line)
        if select:
            current = int(select.group(1))
            at, down = None, False
            continue

        # Rout mode. A cut made with the tool down is a slot; a move with it up is just the
        # machine going somewhere. Each segment of a multi-segment rout becomes its own slot,
        # which draws correctly and is what KiCad's single-segment pad slots always are; an
        # arc is cut as its chord, because nothing that reaches this reader draws one.
        motion = _ROUT.match(line)
        if motion:
            x = number(motion.group(2)) * scale if motion.group(2) else (at[0] if at else None)
            y = number(motion.group(3)) * scale if motion.group(3) else (at[1] if at else None)
            if x is None or y is None:
                continue
            if down and at is not None and (x, y) != at:
                add(at[0], at[1], x, y)
            at = (x, y)
            continue

        hit = _HOLE.match(line)
        if hit and current is not None:
            x, y = number(hit.group(1)) * scale, number(hit.group(2)) * scale
            # A G85 end naming only the axis that moved holds the other from the start point.
            slotted = hit.group(3) is not None or hit.group(4) is not None
            x2 = number(hit.group(3)) * scale if hit.group(3) else (x if slotted else None)
            y2 = number(hit.group(4)) * scale if hit.group(4) else (y if slotted else None)
            add(x, y, x2, y2)

    return holes


def distinct(holes: list[Hole]) -> list[Hole]:
    """
    Each hole once, however many files listed it.

    A package can carry the drill file twice over -- a combined one and a fab house's split
    PTH/NPTH pair -- and every drill file in the package is read. On a flat picture the second
    disc lands on the first and nobody notices. Punched through a solid, two coincident loops
    are exactly what the triangulator cancels: one real board's mounting holes came out as
    solid caps, tan where the mask was open, and every hole cost the triangulation twice. So
    the list is made distinct here, for everything that reads it.

    Position, size and both ends of a slot, to the micron. The first listing wins.

    A slot's two ends are compared unordered, because the two forms a slot is written in are
    written by two different exporters and neither promises which end it starts from. The
    same cut listed once each way is one slot; kept as two it is the double-punch above,
    which does not look like two of anything -- it looks like a slot that is not there.
    """
    seen = set()
    out = []
    for hole in holes:
        ends = [(round(hole.x, 3), round(hole.y, 3))]
        if hole.x2 is not None and hole.y2 is not None:
            ends.append((round(hole.x2, 3), round(hole.y2, 3)))
        key = (tuple(sorted(ends)), round(hole.diameter, 3))
        if key in seen:
            continue
        seen.add(key)
        out.append(hole)
    return out
