"""
The board's shape: `boarddd.io.outline`.

Ported from magpie's tests/pcb/test_geometry.py (outline half). The file-based tests run against
public exports: KiCad's royalblue54L_feather demo (a 58.42 x 22.86 mm board with 2.54 mm rounded
corners, fixtures/royalblue54L_feather/fab), the same Edge.Cuts plotted with the drawing sheet
(fixtures/generated/outline/sheet), pic_programmer and the slots board (test/fixtures).
"""

from __future__ import annotations

import math
import re

import pytest

from boarddd.io import gbrjob, outline

from conftest import GENERATED, RB_FAB, TEST_FIXTURES

MM = 0.02  # tolerance for a coordinate in millimeters

RB_EDGE = RB_FAB / "RoyalBlue54L-Feather-Edge_Cuts.gbr"
SHEET = GENERATED / "outline" / "sheet"
SHEET_EDGE = SHEET / "RoyalBlue54L-Feather-Edge_Cuts.gbr"
#: The board, as the job file states it: the outline's box plus the 0.1 mm line width.
RB_SIZE = (58.52, 22.96)


def _gerber(body: str) -> str:
    return "%FSLAX46Y46*%\n%MOMM*%\n%ADD10C,0.050000*%\nD10*\n" + body + "M02*\n"


def _rect(x0, y0, x1, y1) -> str:
    """A rectangle drawn as four separate strokes, the way KiCad emits an outline."""
    corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]
    out = []
    for (ax, ay), (bx, by) in zip(corners, corners[1:], strict=False):
        out.append(f"X{int(ax * 1e6)}Y{int(ay * 1e6)}D02*\nX{int(bx * 1e6)}Y{int(by * 1e6)}D01*\n")
    return "".join(out)


# ─── Stitching strokes into shapes ───────────────────────────────────────────


def test_separate_strokes_become_one_loop():
    """
    The whole problem: an outline arrives as unrelated strokes in no particular order, and
    nothing in the file says they belong to the same shape.
    """
    found = outline.contours(_gerber(_rect(0, 0, 10, 20)))

    assert len(found) == 1
    assert found[0].width == pytest.approx(10, abs=MM)
    assert found[0].height == pytest.approx(20, abs=MM)
    assert found[0].area == pytest.approx(200, abs=1)


def test_strokes_that_do_not_close_are_not_a_shape():
    """Three sides of a rectangle is a scratch on the drawing, not a board."""
    three_sides = _gerber("X0Y0D02*\nX10000000Y0D01*\nX10000000Y20000000D01*\n")

    assert outline.contours(three_sides) == []


def test_an_arc_closes_a_loop():
    """A rounded corner is a G03 the stitcher has to follow, or the loop never closes."""
    body = "X0Y0D02*\nX10000000Y0D01*\nG03X10000000Y2000000I0J1000000D01*\nX0Y2000000D01*\nX0Y0D01*\n"

    found = outline.contours(_gerber(body))

    assert len(found) == 1
    assert found[0].width == pytest.approx(11, abs=0.2)


def test_a_layer_has_an_extent_even_when_it_has_no_board():
    """What the viewer frames on a package with no outline picked: everything drawn."""
    text = SHEET_EDGE.read_text()

    min_x, min_y, max_x, max_y = outline.extents(text)

    assert max_x > min_x and max_y > min_y
    board = outline.pick_board(outline.contours(text), *RB_SIZE)
    assert min_x <= board.min_x and max_x >= board.max_x, "the sheet, and the board on it"
    assert max_x - min_x == pytest.approx(277.0, abs=0.1), "an A4 sheet border"
    assert outline.extents("%FSLAX46Y46*%\n%MOMM*%\nM02*\n") is None


