"""
Reading pads off a layer, and the pattern they make around one placement.

Two things are defended. The reader must turn the two shapes real tools write -- KiCad's
`RoundRect` macro in millimeters, Altium's primitive macro in inches with the `D03` on its own
line -- into the same boxes, because a pitch read wrong by 25.4 is a part the camera never
finds. And the sorter must say `irregular` when it cannot tell, rather than force the pads of
a part and its neighbour into the nearest row.

Ported from magpie `tests/pcb/test_pads.py` at 3a0374d3.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from boarddd.io import pads
from boarddd.io import pos as posfile

from conftest import GENERATED, RB_FAB

sys.path.insert(0, str(Path(__file__).resolve().parent))
import synth  # noqa: E402  (tests/io is not a package)

# ─── Reading a layer ─────────────────────────────────────────────────────────


def test_a_kicad_roundrect_flash_is_the_box_it_paints():
    text = synth.kicad_paste([(10.0, -5.0, 0.5, 0.6)])

    [pad] = pads.pads(text)

    assert (pad.x, pad.y) == (10.0, -5.0)
    assert pad.w == pytest.approx(0.5, abs=1e-6) and pad.h == pytest.approx(0.6, abs=1e-6)


def test_an_altium_macro_in_inches_comes_out_in_millimeters():
    """Two center lines and four corner circles, 2.4 format, `D03*` on the line after the
    coordinates: the shape of every pad on a real Altium export."""
    text = synth.altium_paste([(25.4, 12.7, 1.0, 0.5)])

    [pad] = pads.pads(text)

    assert pad.x == pytest.approx(25.4, abs=0.003) and pad.y == pytest.approx(12.7, abs=0.003)
    assert pad.w == pytest.approx(1.0, abs=0.01) and pad.h == pytest.approx(0.5, abs=0.01)


def test_a_standard_rectangle_and_a_circle_are_read_directly():
    text = (
        "%FSLAX45Y45*%\n%MOMM*%\n%ADD10R,1.200X0.800*%\n%ADD11C,0.900*%\n"
        "D10*\nX100000Y200000D03*\nD11*\nX300000Y200000D03*\nM02*\n"
    )

    found = pads.pads(text)

    assert [(p.x, p.y, p.w, p.h) for p in found] == [(1.0, 2.0, 1.2, 0.8), (3.0, 2.0, 0.9, 0.9)]


def test_a_filled_region_is_a_pad_the_size_of_its_outline():
    text = (
        "%FSLAX45Y45*%\n%MOMM*%\nG36*\nX0Y0D02*\nX200000Y0D01*\nX200000Y100000D01*\n"
        "X0Y100000D01*\nX0Y0D01*\nG37*\nM02*\n"
    )

    [pad] = pads.pads(text)

    assert (pad.x, pad.y, pad.w, pad.h) == (1.0, 0.5, 2.0, 1.0)
    assert pad.function == "region"


def test_clear_polarity_and_non_pad_functions_are_not_pads():
    """A via flashed on a copper layer is not somewhere a lead sits, and a flash in clear
    polarity removes copper rather than adding a pad."""
    text = (
        "%FSLAX45Y45*%\n%MOMM*%\n%TA.AperFunction,ViaPad*%\n%ADD10C,0.600*%\n%TD*%\n"
        "%TA.AperFunction,SMDPad,CuDef*%\n%ADD11R,1.000X1.000*%\n%TD*%\n"
        "D10*\nX0Y0D03*\nD11*\nX500000Y0D03*\n%LPC*%\nX900000Y0D03*\n%LPD*%\nM02*\n"
    )

    found = pads.pads(text)

    assert [(p.x, p.function) for p in found] == [(5.0, "smdpad")]


def test_the_outline_kicad_plots_onto_the_paste_layer_is_not_a_pad():
    """A real export with the board edge plotted on the paste layer, stroked with a Profile
    aperture: it must not be read as a ring of pads around the board."""
    fixture = GENERATED / "profile" / "RoyalBlue54L-Feather-F_Paste-profile.gbr"
    found = pads.pads(fixture.read_text())
    plain = pads.pads((RB_FAB / "RoyalBlue54L-Feather-F_Paste.gbr").read_text())

    assert all(p.function != "profile" for p in found)
    assert all(p.w < 5 and p.h < 5 for p in found), "nothing the size of the board"
    assert found == plain, "the same openings as the export made without the outline"


def _real_pattern(ref: str) -> pads.Topology:
    found = pads.pads((RB_FAB / "RoyalBlue54L-Feather-F_Paste.gbr").read_text())
    row = next(r for r in posfile.parse((RB_FAB / "pos.csv").read_text()) if r.reference == ref)
    return pads.topology(pads.around(found, float(row.x), float(row.y), float(row.rotation), None, row.side))


@pytest.mark.parametrize(
    "ref,pattern,per_side,pitch",
    [
        ("U2", "four_rows", 8, 0.5),  # QFN-32 5x5 mm, 0.5 mm pitch
        ("U5", "four_rows", 10, 0.4),  # QFN-40 5x5 mm, 0.4 mm pitch
    ],
)
def test_a_real_qfn_reads_as_four_rows_off_the_paste_layer(ref, pattern, per_side, pitch):
    """royalblue54L_feather's own paste and pos files, with no body size to help."""
    found = _real_pattern(ref)

    assert found.pattern == pattern and found.equal
    assert all(found.rows[s].count == per_side for s in "NSEW")
    assert found.rows["E"].pitch == pytest.approx(pitch)
    assert len(found.center) > 0, "the thermal pad windows are under the body"


