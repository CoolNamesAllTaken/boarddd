"""
Reading component positions out of a STEP file.

Two kinds of test here. The hand-written STEP fragments pin the awkward shapes -- entities
wrapped across lines, a vendor model that is an assembly in its own right, a part on the
underside -- because those are cheap to state exactly and expensive to find by accident. The
excerpt fixture is real entities lifted verbatim out of KiCad's export of its royalblue54L_feather
demo (fixtures/generated/step), so the parser is also held to a file nobody wrote for it.

Ported from magpie's tests/step/test_stepmeta.py at 3a0374d3; the excerpt replaces a private board's.
"""

from __future__ import annotations

import math

import pytest

from boarddd.step import text as stepmeta

from conftest import GENERATED

# A whole miniature assembly: a root product with two components, written the way the real
# exporter writes them -- the placement chain runs backwards from the shape definition, and
# entities wrap.
MINI = """ISO-10303-21;
HEADER;
FILE_DESCRIPTION(('KiCad electronic assembly'),'2;1');
FILE_SCHEMA(('AUTOMOTIVE_DESIGN { 1 0 10303 214 1 1 1 1 }'));
ENDSEC;
DATA;
#5 = PRODUCT_DEFINITION('design','',#6,#7);
#10 = SHAPE_REPRESENTATION('',(#11),#20);
#11 = AXIS2_PLACEMENT_3D('',#12,#13,#14);
#12 = CARTESIAN_POINT('',(0.,0.,0.));
#13 = DIRECTION('',(0.,0.,1.));
#14 = DIRECTION('',(1.,0.,0.));
#100 = PRODUCT_DEFINITION('design','',#101,#102);
#110 = AXIS2_PLACEMENT_3D('',#111,#112,#113);
#111 = CARTESIAN_POINT('',(10.5,-4.25,0.811));
#112 = DIRECTION('',(0.,0.,1.));
#113 = DIRECTION('',(1.,0.,0.));
#120 = CONTEXT_DEPENDENT_SHAPE_REPRESENTATION(#121,#123);
#121 = ( REPRESENTATION_RELATIONSHIP('','',#130,#10)
REPRESENTATION_RELATIONSHIP_WITH_TRANSFORMATION(#122)
SHAPE_REPRESENTATION_RELATIONSHIP() );
#122 = ITEM_DEFINED_TRANSFORMATION('','',#11,#110);
#123 = PRODUCT_DEFINITION_SHAPE('Placement','Placement of an item',
  #124);
#124 = NEXT_ASSEMBLY_USAGE_OCCURRENCE('1','C1','',#5,#100,$);
#200 = PRODUCT_DEFINITION('design','',#201,#202);
#210 = AXIS2_PLACEMENT_3D('',#211,#212,#213);
#211 = CARTESIAN_POINT('',(3.,-7.,-0.8));
#212 = DIRECTION('',(0.,0.,-1.));
#213 = DIRECTION('',(1.,0.,0.));
#220 = CONTEXT_DEPENDENT_SHAPE_REPRESENTATION(#221,#223);
#221 = ( REPRESENTATION_RELATIONSHIP('','',#230,#10)
REPRESENTATION_RELATIONSHIP_WITH_TRANSFORMATION(#222)
SHAPE_REPRESENTATION_RELATIONSHIP() );
#222 = ITEM_DEFINED_TRANSFORMATION('','',#11,#210);
#223 = PRODUCT_DEFINITION_SHAPE('Placement','Placement of an item',#224);
#224 = NEXT_ASSEMBLY_USAGE_OCCURRENCE('2','R7','',#5,#200,$);
ENDSEC;
END-ISO-10303-21;
"""


def _by_ref(text):
    return {item.ref: item for item in stepmeta.instances(text)}


# ─── The basics ──────────────────────────────────────────────────────────────


def test_a_component_is_found_with_its_designator_and_position():
    found = _by_ref(MINI)

    assert set(found) == {"C1", "R7"}
    assert (found["C1"].x, found["C1"].y, found["C1"].z) == (10.5, -4.25, 0.811)


def test_an_entity_wrapped_across_lines_still_reads():
    """
    The real exporter wraps freely, including mid-argument. C1's shape definition here is split
    exactly the way a real export splits it -- read a line at a time, this component vanishes.
    """
    assert "PRODUCT_DEFINITION_SHAPE('Placement','Placement of an item',\n  #124)" in MINI
    assert "C1" in _by_ref(MINI)


def test_the_side_comes_from_the_placement_axis():
    found = _by_ref(MINI)
    assert found["C1"].side == stepmeta.SIDE_TOP
    assert found["R7"].side == stepmeta.SIDE_BOTTOM


