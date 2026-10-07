"""The royalblue54L_feather golden board: built by its script, consistent with the fab files it lists."""

import hashlib
import math
import subprocess
import sys

from conftest import FIXTURES

RB = FIXTURES / "royalblue54L_feather"


def footprint_to_board(c, fx, fy):
    """docs/model.md placement: p = c + R(rotation) q, q = (fx, -fy) top / (fx, fy) bottom."""
    qy = fy if c["side"] == "bottom" else -fy
    a = math.radians(c["rotation"])
    return c["x"] + math.cos(a) * fx - math.sin(a) * qy, c["y"] + math.sin(a) * fx + math.cos(a) * qy


def test_script_reproduces_board_json():
    r = subprocess.run([sys.executable, str(RB / "make_board.py"), "--check"], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_source_files_exist_with_their_hashes(golden):
    for f in golden["source"]["files"]:
        assert hashlib.sha256((RB / f["path"]).read_bytes()).hexdigest() == f["sha256"], f["path"]
    for layer in golden["layers"]:
        for path in layer["files"]:
            assert (RB / path).exists()


def test_board_summary(golden):
    xs = [p[0] for p in golden["outline"]["board"]]
    ys = [p[1] for p in golden["outline"]["board"]]
    assert math.isclose(max(xs) - min(xs), 58.42, abs_tol=1e-6) and math.isclose(max(ys) - min(ys), 22.86, abs_tol=1e-6)
    pts = golden["outline"]["board"]
    area2 = sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(pts, pts[1:] + pts[:1], strict=True))
    assert area2 > 0, "outer edge must be counter-clockwise"
    st = golden["stackup"]
    assert (st["thickness"], st["copper_layers"], st["finish"]) == (1.6, 8, "ENIG")
    assert st["mask_color"] == {"top": "Blue", "bottom": "Blue"} and st["silk_color"]["top"] == "White"
    copper = [la for la in golden["layers"] if la["role"] == "copper"]
    assert [la["order"] for la in copper] == list(range(1, 9))
    assert [la["side"] for la in copper] == ["top"] + ["inner"] * 6 + ["bottom"]
    comps = golden["components"]
    assert len(comps) == 71 and sum(c["in_pos"] for c in comps) == 50
    assert sum(c["side"] == "bottom" for c in comps) == 15
    assert {c["ref"] for c in comps if not c["populate"]} >= {"H1", "H2", "H3", "H4"}


def test_thru_hole_pads_land_on_the_drills(golden):
    """Footprints are stored in library orientation; placing them reproduces every component hole of the drill files
    (this checks the bottom-side flip-back too: J1 is a 16-pin header on the bottom)."""
    holes = {True: [], False: []}
    for d in golden["drills"]:
        if d["function"] != "via":
            # a slot (oval pad hole) is written end to end: its centre is the midpoint
            x2, y2 = (d["x2"], d["y2"]) if d["x2"] is not None else (d["x"], d["y"])
            holes[d["plated"]].append(((d["x"] + x2) / 2, (d["y"] + y2) / 2))
    placed = {True: [], False: []}
    refs_bottom = set()
    for c in golden["components"]:
        for pad in golden["footprints"][c["footprint"]]["pads"]:
            if pad["type"] in ("thru_hole", "np_thru_hole"):
                placed[pad["type"] == "thru_hole"].append(footprint_to_board(c, pad["at"][0], pad["at"][1]))
                if c["side"] == "bottom":
                    refs_bottom.add(c["ref"])
    assert "J1" in refs_bottom
    for plated in (True, False):
        assert len(placed[plated]) == len(holes[plated])
        for p in placed[plated]:
            assert min(math.dist(p, h) for h in holes[plated]) < 1e-3, (plated, p)
