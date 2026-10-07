"""
Reading and writing pick-and-place files (`boarddd.io.pos`), from KiCad and every other tool.

Run against kicad-cli's CSV of royalblue54L_feather and KiCad's tiny_tapeout demo exports,
because a placement reader that only agrees with a file somebody invented has proved nothing.

Ported from magpie `tests/pcb/test_formats.py` (the placement file) at 3a0374d3.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from boarddd.io import pos as posfile

from conftest import RB_FAB

RB_POS = RB_FAB / "pos.csv"


# ─── The placement file ──────────────────────────────────────────────────────

ALTIUM_TXT = (
    "Altium Designer Pick and Place Locations\n"
    "C:\\\\projects\\\\PCB1.PcbDoc\n"
    "\n"
    "========================================================================\n"
    "File Design Information:\n"
    "\n"
    "Date:       11/09/26\n"
    "Units used:   mm\n"
    "\n"
    "Designator Comment    Layer       Footprint   Center-X(mm) Center-Y(mm) Rotation Description\n"
    "C1         100nF      TopLayer    0402        12.7000      5.0800       90       Cap 100 nF\n"
    "R1         10k 1%     BottomLayer 0603        -3.1750      2.5400       180      Res\n"
)


def test_the_real_placement_file_parses():
    """kicad-cli's CSV of royalblue54L_feather (every placed part is on the top side)."""
    rows = posfile.parse(RB_POS.read_text("utf-8-sig"))

    assert len(rows) == 50
    first = next(r for r in rows if r.reference == "C1")
    assert (first.x, first.y) == (Decimal("151.140000"), Decimal("-105.690000"))
    assert first.rotation == Decimal("-90.000000")
    assert first.side == "top"
    assert (first.value, first.footprint) == ("100nF", "C_0402_1005Metric")
    assert {r.side for r in rows} == {"top"}


def test_an_untouched_file_survives_the_round_trip():
    """
    Parse then render must be a no-op. This is what lets the original file stay pristine on
    disk while the rows are edited: a diff of the regenerated CSV shows the placements
    somebody changed and nothing else.
    """
    text = RB_POS.read_text("utf-8-sig")
    once = posfile.render(posfile.parse(text))
    twice = posfile.render(posfile.parse(once))

    assert once == twice
    assert once == text, "kicad-cli's own CSV comes back byte for byte"
    assert once.splitlines()[0] == "Ref,Val,Package,PosX,PosY,Rot,Side"
    assert once.splitlines()[1] == '"C1","100nF","C_0402_1005Metric",151.140000,-105.690000,-90.000000,top'


def test_one_edit_changes_one_line():
    text = RB_POS.read_text("utf-8-sig")
    rows = posfile.parse(text)
    before = posfile.render(rows).splitlines()

    next(r for r in rows if r.reference == "C1").x = Decimal("152.140000")
    after = posfile.render(rows).splitlines()

    differing = [i for i, (a, b) in enumerate(zip(before, after, strict=False)) if a != b]
    assert len(differing) == 1


def test_columns_are_found_by_name_not_position():
    """Another tool's column order must not put Y in the rotation column and be believed."""
    text = "Designator,Rotation,Layer,MidX,MidY,Comment\nR1,90,bottom,10.5,-4.25,10k\n"

    rows = posfile.parse(text)

    assert (rows[0].reference, rows[0].x, rows[0].y) == ("R1", Decimal("10.5"), Decimal("-4.25"))
    assert (rows[0].rotation, rows[0].side, rows[0].value) == (Decimal(90), "bottom", "10k")


@pytest.mark.parametrize(
    "side,expected",
    [
        ("top", "top"),
        ("Top", "top"),
        ("bottom", "bottom"),
        ("B", "bottom"),
        ("", "top"),
    ],
)
def test_the_side_column_is_read_loosely(side, expected):
    rows = posfile.parse(f"Ref,Val,Package,PosX,PosY,Rot,Side\nR1,10k,0402,1,2,0,{side}\n")
    assert rows[0].side == expected


def test_rows_without_a_reference_are_skipped():
    rows = posfile.parse("Ref,Val,Package,PosX,PosY,Rot,Side\nR1,10k,0402,1,2,0,top\n,,,,,,\n")
    assert [r.reference for r in rows] == ["R1"]


# ─── Other tools' placement files ────────────────────────────────────────────


