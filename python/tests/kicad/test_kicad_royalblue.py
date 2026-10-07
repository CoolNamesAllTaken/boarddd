"""read_kicad_pcb on KiCad's royalblue54L_feather demo, checked three ways:

* it reproduces the golden board.json (every field the board file holds; the fab half of board.json, layers
  and drill files, comes from fab/ and is compared by position);
* pcbnew's own numbers (fixtures/royalblue54L_pcbnew/golden.json, made by its make_golden.py with KiCad 10.0.6):
  every pad placed through the model's placement transform lands on pcbnew's pad, copper centre, copper bbox
  and hole; every net has pcbnew's net class;
* the stackup kicad-cli exported into the gbrjob (fab/*.gbrjob MaterialStackup).
"""

import json
import math
from pathlib import Path

import pytest

from boarddd import model as m
from boarddd.io.kicad import read_kicad_pcb
from boarddd.io.kicad.geom import norm_angle, pad_copper, pad_offset, rotate
from boarddd.validate import validate_board

from conftest import FIXTURES

RB = FIXTURES / "royalblue54L_feather"
PCB = RB / "kicad" / "RoyalBlue54L-Feather.kicad_pcb"
PCBNEW = json.loads((Path(__file__).parent / "fixtures" / "royalblue54L_pcbnew" / "golden.json").read_text())
FAB_FIELDS = ("layers", "drills", "source", "warnings")  # what board.json takes from fab/, not the board file


@pytest.fixture(scope="module")
def board():
    return read_kicad_pcb(PCB)


@pytest.fixture(scope="module")
def data(board):
    return board.to_dict()


def test_valid_and_round_trips(data):
    assert validate_board(data) == []
    assert m.Board.from_dict(data).to_dict() == data


def test_reproduces_the_golden_board(data, golden):
    for key in golden:
        if key not in FAB_FIELDS:
            assert data[key] == golden[key], key
    assert data["source"]["kind"] == "kicad_pcb" and data["source"]["created"] == golden["source"]["created"]
    assert [f["path"] for f in data["source"]["files"]] == [
        "RoyalBlue54L-Feather.kicad_pcb",
        "RoyalBlue54L-Feather.kicad_pro",
    ]
    assert data["layers"] == [] and data["warnings"] == []


def test_drills_match_the_drill_files(data, golden):
    """Pad holes and vias from the board file = kicad-cli's Excellon (slots end to end, either direction)."""

    def ends(d):
        a = (d["x"], d["y"])
        b = (d["x2"], d["y2"]) if d["x2"] is not None else a
        return sorted([a, b])

    fab = golden["drills"]
    assert len(data["drills"]) == len(fab) == 278
    left = list(fab)
    for d in data["drills"]:
        i = min(
            range(len(left)), key=lambda i: sum(math.dist(p, q) for p, q in zip(ends(d), ends(left[i]), strict=True))
        )
        f = left.pop(i)
        assert sum(math.dist(p, q) for p, q in zip(ends(d), ends(f), strict=True)) < 2e-3, (
            d,
            f,
        )  # the drill file has 3 decimals
        assert (d["plated"], d["function"]) == (f["plated"], f["function"])
        assert abs(d["diameter"] - f["diameter"]) < 1e-3  # the vias' 0.00001 mm placeholder is 0 in the drill file
    assert sum(d["x2"] is not None for d in data["drills"]) == sum(d["x2"] is not None for d in fab) > 0


def place(c: m.Component, p) -> tuple[float, float]:
    """docs/model.md: footprint point -> board frame."""
    qy = p[1] if c.side == "bottom" else -p[1]
    a = math.radians(c.rotation)
    return c.x + math.cos(a) * p[0] - math.sin(a) * qy, c.y + math.sin(a) * p[0] + math.cos(a) * qy


def to_kicad(c: m.Component, q) -> tuple[float, float]:
    x, y = place(c, q)
    return (x, -y)


