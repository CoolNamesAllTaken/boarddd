"""The KiCad readers on small hand-written files: the stackup grammar (sublayers, locked thickness, frequency,
impedance control), KiCad 5 and KiCad 10 forms, outlines, footprints on the back, symbols."""

import math

import pytest

from boarddd import model as m
from boarddd.io.kicad import KicadFootprint, read_kicad_mod, read_kicad_pcb, read_kicad_sym
from boarddd.io.kicad.geom import signed_area
from boarddd.io.kicad.pcb import load
from boarddd.io.kicad.sexpr import Atom, dumps, parse
from boarddd.validate import validate_board

STACKUP = """
(kicad_pcb (version 20260206) (generator "pcbnew") (generator_version "10.0")
  (general (thickness 1.56252))
  (layers (0 "F.Cu" signal) (4 "In1.Cu" signal) (6 "In2.Cu" signal) (2 "B.Cu" signal) (25 "Edge.Cuts" user))
  (setup
    (stackup
      (layer "F.SilkS" (type "Top Silk Screen") (color "White"))
      (layer "F.Mask" (type "Top Solder Mask") (color "Blue") (thickness 0.01) (material "Epoxy") (epsilon_r 3.8))
      (layer "F.Cu" (type "copper") (thickness 0.035))
      (layer "dielectric 1" (type "prepreg") (color "FR4 natural") (thickness 0.06813 locked) (material "R-1551(W)")
        (epsilon_r 4.3) (loss_tangent 0.02) (spec_frequency 1000000000) (dielectric_model djordjevic_sarkar)
        addsublayer (color "FR4 natural") (thickness 0.06813) (material "R-1551(W)") (epsilon_r 4.3) (loss_tangent 0.02))
      (layer "In1.Cu" (type "copper") (thickness 0.0152))
      (layer "dielectric 2" (type "core") (thickness 1.13) (material "R-1566(W)") (epsilon_r 4.6) (loss_tangent 0.02))
      (layer "In2.Cu" (type "copper") (thickness 0.0152))
      (layer "dielectric 3" (type "prepreg") (thickness 0.07) (material "PR1080") (epsilon_r 3.96) (loss_tangent 0.02)
        addsublayer (thickness 0.1) (material "PR2116") (epsilon_r 4.4) (loss_tangent 0))
      (layer "B.Cu" (type "copper") (thickness 0.035))
      (layer "B.Mask" (type "Bottom Solder Mask") (thickness 0.01))
      (copper_finish "ENIG") (dielectric_constraints yes))
    (aux_axis_origin 100 120))
  (net "GND") (net "/USB/D+")
  (footprint "Lib:R" (layer "B.Cu") (at 110 105 90)
    (property "Reference" "R1") (property "Value" "10k")
    (attr smd dnp)
    (pad "1" smd roundrect (at -0.5 0 90) (size 0.6 0.5) (layers "B.Cu" "B.Paste" "B.Mask") (roundrect_rratio 0.25)
      (net "GND"))
    (pad "2" smd rect (at 0.5 0.2 90) (size 0.6 0.5) (layers "B.Cu" "B.Paste" "B.Mask") (chamfer_ratio 0.2)
      (chamfer top_left) (net "/USB/D+"))
    (fp_line (start -1 -0.5) (end 1 -0.5) (stroke (width 0.1) (type solid)) (layer "B.SilkS"))
    (fp_rect (start -2 -2) (end -1 -1) (stroke (width 0.05) (type solid)) (fill no) (layer "Edge.Cuts")))
  (gr_rect (start 100 100) (end 130 120) (stroke (width 0.1) (type solid)) (fill no) (layer "Edge.Cuts"))
  (gr_circle (center 120 110) (end 121 110) (stroke (width 0.1) (type solid)) (fill no) (layer "Edge.Cuts"))
  (segment (start 1 1) (end 2 2) (width 0.2) (layer "F.Cu") (net "GND") (uuid "a"))
  (via (at 105 105) (size 0.6) (drill 0.3) (layers "F.Cu" "B.Cu") (net "GND") (uuid "b"))
)
"""


@pytest.fixture(scope="module")
def board():
    return read_kicad_pcb(STACKUP)


