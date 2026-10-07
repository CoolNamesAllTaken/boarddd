"""boarddd.render.footprint/symbol draw exactly what kipr draws (byte for byte).

fixtures/kipr/golden.json is kipr's own output (kipr main f632b3f, `make_golden.py`) on kipr's library fixtures
(fixtures/kicad-libs: PantsForBirds/kicad-libs parts, MIT), the pad fixtures and an excerpt of KiCad's stock
symbols. Same viewBox, scale, combined and per-layer SVGs, geom.json and statistics.
"""

import json
from pathlib import Path

import pytest

from boarddd.io.kicad.sexpr import load
from boarddd.render import footprint as fpmod
from boarddd.render import symbol as symmod

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
GOLDEN = json.loads((HERE / "fixtures" / "kipr" / "golden.json").read_text("utf-8"))


def normal(value):
    """JSON round trip, as the golden was stored (tuples become lists)."""
    return json.loads(json.dumps(value))


@pytest.mark.parametrize("name", sorted(GOLDEN["footprints"]))
def test_footprint(name):
    gold = GOLDEN["footprints"][name]
    fp = fpmod.Footprint(load(REPO / gold["source"]))
    vb = fpmod.viewbox(fpmod.footprint_bbox(fp))
    assert normal(vb) == gold["viewbox"]
    scale = fpmod.px_scale(vb)
    assert scale == gold["scale"]
    combined, layers = fpmod.render_footprint(fp, vb, scale)
    assert combined == gold["combined"]
    assert layers == gold["layers"]
    assert normal(fpmod.geom_json(fp, vb)) == gold["geom"]
    assert normal(fpmod.Footprint(load(REPO / gold["source"])).stats()) == gold["stats"]


@pytest.mark.parametrize("lib", sorted(GOLDEN["symbols"]))
def test_symbols(lib):
    gold = GOLDEN["symbols"][lib]
    root = load(REPO / gold["source"])
    library = symmod.parse_library(root)
    assert sorted(library) == sorted(gold["symbols"])
    for name, want in gold["symbols"].items():
        sym = symmod.Symbol(library[name], library)
        layout = symmod.unit_layout(sym)
        slots, vb = symmod.shared_layout([layout])
        assert normal(vb) == want["viewbox"], name
        w, h = vb[2] - vb[0], vb[3] - vb[1]
        scale = min(80.0, 1600 / max(w, h, 1e-3))
        assert symmod.render_symbol(layout, slots, vb, scale, sym) == want["svg"], name
        assert normal(sym.stats()) == want["stats"], name


def test_two_revisions_share_one_layout():
    gold = GOLDEN["pair"]
    root = load(HERE / "fixtures" / "KiCad_Stock_Excerpt.kicad_sym")
    library = symmod.parse_library(root)
    syms = {name: symmod.Symbol(library[name], library) for name in gold["svg"]}
    layouts = {name: symmod.unit_layout(s) for name, s in syms.items()}
    slots, vb = symmod.shared_layout(list(layouts.values()))
    assert normal(vb) == gold["viewbox"] and {str(k): v for k, v in slots.items()} == gold["slots"]
    for name, s in syms.items():
        assert symmod.render_symbol(layouts[name], slots, vb, gold["scale"], s) == gold["svg"][name], name
