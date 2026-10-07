"""io.gerber_copper (and the browser's copperFromGerbers): Gerber X2 copper -> boarddd/copper@1.

Golden: royalblue54L_feather and the NFC antenna, each read from its .kicad_pcb (io.kicad.read_kicad_copper) and
from kicad-cli's Gerber X2 + Excellon export of it, give the same nets, tracks (arcs with their mid), vias, pads
and filled area per layer and net (tests/io/copperkit.py says what may differ). The JS reader gives what the
Python one does on both. Small Gerbers written here cover what KiCad does not write.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from copperkit import compare

from boarddd import copper as cu
from boarddd.io.gerber_copper import parse_copper_layer, read_gerber_copper, read_gerber_copper_files
from boarddd.io.kicad import read_kicad_copper
from boarddd.validate import validate_copper

from conftest import FIXTURES, RB_FAB, REPO

RB_PCB = FIXTURES / "royalblue54L_feather" / "kicad" / "RoyalBlue54L-Feather.kicad_pcb"
NFC = FIXTURES / "royalblue54L_nfc_antenna"
NODE = shutil.which("node")
DUMP = Path(__file__).with_name("copper_dump.mjs")


@pytest.fixture(scope="module")
def rb_gerber():
    return read_gerber_copper(RB_FAB)


def test_royalblue_gerber_equals_kicad(rb_gerber):
    rep = compare(read_kicad_copper(RB_PCB), rb_gerber)
    assert rep.problems == []
    assert (rep.tracks, rep.vias, rep.pads) == (943, 183, 991)  # pads: one per copper layer in Gerber
    assert rep.worst_zone_rel < 1e-5  # the same polygons: KiCad plots its saved fills
    assert rep.zone_keys == 114  # (layer, net) pairs with filled copper: 10 pours, the rest teardrops


def test_royalblue_gerber_document(rb_gerber):
    doc = rb_gerber
    assert validate_copper(json.loads(doc.to_json())) == []
    assert doc.layers == ["F.Cu", "In1.Cu", "In2.Cu", "In3.Cu", "In4.Cu", "In5.Cu", "In6.Cu", "B.Cu"]
    assert doc.source.kind == "gerber" and doc.source.generator.startswith("KiCad")
    assert doc.warnings == []
    assert {z.kind for z in doc.zones} == {"region", "shape"}  # JP1's bridge is an EtchedComponent region
    assert {v.drill for v in doc.vias} == {0.0}  # the demo's placeholder drill: 'T1C0.000'
    # inner-layer flashes carry no %TO.P: they take it from the outer pad at the same point
    assert all(p.ref for p in doc.pads)
    solid = {(p.layer, p.net) for p in doc.planes if p.solid}
    assert ("In1.Cu", "GND") in solid and ("In4.Cu", "VDD") in solid


def test_nfc_gerber_equals_golden():
    golden = cu.Copper.from_json((NFC / "copper.json").read_text("utf-8"))
    doc = read_gerber_copper(NFC / "fab")
    rep = compare(golden, doc)
    assert rep.problems == []
    # kicad-cli writes the three arcs shorter than 3 um as straight draws
    assert (rep.tracks, rep.arcs, rep.arcs_as_lines, rep.vias, rep.pads) == (160, 48, 3, 2, 2)
    assert {v.drill for v in doc.vias} == {0.711}  # from the Excellon file


@pytest.mark.skipif(NODE is None, reason="node is not installed: the JS side of the parity test needs it")
@pytest.mark.parametrize("fab", [RB_FAB, NFC / "fab"], ids=["royalblue54L_feather", "nfc_antenna"])
def test_js_reads_the_same(fab):
    py = json.loads(read_gerber_copper(fab).to_json())
    files = [str(p) for p in sorted(fab.iterdir())]
    out = subprocess.run([NODE, str(DUMP), py["board"], *files], capture_output=True, text=True, check=True, cwd=REPO)
    js = json.loads(out.stdout)
    assert validate_copper(js) == []
    for key in ("layers", "nets", "tracks", "vias", "zones", "pads", "planes", "warnings"):
        _same(py[key], js[key], key)


def _same(a, b, path):
    if isinstance(a, float) or isinstance(b, float):
        assert a is not None and b is not None and abs(a - b) <= 2e-6, path
    elif isinstance(a, dict):
        assert set(a) == set(b), path
        for k in a:
            _same(a[k], b[k], f"{path}/{k}")
    elif isinstance(a, list):
        assert len(a) == len(b), path
        for i, (x, y) in enumerate(zip(a, b, strict=True)):
            _same(x, y, f"{path}/{i}")
    else:
        assert a == b, path


# ---------------------------------------------------------------------------------------------------------------------
# small Gerbers


def gerber(body: str, function: str = "Copper,L1,Top", fs: str = "%FSLAX46Y46*%\n%MOMM*%") -> str:
    return f"%TF.GenerationSoftware,Test,Writer,1.0*%\n%TF.FileFunction,{function}*%\n{fs}\n%LPD*%\n{body}\nM02*\n"


def test_tracks_arcs_and_attributes():
    text = gerber(
        """%TA.AperFunction,Conductor*%