def test_altiums_fixed_width_txt_with_its_title_block():
    """The header is found by looking, not assumed to be first, and the columns are cut at it."""
    rows = posfile.parse(ALTIUM_TXT)

    assert [(r.reference, r.x, r.y, r.side, r.value, r.footprint) for r in rows] == [
        ("C1", Decimal("12.7000"), Decimal("5.0800"), "top", "100nF", "0402"),
        ("R1", Decimal("-3.1750"), Decimal("2.5400"), "bottom", "10k 1%", "0603"),
    ]
    assert posfile.looks_like_placements(ALTIUM_TXT)


def test_altiums_csv_in_mils_comes_out_in_millimeters():
    """The unit is in the column header; a machine fed 500 mm instead of 500 mil is a bad day."""
    rows = posfile.parse(
        "Designator,Comment,Layer,Footprint,Center-X(mil),Center-Y(mil),Rotation\nC1,100nF,TopLayer,0402,500,200,90\n"
    )
    assert (rows[0].x, rows[0].y) == (Decimal("12.7000"), Decimal("5.0800"))


def test_a_units_line_in_the_title_block_is_honored():
    rows = posfile.parse("Units used:   mil\n\nDesignator,Mid X,Mid Y,Layer,Rotation\nC1,1000,0,T,0\n")
    assert rows[0].x == Decimal("25.4000")


def test_jlcs_values_carry_their_own_unit():
    rows = posfile.parse("Designator,Mid X,Mid Y,Layer,Rotation\nC1,12.7mm,5.08mm,T,90\nR1,-125mil,100mil,B,180\n")
    assert (rows[0].x, rows[0].side) == (Decimal("12.7"), "top")
    assert (rows[1].x, rows[1].side) == (Decimal("-3.1750"), "bottom")


def test_altiums_mid_x_is_preferred_over_its_ref_x_and_pad_x():
    """Three X columns side by side; the middle of the part is the one a machine wants."""
    rows = posfile.parse(
        "Designator,Footprint,Mid X,Mid Y,Ref X,Ref Y,Pad X,Pad Y,Layer,Rotation,Comment\n"
        "C1,0402,10,20,11,21,12,22,T,0,100nF\n"
    )
    assert (rows[0].x, rows[0].y) == (Decimal(10), Decimal(20))


def test_eagles_mountsmd_file_has_no_header_at_all():
    """`C1 15.24 12.70 180 100nF C0603`: which order it is in, the numbers say."""
    rows = posfile.parse("C1 15.24 12.70 180 100nF C0603\nR1 5.08 2.54 90 10k R0603\n")
    assert [(r.reference, r.x, r.y, r.rotation, r.value, r.footprint) for r in rows] == [
        ("C1", Decimal("15.24"), Decimal("12.70"), Decimal(180), "100nF", "C0603"),
        ("R1", Decimal("5.08"), Decimal("2.54"), Decimal(90), "10k", "R0603"),
    ]


def test_a_headerless_kicad_csv_still_reads_in_kicads_order():
    rows = posfile.parse('"C1","47pF","C_0201",70.3,-63.2,90,top\n')
    assert (rows[0].value, rows[0].x, rows[0].side) == ("47pF", Decimal("70.3"), "top")


def test_diptraces_semicolons_and_bracketed_units():
    rows = posfile.parse("RefDes;Name;X (mm);Y (mm);Side;Rotate;Value\nC1;CAP0402;1.5;2.5;Top;90;100nF\n")
    assert (rows[0].reference, rows[0].x, rows[0].y, rows[0].side, rows[0].value) == (
        "C1",
        Decimal("1.5"),
        Decimal("2.5"),
        "top",
        "100nF",
    )


def test_gedas_commented_header_and_mil_note():
    text = (
        "# PcbXY Version 1.0\n"
        "# RefDes, Description, Value, X, Y, rotation, top/bottom\n"
        "# X,Y in mil.  rotation in degrees.\n"
        "C1,0603,100n,1000.00,500.00,0,bottom\n"
    )
    rows = posfile.parse(text)
    assert (rows[0].x, rows[0].y, rows[0].side) == (Decimal("25.400000"), Decimal("12.700000"), "bottom")


def test_allegro_says_bottom_with_sym_mirror():
    rows = posfile.parse(
        "REFDES,SYM_X,SYM_Y,SYM_ROTATE,SYM_MIRROR,COMP_VALUE\nC1,10,20,90,YES,100n\nC2,10,20,90,NO,100n\n"
    )
    assert [r.side for r in rows] == ["bottom", "top"]