def test_a_sideways_placement_axis_answers_nothing_rather_than_guessing():
    """
    Two of a real board's components have a z axis pointing along the board, because the model was
    authored lying down. The axis describes the whole rotation, not just the side, so there is
    no answer to give -- and the pick-and-place file has one.
    """
    text = MINI.replace("#112 = DIRECTION('',(0.,0.,1.));", "#112 = DIRECTION('',(0.,-1.,0.));")
    assert _by_ref(text)["C1"].side == ""


# ─── What is not a component ─────────────────────────────────────────────────


def test_the_boards_own_bodies_are_not_components():
    """
    KiCad has no designator for the substrate, mask, silkscreen and pads, so it falls back to an
    OpenCascade label path. Counting those as parts would put four phantom components on every
    board -- and they are wanted separately, to find the board's surfaces.
    """
    text = MINI.replace("'2','R7'", "'2','=>[0:1:1:16]'")

    assert set(_by_ref(text)) == {"C1"}
    assert [node.ref for node in stepmeta.board_nodes(text)] == ["=>[0:1:1:16]"]


def test_a_vendor_models_own_internals_are_not_components():
    """
    A supplied model can be an assembly: a TI part on one real board brings its own body and
    pin-1 marker, each a legitimate assembly occurrence. They hang off the component, not off
    the board, which is the difference that excludes them -- one revision of that board has 125
    occurrences and still only 66 components.
    """
    text = MINI.replace(
        "ENDSEC;\nEND-ISO",
        """#300 = PRODUCT_DEFINITION('design','',#301,#302);
#310 = NEXT_ASSEMBLY_USAGE_OCCURRENCE('3','BODY-QFN','',#100,#300,$);
ENDSEC;
END-ISO""",
    )

    assert set(_by_ref(text)) == {"C1", "R7"}, "the QFN's insides are not on the board"


def test_an_occurrence_with_no_placement_is_still_reported():
    """Better a component at the origin, named, than a component silently missing."""
    text = MINI.replace("#220 = CONTEXT_DEPENDENT_SHAPE_REPRESENTATION(#221,#223);", "")

    found = _by_ref(text)
    assert "R7" in found and (found["R7"].x, found["R7"].y) == (0.0, 0.0)


# ─── The board's surfaces, guessed from mounting heights ─────────────────────


def test_the_mounting_planes_give_a_thickness():
    found = stepmeta.instances(MINI)
    fallback = stepmeta.board_z_fallback(found)

    assert fallback["top_z"] == 0.81 and fallback["bottom_z"] == -0.8
    assert fallback["thickness_mm"] == pytest.approx(1.61)


def test_a_one_sided_board_reports_no_thickness():
    """Nothing was mounted on the other face, so there is nothing to measure across."""
    fallback = stepmeta.board_z_fallback([item for item in stepmeta.instances(MINI) if item.side == stepmeta.SIDE_TOP])

    assert fallback["bottom_z"] is None and fallback["thickness_mm"] is None


def _mounted(ref, z, side):
    """A part mounted at `z` on `side`, as instances() would give it."""
    down = side == stepmeta.SIDE_BOTTOM
    return stepmeta.Instance(
        seq=ref, ref=ref, x=0.0, y=0.0, z=z, z_dir=(0.0, 0.0, -1.0 if down else 1.0), x_dir=(1.0, 0.0, 0.0)
    )


# The parachute fixture (design 16): seventy parts underneath on a 1.51 mm board, and the only
# part on top a daughterboard on standoffs, 12.6 mm up.
PARACHUTE = [_mounted(f"R{n}", -0.085, stepmeta.SIDE_BOTTOM) for n in range(70)] + [
    _mounted("TPCB1", 12.595, stepmeta.SIDE_TOP)
]


def test_a_daughterboard_on_standoffs_is_not_the_top_face():
    """
    Given the model's own board, a face is where parts sit ON it. The one part on top is
    11 mm off the board, so the top face is the board's -- not 12.6 mm, which is where the
    board was drawn, a centimetre under every part and all of the paste.
    """
    substrate = stepmeta.Extent(min=(37.5, -110.5, 0.0), max=(162.5, -10.5, 1.51), solids=1)
    fallback = stepmeta.board_z_fallback(PARACHUTE, substrate)

    assert fallback["top_z"] == pytest.approx(1.51)
    assert fallback["bottom_z"] == pytest.approx(-0.085, abs=0.006)
    assert fallback["thickness_mm"] == pytest.approx(1.6, abs=0.006)


def test_with_the_board_a_part_mounted_on_it_still_names_the_face():
    """The mount height is kept when it IS on the face: it is a mask and a pad off the core."""
    substrate = stepmeta.Extent(min=(0.0, 0.0, -0.741), max=(10.0, 10.0, 0.0), solids=1)
    found = [
        _mounted("C1", 0.081, stepmeta.SIDE_TOP),
        _mounted("C2", 0.081, stepmeta.SIDE_TOP),
        _mounted("R1", -0.82, stepmeta.SIDE_BOTTOM),
    ]

    fallback = stepmeta.board_z_fallback(found, substrate)

    assert fallback["top_z"] == pytest.approx(0.08, abs=0.006)
    assert fallback["bottom_z"] == pytest.approx(-0.82)


