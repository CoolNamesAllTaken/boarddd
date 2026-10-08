"""Splitting the tiny fixture board (see fixtures_occ/make_board.py for what is on it)."""

from __future__ import annotations

import pytest
from stepkit import require_occ

require_occ()

import numpy as np  # noqa: E402
from stepkit import LIBRARY, REPO, TINY, TINY_POS  # noqa: E402

EXCERPT = REPO / "fixtures" / "generated" / "step" / "royalblue54L_feather-excerpt.step"

from boarddd.step.fingerprint import same  # noqa: E402
from boarddd.step.split import split  # noqa: E402

# A geometry-free assembly (as in test_step_text.py's MINI): placements and names, no solids.
NO_GEOMETRY = """ISO-10303-21;
HEADER;
FILE_DESCRIPTION(('KiCad electronic assembly'),'2;1');
FILE_NAME('mini.step','2026-01-01T00:00:00',(''),(''),'','','');
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
#123 = PRODUCT_DEFINITION_SHAPE('Placement','Placement of an item',#124);
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


@pytest.fixture(scope="module")
def tiny():
    return split(TINY, pos=TINY_POS)


@pytest.fixture(scope="module")
def tiny_no_pos():
    return split(TINY)


def test_designators(tiny):
    assert sorted(tiny.components) == sorted(["R1", "R2", "R3", "R4", "R5", "U1", "U2", "U3", "C1", "D1", "Q1"])
    assert all(c.name_source == "instance" for c in tiny.components.values())
    assert tiny.missing_models == []


def test_board_body(tiny):
    board = tiny.board
    assert board.source == "kicad"
    assert board.name == "tiny_PCB"
    assert board.top_z == pytest.approx(1.51, abs=1e-6)
    assert board.thickness == pytest.approx(1.51, abs=1e-6)
    # KiCad seats every model on copper and pad: 0.035 + 0.05 above the substrate.
    assert tiny.seat_z == pytest.approx(0.085, abs=1e-6)


def test_pos_fit_and_frames(tiny):
    fit = tiny.pos_fit
    assert fit.ok and fit.residual_mm < 1e-3 and fit.matched == 11
    assert [ref for ref, _miss in fit.elsewhere] == ["Q1"]
    for component in tiny.components.values():
        assert component.frame_source == "pos"
        assert component.pos["rot_diff_deg"] == pytest.approx(0.0, abs=1e-6)
    assert tiny.components["R5"].side == "bottom"
    assert tiny.components["U3"].side == "bottom"
    assert tiny.components["R3"].rotation_deg == pytest.approx(45.0)
    # The component frame of a bottom part is turned over: its +Z points down in the board.
    assert tiny.components["R5"].transform[2, 2] == pytest.approx(-1.0)
    assert tiny.components["R1"].transform[2, 2] == pytest.approx(1.0)


def test_model_offset_recovered_from_pos(tiny, tiny_no_pos):
    q1 = tiny.components["Q1"]
    assert q1.offset[:2] == pytest.approx((0.5, 0.2), abs=1e-6)
    assert q1.pos["offset_mm"] == pytest.approx(0.5385, abs=1e-3)
    # Without the pos file the frame is the placement, offset included.
    assert tiny_no_pos.components["Q1"].offset[:2] == pytest.approx((0.0, 0.0), abs=1e-6)
    assert tiny_no_pos.components["Q1"].frame_source == "step"


def test_chip_resistor_height_matches_its_model(tiny):
    r1 = tiny.components["R1"]
    assert r1.model.model_height == pytest.approx(0.35, abs=0.01)
    assert r1.measurements.height == pytest.approx(0.35 + tiny.seat_z, abs=0.01)
    assert r1.measurements.size == pytest.approx((1.0, 0.5, 0.35), abs=0.01)
    # The KiCad stock model, read as a file of its own, is the same model.
    (model,) = split(LIBRARY / "R_0402_1005Metric.step").components.values()
    assert r1.model.model_height == pytest.approx(model.measurements.height, abs=0.01)
    assert same(model.fingerprint, r1.fingerprint)
    assert model.fingerprint.frame_key == r1.fingerprint.frame_key


def test_repeated_instances_share_one_model(tiny):
    resistors = [tiny.components[ref] for ref in ("R1", "R2", "R3", "R5")]
    assert len({id(c.model) for c in resistors}) == 1
    keys = {(c.fingerprint.shape_key, c.fingerprint.frame_key, c.fingerprint.brep_key) for c in resistors}
    assert len(keys) == 1
    assert tiny.components["U1"].fingerprint == tiny.components["U3"].fingerprint
    assert len(tiny.models) == 11 - 3 - 1  # R2, R3, R5 reuse R1's; U3 reuses U1's


def test_different_models_differ(tiny):
    r1, r4 = tiny.components["R1"].fingerprint, tiny.components["R4"].fingerprint
    assert r1.shape_key != r4.shape_key and not same(r1, r4)
    # Same footprint, model differing only in the exposed pad underneath.
    u1, u2 = tiny.components["U1"].fingerprint, tiny.components["U2"].fingerprint
    assert u1.shape_key != u2.shape_key and u1.brep_key != u2.brep_key
    assert not same(u1, u2)
    # The offset SOT-23 is the same shape in a different place.
    q1 = tiny.components["Q1"].fingerprint
    assert same(q1, q1)


def test_offset_changes_frame_key_only(tiny, tiny_no_pos):
    framed, unframed = tiny.components["Q1"].fingerprint, tiny_no_pos.components["Q1"].fingerprint
    assert framed.shape_key == unframed.shape_key
    assert framed.frame_key != unframed.frame_key
    assert same(framed, unframed, frame=False) and not same(framed, unframed)


def test_domed_led_goes_through_the_board(tiny):
    measured = tiny.components["D1"].measurements
    assert "below_board_surface" in measured.flags  # its leads go through the board


def test_measurements_are_sane(tiny):
    for component in tiny.components.values():
        measured = component.measurements
        assert measured.volume > 0 and measured.area > 0
        assert measured.principal_moments[0] <= measured.principal_moments[2]
        assert measured.solids >= 1


def test_step_export_round_trips(tiny):
    for ref in ("R1", "U2", "Q1"):
        component = tiny.components[ref]
        data = component.step_bytes()
        assert data.startswith(b"ISO-10303-21")
        assert b"COLOUR_RGB" in data
        (again,) = split(data).components.values()
        assert again.fingerprint.shape_key == component.fingerprint.shape_key
        assert again.fingerprint.brep_key == component.fingerprint.brep_key
        assert again.fingerprint.frame_key == component.fingerprint.frame_key
        # The file is measured from the seat, the board's numbers from the substrate.
        lifted = np.array(component.measurements.bbox_max) - (0.0, 0.0, tiny.seat_z)
        assert again.measurements.bbox_max == pytest.approx(tuple(lifted), abs=1e-6)


def test_glb_export(tiny):
    data = tiny.components["U1"].glb_bytes()
    assert data[:4] == b"glTF" and len(data) > 1000


def test_geometry_free_file():
    """F1's royalblue excerpt keeps the assembly and empties every shell: designators, no geometry."""
    assembly = split(EXCERPT, cache=False)
    assert len(assembly.components) == 16 and {"C1", "U2", "J4", "D3"} <= set(assembly.components)
    assert all(c.model.measurements is None and "no_geometry" in c.flags for c in assembly.components.values())
    assert all(c.side == "top" for c in assembly.components.values())


