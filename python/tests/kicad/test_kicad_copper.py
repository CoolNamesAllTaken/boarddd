"""read_kicad_copper: the KiCad half of boarddd/copper@1.

* royalblue54L_feather: what the board file holds (943 segments, 183 vias, 4 pours on 8 layers, 263 teardrops,
  2 footprint keepouts, the JP1 net tie), pads against pcbnew's own copper centres, boxes and nets
  (fixtures/royalblue54L_pcbnew/golden.json), and the plane summary;
* the NFC antenna demo: track arcs, and the golden copper.json is reproducible;
* small boards written here for what the demos lack: KiCad 5 syntax, unfilled and stale fills, via padstacks and
  'remove unused layers', copper drawings, wildcard zone layers.
"""

import json
import math
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

from boarddd import copper as cu
from boarddd.io.kicad import read_kicad_copper
from boarddd.validate import validate_copper

from conftest import FIXTURES

RB = FIXTURES / "royalblue54L_feather" / "kicad" / "RoyalBlue54L-Feather.kicad_pcb"
NFC = FIXTURES / "royalblue54L_nfc_antenna"
PCBNEW = json.loads((Path(__file__).parent / "fixtures" / "royalblue54L_pcbnew" / "golden.json").read_text())
INNER = ["In1.Cu", "In2.Cu", "In3.Cu", "In4.Cu", "In5.Cu", "In6.Cu"]


@pytest.fixture(scope="module")
def rb():
    return read_kicad_copper(RB)


def test_royalblue_valid_and_round_trips(rb):
    data = json.loads(rb.to_json())
    assert validate_copper(data) == []
    assert cu.Copper.from_dict(data).to_json() == rb.to_json()
    assert rb.warnings == []
    assert rb.layers == ["F.Cu", *INNER, "B.Cu"]
    assert rb.source.kind == "kicad_pcb" and rb.source.files[0].path == "RoyalBlue54L-Feather.kicad_pcb"


def test_royalblue_tracks(rb):
    assert len(rb.tracks) == 943
    assert all(t.mid is None for t in rb.tracks)  # the demo routes no arcs (see the NFC antenna)
    assert Counter(t.layer for t in rb.tracks) == {"F.Cu": 463, "B.Cu": 209, "In4.Cu": 127, "In5.Cu": 78, "In2.Cu": 66}
    first = rb.tracks[0]  # (segment (start 136.88 108.13) (end 136.68 107.93) (width 0.15) (layer "F.Cu") (net 1))
    assert (first.start, first.end, first.width, first.net) == ((136.88, -108.13), (136.68, -107.93), 0.15, "GND")
    assert first.id == "05e0026b-adeb-4ee1-b0a4-032463a94c48"


def test_royalblue_vias(rb):
    assert len(rb.vias) == 183
    # written '(via blind ... (layers "F.Cu" "B.Cu"))': a span from outer to outer is a through via
    assert {(v.span, v.type, v.pad_layers, v.padstack) for v in rb.vias} == {(("F.Cu", "B.Cu"), "through", None, None)}
    assert Counter(v.diameter for v in rb.vias) == {0.45: 154, 0.35: 23, 0.4: 6}
    assert {v.drill for v in rb.vias} == {0.00001}  # the demo's placeholder drill, as written
    assert rb.vias[0].at == (158.66, -105.105) and rb.vias[0].net == "GND"


def test_royalblue_zones(rb):
    kinds = Counter((z.kind, z.layer) for z in rb.zones)
    assert sum(n for (k, _), n in kinds.items() if k == "teardrop") == 263
    pours = [(z.layer, z.net, z.priority) for z in rb.zones if z.kind == "pour"]
    assert sorted(pours) == sorted(
        [(lid, "GND", 1) for lid in ("F.Cu", "In1.Cu", "In3.Cu", "In5.Cu", "In6.Cu", "B.Cu")]
        + [("B.Cu", "VSYS", 2), ("In2.Cu", "+BATT", 2), ("In2.Cu", "VDD", 1), ("In4.Cu", "VDD", 1)]
    )
    # the GND zone is one zone on six layers: one entry per layer, sharing its uuid
    gnd = [z for z in rb.zones if z.kind == "pour" and z.net == "GND"]
    assert len({z.id for z in gnd}) == 1 and all(len(z.outline) >= 4 for z in gnd)
    assert all(z.filled and z.stale is False for z in rb.zones if z.kind in ("pour",))
    shapes = [z for z in rb.zones if z.kind == "shape"]
    assert [(z.layer, z.net) for z in shapes] == [("B.Cu", "")]  # JP1's copper bridge (an fp_poly)
    # the inner GND planes are fractured polygons: holes come back as holes
    in1 = next(z for z in gnd if z.layer == "In1.Cu")
    assert sum(len(p.holes) for p in in1.fill) > 100
    assert in1.area == pytest.approx(986.7479, abs=1e-3)
    for z in rb.zones:
        for p in z.fill:
            assert cu.signed_area(p.outline) > 0 and all(cu.signed_area(h) < 0 for h in p.holes)


