"""boarddd.step.glb: the tiny fixture board as a board GLB (see fixtures_occ/make_board.py)."""

from __future__ import annotations

import pytest
from stepkit import require_occ

require_occ()

import numpy as np  # noqa: E402
from stepkit import TINY  # noqa: E402

from boarddd.step import glb  # noqa: E402
from boarddd.step.cache import Store  # noqa: E402

REFS = {"R1", "R2", "R3", "R4", "R5", "U1", "U2", "U3", "C1", "D1", "Q1"}


@pytest.fixture(scope="module")
def cache(tmp_path_factory):
    return Store(tmp_path_factory.mktemp("glb-cache"))


@pytest.fixture(scope="module")
def draco(cache):
    pytest.importorskip("DracoPy")
    return glb.convert(TINY, cache=cache)


@pytest.fixture(scope="module")
def quantized(cache):
    return glb.convert(TINY, cache=cache, compression="quantize")


def _instances(data: bytes) -> dict[str, dict]:
    document, _ = glb.read_glb(data)
    nodes = document["nodes"]
    root = nodes[0]
    assert root["name"] == "boarddd-board"
    out = {}
    for index in root["children"]:
        node = nodes[index]
        mesh = node.get("mesh")
        if mesh is None:  # quantized: the mesh hangs off the dequantization node
            mesh = nodes[node["children"][0]]["mesh"]
        out[node["name"]] = {"mesh": mesh, "product": node["extras"]["product"]}
    return out


def test_one_node_per_instance_named_by_designator(quantized):
    names = set(_instances(quantized.glbs["full"]))
    assert REFS <= names
    # The board body is an instance too (KiCad's `=>[...]` node), for the viewer's board split.
    assert any(name.startswith("=>[") for name in names)
    assert quantized.source == "text"


def test_repeated_instances_share_one_mesh(quantized):
    found = _instances(quantized.glbs["full"])
    assert found["R1"]["mesh"] == found["R2"]["mesh"] == found["R3"]["mesh"] == found["R5"]["mesh"]
    assert found["U1"]["mesh"] == found["U3"]["mesh"]
    assert found["R4"]["mesh"] != found["R1"]["mesh"]
    document, _ = glb.read_glb(quantized.glbs["full"])
    assert len(document["meshes"]) == quantized.products < quantized.nodes
    full = quantized.triangles["full"]
    assert full["stored"] < full["drawn"]


def test_primitives_are_merged_per_color(quantized):
    document, _ = glb.read_glb(quantized.glbs["full"])
    for mesh in document["meshes"]:
        materials = [primitive["material"] for primitive in mesh["primitives"]]
        assert len(materials) == len(set(materials))
    for material in document["materials"]:
        assert len(material["extras"]["step_color"]) == 3


def test_lods_share_structure_and_coarse_is_lighter(quantized):
    coarse, full = (glb.read_glb(quantized.glbs[name])[0] for name in ("coarse", "full"))
    assert [n.get("name") for n in coarse["nodes"]] == [n.get("name") for n in full["nodes"]]
    assert [len(m["primitives"]) for m in coarse["meshes"]] == [len(m["primitives"]) for m in full["meshes"]]
    assert quantized.triangles["coarse"]["drawn"] < quantized.triangles["full"]["drawn"]
    assert coarse["nodes"][0]["extras"]["lod"] == "coarse"


def test_boxes_match_the_whole_file_read(quantized):
    """Products cut out of the text land where OpenCascade's own read of the whole file puts them."""
    instances, products = glb._from_whole(TINY.read_bytes(), (glb.LODS[-1],), None)
    whole, _ = glb._write(instances, products, "full", {}, "quantize")
    expected, got = glb.node_boxes(whole), glb.node_boxes(quantized.glbs["full"])
    for ref in REFS:
        assert np.allclose(got[ref][0], expected[ref][0], atol=0.02), ref
        assert np.allclose(got[ref][1], expected[ref][1], atol=0.02), ref


def test_bottom_side_part_is_under_the_board(quantized):
    boxes = glb.node_boxes(quantized.glbs["full"])
    assert boxes["R5"][1][2] < 0.1 < boxes["R1"][0][2] + 0.1
    assert boxes["R1"][0][2] > 1.0


def test_draco_decodes_to_the_same_boxes(draco, quantized):
    a, b = glb.node_boxes(draco.glbs["full"]), glb.node_boxes(quantized.glbs["full"])
    assert set(a) == set(b)
    for name in a:
        assert np.allclose(a[name][0], b[name][0], atol=0.01), name
        assert np.allclose(a[name][1], b[name][1], atol=0.01), name
    document, _ = glb.read_glb(draco.glbs["full"])
    assert document["extensionsRequired"] == ["KHR_draco_mesh_compression"]
    assert len(draco.glbs["full"]) < len(quantized.glbs["full"])


def test_tessellations_come_from_the_cache(cache, monkeypatch):
    glb.convert(TINY, cache=cache, compression="quantize")

    def boom(*args, **kwargs):
        raise AssertionError("meshed again")

    monkeypatch.setattr(glb, "_mesh_doc", boom)
    again = glb.convert(TINY, cache=cache, compression="quantize")
    assert REFS <= set(_instances(again.glbs["full"]))


def test_cache_key_has_converter_version_and_lods(monkeypatch):
    key = glb._cache_key("abc", glb.LODS)
    assert key != glb._cache_key("abc", (glb.LODS[0],))
    monkeypatch.setattr(glb, "VERSION", "boarddd-board-glb/999")
    assert glb._cache_key("abc", glb.LODS) != key


def test_root_turns_mm_z_up_into_metres_y_up(quantized):
    document, _ = glb.read_glb(quantized.glbs["full"])
    matrix = glb._node_matrix(document["nodes"][0])
    assert np.allclose(matrix[:3, :3] @ [0, 0, 1], [0, 0.001, 0])
    meta = document["nodes"][0]["extras"]
    assert meta["units"] == "mm" and meta["up"] == "z" and meta["format"] == glb.VERSION


def test_cli_writes_each_lod(tmp_path):
    assert glb._main([str(TINY), "--out", str(tmp_path), "--compression", "quantize"]) == 0
    assert (tmp_path / "coarse.glb").stat().st_size > 0 and (tmp_path / "full.glb").stat().st_size > 0
    assert (tmp_path / "summary.json").exists()