def test_without_the_board_the_face_most_parts_agree_on_wins():
    """Two faces 12.7 mm apart are not a board; seventy parts outvote one."""
    fallback = stepmeta.board_z_fallback(PARACHUTE)

    assert fallback["bottom_z"] == pytest.approx(-0.085, abs=0.006)
    assert fallback["top_z"] == pytest.approx(fallback["bottom_z"] + stepmeta.NOMINAL_MM)


def test_components_of_unknown_side_join_neither_group():
    """A few in the wrong pile would move the mode, and this figure is already a fallback."""
    text = MINI.replace("#112 = DIRECTION('',(0.,0.,1.));", "#112 = DIRECTION('',(0.,-1.,0.));")
    fallback = stepmeta.board_z_fallback(stepmeta.instances(text))

    assert fallback["top_z"] is None


# ─── Against real entities ───────────────────────────────────────────────────

EXCERPT = GENERATED / "step" / "royalblue54L_feather-excerpt.step"
#: The components make.sh keeps. Every one is in the pick-and-place file too.
EXCERPT_REFS = {"C1", "C2", "C3", "C4", "C10", "C11", "C24", "C25", "R1", "R4", "R8", "L1", "U2", "Y2", "J4", "D3"}


@pytest.fixture
def excerpt():
    return EXCERPT.read_text(encoding="utf-8", errors="replace")


def test_the_real_export_reads(excerpt):
    """
    Sixteen components lifted verbatim from KiCad's export of royalblue54L_feather, positions
    and all (make_excerpt.py keeps their entities and ids; only the shells are emptied).

    Compared with a tolerance because real coordinates carry the exporter's own rounding. A
    micron of noise is far below anything that matters here, and registration's thresholds are
    set knowing it.
    """
    found = _by_ref(excerpt)

    assert set(found) == EXCERPT_REFS
    assert found["C1"].x == pytest.approx(151.14, abs=1e-5)
    assert found["C1"].y == pytest.approx(-105.69, abs=1e-5)
    assert found["C1"].z == pytest.approx(1.595, abs=1e-5)
    assert found["U2"].side == stepmeta.SIDE_TOP


def test_two_components_of_one_footprint_share_their_product(excerpt):
    """The prototype names what a thing is, never which one: both 0402 capacitors say so."""
    found = _by_ref(excerpt)
    assert found["C1"].product == found["C25"].product == "C_0402_1005Metric"


def test_the_real_export_has_a_sideways_crystal(excerpt):
    """Y2's model is authored lying down: the reason `side` may answer nothing, for real."""
    found = _by_ref(excerpt)
    assert found["Y2"].side == ""
    assert found["C1"].side == stepmeta.SIDE_TOP


def test_the_real_vendor_models_insides_are_not_components(excerpt):
    """D3's model is an assembly of its own (Body, Pins, Resin): one component, not four."""
    assert "D3" in _by_ref(excerpt)
    assert "Body" in excerpt and not {"Body", "Pins", "Resin"} & set(_by_ref(excerpt))


def test_the_real_boards_body_is_set_aside(excerpt):
    nodes = stepmeta.board_nodes(excerpt)
    assert [node.product for node in nodes] == ["RoyalBlue54L-Feather_PCB"]
    assert nodes[0].ref.startswith("=>[")


def test_the_real_mounting_plane_is_the_boards_top(excerpt):
    """
    Every part is placed at z = 1.595 on the 1.6 mm board (the top face as the 3D export puts
    it), read to the fallback's own rounding; nothing is on the bottom, so no thickness.
    """
    fallback = stepmeta.board_z_fallback(stepmeta.instances(excerpt))
    assert fallback["top_z"] == pytest.approx(1.595, abs=0.006)
    assert fallback["bottom_z"] is None and fallback["thickness_mm"] is None


# ─── Not falling over ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text",
    ["", "ISO-10303-21;\nHEADER;\nENDSEC;\nDATA;\nENDSEC;\n", "not a step file at all", "DATA;\n#1 = ;\nENDSEC;"],
)
def test_nothing_useful_is_not_a_crash(text):
    """The file is somebody's upload; a bad one is an empty answer, not a 500."""
    assert stepmeta.instances(text) == []
    assert stepmeta.board_z_fallback([]) == {"top_z": None, "bottom_z": None, "thickness_mm": None}


# ─── Where the solids are, as opposed to where the placement is ──────────────

