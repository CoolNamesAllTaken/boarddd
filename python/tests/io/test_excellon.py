"""
The holes through the board: `boarddd.io.excellon`.

Ported from magpie's tests/pcb/test_geometry.py (drill half) and test_excellon_formats.py. The
file-based tests run against public exports: KiCad's royalblue54L_feather demo
(fixtures/royalblue54L_feather/fab), the slots board (test/fixtures/slots-board) and its
generated variants (fixtures/generated/drill: rout-mode and G85 slots, inches with implied
decimals).
"""

from __future__ import annotations

import pytest

from boarddd.io import excellon

from conftest import GENERATED, RB_FAB

MM = 0.02  # tolerance for a coordinate in millimeters
INCH_MM = 0.003  # an inch export rounds to 0.0001 in


def _read(path) -> list[excellon.Hole]:
    return excellon.parse(path.read_text("utf-8"))


def _at(text: str) -> list[tuple[float, float]]:
    return [(round(h.x, 4), round(h.y, 4)) for h in excellon.parse(text)]


# ─── Real exports ────────────────────────────────────────────────────────────


def test_the_real_drill_file_parses():
    holes = _read(RB_FAB / "RoyalBlue54L-Feather-PTH.drl")

    assert len(holes) == 86
    assert {round(h.diameter, 2) for h in holes} == {0.2, 0.6, 1.0}
    assert all(h.plated for h in holes), "the PTH file's holes are all plated"
    first = holes[0]
    assert first.x == pytest.approx(135.33, abs=MM)
    assert first.y == pytest.approx(-106.58, abs=MM)
    assert sum(h.x2 is not None for h in holes) == 4, "the USB-C shell tabs are oval"


def test_the_non_plated_file_is_non_plated():
    holes = _read(RB_FAB / "RoyalBlue54L-Feather-NPTH.drl")

    assert len(holes) == 9
    assert not any(h.plated for h in holes)
    assert sorted({round(h.diameter, 3) for h in holes}) == [0.65, 0.991, 2.5]


def test_the_drill_function_comes_from_the_attribute_comment():
    holes = excellon.parse((RB_FAB / "RoyalBlue54L-Feather-PTH.drl").read_text(), keep_empty=True)

    assert {h.function for h in holes if h.diameter == 0} == {"ViaDrill"}
    assert {h.function for h in holes if h.diameter > 0} == {"ComponentDrill"}


# ─── boarddd additions: the tool, and placeholder tools ───────────────────────


def test_each_hole_names_its_tool():
    holes = excellon.parse((RB_FAB / "RoyalBlue54L-Feather-PTH.drl").read_text(), keep_empty=True)

    by_tool = {}
    for h in holes:
        by_tool[h.tool] = by_tool.get(h.tool, 0) + 1
    assert by_tool == {"T1": 183, "T2": 25, "T3": 4, "T4": 57}
    assert {round(h.diameter, 3) for h in holes if h.tool == "T4"} == {1.0}


def test_a_tool_with_no_diameter_is_left_out_unless_asked_for():
    """
    KiCad 10 writes the demo's 0.00001 mm placeholder via drill as `T1C0.000`. The renderer
    and the default read leave those hits out (as boarddd/gerber's parseExcellon does); the
    board model keeps them, with diameter 0.
    """
    text = (RB_FAB / "RoyalBlue54L-Feather-PTH.drl").read_text()

    assert len(excellon.parse(text)) == 86
    kept = excellon.parse(text, keep_empty=True)
    assert len(kept) == 86 + 183
    assert all(h.tool == "T1" and h.function == "ViaDrill" for h in kept if h.diameter == 0)


def test_keep_empty_still_drops_an_undefined_or_negative_tool():
    text = "M48\nMETRIC\nT1C0.000\nT2C-1.0\n%\nG90\nT1\nX1.0Y1.0\nT2\nX2.0Y2.0\nT9\nX3.0Y3.0\nM30\n"

    assert [(h.x, h.diameter, h.tool) for h in excellon.parse(text, keep_empty=True)] == [(1.0, 0.0, "T1")]
    assert excellon.parse(text) == []


def test_a_tool_numbered_with_leading_zeros_is_named_without_them():
    holes = excellon.parse("M48\nMETRIC\nT01C0.3\n%\nT01\nX1.0Y1.0\nM30\n")

    assert holes[0].tool == "T1"


# ─── Plating, slots, units ───────────────────────────────────────────────────