def test_royalblue_keepouts_and_net_tie(rb):
    # the module's antenna keepout (all layers) and the Tag-Connect footprint's (B.Cu)
    by_ref = {k.ref: k for k in rb.keepouts}
    assert sorted(by_ref) == ["J8", "U1"]
    assert by_ref["U1"].layers == rb.layers and by_ref["J8"].layers == ["B.Cu"]
    assert by_ref["U1"].rules == cu.KeepoutRules(tracks=True, vias=True, pours=True)
    assert by_ref["J8"].rules == cu.KeepoutRules(vias=True, pours=True, footprints=True)
    assert all(cu.signed_area(k.outline) > 0 for k in rb.keepouts)
    assert rb.net_ties == [cu.NetTie(ref="JP1", groups=[["1", "2"]], nets=["Net-(JP1-A)", "VDD"])]


def test_royalblue_planes(rb):
    solid = {(p.layer, p.net) for p in rb.planes if p.solid}
    assert solid == {
        ("In1.Cu", "GND"),
        ("In3.Cu", "GND"),
        ("In5.Cu", "GND"),
        ("In6.Cu", "GND"),
        ("B.Cu", "GND"),
        ("In2.Cu", "VDD"),
        ("In4.Cu", "VDD"),
    }
    f = next(p for p in rb.planes if (p.layer, p.net) == ("F.Cu", "GND"))
    assert 0.4 < f.coverage < 0.5  # the top pour around the parts: not a solid plane


def test_royalblue_pads_match_pcbnew(rb):
    """Each copper pad sits on pcbnew's copper centre with pcbnew's net and angle, and its outline fills pcbnew's
    copper box; the pads without copper (a bare 2.5 mm NPTH hole) are left out."""
    gold: dict = {}
    for g in PCBNEW["pads"]:
        gold.setdefault((g["ref"], g["number"]), []).append(g)
    seen = 0

    def box(p):
        xs, ys = [q[0] for q in p.polygons[0]], [-q[1] for q in p.polygons[0]]
        return [min(xs), min(ys), max(xs), max(ys)]

    for p in rb.pads:  # several pads may share a number and a centre (U2.33: vias in a paddle, F and B pads)
        cands = gold[(p.ref, p.number)]
        g = min(
            cands,
            key=lambda g: math.dist(g["copper_center"], (p.at[0], -p.at[1]))
            + sum(abs(a - b) for a, b in zip(box(p), g["copper_bbox"], strict=True)),
        )
        where = f"{p.ref}.{p.number}"
        assert math.dist(g["copper_center"], (p.at[0], -p.at[1])) < 1e-5, where
        assert p.net == g["net"], where
        assert abs((p.rotation - g["angle"] + 180) % 360 - 180) < 1e-6, where
        if p.shape != "custom":
            assert box(p) == pytest.approx(g["copper_bbox"], abs=0.01), where
        seen += 1
    assert seen == len(rb.pads) == 389
    # pcbnew's other pads have no copper: paste/mask-only pads and bare NPTH holes (board.json's footprints say so)
    gb = json.loads((FIXTURES / "royalblue54L_feather" / "board.json").read_text())
    bare = 0
    for c in gb["components"]:
        for pad in gb["footprints"][c["footprint"]]["pads"]:
            cu_layers = [lid for lid in pad["layers"] if lid.endswith(".Cu")]
            hole = pad["drill"] and min(pad["size"]) <= min(pad["drill"]["size"]) and pad["type"] == "np_thru_hole"
            bare += not cu_layers or bool(hole)
    assert len(PCBNEW["pads"]) - len(rb.pads) == bare == 39
    thru = [p for p in rb.pads if p.type == "thru_hole"]
    assert thru and all(p.layers == rb.layers and p.drill for p in thru)


