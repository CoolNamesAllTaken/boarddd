"""boarddd.impedance.route (analyze_net) and its JS twin analyzeNet: impedance along a route on a real board.

* synthetic boards (fixtures/impedance/route, make_route_boards.py): a microstrip over a plane split (the return
  path on another plane, and none), a CPWG whose gap changes, an inner trace going from offset stripline to
  embedded microstrip, a pair breaking out; each against tier 1 or a direct field solve of the same geometry;
* royalblue54L_feather's USB pair (the fixture subset = the whole board);
* KiCad's CM5 MINIMA demo (fixtures/cm5_minima), the controlled-impedance board: its 100 Ω Ethernet pair and 90 Ω
  USB lines against kipr's per-class check (kipr main cf51ef7, checks.impedance with the field solver:
  100ohm F/B.Cu 0.13/0.25 mm coupled microstrip 101.168 Ω, 90ohm F/B.Cu 0.14 mm microstrip 52.517 Ω);
* Python = JS on all of them, overrides, tier 1, the cache, and the document's schema.
"""

from __future__ import annotations

import gzip
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from boarddd import impedance as z
from boarddd.impedance.result import ImpedanceAnalysis
from boarddd.impedance.route import analyze_net, net_route
from boarddd.io.kicad import read_kicad_copper, read_kicad_pcb
from boarddd.validate import validate_impedance

from conftest import FIXTURES, REPO

pytest.importorskip("scipy")

ROUTE = FIXTURES / "impedance" / "route"
NODE = shutil.which("node")
DUMP = Path(__file__).with_name("impedance_route_dump.mjs")
CM5 = FIXTURES / "cm5_minima" / "kicad" / "CM5_MINIMA_3.kicad_pcb.gz"
USB = ["/Debugger/D+", "/Debugger/D-"]
ETH = ["/CM5/ETH_PI.TRD0_P", "/CM5/ETH_PI.TRD0_N"]
KIPR_100_PAIR = 101.168  # kipr checks.impedance (field) on CM5: 100ohm, 0.13 mm / 0.25 mm coupled microstrip
KIPR_90_LINE = 52.517  # 90ohm, 0.14 mm microstrip


def case(name: str) -> dict:
    path = ROUTE / name
    raw = path.read_bytes()
    return json.loads(gzip.decompress(raw) if name.endswith(".gz") else raw)


@pytest.fixture(scope="module")
def cm5(tmp_path_factory) -> dict:
    text = gzip.decompress(CM5.read_bytes()).decode()
    board = read_kicad_pcb(text, CM5.parent / "CM5_MINIMA_3.kicad_pro", name="CM5_MINIMA_3")
    data = {"board": board.to_dict(), "copper": read_kicad_copper(text, name="CM5_MINIMA_3").to_dict()}
    path = tmp_path_factory.mktemp("cm5") / "cm5.json"
    path.write_text(json.dumps(data))
    data["path"] = path
    return data


def run(data: dict, net, **opts) -> dict:
    doc = analyze_net(data["board"], data["copper"], net, **opts)
    d = doc.to_dict()
    assert validate_impedance(d) == []
    return d


def rel(a: float, b: float) -> float:
    return abs(a - b) / abs(b)


def tier1(stackup, layer, **kw) -> dict:
    line = z.line_from_stackup(stackup, layer, **kw)
    return z.calculate(line.model, line.params).to_dict()


# ---------------------------------------------------------------------------------------------------------------------
# synthetic boards


