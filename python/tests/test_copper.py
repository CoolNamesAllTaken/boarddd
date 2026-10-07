"""boarddd/copper@1: the model (round trip, schema), the validator against the shared cases (src/copper's
validateCopper runs the same file) and the polygon helpers both readers use."""

import json
import math

import jsonschema
import pytest

from boarddd import _codegen
from boarddd import copper as cu
from boarddd.validate import main, validate, validate_copper

from conftest import FIXTURES, apply_patch, load

CASES = load("copper/cases.json")
RULES = ("duplicate", "is not in", "must run top to bottom")
NFC = FIXTURES / "royalblue54L_nfc_antenna" / "copper.json"


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_case(case):
    assert validate_copper(apply_patch(load("copper/minimal.json"), case["patch"])) == case["errors"]


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_jsonschema_agrees(case):
    doc = apply_patch(load("copper/minimal.json"), case["patch"])
    schema_errors = [e for e in case["errors"] if not any(r in e for r in RULES)]
    v = jsonschema.Draft202012Validator(_codegen.schema(cu.Copper))
    assert bool(list(v.iter_errors(doc))) == bool(schema_errors)


def test_schema():
    s = _codegen.schema(cu.Copper)
    jsonschema.Draft202012Validator.check_schema(s)
    assert s["$id"] == cu.SCHEMA_ID == "boarddd/copper@1"
    # the shared provenance types are board@1's
    assert s["$defs"]["Source"] == _codegen.schema()["$defs"]["Source"]


def test_golden_round_trip():
    text = NFC.read_text("utf-8")
    doc = cu.Copper.from_json(text)
    assert doc.to_json() == text
    assert validate_copper(json.loads(text)) == []
    assert isinstance(doc.tracks[0].start, tuple) and isinstance(doc.vias[0].span, tuple)


def test_minimal_round_trip():
    data = load("copper/minimal.json")
    doc = cu.Copper.from_dict(data)
    assert doc.vias[1].padstack[1] == cu.ViaPad(layer="In1.Cu", diameter=0.4)
    again = json.loads(doc.to_json())
    assert validate_copper(again) == []
    assert again["tracks"][0]["mid"] is None and again["keepouts"][0]["rules"]["pads"] is False


def test_validate_dispatches_on_schema(tmp_path, capsys):
    assert validate(load("copper/minimal.json")) == []
    bad = apply_patch(load("copper/minimal.json"), [["set", "/tracks/0/layer", "X"]])
    p = tmp_path / "copper.json"
    p.write_text(json.dumps(bad))
    assert main([str(p)]) == 1
    assert "/tracks/0/layer: 'X' is not in /layers" in capsys.readouterr().out


# ---------------------------------------------------------------------------------------------------------------------
# polygon helpers


def square(x0, y0, x1, y1):
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


def fractured(outer, holes):
    """KiCad's way: walk the outline, and at its first vertex cut in to each hole and back out."""
    ring = [outer[0]]
    for h in holes:
        ring += [*h, h[0], outer[0]]
    return ring + outer[1:]


def test_unfracture_square_with_two_holes():
    outer = square(0, 0, 10, 10)
    holes = [square(2, 2, 4, 4)[::-1], square(6, 6, 8, 7)[::-1]]
    polys = cu.unfracture(fractured(outer, holes))
    assert len(polys) == 1
    p = polys[0]
    assert cu.signed_area(p.outline) == pytest.approx(100)
    assert sorted(cu.signed_area(h) for h in p.holes) == pytest.approx([-4, -2])
    assert cu.fill_area(polys) == pytest.approx(94)
    assert cu.in_fill((1, 1), p) and not cu.in_fill((3, 3), p) and not cu.in_fill((11, 1), p)


def test_unfracture_orients_and_keeps_plain_rings():
    cw = square(0, 0, 2, 3)[::-1]
    (p,) = cu.unfracture(cw)
    assert cu.signed_area(p.outline) == pytest.approx(6) and p.holes == []
    # repeated points and a closing point are dropped
    (p,) = cu.unfracture([(0, 0), (0, 0), (4, 0), (4, 4), (0, 4), (0, 0)])
    assert len(p.outline) == 4
    assert cu.unfracture([(0, 0), (1, 1)]) == []