def test_plated_and_non_plated_are_told_apart():
    """
    The distinction the viewer draws. KiCad writes both into one file and marks each tool, so
    reading it per file -- which is all the renderer offers -- would have to be a guess, and
    drawing a mounting hole as a via is a fabrication error.
    """
    text = (
        "M48\nMETRIC\n"
        "; #@! TA.AperFunction,Plated,PTH,ViaDrill\nT1C0.300\n"
        "; #@! TA.AperFunction,NonPlated,NPTH,ComponentDrill\nT2C3.200\n"
        "%\nG90\nT1\nX10.0Y-10.0\nT2\nX20.0Y-20.0\nM30\n"
    )

    holes = excellon.parse(text)

    assert [(h.diameter, h.plated) for h in holes] == [(0.3, True), (3.2, False)]


def test_a_combined_file_keeps_plating_per_tool():
    """The slots board exported as one file: plated and non-plated tools side by side."""
    holes = _read(GENERATED / "drill" / "alternate" / "slots.drl")

    assert [(h.diameter, h.plated) for h in holes if not h.plated] == [(0.65, False), (0.65, False), (1.5, False)]
    assert sum(h.plated for h in holes) == 7


def test_a_tool_with_no_attribute_is_taken_as_plated():
    """Plated is overwhelmingly the common case, and the more misleading mistake is the other
    way round: a via drawn as a hole for a screw."""
    holes = excellon.parse("M48\nMETRIC\nT1C0.250\n%\nG90\nT1\nX5.0Y-5.0\nM30\n")

    assert holes[0].plated is True


def test_a_slot_keeps_both_ends():
    """A routed slot is a line, not a point; drawing it as a dot loses most of the hole."""
    text = "M48\nMETRIC\nT1C1.000\n%\nG90\nT1\nX5.0Y-5.0G85X9.0Y-5.0\nM30\n"

    holes = excellon.parse(text)

    assert (holes[0].x, holes[0].x2) == (5.0, 9.0)


@pytest.mark.parametrize(
    "line",
    ["T2C.", "X1..2Y2", "G00X-.Y1", "X" + "9" * 400 + "Y2", "X1Y2G85X" + "9" * 400],
    ids=["bare-point", "two-points", "bare-minus", "huge", "huge-slot-end"],
)
def test_a_drill_line_that_is_not_a_number_is_passed_over(line):
    """
    `[\\d.]+` matched `.`, `1..2` and `-.`, and float() refused them: an exception on every
    page drawing the board's holes. The rest of the file is still read.
    """
    text = f"M48\nMETRIC\nT1C0.300\n{line}\n%\nG90\nT1\n{line}\nX5.0Y-5.0\nM30\n"

    holes = excellon.parse(text)

    assert [(h.x, h.y, h.diameter) for h in holes] == [(5.0, -5.0, 0.3)]


def test_inches_become_millimeters():
    holes = excellon.parse("M48\nINCH\nT1C0.100\n%\nG90\nT1\nX1.0Y-1.0\nM30\n")

    assert holes[0].diameter == pytest.approx(2.54, abs=0.001)
    assert holes[0].x == pytest.approx(25.4, abs=0.001)


def test_a_file_with_no_tools_yields_nothing():
    assert excellon.parse("M48\nMETRIC\n%\nG90\nX1.0Y-1.0\nM30\n") == []


# ─── The two ways a drill file writes a slot ─────────────────────────────────

#: The same 4 mm slot, written both ways KiCad's drill export can write one. Which one a
#: package carries is a checkbox somebody ticked ("use route command"), and for a long time
#: only the first was read -- so a board exported the other way showed no slots at all.
_CANNED = "M48\nMETRIC\nT1C1.000\n%\nG90\nG05\nT1\nX5.0Y-5.0G85X9.0Y-5.0\nM30\n"
_ROUTED = "M48\nMETRIC\nT1C1.000\n%\nG90\nG05\nT1\nG00X5.0Y-5.0\nM15\nG01X9.0Y-5.0\nM16\nG05\nM30\n"


def test_a_slot_cut_with_route_commands_is_the_same_slot():
    """
    The form KiCad writes with "use route command": the router is positioned (G00), plunged
    (M15), moved while down (G01), and lifted (M16). Read as a drill file of plain X/Y lines,
    not one of those lines is a hole, so every slot on the board vanished silently -- the
    file parsed, it just had nothing in it.
    """
    canned, routed = excellon.parse(_CANNED), excellon.parse(_ROUTED)

    assert routed == canned == [excellon.Hole(x=5.0, y=-5.0, diameter=1.0, plated=True, x2=9.0, y2=-5.0, tool="T1")]