def test_microstrip_across_a_plane_split():
    data = case("split.json")
    d = run(data, "SIG")
    st = data["board"]["stackup"]
    a, gap, b = d["sections"]
    assert [(s["s0"], s["s1"]) for s in d["sections"]] == [(0, 14), (14, 16), (16, 30)]
    assert a["structure"] == b["structure"] == gap["structure"] == "microstrip"
    assert a["refs"] == [
        {"side": "bottom", "layer": "In1.Cu", "net": "GND", "h": 0.2, "extent": [None, None], "skipped": []}
    ]
    # over the split the return current takes the +3V3 plane on In2.Cu, 1.235 mm down
    assert gap["refs"][0] | {"extent": None} == {
        "side": "bottom",
        "layer": "In2.Cu",
        "net": "+3V3",
        "h": 1.235,
        "extent": None,
        "skipped": ["In1.Cu"],
    }
    assert gap["flags"] == ["plane_gap"] and a["flags"] == []
    ms = tier1(st, "F.Cu", width=0.35)["Z0"]
    far = tier1(st, "F.Cu", width=0.35, ref_bottom="In2.Cu")["Z0"]
    assert rel(a["z"]["Z0"], ms) < 0.015  # tier 1 is ±2 % of the field solver here
    assert rel(gap["z"]["Z0"], far) < 0.03
    assert [x["type"] for x in d["discontinuities"]] == ["ref_change", "plane_gap", "ref_change"]
    assert d["discontinuities"][0]["at"] == [14, 2] and d["discontinuities"][2]["s"] == 16
    assert d["target"] == {
        "value": 50,
        "key": "Z0",
        "tolerance_pct": 10,
        "source": "net_class",
        "net_class": "SE_50_MS",
    }
    s = d["summary"]
    assert (s["length"], s["length_with_z"], s["out_of_tolerance_length"], s["within"]) == (30, 30, 2, False)
    assert s["z_min"] == a["z"]["Z0"] and s["z_max"] == gap["z"]["Z0"]


def test_split_with_nothing_under_it():
    d = run(case("split.json"), "BARE")
    gap = d["sections"][1]
    assert (gap["structure"], gap["z"], gap["flags"]) == ("none", None, ["no_ref", "plane_gap"])
    assert d["summary"]["length_with_z"] == 28 and d["target"] is None
    assert [x["type"] for x in d["discontinuities"]] == ["ref_change", "plane_gap", "no_ref", "ref_change"]


def test_cpwg_with_a_changing_gap():
    data = case("cpwg.json")
    d = run(data, "RF")
    st = data["board"]["stackup"]
    narrow, wide = d["sections"]
    assert narrow["structure"] == wide["structure"] == "cpwg"
    # gaps 0.15 and 0.4 mm, on a 10 % geometric grid
    assert all(rel(g, 0.15) < 0.05 for g in narrow["geometry"]["coplanar_gap"])
    assert all(rel(g, 0.4) < 0.05 for g in wide["geometry"]["coplanar_gap"])
    for sec in (narrow, wide):
        gap = sec["geometry"]["coplanar_gap"][0]
        ref = tier1(st, "F.Cu", width=0.3, structure="coplanar_grounded", coplanar_gap=gap)["Z0"]
        assert rel(sec["z"]["Z0"], ref) < 0.03
    assert narrow["z"]["Z0"] < wide["z"]["Z0"]


def test_offset_stripline_then_embedded_microstrip():
    data = case("inner.json")
    d = run(data, "DATA")
    st = data["board"]["stackup"]
    strip, embedded = d["sections"]
    assert (strip["structure"], embedded["structure"]) == ("offset_stripline", "embedded_microstrip")
    assert (strip["geometry"]["h_top"], strip["geometry"]["h_bottom"]) == (0.2, 1.0)
    ref = tier1(st, "In1.Cu", width=0.15)["Z0"]  # stripline between F.Cu and In2.Cu
    assert rel(strip["z"]["Z0"], ref) < 0.02
    # the embedded microstrip, solved directly (no plane above: the dielectric to the surface, then the mask)
    line = z.line_from_stackup(st, "In1.Cu", width=0.15, ref_top=False, ref_bottom="In2.Cu", solver="field")
    direct = z.solve_cross_section(line.section, tol=0.002).Z0
    assert rel(embedded["z"]["Z0"], direct) < 0.01
    assert [x["type"] for x in d["discontinuities"]] == ["ref_change"]


