"""gbrjob.read_stackup: the job file's MaterialStackup and GeneralSpecs as a model Stackup, agreeing with the
.kicad_pcb reader on the board kicad-cli wrote it from."""

import json

import pytest

from boarddd.io import gbrjob
from boarddd.io.kicad import read_kicad_pcb

from conftest import FIXTURES

RB = FIXTURES / "royalblue54L_feather"


def test_royalblue_job_matches_the_board_file():
    job = next((RB / "fab").glob("*.gbrjob")).read_text()
    st = gbrjob.read_stackup(job, [f"L{i}" for i in range(1, 9)])
    kicad = read_kicad_pcb(RB / "kicad" / "RoyalBlue54L-Feather.kicad_pcb").stackup
    assert (st.thickness, st.copper_layers, st.finish) == (kicad.thickness, kicad.copper_layers, kicad.finish)
    assert st.mask_color == kicad.mask_color and st.silk_color == kicad.silk_color
    assert st.impedance_controlled is None  # this job doesn't say
    assert [(la.kind, la.side, la.thickness) for la in st.layers] == [
        (la.kind, la.side, la.thickness) for la in kicad.layers
    ]
    assert [la.layer for la in st.layers if la.kind == "copper"] == [f"L{i}" for i in range(1, 9)]
    # KiCad leaves FR4's default Er/Df out of the job: only the board file has 4.5 / 0.02
    assert all(la.epsilon_r is None for la in st.layers)


JOB = {
    "GeneralSpecs": {"LayerNumber": 4, "BoardThickness": 1.5625, "Finish": "ENIG", "ImpedanceControlled": True},
    "MaterialStackup": [
        {"Type": "SolderMask", "Thickness": 0.01, "Color": "Green", "Name": "Top Solder Mask"},
        {"Type": "Copper", "Thickness": 0.035, "Name": "F.Cu", "Conductivity": 5.959e7},
        {
            "Type": "Dielectric",
            "Thickness": 0.0681,
            "Material": "R-1551(W)",
            "DielectricConstant": "4.3",
            "LossTangent": "0.02",
            "Name": "F.Cu/In1.Cu (1/2)",
        },
        {
            "Type": "Dielectric",
            "Thickness": 0.0681,
            "Material": "R-1551(W)",
            "DielectricConstant": "4.3",
            "LossTangent": "0.02",
            "Name": "F.Cu/In1.Cu (2/2)",
        },
        {"Type": "Copper", "Thickness": 0.0152, "Name": "In1.Cu"},
        {
            "Type": "Dielectric",
            "Thickness": 1.13,
            "Material": "R-1566(W)",
            "DielectricConstant": 4.6,
            "LossTangent": 0.02,
            "Name": "In1.Cu/In2.Cu",
        },
        {"Type": "Copper", "Thickness": 0.0152, "Name": "In2.Cu"},
        {"Type": "Dielectric", "Thickness": 0.1, "DielectricConstant": "NaN", "Name": "In2.Cu/B.Cu"},
        {"Type": "Copper", "Thickness": 0.035, "Name": "B.Cu"},
        {"Type": "Legend", "Color": "White", "Name": "Bottom Silk Screen"},
    ],
}


def test_kicad_job_with_sublayers_and_strings():
    st = gbrjob.read_stackup(json.dumps(JOB), layer_id=lambda kind, side: f"{side}-{kind}")
    assert (st.thickness, st.copper_layers, st.finish, st.impedance_controlled) == (1.5625, 4, "ENIG", True)
    names = [la.name for la in st.layers]
    assert names == [
        "Top Solder Mask",
        "F.Cu",
        "F.Cu/In1.Cu",
        "In1.Cu",
        "In1.Cu/In2.Cu",
        "In2.Cu",
        "In2.Cu/B.Cu",
        "B.Cu",
        "Bottom Silk Screen",
    ]
    pre = st.layers[2]
    assert (pre.thickness, pre.epsilon_r, pre.loss_tangent, pre.material) == (0.1362, 4.3, 0.02, "R-1551(W)")
    assert [s.thickness for s in pre.sublayers] == [0.0681, 0.0681]
    core = st.layers[4]
    assert (core.epsilon_r, core.sublayers, core.side) == (4.6, [], "inner")
    assert st.layers[6].epsilon_r is None  # 'NaN' is no constant
    assert st.layers[1].conductivity == pytest.approx(5.959e7) and st.layers[1].layer is None  # no copper ids given
    assert (st.layers[0].layer, st.layers[-1].layer) == ("top-mask", "bottom-silk")
    assert st.mask_color.top == "Green" and st.silk_color.bottom == "White"


@pytest.mark.parametrize("text", ["", "[]", "{", '{"GeneralSpecs": "x", "MaterialStackup": "y"}'])
def test_junk(text):
    st = gbrjob.read_stackup(text)
    assert st.layers == [] and st.thickness is None and st.copper_layers is None