def test_unfracture_island_in_a_hole():
    """A hole with an island in it, all in one ring (KiCad writes islands as their own polygons; a region may not)."""
    outer, hole, island = square(0, 0, 10, 10), square(2, 2, 8, 8)[::-1], square(4, 4, 6, 6)
    polys = cu.unfracture(fractured(outer, [hole, island]))
    assert [round(cu.fill_area([p])) for p in polys] == [64, 4]
    assert [len(p.holes) for p in polys] == [1, 0]


def test_circle_halves():
    (s1, e1, m1), (s2, e2, m2) = cu.circle_halves((1.0, 1.0), (2.0, 1.0))
    assert (s1, e1, m1) == ((2.0, 1.0), (0.0, 1.0), (1.0, 2.0))
    assert (s2, e2, m2) == ((0.0, 1.0), (2.0, 1.0), (1.0, 0.0))


def test_planes_and_outline_area():
    z = [
        cu.Zone(layer="In1.Cu", net="GND", kind="pour", fill=[], area=60.0),
        cu.Zone(layer="F.Cu", net="GND", kind="pour", fill=[], area=10.0),
        cu.Zone(layer="F.Cu", net="VCC", kind="pour", fill=[], area=20.0),
        cu.Zone(layer="F.Cu", net="GND", kind="teardrop", fill=[], area=5.0),
        cu.Zone(layer="F.Cu", net="", kind="shape", fill=[], area=5.0),
    ]
    from boarddd.model import Outline

    area = cu.outline_area(Outline(board=square(0, 0, 10, 10), cutouts=[square(1, 1, 3, 3)[::-1]]))
    assert area == pytest.approx(96)
    got = cu.planes(z, area, ["F.Cu", "In1.Cu", "B.Cu"])
    assert [(p.layer, p.net, p.area, p.solid) for p in got] == [
        ("F.Cu", "VCC", 20.0, False),
        ("F.Cu", "GND", 10.0, False),
        ("In1.Cu", "GND", 60.0, True),
    ]
    assert got[2].coverage == round(60 / 96, 4)
    assert cu.planes(z, None)[0].coverage is None


def test_check_fills_flags_other_nets_only():
    fill = [cu.FillPolygon(outline=square(0, 0, 10, 10), holes=[square(4, 4, 6, 6)[::-1]])]
    doc = cu.Copper(
        board="t",
        source=cu.Source(kind="other"),
        layers=["F.Cu", "B.Cu"],
        zones=[
            cu.Zone(layer="B.Cu", net="GND", kind="pour", fill=fill, area=96.0),
            cu.Zone(layer="F.Cu", net="GND", kind="pour", fill=fill, area=96.0),
            cu.Zone(layer="F.Cu", net="", kind="shape", fill=fill, area=96.0),  # drawings are not checked
        ],
        tracks=[
            cu.Track(layer="F.Cu", net="GND", width=0.2, start=(1, 1), end=(2, 2)),  # same net: fine
            cu.Track(layer="F.Cu", net="SIG", width=0.2, start=(4.5, 5), end=(5.5, 5)),  # in the hole: fine
            cu.Track(layer="B.Cu", net="SIG", width=0.2, start=(20, 0), end=(1, 9)),  # end inside the B.Cu fill
        ],
        vias=[cu.Via(at=(5, 5), net="SIG", diameter=0.6, drill=0.3, span=("F.Cu", "B.Cu"), type="through")],
    )
    assert cu.check_fills(doc) == 1
    assert [z.stale for z in doc.zones] == [True, False, None]
    assert "track of net 'SIG' at (1.000, 9.000)" in doc.warnings[0]
    # a via of another net in the copper (not in the hole) also counts
    doc.vias[0].at = (2, 8)
    doc.warnings.clear()
    assert cu.check_fills(doc) == 2 and "via of net 'SIG'" in doc.warnings[1]


def test_point_in_ring_edges():
    ring = square(0, 0, 1, 1)
    assert cu.point_in_ring((0.5, 0.5), ring)
    assert not cu.point_in_ring((1.5, 0.5), ring)
    assert not cu.point_in_ring((0.5, -math.ulp(0) - 1e-9), ring)