def test_stackup_grammar(board):
    st = board.stackup
    assert (st.thickness, st.copper_layers, st.finish, st.impedance_controlled) == (1.56252, 4, "ENIG", True)
    by = {la.name: la for la in st.layers}
    mask = by["F.Mask"]
    assert (mask.kind, mask.thickness, mask.material, mask.epsilon_r, mask.color) == (
        "mask",
        0.01,
        "Epoxy",
        3.8,
        "Blue",
    )
    d1 = by["dielectric 1"]
    assert (d1.dielectric, d1.locked, d1.frequency, d1.dielectric_model) == ("prepreg", True, 1e9, "djordjevic_sarkar")
    assert len(d1.sublayers) == 2 and d1.thickness == 0.13626 and d1.epsilon_r == 4.3 and d1.material == "R-1551(W)"
    d2 = by["dielectric 2"]
    assert (d2.dielectric, d2.sublayers, d2.locked, d2.epsilon_r, d2.loss_tangent) == ("core", [], None, 4.6, 0.02)
    d3 = by["dielectric 3"]  # different sublayers: series epsilon, thickness-weighted loss, both materials
    assert d3.thickness == 0.17 and d3.material == "PR1080 + PR2116"
    assert d3.epsilon_r == pytest.approx(0.17 / (0.07 / 3.96 + 0.1 / 4.4), abs=1e-6)
    assert d3.loss_tangent == pytest.approx(0.07 * 0.02 / 0.17, abs=1e-6)
    assert [s.material for s in d3.sublayers] == ["PR1080", "PR2116"] and d3.sublayers[1].loss_tangent == 0.0
    assert [la.layer for la in st.layers if la.kind == "copper"] == ["F.Cu", "In1.Cu", "In2.Cu", "B.Cu"]
    assert by["In1.Cu"].side == "inner" and by["B.Mask"].side == "bottom"


def test_kicad10_board(board):
    assert validate_board(board.to_dict()) == []
    assert [(n.name, n.pair) for n in board.nets] == [("GND", None), ("/USB/D+", None)]
    assert board.source.generator == "pcbnew 10.0" and board.origin.aux == (100.0, -120.0)
    (c,) = board.components
    assert (c.ref, c.side, c.x, c.y, c.rotation, c.populate, c.mount) == (
        "R1",
        "bottom",
        110.0,
        -105.0,
        90.0,
        False,
        "smd",
    )
    fp = board.footprints["Lib:R"]
    # flipped back to the library form: y mirrored, B -> F, pad angles relative to the footprint
    assert [p.at for p in fp.pads] == [(-0.5, 0.0, 0.0), (0.5, -0.2, 0.0)]
    assert fp.pads[0].layers == ["F.Cu", "F.Paste", "F.Mask"]
    assert fp.pads[1].chamfer == ["bottom_left"]  # mirrored across x: top <-> bottom
    assert fp.graphics[0].layer == "F.SilkS" and fp.graphics[0].pts == [(-1.0, 0.5), (1.0, 0.5)]
    assert [(d.x, d.y, d.diameter, d.function) for d in board.drills] == [(105.0, -105.0, 0.3, "via")]


def test_outline_with_cutouts(board):
    o = board.outline
    assert not o.approximate and signed_area(o.board) > 0
    assert len(o.board) == 4 and {p for p in o.board} == {
        (100.0, -100.0),
        (130.0, -100.0),
        (130.0, -120.0),
        (100.0, -120.0),
    }
    assert len(o.cutouts) == 2 and all(signed_area(c) < 0 for c in o.cutouts)
    circle, square = sorted(o.cutouts, key=lambda c: len(c), reverse=True)
    assert len(circle) == 72 and all(math.isclose(math.dist(p, (120, -110)), 1, abs_tol=1e-6) for p in circle)
    # the footprint's Edge.Cuts rect, rotated 90 degrees with the footprint at (110, 105), y down
    xs, ys = sorted({p[0] for p in square}), sorted({p[1] for p in square})
    assert xs == [108.0, 109.0] and ys == [-107.0, -106.0]  # (x, y) -> (y, -x) on screen, then y negated


