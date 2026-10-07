"""read_package on public fab exports: royalblue54L_feather's fab/ against its golden board.json, pic_programmer.

The golden (fixtures/royalblue54L_feather/board.json) was hand-built from the .kicad_pcb, which knows
more than a fab package does. These tests pin what a Gerber/Excellon/gbrjob/pos package CAN give and
that it equals the golden there; docs/readers.md lists every difference and why.
"""

import io
import json
import math
import zipfile

import pytest

from boarddd.io import package
from boarddd.io.package import read_files, read_package
from boarddd.model import Board
from boarddd.validate import validate_board

from conftest import FIXTURES, RB_FAB, TEST_FIXTURES


@pytest.fixture(scope="module")
def board():
    return read_package(RB_FAB).to_dict()


@pytest.fixture(scope="module")
def gold():
    return json.loads((FIXTURES / "royalblue54L_feather" / "board.json").read_text("utf-8"))


DNP = ["R2", "R3"]


def _area(pts):
    return sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(pts, pts[1:] + pts[:1], strict=True)) / 2


def _to_segments(p, poly):
    """Distance from p to the closed polyline poly."""
    best = math.inf
    for a, b in zip(poly, poly[1:] + poly[:1], strict=True):
        dx, dy = b[0] - a[0], b[1] - a[1]
        length = dx * dx + dy * dy
        t = 0.0 if length == 0 else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / length))
        best = min(best, math.hypot(a[0] + t * dx - p[0], a[1] + t * dy - p[1]))
    return best


def test_the_model_is_valid_and_round_trips(board):
    assert validate_board(board) == []
    assert Board.from_dict(board).to_dict() == board


def test_name_revision_and_provenance(board, gold):
    assert board["name"] == gold["name"] == "RoyalBlue54L-Feather"
    assert board["revision"] is gold["revision"] is None  # the gbrjob says "rev?": no revision
    src = board["source"]
    assert src["kind"] == "gerber" and src["reader"] == "boarddd.io.package"
    assert src["generator"] == "KiCad Pcbnew 10.0.6"
    assert src["created"] == "2026-10-07T08:23:33+00:00"  # the gbrjob's CreationDate


def test_source_files_are_the_goldens_fab_files(board, gold):
    """Same files, roles, sides and hashes; the golden's paths are relative to the fixture (fab/...)."""
    ours = [{**f, "path": "fab/" + f["path"]} for f in board["source"]["files"]]
    theirs = [f for f in gold["source"]["files"] if f["path"].startswith("fab/")]
    assert ours == theirs
    assert len(ours) == 19


def test_layers_equal_the_golden_but_for_the_function_spelling(board, gold):
    """Ids, roles, sides, order, format, polarity and plating match; `function` is the file's own %TF
    (`Soldermask,Top`), where the golden copied the gbrjob's FilesAttributes (`SolderMask,Top`)."""
    keys = ("id", "role", "side", "order", "format", "polarity", "plated")
    assert [{k: la[k] for k in keys} for la in board["layers"]] == [{k: la[k] for k in keys} for la in gold["layers"]]
    assert [["fab/" + f for f in la["files"]] for la in board["layers"]] == [la["files"] for la in gold["layers"]]
    differ = {la["id"]: (la["function"], g["function"]) for la, g in zip(board["layers"], gold["layers"], strict=True)}
    differ = {k: v for k, v in differ.items() if v[0] != v[1]}
    assert differ == {
        "Edge.Cuts": ("Profile,NP", "Profile"),
        "F.Paste": ("Paste,Top", "SolderPaste,Top"),
        "B.Paste": ("Paste,Bot", "SolderPaste,Bot"),
        "F.Mask": ("Soldermask,Top", "SolderMask,Top"),
        "B.Mask": ("Soldermask,Bot", "SolderMask,Bot"),
    }