%ADD10C,0.250000*%
%TD*%
D10*
%TO.N,/USB/D\\u002B*%
X0Y0D02*
X10000000Y0D01*
G75*
G03*
X15000000Y5000000I0J5000000D01*
G01*
%TD.N*%
X15000000Y8000000D01*
%TO.N,N/C*%
X15000000Y9000000D01*
G75*
G02*
X15000000Y9000000I1000000J0D01*"""
    )
    lc = parse_copper_layer(text, "F.Cu")
    t = lc.tracks
    assert [x.net for x in t] == ["/USB/D+", "/USB/D+", "", "", "", ""]
    assert t[0].width == 0.25 and t[0].start == (0.0, 0.0) and t[0].end == (10.0, 0.0)
    # a quarter turn counter-clockwise about (10, 5): the mid is at 45 degrees
    assert t[1].mid == pytest.approx((10 + 5 * 2**-0.5, 5 - 5 * 2**-0.5))
    # the full circle (start == end) comes back as two half arcs
    assert [(x.start, x.end, x.mid) for x in t[4:]] == [
        ((15.0, 9.0), (17.0, 9.0), (16.0, 8.0)),
        ((17.0, 9.0), (15.0, 9.0), (16.0, 10.0)),
    ]
    assert lc.generator == "Test Writer 1.0"


def test_inch_trailing_zeros_and_single_quadrant():
    text = gerber(
        """%ADD10C,0.010*%
D10*
X0Y0D02*
X001Y0D01*
G74*
G02*
X002Y-001I0J001D01*""",
        fs="%FSTAX24Y24*%\n%MOIN*%",
    )
    t = parse_copper_layer(text, "F.Cu").tracks  # 2.4 digits, trailing zeros left out: X001 is 00.1000 inch
    assert t[0].end == (2.54, 0.0) and t[0].width == 0.254
    # G74: I/J unsigned; the centre is (2.54, -2.54) and the arc turns clockwise through 90 degrees
    assert t[1].end == (5.08, -2.54)
    assert t[1].mid == pytest.approx((2.54 + 2.54 * 2**-0.5, -2.54 + 2.54 * 2**-0.5), abs=1e-6)


def test_regions_pads_macros_and_skips():
    text = gerber(
        """%AMRot*