KICAD5 = """
(kicad_pcb (version 20171130) (host pcbnew 5.1.9)
  (general (thickness 1.6))
  (layers (0 F.Cu signal) (31 B.Cu signal))
  (net 0 "") (net 1 GND) (net 2 /D_P) (net 3 /D_N)
  (net_class Default "default" (clearance 0.2) (trace_width 0.25) (add_net GND))
  (net_class DP_90_MS "usb" (clearance 0.15) (trace_width 0.2) (diff_pair_width 0.2) (diff_pair_gap 0.15)
    (add_net /D_P) (add_net /D_N))
  (module Lib:J (layer F.Cu) (at 10 10 180)
    (fp_text reference J1 (at 0 0) (layer F.SilkS)) (fp_text value CONN (at 0 0) (layer F.Fab))
    (fp_arc (start 0 0) (end 1 0) (angle 90) (layer F.SilkS) (width 0.12))
    (pad 1 thru_hole oval (at 0 0 180) (size 1.7 1.2) (drill oval 1 0.6) (layers *.Cu *.Mask) (net 1 GND)))
  (gr_line (start 0 0) (end 20 0) (layer Edge.Cuts) (width 0.1))
  (gr_line (start 20 0) (end 20 20) (layer Edge.Cuts) (width 0.1))
  (gr_arc (start 10 20) (end 20 20) (angle 180) (layer Edge.Cuts) (width 0.1))
)
"""


def test_kicad5_board():
    b = read_kicad_pcb(KICAD5)
    assert validate_board(b.to_dict()) == []
    assert [c.ref for c in b.components] == ["J1"] and b.components[0].value == "CONN"
    assert {n.name: (n.net_class, n.pair) for n in b.nets} == {
        "GND": ("Default", None),
        "/D_P": ("DP_90_MS", "/D_N"),
        "/D_N": ("DP_90_MS", "/D_P"),
    }
    dp = next(c for c in b.net_classes if c.name == "DP_90_MS")
    assert (dp.track_width, dp.diff_pair_gap) == (0.2, 0.15)
    assert dp.impedance == m.ImpedanceTarget(kind="differential", target=90, structure="microstrip", source="name")
    # a 1 x 0.6 oval hole at 180 degrees: a horizontal slot, end to end
    (d,) = b.drills
    assert sorted([(d.x, d.y), (d.x2, d.y2)]) == [(9.8, -10.0), (10.2, -10.0)] and d.diameter == 0.6
    assert b.footprints["Lib:J"].pads[0].at == (0.0, 0.0, 0.0)
    # the legacy arc (centre, start, angle) is flattened like a 3-point one
    arc = next(g for g in b.footprints["Lib:J"].graphics if g.kind == "arc")
    assert arc.pts[0] == (1.0, 0.0) and arc.pts[-1] == pytest.approx((0.0, 1.0), abs=1e-6)
    # an outline that doesn't close: bounding box of the drawings, flagged
    assert b.outline.approximate and any("do not close" in w for w in b.warnings)


def test_kicad_mod_and_raw_footprint():
    text = """(footprint "X" (version 20240108) (generator "pcbnew") (layer "F.Cu") (attr through_hole)
      (property "Reference" "REF**" (at 0 0 0) (layer "F.SilkS"))
      (fp_circle (center 0 0) (end 1 0) (stroke (width 0.1) (type solid)) (fill none) (layer "F.CrtYd"))
      (fp_poly (pts (xy -2 -2) (xy -1.5 -2) (xy -1.5 -1.5)) (stroke (width 0) (type solid)) (fill solid) (layer "F.Paste"))
      (pad "1" smd rect (at -1.75 -1.75) (size 1 1) (layers "F.Cu" "F.Paste" "F.Mask"))
      (pad "" np_thru_hole circle (at 3 0) (size 2 2) (drill 2) (layers "*.Cu" "*.Mask"))
      (pad "2" smd custom (at 0 3 90) (size 0.5 0.5) (layers "F.Cu") (options (clearance outline) (anchor circle))
        (primitives (gr_poly (pts (xy 0 0) (xy 1 0) (xy 1 1)) (width 0) (fill yes))))
      (model "${KICAD9_3DMODEL_DIR}/x.step" (offset (xyz 1 2 3)) (scale (xyz 1 1 1)) (rotate (xyz 0 0 90))))"""
    fp = read_kicad_mod(text, name="Lib:X")
    assert fp.name == "Lib:X" and fp.attr == ["through_hole"]
    assert [p.type for p in fp.pads] == ["smd", "np_thru_hole", "smd"]
    assert fp.pads[1].drill == m.PadDrill(shape="circle", size=(2.0, 2.0))
    assert fp.pads[2].anchor == "circle" and fp.pads[2].primitives[0].pts == [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0)]
    assert fp.pads[2].at == (0.0, 3.0, 90.0)
    (circle,) = fp.graphics
    assert circle.layer == "F.CrtYd" and circle.closed and len(circle.pts) == 48
    # a filled shape on the paste layer is a stencil opening of the pad under it, pad-local
    (opening,) = fp.pads[0].paste
    assert opening.shape == "polygon" and opening.size == (0.5, 0.5) and opening.center == (0.0, 0.0)
    raw = KicadFootprint(parse(text))
    assert raw.properties["Reference"] == "REF**" and raw.models[0]["offset"] == [1.0, 2.0, 3.0]
    assert [p["number"] for p in raw.pads] == ["1", "", "2"] and raw.pads[2]["primitives"][0]["kind"] == "poly"