@pytest.mark.parametrize("ref", ["D1", "Y1"])
def test_a_real_two_terminal_part_is_two_ends(ref):
    assert _real_pattern(ref).pattern == "two_ends"


# ─── Around a placement ───────────────────────────────────────────────────────


def _around(boxes, x=0.0, y=0.0, rotation=0.0, body=None, side="top"):
    found = pads.pads(synth.kicad_paste(boxes))
    return pads.topology(pads.around(found, x, y, rotation, body, side))


def test_a_chip_is_two_ends():
    found = _around(synth.placed(synth.chip(1.0, 0.5), 20, 30), 20, 30, body=(0.5, 1.0))

    assert found.pattern == "two_ends"
    assert found.count == 2
    assert pads.describe(found) == "2 paste openings, one at each end"


def test_a_soic_is_two_equal_rows_with_its_pitch():
    found = _around(synth.placed(synth.two_rows(4, 1.27, 5.4), 12, 8), 12, 8, body=(3.9, 4.9))

    assert found.pattern == "two_rows" and found.equal
    assert found.rows["E"].count == 4 and found.rows["W"].count == 4
    assert found.rows["E"].pitch == pytest.approx(1.27)
    assert found.rows["E"].width == pytest.approx(0.6) and found.rows["E"].length == pytest.approx(1.5)
    assert "4 per side on two sides, 1.27 mm apart" in pads.describe(found)


def test_a_sot23_is_two_unequal_rows():
    boxes = [(-0.95, 1.0, 0.6, 1.0), (0.95, 1.0, 0.6, 1.0), (0.0, -1.0, 0.6, 1.0)]
    found = _around(boxes, body=(1.3, 2.9))

    assert found.pattern == "two_rows" and not found.equal
    assert {found.rows["E"].count, found.rows["W"].count} == {2, 1}
    assert pads.describe(found) == "3 paste openings: 2 on one side, 1 on the other"


def test_a_four_pad_crystal_reads_its_rows_along_the_long_side():
    """
    A 2 x 2 of pads reads as two rows either way. The catalog's own crystal records put the
    pitch along the long side (`XL4-3225` has `LeadPitchE` 2.2 on a 3.2 mm body), so the
    rows run that way and the pitch is the long one.
    """
    boxes = [(x, y, 1.4, 1.2) for x in (-1.1, 1.1) for y in (-0.85, 0.85)]
    found = _around(synth.placed(boxes, 5, 5, 90), 5, 5, 90, body=(2.5, 3.2))

    assert found.pattern == "two_rows" and found.equal
    assert found.rows["E"].count == 2
    assert found.rows["E"].pitch == pytest.approx(2.2)


def test_a_qfn_is_four_rows_and_its_thermal_windows_are_under_the_body():
    boxes = synth.four_rows(4, 4, 0.5, 3.2, thermal=1.6, windows=2)
    found = _around(synth.placed(boxes, 40, 40, 180), 40, 40, 180, body=(3.0, 3.0))

    assert found.pattern == "four_rows" and found.equal
    assert all(found.rows[s].count == 4 for s in "NSEW")
    assert found.rows["E"].pitch == pytest.approx(0.5)
    assert len(found.center) == 4
    assert "plus 4 under the body" in pads.describe(found)