# A second miniature board, for the half of this that is geometry. Three components: a chip
# drawn about its own origin, which is the ordinary case; a connector drawn 57 mm away from
# its own origin, which is what the KiCad library's U.FL does; and a daughterboard, which is
# an assembly with its own substrate and its own component, flipped onto the underside.
GEOMETRY = """ISO-10303-21;
HEADER;
FILE_DESCRIPTION(('KiCad electronic assembly'),'2;1');
FILE_SCHEMA(('AUTOMOTIVE_DESIGN { 1 0 10303 214 1 1 1 1 }'));
ENDSEC;
DATA;
#1 = APPLICATION_CONTEXT('core data for automotive mechanical design processes');
#2 = PRODUCT_CONTEXT('',#1,'mechanical');
#3 = PRODUCT_DEFINITION_CONTEXT('part definition',#1,'design');
#4 = GEOMETRIC_REPRESENTATION_CONTEXT(3);
#5 = PRODUCT_DEFINITION('design','',#6,#3);
#6 = PRODUCT_DEFINITION_FORMATION('','',#7);
#7 = PRODUCT('mini_board','mini_board','',(#2));
#8 = SHAPE_DEFINITION_REPRESENTATION(#9,#10);
#9 = PRODUCT_DEFINITION_SHAPE('','',#5);
#10 = SHAPE_REPRESENTATION('',(#11),#4);
#11 = AXIS2_PLACEMENT_3D('',#12,#13,#14);
#12 = CARTESIAN_POINT('',(0.,0.,0.));
#13 = DIRECTION('',(0.,0.,1.));
#14 = DIRECTION('',(1.,0.,0.));

#100 = PRODUCT_DEFINITION('design','',#101,#3);
#101 = PRODUCT_DEFINITION_FORMATION('','',#102);
#102 = PRODUCT('C_0603_1608Metric','C_0603_1608Metric','',(#2));
#103 = SHAPE_DEFINITION_REPRESENTATION(#104,#105);
#104 = PRODUCT_DEFINITION_SHAPE('','',#100);
#105 = ADVANCED_BREP_SHAPE_REPRESENTATION('',(#106),#4);
#106 = MANIFOLD_SOLID_BREP('',#107);
#107 = CLOSED_SHELL('',(#108,#109,#110,#111));
#108 = VERTEX_POINT('',#112);
#109 = VERTEX_POINT('',#113);
#110 = VERTEX_POINT('',#114);
#111 = VERTEX_POINT('',#115);
#112 = CARTESIAN_POINT('',(-0.8,-0.4,0.));
#113 = CARTESIAN_POINT('',(0.8,0.4,0.));
#114 = CARTESIAN_POINT('',(-0.8,0.4,0.8));
#115 = CARTESIAN_POINT('',(0.8,-0.4,0.8));
#120 = AXIS2_PLACEMENT_3D('',#121,#122,#123);
#121 = CARTESIAN_POINT('',(10.5,-4.25,0.811));
#122 = DIRECTION('',(0.,0.,1.));
#123 = DIRECTION('',(1.,0.,0.));
#130 = CONTEXT_DEPENDENT_SHAPE_REPRESENTATION(#131,#133);
#131 = ( REPRESENTATION_RELATIONSHIP('','',#105,#10)
REPRESENTATION_RELATIONSHIP_WITH_TRANSFORMATION(#132)
SHAPE_REPRESENTATION_RELATIONSHIP() );
#132 = ITEM_DEFINED_TRANSFORMATION('','',#11,#120);
#133 = PRODUCT_DEFINITION_SHAPE('Placement','Placement of an item',#134);
#134 = NEXT_ASSEMBLY_USAGE_OCCURRENCE('1','C1','',#5,#100,$);

#200 = PRODUCT_DEFINITION('design','',#201,#3);
#201 = PRODUCT_DEFINITION_FORMATION('','',#202);
#202 = PRODUCT('Offset_Connector','Offset_Connector','',(#2));
#203 = SHAPE_DEFINITION_REPRESENTATION(#204,#205);
#204 = PRODUCT_DEFINITION_SHAPE('','',#200);
#205 = ADVANCED_BREP_SHAPE_REPRESENTATION('',(#206,#216),#4);
#206 = MANIFOLD_SOLID_BREP('',#207);
#207 = CLOSED_SHELL('',(#208,#209));
#208 = VERTEX_POINT('',#210);
#209 = VERTEX_POINT('',#211);
#210 = CARTESIAN_POINT('',(50.,20.,0.));
#211 = CARTESIAN_POINT('',(56.,22.,1.));
#216 = AXIS2_PLACEMENT_3D('',#217,#218,#219);
#217 = CARTESIAN_POINT('',(0.,0.,0.));
#218 = DIRECTION('',(0.,0.,1.));
#219 = DIRECTION('',(1.,0.,0.));
#220 = AXIS2_PLACEMENT_3D('',#221,#222,#223);
#221 = CARTESIAN_POINT('',(-45.,-24.,0.811));
#222 = DIRECTION('',(0.,0.,1.));
#223 = DIRECTION('',(1.,0.,0.));
#230 = CONTEXT_DEPENDENT_SHAPE_REPRESENTATION(#231,#233);
#231 = ( REPRESENTATION_RELATIONSHIP('','',#205,#10)
REPRESENTATION_RELATIONSHIP_WITH_TRANSFORMATION(#232)
SHAPE_REPRESENTATION_RELATIONSHIP() );
#232 = ITEM_DEFINED_TRANSFORMATION('','',#11,#220);
#233 = PRODUCT_DEFINITION_SHAPE('Placement','Placement of an item',#234);
#234 = NEXT_ASSEMBLY_USAGE_OCCURRENCE('2','J9','',#5,#200,$);

#300 = PRODUCT_DEFINITION('design','',#301,#3);
#301 = PRODUCT_DEFINITION_FORMATION('','',#302);
#302 = PRODUCT('daughterboard','daughterboard','',(#2));
#303 = SHAPE_DEFINITION_REPRESENTATION(#304,#305);
#304 = PRODUCT_DEFINITION_SHAPE('','',#300);
#305 = SHAPE_REPRESENTATION('',(#11),#4);
#320 = AXIS2_PLACEMENT_3D('',#321,#322,#323);
#321 = CARTESIAN_POINT('',(20.,-2.,-5.E-02));
#322 = DIRECTION('',(0.,0.,-1.));
#323 = DIRECTION('',(-1.,0.,0.));
#330 = CONTEXT_DEPENDENT_SHAPE_REPRESENTATION(#331,#333);
#331 = ( REPRESENTATION_RELATIONSHIP('','',#305,#10)
REPRESENTATION_RELATIONSHIP_WITH_TRANSFORMATION(#332)
SHAPE_REPRESENTATION_RELATIONSHIP() );
#332 = ITEM_DEFINED_TRANSFORMATION('','',#11,#320);
#333 = PRODUCT_DEFINITION_SHAPE('Placement','Placement of an item',#334);
#334 = NEXT_ASSEMBLY_USAGE_OCCURRENCE('3','M1','',#5,#300,$);

#400 = PRODUCT_DEFINITION('design','',#401,#3);
#401 = PRODUCT_DEFINITION_FORMATION('','',#402);
#402 = PRODUCT('daughterboard_PCB','daughterboard_PCB','',(#2));
#403 = SHAPE_DEFINITION_REPRESENTATION(#404,#405);
#404 = PRODUCT_DEFINITION_SHAPE('','',#400);
#405 = ADVANCED_BREP_SHAPE_REPRESENTATION('',(#406),#4);
#406 = MANIFOLD_SOLID_BREP('',#407);
#407 = CLOSED_SHELL('',(#408,#409));
#408 = VERTEX_POINT('',#410);
#409 = VERTEX_POINT('',#411);
#410 = CARTESIAN_POINT('',(0.,0.,0.));
#411 = CARTESIAN_POINT('',(8.,6.,0.6));
#420 = AXIS2_PLACEMENT_3D('',#421,#422,#423);
#421 = CARTESIAN_POINT('',(0.,0.,0.));
#422 = DIRECTION('',(0.,0.,1.));
#423 = DIRECTION('',(1.,0.,0.));
#430 = CONTEXT_DEPENDENT_SHAPE_REPRESENTATION(#431,#433);
#431 = ( REPRESENTATION_RELATIONSHIP('','',#405,#305)
REPRESENTATION_RELATIONSHIP_WITH_TRANSFORMATION(#432)
SHAPE_REPRESENTATION_RELATIONSHIP() );
#432 = ITEM_DEFINED_TRANSFORMATION('','',#11,#420);
#433 = PRODUCT_DEFINITION_SHAPE('Placement','Placement of an item',#434);
#434 = NEXT_ASSEMBLY_USAGE_OCCURRENCE('4','=>[0:1:1:9]','',#300,#400,$);

#500 = PRODUCT_DEFINITION('design','',#501,#3);
#501 = PRODUCT_DEFINITION_FORMATION('','',#502);
#502 = PRODUCT('R_0402_1005Metric','R_0402_1005Metric','',(#2));
#503 = SHAPE_DEFINITION_REPRESENTATION(#504,#505);
#504 = PRODUCT_DEFINITION_SHAPE('','',#500);
#505 = ADVANCED_BREP_SHAPE_REPRESENTATION('',(#506),#4);
#506 = MANIFOLD_SOLID_BREP('',#507);
#507 = CLOSED_SHELL('',(#508,#509));
#508 = VERTEX_POINT('',#510);
#509 = VERTEX_POINT('',#511);
#510 = CARTESIAN_POINT('',(-0.5,-0.25,0.));
#511 = CARTESIAN_POINT('',(0.5,0.25,0.35));
#520 = AXIS2_PLACEMENT_3D('',#521,#522,#523);
#521 = CARTESIAN_POINT('',(3.,3.,0.6));
#522 = DIRECTION('',(0.,0.,1.));
#523 = DIRECTION('',(1.,0.,0.));
#530 = CONTEXT_DEPENDENT_SHAPE_REPRESENTATION(#531,#533);
#531 = ( REPRESENTATION_RELATIONSHIP('','',#505,#305)
REPRESENTATION_RELATIONSHIP_WITH_TRANSFORMATION(#532)
SHAPE_REPRESENTATION_RELATIONSHIP() );
#532 = ITEM_DEFINED_TRANSFORMATION('','',#11,#520);
#533 = PRODUCT_DEFINITION_SHAPE('Placement','Placement of an item',#534);
#534 = NEXT_ASSEMBLY_USAGE_OCCURRENCE('5','C9','',#300,#500,$);
ENDSEC;
END-ISO-10303-21;
"""