def test_sexpr():
    root = parse('(a (b 1 "two words") hide "hide" (c yes) |em\nbedded|)')
    assert root.name == "a" and root.child("b").arg(1) == "two words"
    assert isinstance(root.arg(0), Atom) and not isinstance(root.arg(1), Atom)
    assert root.flag("hide") and root.child("c").arg(0) == "yes" and root.arg(2) == "|embedded|"
    assert dumps(parse("(x 1.000 -0 (uuid u))")) == "(x 1.0 0.0)"


def test_item_view():
    """kipr's diff view of the same board: tracks/vias with nets (KiCad 10 names), stackup text, edge box."""
    pcb = load(STACKUP)
    assert [i.kind for i in pcb.items if i.kind in ("track", "via")] == ["track", "via"]
    assert [i.net for i in pcb.items if i.kind in ("track", "via")] == ["GND", "GND"]
    assert pcb.stackup["dielectric_constraints"] == "yes" and pcb.thickness == 1.56252
    assert pcb.footprints[0].pad_nets == {"1": "GND", "2": "/USB/D+"} and pcb.footprints[0].side == "bottom"


SYM = """(kicad_symbol_lib (version 20241209) (generator "kicad_symbol_editor")
  (symbol "OPAMP" (pin_names (offset 0.254))
    (property "Reference" "U" (at 0 5 0) (effects (font (size 1.27 1.27))))
    (property "Value" "OPAMP" (at 0 -5 0) (effects (font (size 1.27 1.27))))
    (symbol "OPAMP_1_1"
      (polyline (pts (xy -5 5) (xy 5 0) (xy -5 -5) (xy -5 5)) (stroke (width 0.254) (type default)) (fill (type background)))
      (pin input line (at -7.62 2.54 0) (length 2.54) (name "+" (effects (font (size 1.27 1.27)))) (number "3" (effects (font (size 1.27 1.27)))))
      (pin output line (at 7.62 0 180) (length 2.54) (name "~" (effects (font (size 1.27 1.27)))) (number "1" (effects (font (size 1.27 1.27))))))
    (symbol "OPAMP_2_1"
      (pin power_in line (at 0 7.62 270) (length 2.54) (name "V+" (effects (font (size 1.27 1.27)))) (number "8" (effects (font (size 1.27 1.27)))))))
  (symbol "OPAMP2" (extends "OPAMP") (property "Reference" "U" (at 0 5 0)) (property "Value" "OPAMP2" (at 0 -5 0))))
"""


def test_symbols():
    lib = read_kicad_sym(SYM)
    op = lib["OPAMP"]
    assert op.unit_count == 2 and op.pin_name_offset == 0.254
    assert [(p["number"], p["type"], p["unit"]) for p in op.pins] == [
        ("3", "input", 1),
        ("1", "output", 1),
        ("8", "power_in", 2),
    ]
    assert op.shapes[0]["kind"] == "polyline" and len(op.shapes[0]["pts"]) == 4
    derived = lib["OPAMP2"]
    assert derived.extends == "OPAMP" and len(derived.pins) == 3 and derived.properties["Value"] == "OPAMP2"
    assert op.stats()["pin_count"] == 3