def test_a_move_with_the_tool_up_is_not_a_slot():
    """G00 is the machine going somewhere. Cutting on the way there would draw a slot from
    the end of one to the start of the next, straight across the board."""
    text = (
        "M48\nMETRIC\nT1C1.000\n%\nG90\nT1\n"
        "G00X5.0Y-5.0\nM15\nG01X9.0Y-5.0\nM16\n"
        "G00X40.0Y-40.0\nM15\nG01X44.0Y-40.0\nM16\nM30\n"
    )

    holes = excellon.parse(text)

    assert [(h.x, h.x2) for h in holes] == [(5.0, 9.0), (40.0, 44.0)]


def test_an_axis_the_cut_did_not_move_holds_its_value():
    """A straight cut along one axis need only name that axis; the other is where it was."""
    text = "M48\nMETRIC\nT1C1.000\n%\nG90\nT1\nG00X5.0Y-5.0\nM15\nG01Y-9.0\nM16\nM30\n"

    holes = excellon.parse(text)

    assert [(h.x, h.y, h.x2, h.y2) for h in holes] == [(5.0, -5.0, 5.0, -9.0)]


def test_a_canned_slot_naming_only_the_axis_that_moved_is_still_a_slot():
    """Taking `…G85X9.0` for a plain hole loses the cut and keeps the dot."""
    holes = excellon.parse("M48\nMETRIC\nT1C1.000\n%\nG90\nT1\nX5.0Y-5.0G85X9.0\nM30\n")

    assert [(h.x, h.y, h.x2, h.y2) for h in holes] == [(5.0, -5.0, 9.0, -5.0)]


def test_a_package_carrying_both_forms_draws_each_slot_once():
    """
    A package can carry a combined drill file and a fab house's split pair, and the two need
    not have used the same form. Two coincident stadiums are what the triangulator cancels --
    the slot comes out capped solid, which is the symptom the missing slot had in the first
    place.
    """
    both = excellon.parse(_CANNED) + excellon.parse(_ROUTED)

    assert len(excellon.distinct(both)) == 1


def test_which_end_a_slot_starts_from_does_not_make_it_a_different_slot():
    """Two exporters, one cut. Neither promises which end it writes first."""
    forward = excellon.Hole(x=5.0, y=-5.0, diameter=1.0, plated=True, x2=9.0, y2=-5.0)
    backward = excellon.Hole(x=9.0, y=-5.0, diameter=1.0, plated=True, x2=5.0, y2=-5.0)

    assert len(excellon.distinct([forward, backward])) == 1


def test_the_same_export_finds_the_same_slots_whichever_form_it_reads():
    """
    The reason the two forms are tested against each other rather than against a hand-written
    expectation: one real export's drill file was route commands and its fab-house PTH file was
    G85, and before this they disagreed about whether the board had slots at all. Here the slots
    board is exported both ways by kicad-cli: eight slots (seven plated, one not) and
    two round mounting holes.
    """
    routed = _read(GENERATED / "drill" / "route" / "slots.drl")
    canned = _read(GENERATED / "drill" / "alternate" / "slots.drl")

    slots = [h for h in routed if h.x2 is not None]
    assert len(slots) == 8
    assert slots == [h for h in canned if h.x2 is not None]
    assert routed == canned
    # Nothing else moved: the round holes are still round, and the tool table still says
    # which of them a screw goes through.
    assert [(h.diameter, h.plated) for h in routed if h.x2 is None] == [(0.65, False), (0.65, False)]
    assert len(excellon.distinct(routed + canned)) == len(routed)


# ─── Number formats ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("zeros", ["suppressleading", "suppresstrailing", "keep"])
def test_an_inch_export_with_implied_decimals_lands_where_the_metric_one_does(zeros):
    """
    The slots board in inches, written with each of KiCad's zero formats (`FORMAT={2:4/ ...}`,
    no decimal point): every hole within the 0.0001 in the format keeps of the mm export.
    """
    metric = _read(GENERATED / "drill" / "alternate" / "slots.drl")
    inch = _read(GENERATED / "drill" / f"inch-{zeros}" / "slots.drl")

    assert "." not in (GENERATED / "drill" / f"inch-{zeros}" / "slots.drl").read_text().split("%", 1)[1]
    assert len(inch) == len(metric)
    for a, b in zip(inch, metric, strict=True):
        assert (a.plated, a.x2 is None) == (b.plated, b.x2 is None)
        assert a.x == pytest.approx(b.x, abs=INCH_MM) and a.y == pytest.approx(b.y, abs=INCH_MM)
        assert a.diameter == pytest.approx(b.diameter, abs=INCH_MM)
        if b.x2 is not None:
            assert (a.x2, a.y2) == pytest.approx((b.x2, b.y2), abs=INCH_MM)