@pytest.fixture
def placed():
    return {item.ref: item for item in stepmeta.instances(GEOMETRY, geometry=True)}


def test_an_ordinary_components_solids_are_where_its_placement_is(placed):
    """The normal case, and the one the whole thing used to assume was the only one."""
    chip = placed["C1"]

    assert chip.extent.min == pytest.approx((9.7, -4.65, 0.811))
    assert chip.extent.max == pytest.approx((11.3, -3.85, 1.611))
    assert chip.extent.solids == 1
    assert chip.where == pytest.approx((chip.x, chip.y))


def test_a_model_drawn_away_from_its_origin_is_found_by_its_solids(placed):
    """
    J9 is the U.FL in miniature: 57 mm between where the file says it is and where it can
    be seen.

    KiCad folds the footprint's model offset into the placement, so a library part drawn
    away from its own origin is placed somewhere nothing is. Its solids are in the right
    place -- that is what the offset was for -- so they are what says where it is.
    """
    connector = placed["J9"]

    assert (connector.x, connector.y) == (-45.0, -24.0)
    assert connector.where == pytest.approx((8.0, -3.0))
    assert math.dist(connector.where, (connector.x, connector.y)) == pytest.approx(57.0, abs=0.1)


def test_a_placement_point_that_is_not_on_the_part_does_not_widen_its_box(placed):
    """
    The surfaces of J9's solid are drawn about a placement at its model origin, and that
    origin is 50 mm from the part. Counted in, the box is nine times too long and its middle
    is nowhere near the connector -- which is the bug this reads vertices to avoid.
    """
    assert placed["J9"].extent.min == pytest.approx((5.0, -4.0, 0.811))
    assert placed["J9"].extent.max == pytest.approx((11.0, -2.0, 1.811))


