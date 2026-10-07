"""
The board outline, plotted onto layers it is not a feature of (`boarddd.io.gerber`).

Run against a real export rather than a hand-written excerpt: royalblue54L_feather's paste,
exported by kicad-cli with Edge.Cuts plotted on every layer (fixtures/generated/profile), which
is how a ring of solder paste around a real board came about. A fixture written from memory
would have agreed with whatever the stripper happened to do.

The negative case runs against the same board's own fab export, which is made the other way.
That pair is the point -- this fault is export-dependent, so a test on one export proves nothing
about the other, and "it looks right on the reference package" is how it survived in the first
place.

Ported from magpie `tests/pcb/test_gerber.py` at 3a0374d3.
"""

from __future__ import annotations

from boarddd.io import gerber

from conftest import GENERATED, RB_FAB

PROFILE = GENERATED / "profile"

#: Coordinate lines from the profile block in the fixture: the rounded rectangle KiCad drew
#: around the board with the 0.1 mm pen it attributed `Profile`.
PROFILE_STROKES = [
    "X177520000Y-113870000D02*",
    "X174980000Y-116410000I-2540000J0D01*",
    "X174980000Y-93550000D01*",
    "X119100000Y-96090000D01*",
    "X121640000Y-116410000D01*",
]
#: Two real paste apertures out of the same file, which must survive untouched.
PADS = ["X131210000Y-112060000D03*", "X132170000Y-112060000D03*"]


def paste() -> str:
    return (PROFILE / "RoyalBlue54L-Feather-F_Paste-profile.gbr").read_text()


def test_the_outline_kicad_plotted_onto_the_paste_layer_comes_back_out():
    """
    The fault as it was reported on a real board: a ring of solder paste all the way round.

    Nothing about these strokes says "outline" except the attribute -- geometrically a 0.1 mm
    trace at the board edge is a 0.1 mm trace at the board edge -- so the attribute is the only
    thing that can tell them from real paste. A rule about position would eat the real deposits
    on a board with pads near its rim.
    """
    text = paste()
    assert all(stroke in text for stroke in PROFILE_STROKES), "the fixture lost its outline"

    stripped = gerber.without_profile(text)

    for stroke in PROFILE_STROKES:
        assert stroke not in stripped, f"{stroke} is the board outline, not a paste deposit"
    for pad in PADS:
        assert pad in stripped, "a real deposit went out with the outline"


def test_only_the_profile_aperture_stops_drawing():
    """
    The attribute attaches to apertures defined while it is open and stops at `%TD*%`.

    An implementation that let it run to the end of the header would strip every pad in the
    file and leave a board with no paste at all -- which, from a distance and in a picture,
    looks exactly like a fix.
    """
    text = paste()
    stripped = gerber.without_profile(text)

    assert stripped.count("D03*") == text.count("D03*") > 100, "a flash is a pad, and all survive"
    assert "%ADD10RoundRect" in stripped, "the aperture definitions are left where they were"
    assert "%ADD59C,0.100000*%" in stripped and "\nD59*\n" in stripped, "the selection stays; the drawing goes"


def test_a_layer_without_the_attribute_is_returned_untouched():
    """
    The board's own fab export is made without the outline on its layers. A file with nothing
    to strip must come back byte for byte -- anything else rewrites every stored package on the
    strength of a fault it does not have.
    """
    text = (RB_FAB / "RoyalBlue54L-Feather-F_Paste.gbr").read_text()
    assert "AperFunction,Profile" not in text

    assert gerber.without_profile(text) == text
    assert not gerber.plots_profile(text)


def test_the_outline_layer_keeps_its_outline():
    """
    On Edge_Cuts the profile is not a reference drawing over the real content, it IS the
    content. Stripping by attribute and forgetting to ask which layer it is would empty the
    file the board's shape, its 3D extrusion and the flat view's clip are all read from.
    """
    text = paste()

    assert gerber.layer_source("gerber_edge", text) == text
    assert gerber.layer_source("gerber_paste", text) != text
    edge = (RB_FAB / "RoyalBlue54L-Feather-Edge_Cuts.gbr").read_text()
    assert gerber.layer_source("gerber_edge", edge) == edge