def test_pair_breaking_out():
    data = case("pair.json")
    d = run(data, ["USB_P", "USB_N"])
    st = data["board"]["stackup"]
    kinds = [(s["net"], s["kind"]) for s in d["sections"]]
    assert kinds == [
        ("USB_P", "single"),
        ("USB_P", "differential"),
        ("USB_P", "single"),
        ("USB_N", "single"),
        ("USB_N", "single"),
    ]
    coupled = d["sections"][1]
    assert coupled["length"] == 16 and coupled["structure"] == "microstrip"
    assert rel(coupled["geometry"]["gap"], 0.15) < 0.02 and coupled["geometry"]["partner_width"] == 0.25
    ref = tier1(st, "F.Cu", width=0.25, kind="differential", gap=0.15)["Zdiff"]
    assert rel(coupled["z"]["Zdiff"], ref) < 0.01
    for s in (d["sections"][0], d["sections"][2]):
        assert "uncoupled" in s["flags"] and s["z"]["Zdiff"] == round(2 * s["z"]["Z0"], 6)
    assert d["target"]["key"] == "Zdiff" and d["target"]["value"] == 90
    assert d["summary"]["length"] == pytest.approx(16 + 2 * 2.39, abs=0.01)  # the first net's route
    assert [x["type"] for x in d["discontinuities"]] == ["uncoupled"] * 4


def test_overrides_and_tier1():
    data = case("cpwg.json")
    base = run(data, "RF")
    forced = run(data, "RF", overrides={"net": {"structure": "microstrip"}})
    assert {s["structure"] for s in forced["sections"]} == {"microstrip"}
    assert all("override" in s["flags"] and s["geometry"]["coplanar_gap"] == [None, None] for s in forced["sections"])
    assert len(forced["sections"]) == 1  # without the coplanar grounds both halves are one cross-section
    assert forced["sections"][0]["z"]["Z0"] > max(s["z"]["Z0"] for s in base["sections"])
    one = run(data, "RF", overrides={"tracks": {"cpwg-b": {"ref_bottom": False, "structure": "cpw"}}})
    assert [s["structure"] for s in one["sections"]] == ["cpwg", "cpw"] and one["sections"][1]["z"] is None
    t1 = run(case("split.json"), "SIG", solver="closedform")
    a = t1["sections"][0]
    assert (a["z"]["solver"], a["z"]["model"]) == ("closedform", "coated_microstrip")
    field = run(case("split.json"), "SIG")["sections"][0]["z"]["Z0"]
    assert rel(a["z"]["Z0"], field) < 0.015
    with pytest.raises(ValueError, match="no tracks"):
        analyze_net(data["board"], data["copper"], "GND")


def test_cache_is_shared_between_calls():
    data = case("pair.json")
    cache: dict = {}
    first = analyze_net(data["board"], data["copper"], ["USB_P", "USB_N"], cache=cache).to_dict()
    again = analyze_net(data["board"], data["copper"], ["USB_P", "USB_N"], cache=cache).to_dict()
    assert first["timing"]["solves"] > 0 and again["timing"]["solves"] == 0
    assert again["sections"] == first["sections"]


def test_net_route_order():
    data = case("pair.json")
    r = net_route(data["copper"], "USB_P")
    assert [(e["track"]["id"], e["forward"], e["run"]) for e in r] == [
        ("p-in", True, 0),
        ("p-run", True, 0),
        ("p-out", True, 0),
    ]
    assert r[-1]["s1"] == pytest.approx(16 + 2 * 2.39, abs=0.01)


# ---------------------------------------------------------------------------------------------------------------------
# real boards


def test_royalblue_usb_pair():
    data = case("royalblue-usb.json.gz")
    d = run(data, USB)
    # the subset is the whole board as far as the pair is concerned
    pcb = FIXTURES / "royalblue54L_feather" / "kicad" / "RoyalBlue54L-Feather.kicad_pcb"
    full = analyze_net(read_kicad_pcb(pcb), read_kicad_copper(pcb), USB).to_dict()
    assert full["sections"] == d["sections"] and full["discontinuities"] == d["discontinuities"]
    assert d["target"] is None and d["summary"]["length"] == pytest.approx(22.55, abs=0.01)
    vias = [x for x in d["discontinuities"] if x["type"] == "via"]
    assert vias and vias[0]["detail"] == "B.Cu → F.Cu"
    # the long coupled run: 0.1 mm lines 0.228 mm apart over In1.Cu GND (0.1 mm), with GND pour beside
    main = max((s for s in d["sections"] if s["kind"] == "differential"), key=lambda s: s["length"])
    assert main["geometry"]["width"] == 0.1 and rel(main["geometry"]["gap"], 0.228) < 0.02
    assert main["refs"][0]["layer"] == "In1.Cu" and 105 < main["z"]["Zdiff"] < 115
    assert {s["structure"] for s in d["sections"]} >= {"microstrip", "cpwg"}


