"""Pads and models from read_kicad_mod land where pcbnew puts them.

fixtures/pad_placement/ is kipr's golden set (kipr ``tests/library/fixtures/pad_placement`` at ``b6b8456``,
regenerated identically here with KiCad 10.0.6's pcbnew by its make_golden.py): pad position, copper centre
(the shape offset), effective-shape bbox, hole position, 3D model offset/rotate/scale, and for the trapezoids
pcbnew's own corners. ``test/fixtures/pad_shapes`` (boarddd's, shared with the JS tests) adds the area and bbox
of every pad shape and the effective hole.
"""

import json
import math
from pathlib import Path

import pytest
from conftest import REPO

from boarddd.io.kicad import KicadFootprint, read_kicad_mod
from boarddd.io.kicad.geom import pad_copper, pad_offset, rotate, signed_area
from boarddd.io.kicad.sexpr import load

FIX = Path(__file__).parent / "fixtures" / "pad_placement"
GOLDEN = json.loads((FIX / "golden.json").read_text())
SHAPES = REPO / "test" / "fixtures" / "pad_shapes"
TOL = 0.01  # mm: our flattened arcs vs KiCad's


def bbox(pts):
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return [min(xs), min(ys), max(xs), max(ys)]


def near(a, b, tol=TOL):
    return len(a) == len(b) and all(abs(x - y) <= tol for x, y in zip(a, b, strict=True))


def same_corners(got, gold, tol=1e-6):
    """The same polygon corners, whatever the start vertex and direction."""
    return len(got) == len(gold) and all(any(near(g, q, tol) for q in got) for g in gold)


@pytest.mark.parametrize("name", sorted(GOLDEN))
def test_pads_and_models(name):
    fp = read_kicad_mod(FIX / f"{name}.kicad_mod")
    gold = GOLDEN[name]
    assert [p.number for p in fp.pads] == [g["number"] for g in gold["pads"]]
    for p, g in zip(fp.pads, gold["pads"], strict=True):
        where = f"{name} pad {p.number}"
        assert near(p.at[:2], g["at"]), where
        ox, oy = rotate(*pad_offset(p), p.at[2])
        assert near((p.at[0] + ox, p.at[1] + oy), g["copper_center"]), where + " copper centre"
        if p.shape != "custom":
            assert near(bbox(pad_copper(p)[0]), g["copper_bbox"]), where + " copper bbox"
        assert (p.drill is not None) == (g["hole_center"] is not None), where + " hole"
        if "copper_polygon" in g:
            assert same_corners(pad_copper(p)[0], g["copper_polygon"]), where + " trapezoid corners"
    models = KicadFootprint(load(FIX / f"{name}.kicad_mod")).models
    assert len(models) == len(gold["models"])
    for md, g in zip(models, gold["models"], strict=True):
        for k in ("offset", "rotate", "scale"):
            assert near(md[k], g[k], 1e-9), f"{name} model {k}"


def test_golden_covers_the_cases():
    assert sorted(GOLDEN) == sorted(p.stem for p in FIX.glob("*.kicad_mod"))
    # castellated SMD pads: the copper sits 0.65 mm outward of `at` (the shape offset, no hole)
    rp = read_kicad_mod(FIX / "RP2040-Zero_Castellated.kicad_mod").pads[0]
    assert rp.drill is None and rp.offset == (-0.65, 0.0)
    trapezoids = [p for p in read_kicad_mod(FIX / "Trapezoid_Delta.kicad_mod").pads if p.shape == "trapezoid"]
    assert len(trapezoids) == 10 and {p.rect_delta for p in trapezoids} >= {(0.4, 0.0), (-0.4, 0.0), (0.0, 0.4), (0.0, -0.4)}


def test_pad_shapes():
    """Every KiCad pad shape (incl. chamfered and rotated ones) against pcbnew's effective polygon and hole."""
    golden = json.loads((SHAPES / "golden.json").read_text())
    fp = read_kicad_mod(SHAPES / "Pad_Shapes_boarddd.kicad_mod")
    assert len(fp.pads) == len(golden)
    for p, g in zip(fp.pads, golden, strict=True):
        where = f"pad {p.number} ({p.shape})"
        loops = pad_copper(p, segments=32)
        if p.shape != "custom":
            assert near(bbox(loops[0]), g["bbox"]), where
            assert math.isclose(abs(signed_area(loops[0])), g["area"], rel_tol=0.01), where  # pcbnew flattens arcs inside (ERROR_INSIDE)
        else:  # unioned in pcbnew: the loops together cover the same box
            assert near(bbox([q for lp in loops for q in lp]), g["bbox"], 0.02), where
        if g["hole"] is None:
            assert p.drill is None, where
            continue
        # the hole stays at `at`; an oval hole runs along the pad's long axis
        w, h = p.drill.size
        half = abs(w - h) / 2
        dx, dy = rotate(half, 0.0, p.at[2]) if w >= h else rotate(0.0, half, p.at[2])
        ends = [(p.at[0] - dx, p.at[1] - dy), (p.at[0] + dx, p.at[1] + dy)]
        assert same_corners(ends, [g["hole"]["start"], g["hole"]["end"]], 1e-5), where
        assert math.isclose(min(w, h), g["hole"]["width"], abs_tol=1e-6), where