def test_a_placement_says_what_surface_a_part_sits_on_unless_it_is_nowhere_near(placed):
    """
    A placement's z is the board under that component, which beats one plane fitted to the
    whole board -- but only for a placement that is where the part is. J9's is 57 mm away,
    and answering 0.811 for it would be answering about a different place on the board.
    """
    assert placed["C1"].surface == pytest.approx(0.811)
    assert placed["J9"].surface is None


def test_a_sub_board_is_one_component_and_its_box_holds_all_of_it(placed):
    """
    M1 is a board on a board: its own substrate and its own components, turned over onto the
    underside. Everything under it is in its box, in this board's coordinates and not its own
    -- and its placement is the corner of its own outline, 5 mm from the middle of it.
    """
    module = placed["M1"]

    assert module.assembly is True
    assert module.side == stepmeta.SIDE_BOTTOM
    assert module.extent.solids == 2, "its substrate and the part on it"
    assert module.extent.min == pytest.approx((12.0, -2.0, -1.0))
    assert module.extent.max == pytest.approx((20.0, 4.0, -0.05))
    assert module.where == pytest.approx((16.0, 1.0))
    assert math.dist(module.where, (module.x, module.y)) == pytest.approx(5.0)


def test_only_a_thing_with_its_own_insides_is_an_assembly(placed):
    assert [ref for ref, item in placed.items() if item.assembly] == ["M1"]


def test_a_sub_boards_own_films_belong_to_it_and_not_to_this_board():
    """
    The daughterboard brings its own substrate, named the way KiCad names one. It is a board
    body of that board and a part of this one, so it is neither a component here nor one of
    this board's own bodies -- and nothing here should count it as either.
    """
    assert set(_by_ref(GEOMETRY)) == {"C1", "J9", "M1"}
    assert stepmeta.board_nodes(GEOMETRY) == []


def test_a_component_knows_what_it_is_an_instance_of(placed):
    """Which is how the board's substrate is told from the films over it."""
    assert placed["C1"].product == "C_0603_1608Metric"
    assert placed["M1"].product == "daughterboard"