def test_altium_inch_leading_zeros_2_5():
    """As Altium 24 writes it: seven digits, read from the left."""
    text = (
        "M48\n;Layer_Color=9474304\n;FILE_FORMAT=2:5\nINCH,LZ\n;TYPE=PLATED\n"
        "T01F00S00C0.01200\n;TYPE=NON_PLATED\nT02F00S00C0.12500\n%\n"
        "T01\nX0040913Y0021549\nT02\nX0250906Y-0010000\nM30\n"
    )
    holes = excellon.parse(text)
    assert [(round(h.x, 4), round(h.y, 4)) for h in holes] == [(10.3919, 5.4734), (63.7301, -2.54)]
    assert [h.plated for h in holes] == [True, False]
    assert holes[0].diameter == pytest.approx(0.3048)


def test_leading_zeros_kept_trailing_suppressed():
    assert _at("M48\n;FILE_FORMAT=2:5\nINCH,LZ\nT1C0.02\n%\nT1\nX025Y01\nM30\n") == [(63.5, 25.4)]


def test_trailing_zeros_kept_leading_suppressed():
    assert _at("M48\n;FILE_FORMAT=2:5\nINCH,TZ\nT1C0.02\n%\nT1\nX250000Y-100000\nM30\n") == [(63.5, -25.4)]


def test_metric_format_on_the_units_line():
    assert _at("M48\nMETRIC,TZ,000.000\nT1C0.8\n%\nT1\nX12345Y500\nM30\n") == [(12.345, 0.5)]


def test_excellon_defaults_without_a_format():
    """Inch 2:4 with leading zeros suppressed, when nothing says otherwise."""
    assert _at("M48\nINCH\nT1C0.02\n%\nT1\nX10000Y2500\nM30\n") == [(25.4, 6.35)]


def test_kicad_suppressed_zero_formats():
    trailing = (
        "M48\n; FORMAT={3:3/ absolute / metric / suppress trailing zeros}\nMETRIC\nT1C0.8\n%\nT1\nX0125Y-0005\nM30\n"
    )
    leading = (
        "M48\n; FORMAT={3:3/ absolute / metric / suppress leading zeros}\nMETRIC\nT1C0.8\n%\nT1\nX125000Y-5000\nM30\n"
    )
    assert _at(trailing) == [(12.5, -0.5)]
    assert _at(leading) == [(125.0, -5.0)]


def test_decimal_files_are_read_as_written():
    kicad = (
        "M48\n; FORMAT={-:-/ absolute / metric / decimal}\nFMAT,2\nMETRIC\nT1C0.6\n%\n"
        "G90\nG05\nT1\nX142Y-82\nX141.5Y-82.25\nM30\n"
    )
    assert _at(kicad) == [(142.0, -82.0), (141.5, -82.25)]
    # No format said, coordinates with points: whole numbers are whole millimeters too.
    assert _at("M48\nMETRIC\nT1C0.6\n%\nT1\nX5Y-5\nX5.5Y-5\nM30\n") == [(5.0, -5.0), (5.5, -5.0)]
    # A point always wins, even in an implied-decimal file.
    assert _at("M48\n;FILE_FORMAT=2:5\nINCH,LZ\nT1C0.02\n%\nT1\nX1.0Y0025\nM30\n") == [(25.4, 6.35)]


def test_units_switch_in_the_body():
    text = "M48\nMETRIC\nT1C0.6\n%\nT1\nX1.0Y1.0\nM72\nX1.0Y1.0\nM71\nX2.0Y2.0\nM30\n"
    assert _at(text) == [(1.0, 1.0), (25.4, 25.4), (2.0, 2.0)]


def test_kicad_attribute_comments_still_decide_plating():
    text = (
        "M48\n;TYPE=PLATED\nMETRIC\n; #@! TA.AperFunction,NonPlated,NPTH,ComponentDrill\nT1C3.2\n%\nT1\nX1.0Y1.0\nM30\n"
    )
    assert [h.plated for h in excellon.parse(text)] == [False]