def test_kicads_own_pos_table_reads_by_column_position():
    """
    A value with a space in it must not shift the coordinates along by one.

    Laid out the way KiCad's exporter prints it (`%-*s %-*s %-*s  %9.9s  %9.9s  %8.8s  %s`,
    unchanged from the 5.x "Module positions" file to 10.x): the text columns are left-aligned
    under their labels, the numbers and their labels right-aligned -- so `70.3000` starts
    three characters left of `PosX`, inside the gap after the package.
    """
    text = (
        "### Module positions - created on Mon 06 Jan 2020 10:00:00 AM ###\n"
        "### Printed by Pcbnew version kicad (5.1.5)-3\n"
        "## Unit = mm, Angle = deg.\n"
        "## Side : All\n"
        "# Ref     Val       Package                    PosX       PosY       Rot  Side\n"
        "C1        47pF      C_0201_0603Metric       70.3000   -63.2000   90.0000  top\n"
        "C10       100 nF    C_0402                   1.0000     2.0000  -90.0000  bottom\n"
        "## End\n"
    )
    rows = posfile.parse(text)
    assert [(r.reference, r.value, r.footprint, r.x, r.y, r.rotation, r.side) for r in rows] == [
        ("C1", "47pF", "C_0201_0603Metric", Decimal("70.3000"), Decimal("-63.2000"), Decimal("90.0000"), "top"),
        ("C10", "100 nF", "C_0402", Decimal("1.0000"), Decimal("2.0000"), Decimal("-90.0000"), "bottom"),
    ]


def test_a_real_kicad_pos_file_reads_into_the_right_columns(sample):
    """
    KiCad 7.0.9's ASCII placement export, unedited: the tiny_tapeout demo that ships with
    KiCad, `pcba/placement/tinytapeout-demo-all.pos` (fixtures/tiny_tapeout/). Filing each number under
    the label it *starts* under put X in the package, Y in X and the rotation in Y, and
    every rotation came out 0 -- a board programmed wrong with nothing to say so.
    """
    rows = posfile.parse(sample("fixtures/tiny_tapeout/kicad7-tinytapeout-demo-all.pos").decode())

    assert len(rows) == 136
    by_ref = {r.reference: r for r in rows}
    assert (by_ref["C1"].value, by_ref["C1"].footprint) == ("1uF", "C_0603_1608Metric")
    assert (by_ref["C1"].x, by_ref["C1"].y, by_ref["C1"].rotation, by_ref["C1"].side) == (
        Decimal("44.0000"),
        Decimal("13.9000"),
        Decimal("90.0000"),
        "top",
    )
    assert (by_ref["C5"].x, by_ref["C5"].y, by_ref["C5"].rotation) == (
        Decimal("18.4000"),
        Decimal("6.9000"),
        Decimal("-90.0000"),
    )
    assert (by_ref["J11"].footprint, by_ref["J11"].x, by_ref["J11"].side) == (
        "PinHeader_1x06_P2.54mm_Vertical_SMD_Pin1Right",
        Decimal("16.6000"),
        "bottom",
    )
    assert (by_ref["X1"].footprint, by_ref["X1"].rotation) == (
        "Oscillator_SMD_ECS_2520MV-xxx-xx-4Pin_2.5x2.0mm",
        Decimal("180.0000"),
    )
    assert all(" " not in r.footprint and " " not in r.value for r in rows), (
        "no number was run into the package or value column"
    )


def test_a_real_kicad_10_pos_file_in_inches(sample):
    """The same board from KiCad 10's `kicad-cli pcb export pos --format ascii --units in`."""
    rows = posfile.parse(sample("fixtures/tiny_tapeout/kicad10-tinytapeout-demo-inches.pos").decode())

    assert len(rows) == 136
    c5 = next(r for r in rows if r.reference == "C5")
    assert (c5.footprint, c5.x, c5.y, c5.rotation) == (
        "C_0603_1608Metric",
        Decimal("2.9488") * Decimal("25.4"),
        Decimal("-5.0236") * Decimal("25.4"),
        Decimal("-90.0000"),
    )


def test_a_pos_table_in_inches_is_converted():
    text = (
        "## Unit = inches, Angle = deg.\n"
        "# Ref  Val   Package  PosX     PosY     Rot   Side\n"
        "C1     1u    0402     1.0000   0.5000   0.0   top\n"
    )
    assert posfile.parse(text)[0].x == Decimal("25.40000")