def test_the_geometry_is_not_read_unless_it_is_asked_for():
    """
    It costs another walk of a file that can be thirty megabytes, and registration does not
    want it. Asked nothing, every answer falls back to the placement, which is what every
    caller got before any of this existed.
    """
    found = _by_ref(GEOMETRY)

    assert found["J9"].extent is None
    assert found["J9"].where == (found["J9"].x, found["J9"].y)
    assert found["J9"].surface == found["J9"].z


def test_an_assembly_that_contains_itself_does_not_hang():
    """Somebody's upload, and a file that is not a tree is still a file worth reading."""
    text = GEOMETRY.replace(
        "#534 = NEXT_ASSEMBLY_USAGE_OCCURRENCE('5','C9','',#300,#500,$);",
        "#534 = NEXT_ASSEMBLY_USAGE_OCCURRENCE('5','C9','',#300,#500,$);\n"
        "#540 = NEXT_ASSEMBLY_USAGE_OCCURRENCE('6','LOOP','',#500,#300,$);",
    )

    module = {item.ref: item for item in stepmeta.instances(text, geometry=True)}["M1"]
    assert module.extent.solids >= 2


def test_a_file_with_no_geometry_at_all_is_not_a_crash(excerpt):
    """The real excerpt has the assembly and stubs where the shells would be."""
    assert all(item.extent is None for item in stepmeta.instances(excerpt, geometry=True))


# ─── Files built to make the walk expensive ──────────────────────────────────


def _shared_chain(depth: int, turned: bool = False) -> str:
    """
    A chain of products in which each uses the next twice, with one vertex at the bottom:
    2**depth paths to that vertex in a file of a few KB. `turned` places the two uses about
    two different axes (a turn about z, a turn about x), so the composite orientations do not
    commute and every path arrives turned differently; otherwise both uses are the identity.
    """
    lines = [
        "ISO-10303-21;",
        "HEADER;",
        "ENDSEC;",
        "DATA;",
        "#1 = CARTESIAN_POINT('',(0.,0.,0.));",
        "#2 = DIRECTION('',(0.,0.,1.));",
        "#3 = DIRECTION('',(1.,0.,0.));",
        "#4 = AXIS2_PLACEMENT_3D('',#1,#2,#3);",
    ]
    uses = (("0.,0.,1.", "0.8,0.6,0."), ("0.,0.6,0.8", "1.,0.,0."))
    serial = 100
    for level in range(depth):
        for z_dir, x_dir in uses:
            occurrence = serial
            lines.append(
                f"#{occurrence} = NEXT_ASSEMBLY_USAGE_OCCURRENCE('{occurrence}',"
                f"'R{occurrence}','',#{10000 + level},#{10001 + level},$);"
            )
            if turned:
                lines += [
                    f"#{serial + 1} = PRODUCT_DEFINITION_SHAPE('','',#{occurrence});",
                    f"#{serial + 2} = CONTEXT_DEPENDENT_SHAPE_REPRESENTATION(#{serial + 3},#{serial + 1});",
                    f"#{serial + 3} = ( REPRESENTATION_RELATIONSHIP('','',#9,#9) "
                    f"REPRESENTATION_RELATIONSHIP_WITH_TRANSFORMATION(#{serial + 4}) "
                    "SHAPE_REPRESENTATION_RELATIONSHIP() );",
                    f"#{serial + 4} = ITEM_DEFINED_TRANSFORMATION('','',#4,#{serial + 5});",
                    f"#{serial + 5} = AXIS2_PLACEMENT_3D('',#1,#{serial + 6},#{serial + 7});",
                    f"#{serial + 6} = DIRECTION('',({z_dir}));",
                    f"#{serial + 7} = DIRECTION('',({x_dir}));",
                ]
            serial += 10
    lines += [f"#{10000 + level} = PRODUCT_DEFINITION('d','',#5000,#5001);" for level in range(depth + 1)]
    lines += [
        f"#60000 = PRODUCT_DEFINITION_SHAPE('','',#{10000 + depth});",
        "#60001 = SHAPE_DEFINITION_REPRESENTATION(#60000,#60002);",
        "#60002 = SHAPE_REPRESENTATION('',(#60003),#9);",
        "#60003 = VERTEX_POINT('',#60004);",
        "#60004 = CARTESIAN_POINT('',(1.,2.,3.));",
        "ENDSEC;",
        "END-ISO-10303-21;",
    ]
    return "\n".join(lines)