def test_drills_equal_the_golden(board, gold):
    """All 278 holes, both drill files, zero-diameter placeholder vias and a stacked pair included."""
    assert board["drills"] == gold["drills"]
    assert len(board["drills"]) == 278
    assert any("183 holes have a 0 mm drill" in w for w in board["warnings"])


def test_outline_is_the_goldens_board_edge(board, gold):
    """The same counter-clockwise loop, flattened differently: KiCad's Gerber arcs at 48 points per
    turn vs the golden's 72 per arc. Same box, same start, area within 0.03 mm^2, every point within
    5 um of the other polygon."""
    ours, theirs = board["outline"]["board"], gold["outline"]["board"]
    assert board["outline"]["cutouts"] == gold["outline"]["cutouts"] == []
    assert board["outline"]["approximate"] is False
    assert _area(ours) > 0 and _area(theirs) > 0
    assert abs(_area(ours) - _area(theirs)) < 0.03
    for axis in (0, 1):
        assert min(p[axis] for p in ours) == min(p[axis] for p in theirs)
        assert max(p[axis] for p in ours) == max(p[axis] for p in theirs)
    assert ours[0] == theirs[0] == [177.52, -96.09]
    assert max(_to_segments(p, theirs) for p in ours) < 0.005
    assert max(_to_segments(p, ours) for p in theirs) < 0.005


def test_stackup_from_the_job_file(board, gold):
    """Build, colours and the physical layers match; names are the gbrjob's, and it has no
    dielectric constants or loss tangents for this board."""
    st, gst = board["stackup"], gold["stackup"]
    for key in ("thickness", "copper_layers", "finish", "mask_color", "silk_color"):
        assert st[key] == gst[key], key
    keys = ("kind", "side", "thickness", "material", "color", "layer")
    assert [{k: la[k] for k in keys} for la in st["layers"]] == [{k: la[k] for k in keys} for la in gst["layers"]]
    assert [la["name"] for la in st["layers"]][:5] == [
        "Top Silk Screen",
        "Top Solder Paste",
        "Top Solder Mask",
        "F.Cu",
        "F.Cu/In1.Cu",
    ]
    assert all(la["epsilon_r"] is None and la["loss_tangent"] is None for la in st["layers"])


def test_components_are_the_goldens_placed_parts(board, gold):
    """The pos file lists the 50 parts the golden marks in_pos; where they sit, how they turn,
    their side and value are the golden's to the micron. The footprint is the pos file's package
    name (`C_0402_1005Metric`), the golden's the library id (`Capacitor_SMD:C_0402_1005Metric`).
    A pos file has no DNP column, so the golden's two DNP parts (R2, R3: populate false) read as
    populated."""
    placed = [c for c in gold["components"] if c["in_pos"]]
    assert [c["ref"] for c in board["components"]] == [c["ref"] for c in placed]
    for ours, theirs in zip(board["components"], placed, strict=True):
        for key in ("side", "x", "y", "rotation", "value", "in_pos"):
            assert ours[key] == theirs[key], (ours["ref"], key)
        assert theirs["footprint"].split(":", 1)[1] == ours["footprint"]
        assert ours["mount"] is None and ours["models"] == [] and ours["mpn"] == [] and ours["populate"]
    assert sorted(c["ref"] for c in placed if not c["populate"]) == DNP
    assert sum(c["side"] == "bottom" for c in board["components"]) == sum(c["side"] == "bottom" for c in placed)


def test_what_a_fab_package_cannot_say_is_left_empty(board, gold):
    assert board["footprints"] == {} and len(gold["footprints"]) == 27
    assert board["origin"] == {"aux": None, "grid": None, "drill": None}
    assert gold["origin"]["aux"] == [119.1, -116.41]
    assert board["panel"] is None and board["meta"] == {}


