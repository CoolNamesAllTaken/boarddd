"""
Python and JS read the same files the same way.

`boarddd.io.excellon.parse` vs `boarddd/gerber`'s `parseExcellon` (src/gerber/drills.js), and
`boarddd.io.outline` (contours + pick_board + cutouts) vs `boardOutline` (src/gerber/outline.js),
on the shared fixtures. The JS side runs through `parity_dump.mjs` under node; the test is skipped
when node is not installed.
"""

from __future__ import annotations

import json
import math
import shutil
import subprocess
from pathlib import Path

import pytest

from boarddd.io import excellon, gbrjob, outline

from conftest import FIXTURES, GENERATED, RB_FAB, REPO, TEST_FIXTURES

NODE = shutil.which("node")
DUMP = Path(__file__).with_name("parity_dump.mjs")
EPS = 1e-9

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed: the JS side of the parity tests needs it")

DRILLS = [
    RB_FAB / "RoyalBlue54L-Feather-PTH.drl",
    RB_FAB / "RoyalBlue54L-Feather-NPTH.drl",
    *(
        TEST_FIXTURES / "pic_programmer" / side / f"pic_programmer-{kind}.drl"
        for side in ("base", "head")
        for kind in ("PTH", "NPTH")
    ),
    *(
        TEST_FIXTURES / board / f"slots-{kind}.drl"
        for board in ("slots-board", "slots-board-base")
        for kind in ("PTH", "NPTH")
    ),
    *(
        GENERATED / "drill" / form / "slots.drl"
        for form in ("route", "alternate", "inch-suppressleading", "inch-suppresstrailing", "inch-keep")
    ),
]

SHEET = GENERATED / "outline" / "sheet"
OUTLINES = [
    (RB_FAB / "RoyalBlue54L-Feather-Edge_Cuts.gbr", RB_FAB / "RoyalBlue54L-Feather-job.gbrjob"),
    (SHEET / "RoyalBlue54L-Feather-Edge_Cuts.gbr", SHEET / "RoyalBlue54L-Feather-job.gbrjob"),
    (TEST_FIXTURES / "pic_programmer" / "base" / "pic_programmer-Edge_Cuts.gbr", None),
    (TEST_FIXTURES / "pic_programmer" / "head" / "pic_programmer-Edge_Cuts.gbr", None),
    (TEST_FIXTURES / "slots-board" / "slots-Edge_Cuts.gbr", None),
]


def _rel(path: Path) -> str:
    return path.relative_to(REPO).as_posix()


def _node(*args: str):
    out = subprocess.run([NODE, str(DUMP), *args], capture_output=True, text=True, check=True, cwd=REPO)
    return json.loads(out.stdout)


def _same(a, b) -> bool:
    return (a is None and b is None) or (a is not None and b is not None and abs(a - b) <= EPS)


@pytest.fixture(scope="module")
def js_drills():
    return dict(zip(DRILLS, _node("drills", *map(str, DRILLS)), strict=True))


def test_the_fixtures_exist():
    assert FIXTURES.is_dir()
    for path in DRILLS + [p for pair in OUTLINES for p in pair if p is not None]:
        assert path.is_file(), path


@pytest.mark.parametrize("path", DRILLS, ids=_rel)
def test_excellon_matches_parse_excellon(path, js_drills):
    """Same holes, same order: position, diameter, plating and both slot ends, to 1e-9 mm."""
    ours = excellon.parse(path.read_text("utf-8"))
    theirs = js_drills[path]

    assert len(ours) == len(theirs) > 0
    for i, (h, j) in enumerate(zip(ours, theirs, strict=True)):
        assert h.plated == j["plated"], (i, h, j)
        for name in ("x", "y", "diameter", "x2", "y2"):
            assert _same(getattr(h, name), j[name]), (i, name, h, j)


def _signed_area(points) -> float:
    return sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(points, points[1:] + points[:1], strict=True)) / 2


def _wound(points, ccw: bool):
    pts = [tuple(p) for p in points]
    return pts if (_signed_area(pts) > 0) == ccw else pts[::-1]


def _same_ring(ours, theirs) -> float:
    """The largest distance from a point of one ring to the nearest of the other (both ways)."""
    assert len(ours) == len(theirs)

    def far(a, b):
        return max(min(math.dist(p, q) for q in b) for p in a)

    return max(far(ours, theirs), far(theirs, ours))


def _python_outline(text: str, size):
    found = outline.contours(text)
    board = outline.pick_board(found, *(size or (None, None)))
    if board is None:
        return None
    return {
        "outer": _wound(board.points, True),
        "holes": [_wound(c.points, False) for c in outline.cutouts(found, board)],
    }


def _check_outline(text: str, size, js) -> None:
    ours = _python_outline(text, size)
    assert (ours is None) == (js is None)
    if ours is None:
        return
    theirs_outer = [tuple(p) for p in js["outer"]]
    assert _signed_area(theirs_outer) > 0, "JS winds the outer ring counter-clockwise"
    assert _same_ring(ours["outer"], theirs_outer) <= EPS
    assert len(ours["holes"]) == len(js["holes"])
    for hole, theirs in zip(ours["holes"], js["holes"], strict=True):
        theirs = [tuple(p) for p in theirs]
        assert _signed_area(theirs) < 0, "JS winds holes clockwise"
        assert _same_ring(hole, theirs) <= EPS


@pytest.mark.parametrize("edge,job", OUTLINES, ids=lambda p: _rel(p) if p else "no-job")
def test_outline_matches_board_outline(edge, job):
    size = gbrjob.board_size(job.read_text("utf-8")) if job else None
    js = _node("outline", str(edge), *(map(str, size) if size else ()))

    _check_outline(edge.read_text("utf-8"), size, js)
    if job:
        assert js["bounds"]["maxX"] - js["bounds"]["minX"] == pytest.approx(58.42, abs=1e-6)


def test_outline_with_cutouts_matches(tmp_path):
    """A board with a slot drawn inside it and a stray loop outside: same board, same one hole."""

    def rect(x0, y0, x1, y1):
        corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]
        return "".join(
            f"X{int(ax * 1e6)}Y{int(ay * 1e6)}D02*\nX{int(bx * 1e6)}Y{int(by * 1e6)}D01*\n"
            for (ax, ay), (bx, by) in zip(corners, corners[1:], strict=False)
        )

    text = (
        "%FSLAX46Y46*%\n%MOMM*%\n%ADD10C,0.050000*%\nD10*\n"
        + rect(0, 0, 20, 30)
        + rect(5, 5, 8, 9)
        + rect(5, 5, 8, 9)  # drawn twice: one hole
        + rect(40, 0, 45, 5)
        + "M02*\n"
    )
    path = tmp_path / "cutouts-Edge_Cuts.gbr"
    path.write_text(text)
    js = _node("outline", str(path), "20", "30")

    assert len(js["holes"]) == 1
    _check_outline(text, (20.0, 30.0), js)