def test_cm5_ethernet_pair_against_kipr(cm5):
    d = run(cm5, ETH)
    assert d["target"] == {
        "value": 100,
        "key": "Zdiff",
        "tolerance_pct": 10,
        "source": "net_class",
        "net_class": "100ohm",
    }
    ms = [
        s
        for s in d["sections"]
        if s["kind"] == "differential"
        and s["structure"] == "microstrip"
        and rel(s["geometry"]["gap"], 0.25) < 0.02
        and "ref_edge" not in s["flags"]
    ]
    assert ms and all(s["geometry"]["width"] == 0.13 for s in ms)
    for s in ms:  # kipr's per-class number is the same cross-section with infinite planes
        assert rel(s["z"]["Zdiff"], KIPR_100_PAIR) < 0.01
    sm = d["summary"]
    assert rel(sm["z_weighted"], 100) < 0.03
    # the controlled run is within ±10 %; vias (no reference) and breakouts make up the rest
    assert sm["out_of_tolerance_pct"] < 15
    assert {x["type"] for x in d["discontinuities"]} >= {"via", "uncoupled", "no_ref"}


def test_cm5_usb_line_against_kipr(cm5):
    d = run(cm5, "/USB_C.D_P")  # the 90ohm class's lines, single-ended as kipr evaluates the class
    # the same cross-section as kipr's (infinite planes): not where the plane ends nearby ('ref_edge')
    ms = [
        s
        for s in d["sections"]
        if s["structure"] == "microstrip" and s["geometry"]["width"] == 0.14 and "ref_edge" not in s["flags"]
    ]
    assert len(ms) >= 4
    for s in ms:
        assert rel(s["z"]["Z0"], KIPR_90_LINE) < 0.01


# ---------------------------------------------------------------------------------------------------------------------
# Python = JS


def _same(a, b, path=""):
    if isinstance(a, float) or isinstance(b, float):
        assert a is not None and b is not None and abs(a - b) <= 1e-6 * max(1.0, abs(b)), path
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


PARITY = [
    ("split.json", "SIG", {}, {}),
    ("split.json", "BARE", {}, {}),
    ("cpwg.json", "RF", {}, {}),
    (
        "cpwg.json",
        "RF",
        {"overrides": {"net": {"structure": "microstrip"}}},
        {"overrides": {"net": {"structure": "microstrip"}}},
    ),
    ("inner.json", "DATA", {}, {}),
    ("pair.json", ["USB_P", "USB_N"], {}, {}),
    ("split.json", "SIG", {"solver": "closedform"}, {"solver": "closedform"}),
    ("royalblue-usb.json.gz", USB, {}, {}),
]


@pytest.mark.skipif(NODE is None, reason="node is not installed: the JS side of the parity test needs it")
@pytest.mark.parametrize("name,net,py_opts,js_opts", PARITY, ids=lambda x: str(x)[:20] if isinstance(x, str) else None)
def test_js_gives_the_same(name, net, py_opts, js_opts):
    _parity(ROUTE / name, case(name), net, py_opts, js_opts)


@pytest.mark.skipif(NODE is None, reason="node is not installed: the JS side of the parity test needs it")
@pytest.mark.parametrize("net", [ETH, "/USB_C.D_P"], ids=["eth-pair", "usb-line"])
def test_js_gives_the_same_on_cm5(cm5, net):
    _parity(cm5["path"], cm5, net, {}, {})


def _parity(path, data, net, py_opts, js_opts):
    py = analyze_net(data["board"], data["copper"], net, **py_opts).to_dict()
    out = subprocess.run(
        [NODE, str(DUMP), str(path), json.dumps(net), json.dumps(js_opts)],
        capture_output=True,
        text=True,
        check=True,
        cwd=REPO,
    )
    js = json.loads(out.stdout)
    assert validate_impedance(js) == []
    py.pop("timing")
    js.pop("timing")
    _same(py, js)


def test_document_round_trip():
    d = run(case("pair.json"), ["USB_P", "USB_N"])
    assert ImpedanceAnalysis.from_dict(d).to_dict() == d
    bad = json.loads(json.dumps(d))
    bad["sections"][0]["net"] = "USB_X"
    assert validate_impedance(bad) == ["/sections/0/net: 'USB_X' is not in /nets"]
