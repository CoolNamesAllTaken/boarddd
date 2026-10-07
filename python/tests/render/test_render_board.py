"""The board render (boarddd.render.board) on royalblue54L's model, and against KiCad's own F.Mask plot."""

import re
import xml.etree.ElementTree as ElementTree
from pathlib import Path

import pytest

from boarddd import model as m
from boarddd.render.board import MARGIN_MM, board_svg

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
NS = {"s": "http://www.w3.org/2000/svg"}


@pytest.fixture(scope="module")
def board():
    return m.Board.from_json((REPO / "fixtures" / "royalblue54L_feather" / "board.json").read_text("utf-8"))


def test_structure(board):
    for side, count in (("top", 56), ("bottom", 15)):
        svg = board_svg(board, side)
        root = ElementTree.fromstring(svg)
        parts = root.findall(".//s:a", NS)
        assert len(parts) == count == sum(c.side == side for c in board.components)
        assert root.get("data-side") == side and root.find("s:path", NS).get("class") == "bm-board"
        assert len(root.findall(".//s:g[@class='bm-drill']/*", NS)) == 278
    # Blue mask from the stackup
    assert "var(--bm-board,#1b3a78)" in board_svg(board)


def test_bottom_is_seen_from_below(board):
    """Mirrored left to right: J1 (a bottom header) is drawn at x = max_x - its pads' middle."""
    from boarddd.io.kicad.geom import pad_copper
    from boarddd.render.board import _place

    def hit(svg):
        found = re.search(
            r'data-ref="J1"><title>[^<]*</title><g><rect class="bm-hit" x="(-?[\d.]+)" y="-?[\d.]+" width="([\d.]+)"',
            svg,
        )
        return float(found.group(1)) + float(found.group(2)) / 2

    j1 = next(c for c in board.components if c.ref == "J1")
    xs = [_place(j1, q)[0] for pad in board.footprints[j1.footprint].pads for q in pad_copper(pad)[0]]
    middle = (min(xs) + max(xs)) / 2
    max_x = max(p[0] for p in board.outline.board)
    min_x = min(p[0] for p in board.outline.board)
    assert hit(board_svg(board, "bottom")) == pytest.approx(max_x - middle, abs=0.01)
    j1.side = "top"  # the same part seen from the top would sit at middle - min_x (its pads mirror in y)
    try:
        xs_top = [_place(j1, q)[0] for pad in board.footprints[j1.footprint].pads for q in pad_copper(pad)[0]]
        assert hit(board_svg(board, "top")) == pytest.approx((min(xs_top) + max(xs_top)) / 2 - min_x, abs=0.01)
    finally:
        j1.side = "bottom"


def test_states_links_and_highlight(board):
    svg = board_svg(
        board, states={"U1": "approved", "C1": "open"}, links={"U1": "/parts/U1"}, highlight="C1", px_per_mm=10
    )
    assert 'class="bm-pl st-approved" data-ref="U1" data-state="approved" href="/parts/U1"' in svg
    assert 'class="bm-pl st-open hl" data-ref="C1"' in svg
    root = ElementTree.fromstring(svg)
    assert float(root.get("width")) == pytest.approx((58.42 + 2 * MARGIN_MM) * 10)


def test_without_outline(board):
    bare = m.Board(
        name="x", source=m.Source(kind="other"), components=board.components[:3], footprints=board.footprints
    )
    svg = board_svg(bare)
    assert "bm-guess" in svg and svg.count("<a ") == sum(c.side == "top" for c in board.components[:3])


def test_pads_match_kicads_mask_plot(board):
    """Every top copper pad drawn sits in an opening of KiCad's F.Mask plot (pad_to_mask_clearance 0)."""
    from rendkit import raster, require_render

    require_render()
    import numpy as np

    scale = 20
    ours = raster(board_svg(board, "top", drills=False, px_per_mm=scale))
    cut = int(MARGIN_MM * scale)
    ours = ours[cut:-cut, cut:-cut]
    kicad = raster((HERE / "fixtures" / "kicad" / "royalblue54L-F_Mask.svg").read_text("utf-8"), width=ours.shape[1])
    assert kicad.shape == ours.shape
    pads = np.abs(ours - (0xC8, 0xA1, 0x4A)).max(axis=2) < 40
    openings = kicad.max(axis=2) < 80
    precision = (pads & openings).sum() / pads.sum()
    # KiCad's mask also opens the bottom header's through-hole pads and the non-plated holes, which a top
    # copper view leaves out; one jumper pad has no mask opening
    recall = (pads & openings).sum() / openings.sum()
    assert precision > 0.98 and recall > 0.65, (precision, recall)