# ─── Which corner a placement file is measured from ──────────────────────────


def _outline(x0: float, y0: float, w: float, h: float) -> list:
    """A board `w` by `h` with its top-left corner at (x0, y0), in gerber coordinates."""
    return [[x0, y0], [x0 + w, y0], [x0 + w, y0 - h], [x0, y0 - h]]


def _rows(points) -> list:
    return [
        posfile.PosRow(
            reference=f"R{i}",
            value="",
            footprint="",
            x=Decimal(str(x)),
            y=Decimal(str(y)),
            rotation=Decimal(0),
            side="top",
        )
        for i, (x, y) in enumerate(points, start=1)
    ]


def test_rows_already_on_the_board_are_left_where_they_are():
    outline = _outline(100, -20, 50, 30)
    rows = _rows([(110, -25), (140, -45), (125, -30)])
    assert posfile.frame_offset(rows, outline) is None


def test_a_file_measured_from_the_top_left_corner_says_so():
    """KiBot's panel placement file: every row relative to the panel's top-left corner."""
    outline = _outline(163.65, -20.0, 104.6, 147.1)
    rows = _rows([(10, -5), (50, -100), (100, -140)])
    assert posfile.frame_offset(rows, outline) == (163.65, -20.0)


def test_a_file_measured_from_the_bottom_left_corner_says_so():
    """KiCad's aux origin is usually put at the bottom-left; rows are then positive both ways."""
    outline = _outline(100, -20, 50, 30)
    rows = _rows([(10, 5), (40, 25), (25, 10)])
    assert posfile.frame_offset(rows, outline) == (100, -50)


def test_a_part_hanging_over_the_edge_does_not_change_the_answer():
    outline = _outline(100, -20, 50, 30)
    rows = _rows([(10, 5), (40, 25), (51.5, 10)])  # a connector 1.5 mm past the edge
    assert posfile.frame_offset(rows, outline) == (100, -50)


def test_rows_no_corner_explains_are_not_moved_to_a_guess():
    outline = _outline(100, -20, 50, 30)
    rows = _rows([(500, 500), (520, 510), (530, 505)])
    assert posfile.frame_offset(rows, outline) is None


def test_the_question_needs_an_outline_and_rows():
    assert posfile.frame_offset(_rows([(10, 5)]), []) is None
    assert posfile.frame_offset([], _outline(100, -20, 50, 30)) is None


def test_a_number_that_is_not_a_placement_leaves_its_row_out():
    """Finite, and within what the placement table holds: 10**6 mm, and 1000 degrees."""
    skipped: list = []
    rows = posfile.parse(
        "Ref,Val,Package,PosX,PosY,Rot,Side\n"
        "C1,1u,0402,1.5,2.5,-450,top\n"
        "C2,1u,0402,1.5,2.5,NaN,top\n"
        "C3,1u,0402,1.5,2.5,Infinity,top\n"
        "C4,1u,0402,1e20,2.5,0,top\n"
        "C5,1u,0402,1.5,-1000000,0,top\n"
        "C6,1u,0402,1.5,2.5,1000,top\n"
        "C7,1u,0402,1e9999999,2.5,0,top\n"
        "C8,1u,0402,1.5,2.5,-1e9999999,top\n",
        skipped=skipped,
    )

    assert [(r.reference, r.rotation) for r in rows] == [("C1", Decimal(-450))]
    assert skipped == ["C2", "C3", "C4", "C5", "C6", "C7", "C8"]


def test_a_comma_decimal_is_read_where_the_comma_is_not_the_delimiter():
    """Excel in half the world: `;` between the columns and `,` in the numbers."""
    rows = posfile.parse("Ref;Val;Package;PosX;PosY;Rot;Side\nC1;1u;0402;12,70;-3,5;90,0;top\n")
    assert (rows[0].x, rows[0].y, rows[0].rotation) == (Decimal("12.70"), Decimal("-3.5"), Decimal("90.0"))


# ─── Not a placement file ────────────────────────────────────────────────────


def test_a_headerless_table_without_coordinates_holds_no_placements():
    """A BOM dropped in the placement slot: no header row with coordinates, and no numbers to vouch for a guess."""
    bom = (
        '"C1","100nF","Capacitor_SMD:C_0402_1005Metric","1"\n"C2,C3,C4","10uF","Capacitor_SMD:C_0603_1608Metric","3"\n'
    )
    assert posfile.parse(bom) == []
