"""
Taking the board's dressing out of a STEP file without taking anything else with it.

The file below is a whole miniature export in the shape KiCad writes: a root product, one
component, and two of the board's own bodies -- the substrate, which stays, and the pads,
which go. Everything shares one geometric context and one colour, which is the point: a slim
that removes what the pads reach would remove those too, and the file would not read.

The real files this was measured against are 8, 21 and 36 MB and cannot be fixtures; the
numbers from them are in the module's docstring. What the fixture pins is the graph surgery:
what goes, what stays, and that the file still parses as the same board afterwards.

Ported from magpie's tests/step/test_stepslim.py at 3a0374d3.
"""

from __future__ import annotations

import re

import pytest

from boarddd.step import slim as stepslim
from boarddd.step import text as stepmeta

_HEAD = """ISO-10303-21;
HEADER;
FILE_DESCRIPTION(('KiCad electronic assembly'),'2;1');
FILE_SCHEMA(('AUTOMOTIVE_DESIGN { 1 0 10303 214 1 1 1 1 }'));
ENDSEC;
DATA;
#2 = APPLICATION_CONTEXT('core data for automotive mechanical design processes');
#5 = PRODUCT_DEFINITION('design','',#6,#9);
#6 = PRODUCT_DEFINITION_FORMATION('','',#7);
#7 = PRODUCT('board','board','',(#8));
#8 = PRODUCT_CONTEXT('',#2,'mechanical');
#9 = PRODUCT_DEFINITION_CONTEXT('part definition',#2,'design');
#10 = SHAPE_REPRESENTATION('',(#11,#110,#210,#310),#20);
#11 = AXIS2_PLACEMENT_3D('',#12,#13,#14);
#12 = CARTESIAN_POINT('',(0.,0.,0.));
#13 = DIRECTION('',(0.,0.,1.));
#14 = DIRECTION('',(1.,0.,0.));
#20 = ( GEOMETRIC_REPRESENTATION_CONTEXT(3)
GLOBAL_UNCERTAINTY_ASSIGNED_CONTEXT((#21)) GLOBAL_UNIT_ASSIGNED_CONTEXT((#22))
REPRESENTATION_CONTEXT('','') );
#21 = UNCERTAINTY_MEASURE_WITH_UNIT(LENGTH_MEASURE(1.E-07),#22,'distance_accuracy_value','');
#22 = ( LENGTH_UNIT() NAMED_UNIT(*) SI_UNIT(.MILLI.,.METRE.) );
#30 = SHAPE_DEFINITION_REPRESENTATION(#31,#10);
#31 = PRODUCT_DEFINITION_SHAPE('','',#5);
#900 = COLOUR_RGB('',0.5,0.5,0.5);
#950 = MECHANICAL_DESIGN_GEOMETRIC_PRESENTATION_REPRESENTATION('',(#150,#250,#350),#20);
"""


def _product(base: int, name: str, seq: str, ref: str, x: float) -> str:
    """One product with a solid, coloured, placed under the root -- the way KiCad writes it."""
    b = base
    return f"""#{b} = PRODUCT_DEFINITION('design','',#{b + 1},#9);
#{b + 1} = PRODUCT_DEFINITION_FORMATION('','',#{b + 2});
#{b + 2} = PRODUCT('{name}','{name}','',(#8));
#{b + 3} = SHAPE_DEFINITION_REPRESENTATION(#{b + 4},#{b + 30});
#{b + 4} = PRODUCT_DEFINITION_SHAPE('','',#{b});
#{b + 10} = AXIS2_PLACEMENT_3D('',#{b + 11},#{b + 12},#{b + 13});
#{b + 11} = CARTESIAN_POINT('',({x},-4.25,0.811));
#{b + 12} = DIRECTION('',(0.,0.,1.));
#{b + 13} = DIRECTION('',(1.,0.,0.));
#{b + 20} = CONTEXT_DEPENDENT_SHAPE_REPRESENTATION(#{b + 21},#{b + 23});
#{b + 21} = ( REPRESENTATION_RELATIONSHIP('','',#{b + 30},#10)
REPRESENTATION_RELATIONSHIP_WITH_TRANSFORMATION(#{b + 22})
SHAPE_REPRESENTATION_RELATIONSHIP() );
#{b + 22} = ITEM_DEFINED_TRANSFORMATION('','',#11,#{b + 10});
#{b + 23} = PRODUCT_DEFINITION_SHAPE('Placement','Placement of an item',
  #{b + 24});
#{b + 24} = NEXT_ASSEMBLY_USAGE_OCCURRENCE('{seq}','{ref}','',#5,#{b},$);
#{b + 30} = ADVANCED_BREP_SHAPE_REPRESENTATION('',(#{b + 31}),#20);
#{b + 31} = MANIFOLD_SOLID_BREP('',#{b + 32});
#{b + 32} = CLOSED_SHELL('',(#{b + 33}));
#{b + 33} = ADVANCED_FACE('',(#{b + 34}),#{b + 36},.T.);
#{b + 34} = FACE_BOUND('',#{b + 35},.T.);
#{b + 35} = VERTEX_LOOP('',#{b + 37});
#{b + 36} = PLANE('',#{b + 10});
#{b + 37} = VERTEX_POINT('',#{b + 38});
#{b + 38} = CARTESIAN_POINT('',(1.,1.,1.));
#{b + 40} = PRODUCT_RELATED_PRODUCT_CATEGORY('part',$,(#{b + 2}));
#{b + 50} = STYLED_ITEM('color',(#{b + 51}),#{b + 31});
#{b + 51} = PRESENTATION_STYLE_ASSIGNMENT((#{b + 52}));
#{b + 52} = SURFACE_STYLE_USAGE(.BOTH.,#{b + 53});
#{b + 53} = SURFACE_SIDE_STYLE('',(#{b + 54}));
#{b + 54} = SURFACE_STYLE_FILL_AREA(#{b + 55});
#{b + 55} = FILL_AREA_STYLE('',(#{b + 56}));
#{b + 56} = FILL_AREA_STYLE_COLOUR('',#900);
"""