def test_text_only_fallback_when_nothing_transfers(monkeypatch):
    """When OpenCascade transfers nothing, designators and placements come from the text (boarddd.step.text)."""
    from boarddd.step import occ

    def refuse(_source):
        raise ValueError("STEP file read but nothing could be transferred")

    monkeypatch.setattr(occ, "read", refuse)
    assembly = split(NO_GEOMETRY.encode(), cache=False)
    assert sorted(assembly.components) == ["C1", "R7"]
    assert all(c.model is None and "no_geometry" in c.flags for c in assembly.components.values())
    assert assembly.warnings
    assert assembly.components["C1"].side == "top" and assembly.components["R7"].side == "bottom"
    assert assembly.components["C1"].transform[:3, 3] == pytest.approx((10.5, -4.25, 0.811))


def test_color_key_tells_identical_solids_apart():
    capacitor = LIBRARY / "C_0603_1608Metric.step"
    inductor = LIBRARY / "L_0603_1608Metric.step"
    (c,) = split(capacitor).components.values()
    (ind,) = split(inductor).components.values()
    assert c.fingerprint.brep_key == ind.fingerprint.brep_key and same(c.fingerprint, ind.fingerprint)
    assert c.fingerprint.color_key and c.fingerprint.color_key != ind.fingerprint.color_key


def test_color_key_survives_export(tiny):
    component = tiny.components["U1"]
    (again,) = split(component.step_bytes()).components.values()
    assert component.fingerprint.color_key and again.fingerprint.color_key == component.fingerprint.color_key