def test_nfc_antenna_arcs():
    doc = read_kicad_copper(NFC / "kicad" / "RoyalBlue54L-NFC-Antenna.kicad_pcb")
    arcs = [t for t in doc.tracks if t.mid is not None]
    assert len(doc.tracks) == 160 and len(arcs) == 48
    a = arcs[0]
    # (arc (start 141.60001 101.699755) (mid 141.892827 100.992832) (end 142.59975 100.700015) (width 0.3))
    raw = [t for t in arcs if t.start == (141.60001, -101.699755)]
    assert raw and raw[0].mid == (141.892827, -100.992832) and raw[0].end == (142.59975, -100.700015)
    for t in arcs:  # mid is on the circle through the ends, between them
        assert math.dist(t.start, t.mid) > 0 and math.dist(t.end, t.mid) > 0
    assert a.net == "/ANT" and doc.nets == ["/ANT"]
    assert [z.kind for z in doc.zones] == ["shape"] * 4  # gr_poly on copper with a net


def test_nfc_golden_is_reproducible():
    script = NFC / "make_copper.py"
    r = subprocess.run([sys.executable, str(script), "--check"], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


# ---------------------------------------------------------------------------------------------------------------------
# small boards for what the demos lack


def board(body: str, layers: str = "") -> str:
    layers = (
        layers or '(0 "F.Cu" signal) (1 "In1.Cu" signal) (2 "In2.Cu" power) (31 "B.Cu" signal) (44 "Edge.Cuts" user)'
    )
    return f"""(kicad_pcb (version 20240108) (generator "pcbnew") (generator_version "8.0")
  (layers {layers})
  (net 0 "") (net 1 "GND") (net 2 "SIG")
  (gr_rect (start 0 0) (end 20 10) (stroke (width 0.1)) (fill none) (layer "Edge.Cuts"))
  {body})"""


PLANE = """(zone (net 1) (net_name "GND") (layers "In1.Cu") (uuid "z1") (name "plane") (priority 3)
    (fill yes (thermal_gap 0.5) (thermal_bridge_width 0.5))
    (polygon (pts (xy 0 0) (xy 20 0) (xy 20 10) (xy 0 10)))
    {fill})"""
FILL = '(filled_polygon (layer "In1.Cu") (pts (xy 0.5 0.5) (xy 19.5 0.5) (xy 19.5 9.5) (xy 0.5 9.5)))'


def test_unfilled_zone_warns():
    doc = read_kicad_copper(board(PLANE.format(fill="")))
    (z,) = doc.zones
    assert (z.filled, z.fill, z.area, z.name, z.priority) == (False, [], 0.0, "plane", 3)
    assert any("have no fill" in w for w in doc.warnings)
    assert z.outline == [(0.0, 0.0), (20.0, 0.0), (20.0, -10.0), (0.0, -10.0)][::-1] or cu.signed_area(z.outline) > 0


def test_stale_fill_is_flagged():
    track = '(segment (start 2 5) (end 8 5) (width 0.2) (layer "In1.Cu") (net 2) (uuid "t1"))'
    doc = read_kicad_copper(board(PLANE.format(fill=FILL) + track))
    (z,) = doc.zones
    assert z.stale is True and z.area == pytest.approx(19 * 9)
    assert any("fill is stale: track of net 'SIG'" in w for w in doc.warnings)
    assert read_kicad_copper(board(PLANE.format(fill=FILL) + track), check_fills=False).zones[0].stale is None
    # the same track elsewhere: a fresh fill
    fresh = read_kicad_copper(board(PLANE.format(fill=FILL) + track.replace("In1.Cu", "F.Cu")))
    assert fresh.zones[0].stale is False and fresh.warnings == []
    plane = fresh.planes[0]
    assert (plane.layer, plane.net, plane.area, plane.coverage, plane.solid) == ("In1.Cu", "GND", 171.0, 0.855, True)


def test_kicad5_syntax():
    """KiCad 5: numbered nets, (layer) zones, filled_polygon without a layer, fp_text references, module."""
    body = """(segment (start 1 1) (end 5 1) (width 0.25) (layer F.Cu) (net 2) (tstamp 5A1))
  (via (at 5 1) (size 0.6) (drill 0.3) (layers F.Cu B.Cu) (net 2) (tstamp 5A2))
  (zone (net 1) (net_name GND) (layer B.Cu) (tstamp 5A3) (hatch edge 0.508)
    (connect_pads (clearance 0.5)) (min_thickness 0.254) (fill yes (arc_segments 32))
    (polygon (pts (xy 0 0) (xy 10 0) (xy 10 10) (xy 0 10)))
    (filled_polygon (pts (xy 1 1) (xy 9 1) (xy 9 9) (xy 1 9))))
  (module R_0603 (layer F.Cu) (tedit 0) (tstamp 5A4) (at 10 5 90)
    (fp_text reference R1 (at 0 -1.5) (layer F.SilkS))
    (pad 1 smd rect (at -0.8 0 90) (size 0.9 0.95) (layers F.Cu F.Paste F.Mask) (net 2 SIG))
    (pad 2 smd rect (at 0.8 0 90) (size 0.9 0.95) (layers F.Cu F.Paste F.Mask) (net 1 GND)))"""
    layers = "(0 F.Cu signal) (31 B.Cu signal) (44 Edge.Cuts user)"
    doc = read_kicad_copper(board(body, layers))
    assert doc.layers == ["F.Cu", "B.Cu"]
    assert (doc.tracks[0].net, doc.tracks[0].id) == ("SIG", "5A1")
    assert doc.vias[0].span == ("F.Cu", "B.Cu") and doc.vias[0].net == "SIG"
    (z,) = doc.zones
    assert (z.layer, z.net, z.area) == ("B.Cu", "GND", 64.0)
    pads = {p.number: p for p in doc.pads}
    assert pads["1"].ref == "R1" and pads["1"].net == "SIG"
    # (at 10 5 90) and pad (at -0.8 0): KiCad rotates footprints counter-clockwise on screen
    assert pads["1"].at == pytest.approx((10, -5.8)) and pads["1"].rotation == 90
    assert validate_copper(json.loads(doc.to_json())) == []


def test_via_padstack_and_unused_layers():
    body = """(segment (start 1 1) (end 5 1) (width 0.2) (layer "In1.Cu") (net 2) (uuid "t"))
  (via (at 5 1) (size 0.6) (drill 0.3) (layers "F.Cu" "B.Cu") (remove_unused_layers yes) (keep_end_layers yes)
    (net 2) (uuid "v1"))
  (via micro (at 8 2) (size 0.3) (drill 0.1) (layers "F.Cu" "In1.Cu") (net 2) (uuid "v2"))
  (via (at 12 2) (size 0.6) (drill 0.3) (layers "In1.Cu" "In2.Cu") (net 1) (uuid "v3"))
  (via (at 15 2) (size 0.7) (drill 0.3) (layers "F.Cu" "B.Cu") (net 1) (uuid "v4")
    (padstack (mode front_inner_back) (layer "Inner" (size 0.5)) (layer "B.Cu" (size 0.8))))"""
    doc = read_kicad_copper(board(body))
    v1, v2, v3, v4 = doc.vias
    assert (v1.type, v1.pad_layers) == ("through", ["F.Cu", "In1.Cu", "B.Cu"])  # In1.Cu: the track ends on it
    assert (v2.type, v2.span) == ("micro", ("F.Cu", "In1.Cu"))
    assert (v3.type, v3.span) == ("buried", ("In1.Cu", "In2.Cu"))
    assert v4.diameter == 0.8
    assert [(p.layer, p.diameter) for p in v4.padstack] == [
        ("F.Cu", 0.7),
        ("In1.Cu", 0.5),
        ("In2.Cu", 0.5),
        ("B.Cu", 0.8),
    ]
    assert validate_copper(json.loads(doc.to_json())) == []


def test_copper_drawings_and_wildcard_layers():
    body = """(gr_rect (start 1 1) (end 3 2) (stroke (width 0.2) (type solid)) (fill yes) (layer "F.Cu") (net 2) (uuid "r"))
  (gr_circle (center 10 5) (end 11 5) (stroke (width 0.1) (type solid)) (fill none) (layer "B.Cu") (uuid "c"))
  (gr_text "COPPER" (at 5 5) (layer "F.Cu") (uuid "x") (effects (font (size 1 1))))
  (zone (net 1) (net_name "GND") (layers "*.Cu") (uuid "kz") (hatch edge 0.5)
    (keepout (tracks not_allowed) (vias allowed) (pads allowed) (copperpour not_allowed) (footprints allowed))
    (polygon (pts (xy 15 1) (xy 18 1) (xy 18 4))))"""
    doc = read_kicad_copper(board(body))
    (shape,) = doc.zones
    assert (shape.kind, shape.net, shape.layer, shape.area) == ("shape", "SIG", "F.Cu", 2.0)
    rect_edges = [t for t in doc.tracks if t.id == "r"]
    assert len(rect_edges) == 4 and {t.width for t in rect_edges} == {0.2}
    circle = [t for t in doc.tracks if t.id == "c"]
    assert [(t.start, t.end, t.mid) for t in circle] == [
        ((11.0, -5.0), (9.0, -5.0), (10.0, -4.0)),
        ((9.0, -5.0), (11.0, -5.0), (10.0, -6.0)),
    ]
    assert any(re.match(r"1 copper drawing\(s\) \(text, curves\)", w) for w in doc.warnings)
    (k,) = doc.keepouts
    assert k.layers == doc.layers and (k.rules.tracks, k.rules.vias, k.rules.pours) == (True, False, True)