def test_pads_land_on_pcbnew(board):
    gold = {}
    for p in PCBNEW["pads"]:
        gold.setdefault(p["ref"], []).append(p)
    assert sum(len(v) for v in gold.values()) == 428
    checked = 0
    for c in board.components:
        pads = board.footprints[c.footprint].pads
        assert len(pads) == len(gold.get(c.ref, [])), c.ref
        for pad, g in zip(pads, gold.get(c.ref, []), strict=True):
            where = f"{c.ref} pad {pad.number}"
            assert math.dist(to_kicad(c, pad.at[:2]), g["at"]) < 1e-5, where
            want = norm_angle(c.rotation - pad.at[2]) if c.side == "bottom" else norm_angle(c.rotation + pad.at[2])
            assert abs(norm_angle(want - g["angle"])) < 1e-6, where
            ox, oy = rotate(*pad_offset(pad), pad.at[2])
            assert math.dist(to_kicad(c, (pad.at[0] + ox, pad.at[1] + oy)), g["copper_center"]) < 1e-5, where
            assert (pad.drill is not None) == (g["hole"] is not None), where
            if pad.drill is not None:
                assert sorted(pad.drill.size) == pytest.approx(sorted(g["hole"]), abs=1e-6), where
            if pad.shape != "custom":
                pts = [to_kicad(c, q) for q in pad_copper(pad, 16)[0]]
                xs, ys = [q[0] for q in pts], [q[1] for q in pts]
                assert [min(xs), min(ys), max(xs), max(ys)] == pytest.approx(g["copper_bbox"], abs=0.01), where
            checked += 1
    assert checked == 428


def test_nets_and_classes_match_pcbnew(board):
    assert {n.name: n.net_class for n in board.nets} == PCBNEW["net_classes"]
    pairs = {n.name: n.pair for n in board.nets if n.pair}
    assert pairs == {"/Debugger/D+": "/Debugger/D-", "/Debugger/D-": "/Debugger/D+"}
    classes = {c.name: c for c in board.net_classes}
    usb = classes["USB_DIFF"]
    assert (usb.track_width, usb.diff_pair_width, usb.diff_pair_gap, usb.clearance) == (0.125, 0.125, 0.2032, 0.2032)
    assert sorted(usb.nets) == ["/Debugger/D+", "/Debugger/D-"]
    assert usb.impedance is None  # 'USB_DIFF' names no value
    assert len(classes["Default"].nets) == 93


def test_stackup(board):
    """8 copper layers, FR4 4.5 / 0.02, as the board file says and the gbrjob kicad-cli wrote shows."""
    st = board.stackup
    assert (st.thickness, st.copper_layers, st.finish, st.impedance_controlled) == (1.6, 8, "ENIG", False)
    copper = [la for la in st.layers if la.kind == "copper"]
    diel = [la for la in st.layers if la.kind == "dielectric"]
    assert [la.name for la in copper] == ["F.Cu", *(f"In{i}.Cu" for i in range(1, 7)), "B.Cu"]
    assert len(diel) == 7
    assert all((la.epsilon_r, la.loss_tangent, la.material) == (4.5, 0.02, "FR4") for la in diel)
    assert [la.dielectric for la in diel] == ["prepreg", "core"] * 3 + ["prepreg"]
    job = json.loads(next((RB / "fab").glob("*.gbrjob")).read_text())
    ours = [la for la in st.layers if la.kind in ("copper", "dielectric", "mask")]
    theirs = [e for e in job["MaterialStackup"] if e["Type"] in ("Copper", "Dielectric", "SolderMask")]
    assert len(ours) == len(theirs) == 17
    for a, b in zip(ours, theirs, strict=True):
        assert a.kind == {"Copper": "copper", "Dielectric": "dielectric", "SolderMask": "mask"}[b["Type"]]
        assert a.thickness == b["Thickness"], (a.name, b["Name"])
        if b["Type"] == "Dielectric":
            assert a.material == b["Material"]
    assert sum(
        la.thickness for la in st.layers if la.thickness and la.kind in ("copper", "dielectric")
    ) == pytest.approx(1.58)
