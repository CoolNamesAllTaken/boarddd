"""Every KiCad demo board reads and validates (local check: set KICAD_DEMOS to KiCad's demos folder, e.g.
/usr/share/kicad/demos, the default; skipped when it isn't there)."""

import os
from pathlib import Path

import pytest

from boarddd.io.kicad import read_kicad_pcb
from boarddd.validate import validate_board

DEMOS = Path(os.environ.get("KICAD_DEMOS", "/usr/share/kicad/demos"))
BOARDS = sorted(p for p in DEMOS.rglob("*.kicad_pcb") if p.stat().st_size < 10_000_000) if DEMOS.is_dir() else []


@pytest.mark.skipif(not BOARDS, reason="KiCad demos not installed")
@pytest.mark.parametrize("path", BOARDS, ids=[p.stem for p in BOARDS])
def test_demo_board(path):
    board = read_kicad_pcb(path)
    assert validate_board(board.to_dict()) == []
    assert board.outline is not None and board.components
    pads = sum(len(board.footprints[c.footprint].pads) for c in board.components)
    assert pads > 0