21,1,2,1,0,0,90*%
%AMHex*
5,1,6,0,0,1,0*%
%AMRoundRect*
0 corners*
4,1,4,-0.5,-0.5,0.5,-0.5,0.5,0.5,-0.5,0.5,-0.5,-0.5,0*
1,1,0.2,-0.5,-0.5*
1,1,0.2,0.5,-0.5*
1,1,0.2,0.5,0.5*
1,1,0.2,-0.5,0.5*%
%TA.AperFunction,SMDPad,CuDef*%
%ADD10Rot*%
%ADD11Hex*%
%ADD12RoundRect*%
%TA.AperFunction,NonConductor*%
%ADD13C,0.1*%
%TD*%
%TO.P,U1,1,VCC*%
%TO.N,VCC*%
D10*
X1000000Y1000000D03*
%TO.P,U1,2*%
D11*
X3000000Y1000000D03*
D12*
X5000000Y1000000D03*
%TD*%
D13*
X0Y0D02*
X1000000Y0D01*
%LPC*%
D10*
X9000000Y9000000D03*
%LPD*%
%TA.AperFunction,Conductor*%
%TO.N,GND*%
G36*
X0Y-10000000D02*
G01*
X10000000Y-10000000D01*
X10000000Y-5000000D01*
X0Y-5000000D01*
X0Y-10000000D01*
G37*
%TA.AperFunction,ComponentPad*%
%TO.P,J1,3*%
G36*
X20000000Y0D02*
X22000000Y0D01*
X22000000Y1000000D01*
X20000000Y0D01*
G37*"""
    )
    lc = parse_copper_layer(text, "F.Cu")
    rot, hexa, rr, tri = lc.pads
    # 21: a 2 x 1 rectangle turned 90 degrees about the origin
    assert (rot.ref, rot.number, rot.net, rot.function, rot.shape) == ("U1", "1", "VCC", "SMDPad,CuDef", "Rot")
    assert rot.size == pytest.approx((1, 2))
    assert hexa.number == "2" and len(hexa.polygons[0]) == 6 and hexa.size == pytest.approx((1, 3**0.5 / 2))
    # KiCad's RoundRect macro: one convex outline (the hull), not five loops
    assert len(rr.polygons) == 1 and rr.size == pytest.approx((1.2, 1.2))
    assert tri.ref == "J1" and tri.number == "3" and tri.shape == "polygon" and tri.function == "ComponentPad"
    (z,) = lc.zones
    assert (z.kind, z.net, z.area) == ("region", "GND", 50.0)
    assert lc.skipped == {"NonConductor draws": 1, "clear-polarity objects": 1}


def via(at: str, size: float) -> str:
    return f"%TA.AperFunction,ViaPad*%\n%ADD10C,{size}*%\n%TD*%\n%TO.N,GND*%\nD10*\n{at}D03*"


def test_package_vias_drills_and_spans():
    files = {
        "b-F_Cu.gbr": gerber(via("X1000000Y1000000", 0.6) + "\n" + via("X5000000Y1000000", 0.6)),
        "b-In1_Cu.gbr": gerber(via("X1000000Y1000000", 0.5), "Copper,L2,Inr"),
        "b-In2_Cu.gbr": gerber(via("X5000000Y1000000", 0.5), "Copper,L3,Inr"),
        "b-B_Cu.gbr": gerber(via("X1000000Y1000000", 0.6), "Copper,L4,Bot"),
        "b-PTH.drl": "M48\n; #@! TF.FileFunction,Plated,1,4,PTH\n; #@! TA.AperFunction,Plated,PTH,ViaDrill\n"
        "FMAT,2\nMETRIC\nT1C0.300\n%\nG90\nG05\nT1\nX1.0Y1.0\nM30\n",
    }
    doc = read_gerber_copper_files({k: v.encode() for k, v in files.items()}, name="b")
    assert doc.layers == ["F.Cu", "In1.Cu", "In2.Cu", "B.Cu"]
    a, b = doc.vias
    assert (a.span, a.type, a.drill, a.diameter, a.pad_layers) == (
        ("F.Cu", "B.Cu"),
        "through",
        0.3,
        0.6,
        [
            "F.Cu",
            "In1.Cu",
            "B.Cu",
        ],
    )
    assert [(p.layer, p.diameter) for p in a.padstack] == [("F.Cu", 0.6), ("In1.Cu", 0.5), ("B.Cu", 0.6)]
    assert (b.span, b.type, b.drill) == (("F.Cu", "In2.Cu"), "blind", 0.0)
    assert doc.nets == ["GND"]
    assert validate_copper(json.loads(doc.to_json())) == []


def test_no_x2_nets_warns():
    plain = "%FSLAX46Y46*%\n%MOMM*%\n%ADD10C,0.2*%\nD10*\nX0Y0D02*\nX1000000Y0D01*\nM02*\n"
    doc = read_gerber_copper_files({"b-F_Cu.gbr": plain.encode()}, name="b")
    assert doc.tracks[0].net == "" and doc.nets == [""]
    assert any("no %TO.N net attributes" in w for w in doc.warnings)
