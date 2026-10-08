"""The whole royalblue54L_feather board STEP (KiCad's demo, stock 3D models): split against its pos file, checked
against boarddd's board model (fixtures/royalblue54L_feather/board.json) and against boarddd's browser path
(src/models: occt-import-js, mapNodesToRefs, splitBoardBodies, measureBoard) on the same file."""

import json
import shutil
import subprocess

import pytest
from stepkit import HERE, REPO, REQUIRED, ROYALBLUE_POS, require_occ, royalblue

require_occ()

from boarddd.step.measure import bbox  # noqa: E402
from boarddd.step.split import _location, split  # noqa: E402

GOLDEN = json.loads((REPO / "fixtures" / "royalblue54L_feather" / "board.json").read_text())
#: In the pos file but with no 3D model in KiCad's stock library: nothing to split.
NO_MODEL = ["SW1", "SW2", "U6"]


@pytest.fixture(scope="module")
def step(tmp_path_factory):
    return royalblue(tmp_path_factory)


@pytest.fixture(scope="module")
def assembly(step):
    return split(step, pos=ROYALBLUE_POS, cache=False)


def test_split(assembly):
    assert assembly.read_from == "text"
    assert len(assembly.components) == 47 and len(assembly.models) == 17
    assert assembly.missing_models == NO_MODEL
    board = assembly.board
    assert (board.name, board.source, board.top_z, board.bottom_z) == ("RoyalBlue54L-Feather_PCB", "kicad", 1.51, 0.0)
    assert assembly.seat_z == pytest.approx(0.085)
    fit = assembly.pos_fit
    assert fit.ok and fit.method == "designators" and fit.residual_mm < 1e-6 and fit.matched == 47
    # J4 (a connector modeled 4.5 mm from its anchor) and two others are left out of the fit, named
    assert dict(fit.elsewhere) == pytest.approx({"J4": 4.5, "J5": 1.4, "J6": 0.07}, abs=1e-3)
    assert assembly.components["J4"].offset[:2] == pytest.approx((0.0, -4.5), abs=1e-6)


def test_components_sit_where_the_board_model_says(assembly):
    golden = {c["ref"]: c for c in GOLDEN["components"]}
    for ref, component in assembly.components.items():
        want = golden[ref]
        assert component.side == want["side"], ref
        assert component.transform[:2, 3] == pytest.approx((want["x"], want["y"]), abs=1e-6), ref
        assert (component.rotation_deg - want["rotation"]) % 360 == pytest.approx(0.0, abs=1e-6), ref
        assert component.frame_source == "pos" and component.measurements is not None, ref
    # royalblue's 15 bottom-side parts (headers, test points) carry no 3D model; the tiny board covers
    # the turned-over frame (test_step_split.py)
    assert all(c.side == "top" and c.transform[2, 2] == pytest.approx(1.0) for c in assembly.components.values())


def test_measurements(assembly):
    c1 = assembly.components["C1"].measurements  # 0402 capacitor
    assert c1.size == pytest.approx((1.0, 0.5, 0.5), abs=0.01) and c1.own_height == pytest.approx(0.5, abs=0.01)
    u2 = assembly.components["U2"].measurements  # 5 x 5 QFN
    assert u2.size[:2] == pytest.approx((5.0, 5.0), abs=0.01)
    # every instance of one model shares it, and its fingerprint
    by_model = {}
    for component in assembly.components.values():
        by_model.setdefault(component.model.key, set()).add(component.product)
    assert all(len(products) == 1 for products in by_model.values())


def test_exports(assembly):
    u2 = assembly.components["U2"]
    assert u2.glb_bytes()[:4] == b"glTF"
    (again,) = split(u2.step_bytes(), cache=False).components.values()
    assert again.fingerprint.frame_key == u2.fingerprint.frame_key


def _box_in_board(component):
    low, high = bbox(component.model.shape.Moved(_location(component.transform)))
    return low, high


@pytest.mark.skipif(
    not REQUIRED and (not shutil.which("node") or not (REPO / "node_modules" / "occt-import-js").exists()),
    reason="needs node and npm ci (occt-import-js)",
)
def test_parity_with_the_browser_path(step, assembly):
    """src/models on the same file: the same designators, board surfaces and component boxes."""
    out = subprocess.run(
        ["node", str(HERE / "js_step_dump.mjs"), str(step), str(ROYALBLUE_POS)],
        capture_output=True,
        text=True,
        check=True,
        cwd=REPO,
        timeout=600,
    )
    js = json.loads(out.stdout)
    assert sorted(js["refs"]) == sorted(assembly.components)
    assert js["unmatched"] == NO_MODEL and js["leftover"] == []
    assert js["bodies"] == ["RoyalBlue54L-Feather_PCB"]
    board = assembly.board
    assert (js["board"]["bottom"], js["board"]["top"]) == pytest.approx((board.bottom_z, board.top_z), abs=1e-4)
    worst = 0.0
    for ref, component in assembly.components.items():
        low, high = _box_in_board(component)
        theirs = js["refs"][ref]
        # occt-import-js tessellates: its box can sit a little inside a curved face
        worst = max(worst, *(abs(a - b) for a, b in zip((*low, *high), (*theirs["min"], *theirs["max"]), strict=True)))
    assert worst < 0.05, worst
