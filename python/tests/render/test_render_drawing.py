"""Review drawings (boarddd.render.drawing): structure, the diff, and magpie's own drawings as pixel goldens.

fixtures/magpie/*.svg are magpie's ``footprint_svg`` (internal claud/magpie 3a0374d3, ``make_golden.py``) of the
same public footprints, read by magpie's own reader. Both are rasterised (strokes in mm, see
``drawing.scaled_style``) and compared: the copper (the pad colour) and the whole picture.
"""

import copy
import re
import xml.etree.ElementTree as ElementTree
from pathlib import Path

import pytest

from boarddd.io.kicad import read_kicad_mod
from boarddd.render.drawing import diff_svg, footprint_diff, footprint_svg, scaled_style

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
MAGPIE = HERE / "fixtures" / "magpie"
SOURCES = {
    p.stem: p
    for p in [
        *(HERE / "fixtures" / "kicad-libs").glob("*.kicad_mod"),
        *(REPO / "python" / "tests" / "kicad" / "fixtures" / "pad_placement").glob("*.kicad_mod"),
        *(REPO / "test" / "fixtures" / "footprints").glob("*.kicad_mod"),
    ]
}
#: Where magpie's drawing is wrong and boarddd's is right (checked against pcbnew in tests/kicad):
KNOWN = {
    # magpie's reader drops an SMD pad's shape offset ((drill (offset)) without a hole): castellated pads sit
    # 0.65 mm in from where KiCad puts them (kipr PR #13's bug)
    "RP2040-Zero_Castellated": "magpie drops SMD shape offsets",
    # magpie draws holes axis-aligned at the copper's centre; a rotated slot stays horizontal
    "MountingHole_Slotted_boarddd": "magpie draws holes unrotated",
}
#: Pictures that differ only in graphics magpie draws as their defining points (an arc as start-mid-end).
ARCS = {"MagneticBuzzer_9.6mm_5mm_right-angle"}
PAD = (0xC8, 0xA1, 0x4A)


def soic():
    return read_kicad_mod(SOURCES["SOIC-8-1EP_3.9x4.9mm_P1.27mm_EP2.41x3.3mm"])


def test_footprint_svg_structure():
    svg = footprint_svg(soic(), title="SOIC-8", pin1="1")
    root = ElementTree.fromstring(svg)
    assert root.get("width") == "400" and "SOIC-8" in root.get("aria-label")
    assert svg.count('class="fp-pad"') == 9 and svg.count('class="fp-text"') == 9  # 8 pins + the exposed pad
    assert re.search(r'class="fp-dimtext"[^>]*>1\.27( mm)?<', svg)  # the pitch
    assert 'class="fp-pin1"' in svg and 'class="fp-mm"' in svg
    assert "--fp-pad" in svg and "prefers" not in svg


def test_scaled_style_puts_strokes_in_mm():
    style = scaled_style(50.0)
    assert ".fp-mm .fp-pad{" in style and "stroke-width:0.02;" in style and "vector-effect:none" in style
    assert "stroke-dasharray:0.06 0.04" in style  # the paste outline's 3 2 px


def test_diff():
    old = soic()
    assert footprint_diff(old, old).same
    new = copy.deepcopy(old)
    pin = {p.number: p for p in new.pads if p.number}
    pin["1"].at = (pin["1"].at[0] + 0.1, *pin["1"].at[1:])  # pin 1 moved
    pin["2"].size = (pin["2"].size[0] * 1.2, pin["2"].size[1])  # pin 2 wider
    new.pads.remove(pin["3"])  # pin 3 gone
    extra = copy.deepcopy(pin["4"])
    extra.number = "10"
    extra.at = (extra.at[0], extra.at[1] + 5, extra.at[2])
    new.pads.append(extra)  # a new pad
    d = footprint_diff(old, new)
    assert len(d.changed) == 2 and len(d.only_a) == 1 and len(d.only_b) == 1
    assert d.summary() == "2 changed, 1 removed, 1 added of 9 pads"
    svg = diff_svg(old, new, labels=("v1", "v2"))
    ElementTree.fromstring(svg)
    assert svg.count('class="fp-changed"') == 3  # two ringed pads + the legend swatch
    assert "v1" in svg and "v2" in svg and d.summary() in svg
    assert "copper identical (9 pads)" in diff_svg(old, old)


@pytest.mark.parametrize("name", sorted(p.stem for p in MAGPIE.glob("*.svg")))
def test_matches_magpie(name):
    from rendkit import raster, require_render

    require_render()
    import numpy as np

    theirs = (MAGPIE / f"{name}.svg").read_text("utf-8")
    scale = float(re.search(r"scale\(([\d.]+) -", theirs).group(1))
    theirs = theirs.replace('<g transform="translate(', '<g class="fp-mm" transform="translate(', 1)
    theirs = theirs.replace("</style>", scaled_style(scale) + "\n</style>", 1)
    a = raster(theirs)
    b = raster(footprint_svg(read_kicad_mod(SOURCES[name]), width=400, title=name))
    assert a.shape == b.shape, name
    pad_a = np.abs(a - PAD).max(axis=2) < 30
    pad_b = np.abs(b - PAD).max(axis=2) < 30
    iou = (pad_a & pad_b).sum() / max((pad_a | pad_b).sum(), 1)
    differ = (np.abs(a - b).max(axis=2) > 40).mean()
    if name in KNOWN:
        assert iou > 0.5, (name, KNOWN[name], iou)
    else:
        assert iou >= 0.96, (name, iou)
        assert differ < (0.025 if name in ARCS else 0.015), (name, differ)  # antialiasing of thin lines
