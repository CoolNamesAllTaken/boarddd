"""
The board's real dimensions, from the .gbrjob file.

Worth its own module for one reason: this is the only trustworthy statement of how big the
board is. Measuring the gerbers does not work -- a KiCad export made with "plot drawing sheet"
enabled draws the sheet border and title block on *every* layer, so on one real package
every single gerber spans 412 x 259 mm around a board that is 14.55 x 23.55. A viewer
that framed itself on gerber extents would show a drawing sheet with a speck in the middle.

The .gbrjob is JSON the gerber standard defines, KiCad writes it, and `GeneralSpecs.Size` is
the board. Absent or malformed, the answer is None and the viewer falls back to framing the
placements -- which is what it is really showing anyway.

Pure: no settings, no filesystem.

Source: `magpie/pcb/jobfile.py` at magpie commit 3a0374d3 (copied for boarddd phase F1; magpie's later
changes are re-diffed before magpie switches to this module).
"""

from __future__ import annotations

import json
import math

__all__ = ["board_size", "project_name", "looks_like_panel"]

#: The largest side accepted: a kilometre. Anything bigger is not a board (and would overflow a numeric(12,6) column).
MAX_SIDE_MM = 10**6


def board_size(text: str) -> tuple[float, float] | None:
    """(width, height) in millimeters, or None if the job file does not say."""
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None

    size = _specs(data).get("Size") or {}
    try:
        width = float(size["X"])
        height = float(size["Y"])
    except (KeyError, TypeError, ValueError, OverflowError):
        return None
    # `"NaN"`, `"Infinity"` and 1e400 are all floats; none is a board, and each was a 500 when
    # the package's board size -- numeric(12,6), so under a kilometre -- was asked to hold it.
    if not all(math.isfinite(side) and 0 < side < MAX_SIDE_MM for side in (width, height)):
        return None
    return width, height


def project_name(text: str) -> str:
    """
    What KiCad called the project, or '' if the job file does not say.

    A KiKit panel is a project of its own, named `<board>-panel`, and that suffix is the one
    thing that tells a panel export from a board export before either is opened -- the gerbers
    inside carry the same layer headers either way.
    """
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return ""
    if not isinstance(data, dict):
        return ""
    project = _specs(data).get("ProjectId") or {}
    return str(project.get("Name") or "").strip() if isinstance(project, dict) else ""


def _specs(data: dict) -> dict:
    """
    The `GeneralSpecs` object, or {} when the file has something else there.

    Checked, because an application reads this for every listing of the board's files:
    `{"GeneralSpecs": "x"}` was an AttributeError, and so a server error on the very page
    that offers to remove the file.
    """
    specs = data.get("GeneralSpecs")
    return specs if isinstance(specs, dict) else {}


def looks_like_panel(name: str) -> bool:
    """Whether a project name is a KiKit panel's. The `-panel` suffix is KiKit's own."""
    return name.lower().endswith("-panel") or name.lower().endswith("_panel")