_TAIL = "ENDSEC;\nEND-ISO-10303-21;\n"

#: A component, the pads, and the substrate: what a KiCad export is, in miniature.
DRESSED = (
    _HEAD
    + _product(100, "C_0402", "1", "C1", 10.5)
    + _product(200, "board_pad", "2", "=>[0:1:1:16]", 0.0)
    + _product(300, "board_PCB", "3", "=>[0:1:1:17]", 0.0)
    + _TAIL
)

#: The same board with the pad product also placed as a component, which is not a thing a
#: board does, and is the case that has to leave the product's geometry alone.
SHARED = DRESSED.replace(
    _TAIL,
    """#424 = NEXT_ASSEMBLY_USAGE_OCCURRENCE('4','J9','',#5,#200,$);
#423 = PRODUCT_DEFINITION_SHAPE('Placement','Placement of an item',#424);
#420 = CONTEXT_DEPENDENT_SHAPE_REPRESENTATION(#421,#423);
#421 = ( REPRESENTATION_RELATIONSHIP('','',#230,#10)
REPRESENTATION_RELATIONSHIP_WITH_TRANSFORMATION(#422)
SHAPE_REPRESENTATION_RELATIONSHIP() );
#422 = ITEM_DEFINED_TRANSFORMATION('','',#11,#110);
"""
    + _TAIL,
)


def _ids(text: str) -> set[int]:
    return set(stepmeta.entities(text))


def _entity(text: str, identifier: int) -> str:
    return stepmeta.entities(text)[identifier][1]


# ─── What goes and what stays ────────────────────────────────────────────────


def test_the_dressing_is_named_and_its_reference_reported():
    out = stepslim.slim(DRESSED)

    assert out.dropped == ["board_pad"]
    assert out.dropped_refs == ["=>[0:1:1:16]"]
    assert out.removed > 0 and out.reason == ""


def test_the_pads_geometry_goes_and_the_components_stays():
    out = stepslim.slim(DRESSED)
    ids = _ids(out.text)

    assert {231, 232, 233, 238} & ids == set(), "the pad's solid, shell, face and point"
    assert {131, 132, 133, 138} <= ids, "the component's"
    assert {331, 332, 333, 338} <= ids, "the substrate's: its thickness is measured"


def test_the_instance_chain_goes_with_it():
    """The occurrence, its placement link, the product itself and its category entry."""
    ids = _ids(stepslim.slim(DRESSED).text)

    assert {224, 223, 220, 221, 222, 200, 201, 202, 203, 204, 240} & ids == set()
    assert {124, 123, 120, 100, 140} <= ids


def test_what_every_product_shares_survives():
    """The context, the units and the colour are reached by the pads too. That is not a reason."""
    ids = _ids(stepslim.slim(DRESSED).text)

    assert {2, 8, 9, 20, 21, 22, 900} <= ids


def test_the_root_is_untouched():
    out = stepslim.slim(DRESSED)
    ids = _ids(out.text)

    assert {5, 6, 7, 10, 11, 30, 31} <= ids
    # The root's shape still lists the pad's placement axis. An axis nothing uses is legal,
    # and rewriting the root to tidy it would be a reason to worry about the root.
    assert 210 in ids


def test_the_styled_item_that_coloured_the_pads_goes_and_the_list_is_repaired():
    out = stepslim.slim(DRESSED)
    ids = _ids(out.text)

    assert 250 not in ids
    assert {251, 252, 253, 254, 255, 256} & ids == set(), "its style chain, orphaned and swept"
    assert re.search(r"\(#150,\s*#350\)", _entity(out.text, 950)), _entity(out.text, 950)