def test_the_real_outline_layer_yields_the_board():
    """
    Forty-one loops, of which the board is one. The rest are the drawing sheet's border, its
    frame and the title block, which this export plots onto the layer.
    """
    found = outline.contours(SHEET_EDGE.read_text())

    assert len(found) > 3
    board = outline.pick_board(found, *gbrjob.board_size((SHEET / "RoyalBlue54L-Feather-job.gbrjob").read_text()))
    assert board is not None
    assert board.width == pytest.approx(58.42, abs=0.01)
    assert board.height == pytest.approx(22.86, abs=0.01)
    assert (board.min_x, board.min_y) == pytest.approx((119.1, -116.41), abs=0.01)


def test_the_biggest_shape_is_not_the_board():
    """
    The trap this exists for. On this export the largest loop is the page border -- 277 x 190
    mm around a 58 mm board, forty times the area -- so picking by size alone frames a sheet
    with the board lost in the middle of it.
    """
    found = outline.contours(SHEET_EDGE.read_text())

    assert found[0].width > 250, "largest really is the sheet"
    assert outline.pick_board(found, *RB_SIZE) is not found[0]


def test_the_plain_export_is_the_board_alone():
    """Without the sheet, the outline layer is one loop: the board, at the job file's size."""
    found = outline.contours(RB_EDGE.read_text())

    assert len(found) == 1
    assert outline.pick_board(found, *RB_SIZE) is found[0]
    assert outline.cutouts(found, found[0]) == []


@pytest.mark.parametrize(
    "path,size",
    [
        (TEST_FIXTURES / "pic_programmer" / "base" / "pic_programmer-Edge_Cuts.gbr", (160.02, 99.06)),
        (TEST_FIXTURES / "pic_programmer" / "head" / "pic_programmer-Edge_Cuts.gbr", (160.02, 99.06)),
        (TEST_FIXTURES / "slots-board" / "slots-Edge_Cuts.gbr", (35.0, 20.0)),
    ],
    ids=["pic_programmer-base", "pic_programmer-head", "slots-board"],
)
def test_other_demo_outlines_are_one_board(path, size):
    found = outline.contours(path.read_text())

    assert len(found) == 1
    assert (found[0].width, found[0].height) == pytest.approx(size, abs=0.001)


def test_without_a_stated_size_the_largest_is_the_guess():
    """And the UI lets somebody say otherwise."""
    found = outline.contours(_gerber(_rect(0, 0, 10, 20) + _rect(30, 0, 33, 3)))

    assert outline.pick_board(found).area == pytest.approx(200, abs=1)


def test_a_shape_inside_the_board_is_a_cutout():
    """Slots and windows are holes in the same silhouette, not separate boards."""
    found = outline.contours(_gerber(_rect(0, 0, 20, 30) + _rect(5, 5, 8, 9)))
    board = outline.pick_board(found, 20, 30)

    cutouts = outline.cutouts(found, board)
    assert len(cutouts) == 1
    assert cutouts[0].width == pytest.approx(3, abs=MM)


def test_a_shape_outside_the_board_is_not_a_cutout():
    found = outline.contours(_gerber(_rect(0, 0, 20, 30) + _rect(40, 0, 45, 5)))
    board = outline.pick_board(found, 20, 30)

    assert outline.cutouts(found, board) == []


def test_an_empty_layer_yields_nothing():
    assert outline.contours(_gerber("")) == []
    assert outline.pick_board([]) is None


@pytest.mark.parametrize(
    "stroke",
    ["G75*\nX{big}Y0D02*\nG03X{big}Y5I{big}J0D01*", "X0Y0D02*\nX{big}Y0D01*\nX0Y1000000D01*\nX0Y0D01*"],
    ids=["arc", "line"],
)
def test_an_outline_coordinate_too_long_to_be_one_is_passed_over(stroke):
    """Four hundred digits is an infinite float, and an arc from it a NaN sweep."""
    text = "%FSLAX46Y46*%\n%MOMM*%\n" + stroke.format(big="9" * 400) + "\n"

    found = outline.contours(text)

    assert all(math.isfinite(value) for c in found for value in (c.area, c.min_x, c.min_y, c.max_x, c.max_y))
    box = outline.extents(text)
    assert box is None or all(math.isfinite(value) for value in box)


# ─── Boards that are not rectangles ──────────────────────────────────────────