def test_a_qfn_with_no_thermal_pad_is_not_a_grid():
    """Three lines each way is what a grid has too; a QFN has nothing inside them."""
    found = _around(synth.four_rows(3, 4, 0.4, 1.8, w=0.2, length=0.6), body=(1.7, 2.0))

    assert found.pattern == "four_rows"
    assert found.rows["N"].count == 3 and found.rows["E"].count == 4


def test_a_ball_grid_is_a_grid():
    found = _around(synth.grid(4, 0.8, 0.4), body=(4.0, 4.0))

    assert found.pattern == "grid"
    assert found.pitch == pytest.approx(0.8)


def test_a_header_is_one_row():
    boxes = [(0, (i - 2) * 2.54, 1.0, 2.0) for i in range(5)]
    found = _around(boxes, body=(2.5, 12.7))

    assert found.pattern == "one_row"
    assert found.rows["E"].count == 5 and found.rows["E"].pitch == pytest.approx(2.54)


def test_a_lone_pad_is_one_and_nothing_is_none():
    assert _around([(0, 0, 1.5, 1.5)]).pattern == "one"
    assert _around([(30, 30, 1.5, 1.5)]).pattern == "none"


def test_the_underside_reads_the_same_as_the_top():
    boxes = synth.placed(synth.two_rows(3, 0.95, 2.4), 7, 7, 90, side="bottom")
    found = _around(boxes, 7, 7, 90, body=(1.6, 2.9), side="bottom")

    assert found.pattern == "two_rows" and found.rows["E"].count == 3


def test_a_neighbours_pads_are_left_out_of_the_window():
    """
    A 0402 sits half a millimeter from the next one on a dense board. Its neighbour's pads
    have no mirror twin about this placement, and they are not the run of same-sized pads
    that spans it -- so a chip still reads as two ends, not four.
    """
    ours = synth.placed(synth.chip(1.0, 0.5), 10, 10)
    theirs = synth.placed(synth.chip(1.0, 0.5), 10, 11.0)
    found = _around(ours + theirs, 10, 10, body=(0.5, 1.0))

    assert found.pattern == "two_ends"
    assert found.count == 2


def test_a_neighbouring_row_further_out_than_the_parts_own_is_peeled_off():
    """A row of pads on the next part over, symmetric enough to pass the mirror test but
    further from the center than its opposite number, is not this part's."""
    ours = synth.four_rows(3, 4, 0.4, 1.8, w=0.2, length=0.6)
    theirs = [(x, 1.7, 0.2, 0.6) for x in (-0.4, 0.0, 0.4)]
    found = _around(ours + theirs, body=(1.7, 2.0))

    assert found.pattern == "four_rows"
    assert found.rows["N"].count == 3 and found.rows["S"].count == 3


def test_pads_of_two_sizes_in_one_row_are_still_one_part():
    """A DFN whose end pads are drawn longer than the rest: the wider run spans the origin
    and the narrower pads lie inside its box."""
    boxes = [
        (-0.5, 0.85, 0.6, 0.4),
        (0.5, 0.85, 0.6, 0.4),
        (-0.5, -0.85, 0.6, 0.4),
        (0.5, -0.85, 0.6, 0.4),
        (-0.5, 0.25, 0.6, 0.2),
        (0.5, 0.25, 0.6, 0.2),
        (-0.5, -0.25, 0.6, 0.2),
        (0.5, -0.25, 0.6, 0.2),
    ]
    found = _around(boxes, body=(1.2, 2.5))

    assert found.pattern == "two_rows"
    assert found.rows["E"].count == 4


def test_without_a_body_size_the_parts_own_pads_are_still_found():
    ours = synth.placed(synth.two_rows(4, 1.27, 5.4), 12, 8)
    theirs = synth.placed(synth.chip(1.0, 0.5), 12, 12.5)
    found = _around(ours + theirs, 12, 8)

    assert found.pattern == "two_rows" and found.rows["E"].count == 4


def test_what_cannot_be_sorted_is_irregular_not_forced():
    boxes = [(0, 0, 1, 1), (2.1, 0.3, 0.5, 0.5), (-1.7, 1.2, 0.5, 0.5), (0.4, -2.0, 0.5, 0.5), (1.5, 1.9, 0.7, 0.3)]
    found = _around(boxes, body=(4, 4))

    assert found.pattern == "irregular"
    assert not found.decisive
    assert "no pattern this recognises" in pads.describe(found)