def test_a_zip_reads_the_same_as_the_folder(board, tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(RB_FAB.iterdir()):
            zf.writestr(f"gerbers/{f.name}", f.read_bytes())
    path = tmp_path / "RoyalBlue54L-Feather.zip"
    path.write_bytes(buf.getvalue())
    zipped = read_package(path).to_dict()
    for f in zipped["source"]["files"]:
        f["path"] = f["path"].removeprefix("gerbers/")
    for la in zipped["layers"]:
        la["files"] = [p.removeprefix("gerbers/") for p in la["files"]]
    assert zipped == board


def test_pic_programmer_without_a_job_file():
    """No gbrjob: the name is the folder's, the copper count comes from the layers, the outline is
    the largest loop, and nothing is invented for the build."""
    b = read_package(TEST_FIXTURES / "pic_programmer" / "base").to_dict()
    assert validate_board(b) == []
    assert b["name"] == "base" and b["source"]["generator"] is None
    assert [(la["id"], la["order"]) for la in b["layers"]] == [
        ("F.Cu", 1),
        ("B.Cu", 2),
        ("Edge.Cuts", 3),
        ("F.Silkscreen", 5),
        ("B.Silkscreen", 5),
        ("F.Mask", 6),
        ("B.Mask", 6),
        ("PTH", 9),
        ("NPTH", 10),
    ]
    assert b["stackup"]["copper_layers"] == 2 and b["stackup"]["thickness"] is None
    assert len(b["drills"]) == 251 and b["components"] == []
    assert _area(b["outline"]["board"]) > 0


def test_the_head_revision_moves_one_mounting_hole():
    """pic_programmer head moved P101 from (77.47, 135.89) to (80.47, 133.89) (KiCad frame, y down)."""

    def holes(rev):
        return {
            (d["x"], d["y"], d["diameter"])
            for d in read_package(TEST_FIXTURES / "pic_programmer" / rev).to_dict()["drills"]
        }

    base, head = holes("base"), holes("head")
    gone, new = base - head, head - base
    assert {(x, -y) for x, y, _ in gone} >= {(77.47, 135.89)}
    assert {(x, -y) for x, y, _ in new} >= {(80.47, 133.89)}


def test_bom_enriches_the_placements():
    pos = (RB_FAB / "pos.csv").read_bytes()
    bom = (
        b"Reference,Value,Footprint,Quantity,Populate,Manufacturer,MPN\n"
        b'"C1","100nF","C_0402_1005Metric",1,yes,SYN Corp,SYN-C-100N\n'
        b'"C2,C3","10uF","C_0603_1608Metric",2,no,,\n'
        b'"X99","?","?",1,yes,,\n'
    )
    b = read_files({"x-pos.csv": pos, "x-bom.csv": bom}, name="x")
    by_ref = {c.ref: c for c in b.components}
    assert [(p.mpn, p.manufacturer) for p in by_ref["C1"].mpn] == [("SYN-C-100N", "SYN Corp")]
    assert by_ref["C2"].populate is False and by_ref["C3"].populate is False and by_ref["C1"].populate
    assert any("1 BOM designators have no placement (X99)" in w for w in b.warnings)
    assert b.source.files[0].role == "bom" and b.source.files[1].role == "placement"


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (("copper", "top", 1, 4), "F.Cu"),
        (("copper", "inner", 3, 4), "In2.Cu"),
        (("copper", "bottom", 4, 4), "B.Cu"),
        (("copper", "bottom", None, None), "B.Cu"),
        (("mask", "bottom", None, None), "B.Mask"),
        (("silk", "top", None, None), "F.Silkscreen"),
        (("outline", "none", None, None), "Edge.Cuts"),
    ],
)
def test_layer_ids(args, expected):
    assert package.layer_id(*args) == expected


def test_drill_ids_follow_plating():
    assert package.layer_id("drill", "none", None, None, plated=True) == "PTH"
    assert package.layer_id("drill", "none", None, None, plated=False) == "NPTH"
    assert package.layer_id("drill", "none", None, None) == "Drill"