def test_the_drawings_about_the_board_keep_theirs_too():
    """
    An assembly drawing is a drawing OF the board, and its outline is the context that makes a
    dimension readable. They are rendered on their own unclipped canvas for the same reason.
    """
    text = paste()

    assert gerber.layer_source("gerber_fab", text) == text
    assert gerber.layer_source("gerber_doc", text) == text


def test_an_export_that_plots_its_profile_can_be_told_apart_from_one_that_does_not():
    """
    Worth asking separately from stripping it, because it is a fact about the EXPORT and not
    about this app. A stencil house cuts the paste layer as given, so an outline in it is a
    0.1 mm slit around the board in the real stencil -- somebody should hear about that rather
    than have it quietly cleaned up on the way to a picture.
    """
    assert gerber.plots_profile(paste())
    assert not gerber.plots_profile((RB_FAB / "RoyalBlue54L-Feather-F_Cu.gbr").read_text())
    assert not gerber.plots_profile("")


def test_the_profile_is_found_even_when_the_header_is_not_read_first():
    """
    Two passes, because an aperture can be selected before the block defining it has been read.
    KiCad puts every definition in a header, so one pass would do -- and relying on that is how
    a file from some other tool comes out silently half-stripped.
    """
    out_of_order = (
        "%FSLAX45Y45*%\n%MOMM*%\n"
        "D14*\nX1000000Y1000000D02*\nX2000000Y2000000D01*\n"
        "%TA.AperFunction,Profile*%\n%ADD14C,0.100000*%\n%TD*%\n"
        "M02*\n"
    )

    stripped = gerber.without_profile(out_of_order)

    assert "X2000000Y2000000D01*" not in stripped
    assert "D14*" in stripped


def test_a_region_is_not_drawn_with_the_current_aperture():
    """
    The one that got away, and the reason to measure a fix rather than read it.

    KiCad ended one real board's silkscreen with nine thousand lines of region -- every glyph
    on the board is one -- following the profile block with no D-code in between, because a
    region is a filled area rather than something stroked with an aperture. A rule that only
    stops stripping at the next aperture selection therefore ate the whole silkscreen, and the
    board came back clean, plausible and with no text on it.

    KiCad 10 writes the profile block last, so the case is built here: the real paste file
    with a glyph-like region (and one ordinary stroke after it) appended right after its
    profile block, the way the older export had it.
    """
    text = paste()
    region = "G36*\nX150000000Y-100000000D02*\nX151000000Y-100000000D01*\nX151000000Y-101000000D01*\n"
    region += "X150000000Y-101000000D01*\nX150000000Y-100000000D01*\nG37*\n"
    text = text.replace("M02*\n", region + "X152000000Y-102000000D01*\nM02*\n")
    assert region.count("D01*") == 4

    stripped = gerber.without_profile(text)

    assert region in stripped, "a region is not drawn with the profile aperture"
    assert "X152000000Y-102000000D01*" in stripped, "nor is a stroke after it"
    assert PROFILE_STROKES[0] not in stripped, "and the outline still goes"


def test_stripping_takes_the_outline_and_nothing_else():
    """
    The profile block is the same closed rounded rectangle in every layer of an export, so on
    a package plotted this way every layer should lose the same handful of lines. A number
    that moves from layer to layer means something layer-specific is being eaten -- which is
    exactly how the silkscreen regions above announced themselves.
    """
    lost = {
        side: len(text.splitlines()) - len(gerber.without_profile(text).splitlines())
        for side, text in (
            ("top", paste()),
            ("bottom", (PROFILE / "RoyalBlue54L-Feather-B_Paste-profile.gbr").read_text()),
        )
    }

    assert lost["top"] == lost["bottom"] == 16, lost