def test_the_file_still_reads_as_the_same_board():
    out = stepslim.slim(DRESSED)

    before = [(i.ref, i.x, i.y, i.z, i.side) for i in stepmeta.instances(DRESSED)]
    after = [(i.ref, i.x, i.y, i.z, i.side) for i in stepmeta.instances(out.text)]
    assert after == before == [("C1", 10.5, -4.25, 0.811, "top")]
    assert [node.ref for node in stepmeta.board_nodes(out.text)] == ["=>[0:1:1:17]"]


def test_every_reference_in_the_result_resolves():
    """The one property that makes a STEP file readable at all."""
    out = stepslim.slim(DRESSED)
    found = stepmeta.entities(out.text)

    for identifier, (_kind, rest) in found.items():
        for target in re.findall(r"#(\d+)", rest):
            assert int(target) in found, f"#{identifier} refers to #{target}, which is gone"


def test_slimming_twice_changes_nothing():
    once = stepslim.slim(DRESSED)
    twice = stepslim.slim(once.text)

    assert twice.text == once.text
    assert twice.dropped == []


# ─── When not to ─────────────────────────────────────────────────────────────


def test_a_product_also_placed_as_a_component_keeps_its_geometry():
    out = stepslim.slim(SHARED)
    ids = _ids(out.text)

    assert out.dropped == ["board_pad"], "the dressing instance still goes"
    assert 224 not in ids
    assert {200, 230, 231, 238} <= ids, "but the product J9 is an instance of stays"
    assert {i.ref for i in stepmeta.instances(out.text)} == {"C1", "J9"}


def test_a_file_with_no_dressing_comes_back_as_it_came():
    plain = _HEAD + _product(100, "C_0402", "1", "C1", 10.5) + _TAIL

    out = stepslim.slim(plain)

    assert out.text == plain
    assert out.dropped == [] and out.removed == 0
    assert "no board dressing" in out.reason


@pytest.mark.parametrize("text", ["", "not a step file", "ISO-10303-21;\nHEADER;\nENDSEC;\n"])
def test_nothing_useful_is_not_a_crash(text):
    out = stepslim.slim(text)

    assert out.text == text and out.dropped == []


@pytest.mark.parametrize(
    "name", ["board_pad", "board_pads", "kibot_abc_silkscreen", "myboard_soldermask", "X_SOLDERMASK"]
)
def test_the_films_are_recognized_by_their_kicad_names(name):
    assert stepslim.DRESSING.search(name)


@pytest.mark.parametrize("name", ["board_PCB", "C_0402", "pad_driver", "silkscreen_light"])
def test_and_nothing_else_is(name):
    assert not stepslim.DRESSING.search(name)


# ─── Weighing ────────────────────────────────────────────────────────────────


def test_the_heaviest_products_are_named_with_their_share():
    heavy = stepslim.weigh(DRESSED)

    names = [product.name for product in heavy]
    assert set(names) == {"C_0402", "board_pad", "board_PCB", "board"}
    assert all(product.entities > 0 and 0 < product.share < 1 for product in heavy)
    assert next(p for p in heavy if p.name == "C_0402").uses == 1


def test_the_slimmed_files_weights_are_of_what_is_left():
    out = stepslim.slim(DRESSED)

    assert "board_pad" not in [product.name for product in out.heaviest]


# ─── The repair itself ───────────────────────────────────────────────────────


def test_a_reference_in_a_list_is_cut_with_its_comma():
    assert stepslim._without("MDGPR", "('',(#1,#2,#3),#9)", {2}) == "('',(#1,#3),#9)"
    assert stepslim._without("MDGPR", "('',(#1,#2,#3),#9)", {1}) == "('',(#2,#3),#9)"
    assert stepslim._without("MDGPR", "('',(#1,#2,#3),#9)", {3}) == "('',(#1,#2),#9)"


def test_a_reference_in_a_scalar_position_takes_the_entity():
    assert stepslim._without("STYLED_ITEM", "('color',(#5),#31)", {31}) is None


def test_a_list_left_empty_takes_the_entity():
    assert stepslim._without("PRPC", "('part',$,(#7))", {7}) is None


def test_a_complex_entitys_own_arguments_are_scalar():
    complex_entity = "( REPRESENTATION_RELATIONSHIP('','',#130,#10) SHAPE_REPRESENTATION_RELATIONSHIP() )"
    assert stepslim._without("", complex_entity, {130}) is None


def test_a_hash_inside_a_string_is_not_a_reference():
    assert stepslim._without("PRODUCT", "('part #2','',(#3))", {2}) == "('part #2','',(#3))"
