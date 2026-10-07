"""
Reading a gerber for what it is a picture OF.

KiCad will plot the board outline onto every layer it exports -- one setting, "plot on all
layers" against Edge.Cuts -- and the file it writes says so, in Gerber X2: the strokes are
drawn with an aperture carrying `%TA.AperFunction,Profile*%`. So the outline is in the paste
file, the copper file, the silkscreen file and the mask file, and on every one of them it is a
picture of the board rather than a feature of that layer.

Which is invisible on a copper plot -- a 0.1 mm line at the rim, under the board's own edge --
and is not invisible at all once a layer becomes geometry. The 3D view traces the paste raster
and extrudes what it finds, so on one real board the outline came out as a 0.12 mm ring of solder
paste standing all the way around the board. It read as a fault in the viewer. It was a
faithful drawing of what the file said.

The attribute is the only thing that can tell those strokes from real ones: geometrically a
0.1 mm trace at the board edge is a 0.1 mm trace at the board edge, and a rule about position
would eat the real paste on a board with pads near its edge. So they go by attribute, and the
stored file is never touched -- that is what the fab house gets, and it is still served
byte for byte. This runs between the store and the renderer.

Pure: no settings, no filesystem.

Source: `magpie/pcb/gerber.py` at magpie commit 3a0374d3 (copied for boarddd phase F1; magpie's later
changes are re-diffed before magpie switches to this module).
"""

from __future__ import annotations

import re

__all__ = ["PROFILE_IS_NOT_A_FEATURE_OF", "without_profile", "layer_source", "plots_profile"]

#: The one `.AperFunction` value meaning "this is the board's outline, drawn here for
#: reference". Matched case-insensitively; the standard fixes the spelling, and a writer that
#: got the case wrong is still telling us what we need to know.
_PROFILE = "profile"

#: `%TA.AperFunction,Profile*%` -- an object attribute, which attaches to every aperture
#: defined after it until something clears it.
_APERTURE_FUNCTION = re.compile(r"^%TA\.AperFunction,([^,*]*)", re.I)
#: `%TD*%` clears every attribute, `%TD.AperFunction*%` clears just this one. Either ends it.
_CLEAR_ATTRIBUTE = re.compile(r"^%TD(\.AperFunction)?\*%", re.I)
#: `%ADD14C,0.100000*%` -- the definition that picks up whatever attribute is open.
_APERTURE_DEFINE = re.compile(r"^%ADD(\d+)")
#: `D14*` alone on a line: the aperture the following operations draw with.
_APERTURE_SELECT = re.compile(r"^D(\d+)\*$")
#: Anything that puts ink on the layer -- D01 draw, D02 move, D03 flash -- with or without a
#: leading G-code. The G-codes on their own lines are left alone: they set modes rather than
#: draw, and dropping a `G01*` would leave the file interpolating in whatever came before it.
_DRAW = re.compile(r"D0[123]\*$")
#: `G36*` opens a region: a filled area, whose boundary is walked with the SAME D01/D02 syntax
#: as a stroke but which is not drawn with the current aperture at all.
#:
#: Which matters here because KiCad does not re-select an aperture on the way in. One real board's
#: silkscreen ends with nine thousand lines of region -- every glyph on the board is one -- and
#: they follow the profile block with no D-code between, so a rule that only stops stripping
#: when the next aperture is selected strips the entire silkscreen and leaves a board with no
#: text on it. It looked exactly like a fix.
#:
#: A profile is always stroked, so leaving the region alone loses nothing. This fails safe in
#: the direction that matters: at worst a profile stroke after a region survives, which is the
#: fault this module exists to fix rather than a feature it has deleted.
_REGION = re.compile(r"^G3[67]\*")

#: The layers a board profile is not a feature of. Every one is a physical layer of the
#: finished board, and the board's own edge is not printed onto any of them.
#:
#: The outline layer is deliberately absent: there the profile is not a reference drawing over
#: the real content, it IS the content, and stripping it would empty the file that the board's
#: shape, its 3D extrusion and the flat view's clip are all read from. So are the assembly and
#: user drawings, which are drawings ABOUT the board -- an outline is the context that makes a
#: dimension readable, and they get their own unclipped canvas for exactly that reason.
PROFILE_IS_NOT_A_FEATURE_OF = frozenset({"gerber_copper", "gerber_mask", "gerber_paste", "gerber_silk"})


def _profile_apertures(lines: list[str]) -> set[str]:
    """The D-codes defined while the Profile attribute was open."""
    found: set[str] = set()
    attribute = ""
    for line in lines:
        stripped = line.strip()
        opened = _APERTURE_FUNCTION.match(stripped)
        if opened:
            attribute = opened.group(1).strip().lower()
            continue
        if _CLEAR_ATTRIBUTE.match(stripped):
            attribute = ""
            continue
        defined = _APERTURE_DEFINE.match(stripped)
        if defined and attribute == _PROFILE:
            found.add(defined.group(1))
    return found


def plots_profile(text: str) -> bool:
    """
    Whether this layer has the board outline plotted onto it.

    Worth asking separately from stripping it, because it is a fact about the EXPORT and not
    about this app: a stencil house cuts the paste layer as given, so an outline in it is a
    0.1 mm slit around the board in the real stencil. Somebody should hear about that rather
    than have it quietly cleaned up on the way to a picture.
    """
    return bool(text) and "AperFunction" in text and bool(_profile_apertures(text.splitlines()))


def without_profile(text: str) -> str:
    """
    The same layer with the board profile taken out of it.

    Everything drawn with an aperture attributed `Profile` goes; every other byte, the aperture
    definitions included, stays where it was. A file carrying no such attribute is returned
    unchanged and is not even rebuilt -- plenty of exports are made this way, which is why the fault
    was board-dependent and why "it works on the reference package" proved nothing.
    """
    if not text or "AperFunction" not in text:
        return text

    lines = text.splitlines(keepends=True)
    # Two passes rather than one: an aperture can be selected before the block defining it has
    # been read. KiCad puts every definition in a header, so in practice one pass would do --
    # and relying on that is how a file from some other tool comes out silently half-stripped.
    profile = _profile_apertures(lines)
    if not profile:
        return text

    kept = []
    drawing = False
    for line in lines:
        stripped = line.strip()
        selected = _APERTURE_SELECT.match(stripped)
        if selected:
            drawing = selected.group(1) in profile
            # The selection itself is kept. It costs one line, and an aperture that draws
            # nothing is easier to diff against the original than a D-code the reader never
            # saw selected.
            kept.append(line)
            continue
        if _REGION.match(stripped):
            drawing = False
        if drawing and _DRAW.search(stripped):
            continue
        kept.append(line)
    return "".join(kept)


def layer_source(kind: str, text: str) -> str:
    """
    A layer's gerber as it should be drawn, given what kind of layer it is.

    The one call between the store and any renderer, which is the point of it being one call:
    the flat view, the board's faces and the paste geometry are three renderings of the same
    files, and two of them agreeing about the outline while the third draws it is worse than
    any single answer would have been.
    """
    return without_profile(text) if kind in PROFILE_IS_NOT_A_FEATURE_OF else text