def _every_path(text: str) -> dict[str, tuple]:
    """
    {ref: (min, max, solids)} for the board's occurrences by walking every path from the root
    -- what the walk did before it remembered anything, kept here as the reference answer.
    """
    found = stepmeta.entities(text)
    occurrences = list(stepmeta._occurrences(found))
    root = stepmeta._root(occurrences)
    placements = stepmeta._transforms(found)
    index = stepmeta._index(found)
    reps = stepmeta.shape_reps(found, index)
    children: dict = {}
    for identifier, _seq, _ref, parent, child in occurrences:
        children.setdefault(parent, []).append((identifier, child))

    def points(product, frame):
        origin, axes = frame
        for rep in reps.get(product, ()):
            for p in stepmeta._vertices(found, index, rep, {}):
                yield tuple(origin[i] + p[0] * axes[0][i] + p[1] * axes[1][i] + p[2] * axes[2][i] for i in range(3))
        for identifier, child in children.get(product, ()):
            yield from points(child, stepmeta._through(frame, stepmeta._frame(found, placements.get(identifier))))

    answer = {}
    for identifier, _seq, ref, parent, child in occurrences:
        if parent == root:
            everything = list(points(child, stepmeta._frame(found, placements.get(identifier))))
            answer[ref] = (
                tuple(min(p[i] for p in everything) for i in range(3)),
                tuple(max(p[i] for p in everything) for i in range(3)),
                len(everything),
            )
    return answer


@pytest.mark.parametrize("turned", [False, True])
def test_a_shared_sub_assembly_is_measured_as_if_every_use_were_walked(turned):
    """Remembering a product's box per orientation gives the answer walking every path gives."""
    text = _shared_chain(6, turned=turned)

    found = {item.ref: item for item in stepmeta.instances(text, geometry=True)}
    expected = _every_path(text)

    assert set(found) == set(expected) and len(found) == 2
    for ref, (low, high, solids) in expected.items():
        assert found[ref].extent.min == pytest.approx(low, abs=1e-9)
        assert found[ref].extent.max == pytest.approx(high, abs=1e-9)
        assert found[ref].extent.solids == solids == 2**5


def _counting_steps(monkeypatch) -> list:
    """Count the walk's steps -- each carries a placement one level down -- not the clock."""
    steps = []
    through = stepmeta._through

    def counted(outer, inner):
        steps.append(1)
        return through(outer, inner)

    monkeypatch.setattr(stepmeta, "_through", counted)
    return steps


def test_a_few_kb_of_shared_sub_assemblies_is_measured_at_once(monkeypatch):
    """
    Each product using the next twice: every use was walked, 2**depth of them, and the file
    from the review -- 3.8 KB, 20 deep -- held a worker for 14 s on `step_manifest`, a public
    page. It is one measurement per product now: two steps a level, not 2**depth.
    """
    steps = _counting_steps(monkeypatch)

    found = stepmeta.instances(_shared_chain(22), geometry=True)

    assert len(steps) <= 2 * 22
    assert found[0].extent.min == found[0].extent.max == (1.0, 2.0, 3.0)
    assert found[0].extent.solids == 2**21


def test_a_file_that_defeats_the_memo_is_left_unmeasured_quickly(monkeypatch):
    """
    Turned about two axes at every level, no two paths arrive in the same orientation and
    remembering does not help; past the ceiling the file is read as having no solids -- the
    placements are still reported -- rather than measured for minutes.
    """
    steps = _counting_steps(monkeypatch)

    found = stepmeta.instances(_shared_chain(16, turned=True), geometry=True)

    assert len(steps) <= 2 * stepmeta.MAX_MEASURED + 2, "2**17 without the ceiling"
    assert [item.extent for item in found] == [None, None]


def test_an_assembly_nested_thousands_deep_is_not_a_crash():
    """3000 levels ran Python's recursion out: a RecursionError, which was a 500."""
    lines = ["DATA;"] + [
        f"#{i} = NEXT_ASSEMBLY_USAGE_OCCURRENCE('{i}','R{i}','',#{10000 + i},#{10001 + i},$);" for i in range(3000)
    ]
    lines += [
        "#60000 = PRODUCT_DEFINITION_SHAPE('','',#13000);",
        "#60001 = SHAPE_DEFINITION_REPRESENTATION(#60000,#60002);",
        "#60002 = SHAPE_REPRESENTATION('',(#60003),#9);",
        "#60003 = VERTEX_POINT('',#60004);",
        "#60004 = CARTESIAN_POINT('',(1.,2.,3.));",
        "ENDSEC;",
    ]

    found = stepmeta.instances("\n".join(lines), geometry=True)

    assert [item.ref for item in found] == ["R0"]


def test_an_entity_id_too_long_for_an_int_is_not_a_crash():
    """Python will not make an int of 5000 digits; that was a ValueError out of a public page."""
    from boarddd.step import slim as stepslim

    huge = "9" * 5000
    assert stepmeta.instances(f"DATA;\n#{huge} = FOO(1);\n#1 = BAR(#{huge});\nENDSEC;") == []
    stepslim.slim(f"DATA;\n#1 = FOO(#{huge});\nENDSEC;")