#: royalblue54L_feather: 58.42 x 22.86 mm with 2.54 mm rounded corners. Each corner takes away a
#: square of side r and gives back a quarter disc: 58.42 * 22.86 - (4 - pi) r^2 = 1329.942. Cut
#: off straight instead -- which is what an arc drawn as its chord comes to -- each corner loses
#: a right triangle of area r^2 / 2, giving 1322.578. Far enough apart to tell from the area.
_R = 2.54
ROUNDED_AREA = 58.42 * 22.86 - (4 - math.pi) * _R * _R
MITRED_AREA = 58.42 * 22.86 - 2 * _R * _R


def _rounded() -> str:
    return RB_EDGE.read_text()


def test_a_rounded_corner_stays_round():
    """
    KiCad writes the interpolation mode on a line of its own -- `G02*`, then the coordinates.
    Those lines were skipped, so the mode stayed "line" for the whole file and every arc came
    out as the chord across it. A plain rectangular board never notices.
    """
    found = outline.contours(_rounded())

    assert len(found) == 1
    board = found[0]
    assert board.area == pytest.approx(ROUNDED_AREA, abs=0.1)
    assert board.area - MITRED_AREA > 4, "a mitred corner is what this used to produce"


def test_a_rounded_board_still_measures_its_own_size():
    board = outline.contours(_rounded())[0]

    assert (board.width, board.height) == pytest.approx((58.42, 22.86), abs=0.001)


def test_the_curve_is_carried_by_enough_points_to_read_as_one():
    """The same outline is extruded into the 3D board, where a coarse corner is a facet."""
    board = outline.contours(_rounded())[0]

    assert len(board.points) > 30


def test_an_arc_whose_offsets_have_no_signs():
    """
    Single-quadrant mode: I and J are magnitudes, so four centers are possible and the file
    does not say which. Only one is equidistant from both ends and reached in a quarter turn.
    KiCad does not write these; other tools do, and the alternative is a board silently wrong.
    """
    text = _rounded().replace("G75*", "G74*")
    # Strip the signs the multi-quadrant form carries, which is what G74 means.
    text = re.sub(r"I-(\d+)", r"I\1", text)
    text = re.sub(r"J-(\d+)", r"J\1", text)
    assert "G74*" in text and "I-" not in text and "J-" not in text

    board = outline.contours(text)[0]

    assert board.area == pytest.approx(ROUNDED_AREA, abs=0.1)
    assert (board.width, board.height) == pytest.approx((58.42, 22.86), abs=0.001)


def test_a_slot_drawn_twice_is_one_cutout():
    """
    KiKit stamps the board's Edge_Cuts per copy, doubled outline and all, so a 6-up panel of
    a real board carried forty cutouts for twenty slots. Two coincident holes cancel under an
    even-odd fill and overlap under the triangulator; one is a hole.
    """

    def square(x0, y0, size):
        return outline.Contour(
            points=((x0, y0), (x0 + size, y0), (x0 + size, y0 + size), (x0, y0 + size)),
            area=size * size,
            min_x=x0,
            min_y=y0,
            max_x=x0 + size,
            max_y=y0 + size,
        )

    board = square(0, 0, 100)
    slot = square(10, 10, 20)
    again = outline.Contour(
        points=tuple((x + 0.001, y) for x, y in slot.points),
        area=slot.area,
        min_x=10.001,
        min_y=10,
        max_x=30.001,
        max_y=30,
    )
    other = square(50, 50, 20)

    found = outline.cutouts([board, slot, again, other], board)

    assert found == [slot, other]


def test_an_outline_drawn_twice_is_one_board_and_no_cutout():
    """The board's own loop drawn again is the board, not a hole that would cancel it."""
    text = RB_EDGE.read_text()
    body = text[text.index("D10*") : text.index("M02*")]
    doubled = text.replace("M02*", body + "M02*")

    found = outline.contours(doubled)

    assert len(found) == 2
    board = outline.pick_board(found, *RB_SIZE)
    assert outline.cutouts(found, board) == []
