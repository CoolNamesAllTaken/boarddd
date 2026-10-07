"""Line drawings and overlay diffs."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ElementTree

import pytest
from stepkit import require_occ

require_occ()

from stepkit import TINY, TINY_POS  # noqa: E402

from boarddd.step import hlr as render  # noqa: E402
from boarddd.step.split import split  # noqa: E402


@pytest.fixture(scope="module")
def tiny():
    return split(TINY, pos=TINY_POS)


def test_views_cover_the_model(tiny):
    found = render.views(tiny.components["R1"])
    assert set(found) == {"top", "front", "side", "bottom"}
    x0, y0, x1, y1 = found["top"].bounds()
    assert (x1 - x0, y1 - y0) == pytest.approx((1.0, 0.5), abs=1e-3)
    _x0, z0, _x1, z1 = found["front"].bounds()
    assert z1 - z0 == pytest.approx(0.35, abs=1e-3)
    assert found["top"].hidden  # the terminations' far edges
    # The bottom view is the top flipped left-right.
    bx0, _by0, bx1, _by1 = found["bottom"].bounds()
    assert (bx0, bx1) == pytest.approx((-x1, -x0), abs=1e-3)


def test_drawing_is_valid_svg_with_dimensions_and_theme(tiny):
    svg = render.drawing(tiny.components["U1"], title="U1")
    root = ElementTree.fromstring(svg)
    assert root.tag.endswith("svg") and "boarddd-step" in root.get("class")
    assert ">3.00<" in svg and "mm<" in svg  # bbox dimension and scale bar
    for variable in ("--boarddd-step-line", "--boarddd-step-hidden", "--boarddd-step-text"):
        assert variable in svg
    assert "prefers-color-scheme: dark" in svg
    assert int(root.get("width")) <= 1100  # about 400 px per view


def test_projections_are_cached_by_fingerprint(tiny):
    first = render.views(tiny.components["R1"])
    again = render.views(tiny.components["R2"])  # another instance of the same model
    assert first is again


def test_disk_cache(tmp_path, tiny):
    svg = render.drawing(tiny.components["R4"], cache_dir=tmp_path)
    assert list(tmp_path.glob("*.svg")) and render.drawing(tiny.components["R4"], cache_dir=tmp_path) == svg


def test_overlay_marks_what_differs(tiny):
    svg = render.overlay(tiny.components["U1"], tiny.components["U2"], labels=("EP1.7", "EP1.8"))
    ElementTree.fromstring(svg)
    assert 'class="old only"' in svg and 'class="new only"' in svg
    assert 'class="differs"' in svg  # the height row
    assert "EP1.7" in svg and "EP1.8" in svg


def test_overlay_of_one_model_with_itself_shows_no_difference(tiny):
    svg = render.overlay(tiny.components["R1"], tiny.components["R2"])
    assert 'class="old only"' not in svg and 'class="new only"' not in svg
    assert "no visible difference" in svg
    assert not re.search(r'class="differs"', svg)
