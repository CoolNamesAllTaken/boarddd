"""
A board STEP as a compact GLB for viewers: tessellated once, each distinct model stored once.

A browser reading a board STEP with its own CAD kernel (occt-import-js) reads the whole file, and a
big board is tens of megabytes of text and gigabytes of WebAssembly heap. This does that work once,
on a server, and writes what the viewer needs:

* **One mesh per distinct product**, used by every instance of it (glTF's own instancing: nodes
  share a mesh, three.js shares the geometry). A board's hundred 0402 resistors are one mesh.
* **One primitive per color** within a product: faces of one color are merged, so a part is a
  draw call per color.
* **One node per top-level instance**, named as the STEP names it (KiCad: the designator), with the
  instance's placement as its matrix. `extras` carry the product name.
* **Levels of detail**: `convert` returns one GLB per entry of `lods` (coarse first), identical
  in structure (same nodes, meshes and primitives in the same order) and different only in
  tessellation, so a viewer can paint the coarse one and swap the fine geometry in.
* **Quantized** (KHR_mesh_quantization): int16 positions, int8 normals, 16-bit indices where they
  fit. About a third of float32; deflate/brotli on the wire takes it further.

Units and axes: the scene's one root node (`boarddd-board`) turns millimetres, Z up (the STEP's
frame) into glTF's metres, Y up. Below it everything is in the STEP's own millimetres, Z up, so a
viewer working in the STEP's frame takes the root's children as they are.

Reading: from the text when the board is an assembly with designators (`index`: each distinct
product cut out and read alone, never the tracks and zones), else the whole file by OpenCascade.
Tessellation per product is cached in the model cache (`cache`) when one is given.

Colors: `extras.step_color` on each material is OpenCascade's color as the STEP gives it (what
occt-import-js reports), so a viewer can color a part exactly as it would from the STEP.
"""

from __future__ import annotations

import hashlib
import json
import os
import struct
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from ._extra import require

require(__name__)

import numpy as np  # noqa: E402

from . import index as stepindex  # noqa: E402
from . import occ  # noqa: E402
from .cache import Store, open_store  # noqa: E402

__all__ = ["VERSION", "LODS", "Lod", "Result", "convert", "read_glb", "node_boxes"]

#: Bump when the output changes for the same input: part of every cache key and of `Result.version`.
VERSION = "boarddd-board-glb/1"


@dataclass(frozen=True)
class Lod:
    name: str
    #: BRepMesh linear deflection (mm) and angular deflection (radians).
    linear: float
    angular: float


#: Coarse first. "full" is close to what occt-import-js gives a board in the browser.
LODS = (Lod("coarse", 0.2, 1.0), Lod("full", 0.02, 0.5))


@dataclass
class Result:
    version: str
    #: lod name -> GLB bytes, in `lods` order.
    glbs: dict[str, bytes]
    nodes: int
    products: int
    #: lod name -> triangles drawn (all instances) and stored (each product once).
    triangles: dict[str, dict[str, int]]
    seconds: float
    source: str  # 'text' (products cut out) or 'whole' (read whole)
    warnings: list[str] = field(default_factory=list)


@dataclass
class _Part:
    """A product's geometry at one level of detail, in the product's frame (mm, Z up)."""

    #: (rgba, positions float32 (n, 3), normals float32 (n, 3), indices uint32 (m,))
    primitives: list


# ─── Reading OCCT's glTF back ────────────────────────────────────────────────

_COMPONENT = {5120: np.int8, 5121: np.uint8, 5122: np.int16, 5123: np.uint16, 5125: np.uint32, 5126: np.float32}
_WIDTH = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}


def read_glb(data: bytes) -> tuple[dict, bytes]:
    """(json, binary chunk) of a GLB."""
    magic, _version, length = struct.unpack_from("<4sII", data, 0)
    if magic != b"glTF":
        raise ValueError("not a GLB")
    offset, document, binary = 12, None, b""
    while offset < length:
        size, kind = struct.unpack_from("<I4s", data, offset)
        chunk = data[offset + 8 : offset + 8 + size]
        if kind == b"JSON":
            document = json.loads(chunk)
        elif kind == b"BIN\x00":
            binary = chunk
        offset += 8 + size
    if document is None:
        raise ValueError("GLB has no JSON chunk")
    return document, binary


def _accessor(document: dict, binary: bytes, index: int) -> np.ndarray:
    accessor = document["accessors"][index]
    view = document["bufferViews"][accessor["bufferView"]]
    dtype = np.dtype(_COMPONENT[accessor["componentType"]])
    width = _WIDTH[accessor["type"]]
    start = view.get("byteOffset", 0) + accessor.get("byteOffset", 0)
    stride = view.get("byteStride", 0) or dtype.itemsize * width
    count = accessor["count"]
    if stride == dtype.itemsize * width:
        values = np.frombuffer(binary, dtype=dtype, count=count * width, offset=start)
    else:
        raw = np.frombuffer(binary, dtype=np.uint8, count=stride * count, offset=start).reshape(count, stride)
        values = raw[:, : dtype.itemsize * width].copy().view(dtype)
    return values.reshape(count, width) if width > 1 else values


def _node_matrix(node: dict) -> np.ndarray:
    if "matrix" in node:
        return np.array(node["matrix"], dtype=np.float64).reshape(4, 4).T
    matrix = np.eye(4)
    x, y, z, w = node.get("rotation", (0.0, 0.0, 0.0, 1.0))
    matrix[:3, :3] = [
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
    ]
    matrix[:3, :3] *= np.asarray(node.get("scale", (1.0, 1.0, 1.0)))[None, :]
    matrix[:3, 3] = node.get("translation", (0.0, 0.0, 0.0))
    return matrix


def _primitives_of(data: bytes, top: np.ndarray | None = None) -> list:
    """
    Every triangle of a GLB in its scene frame, merged by material color: [(rgba, pos, nrm, idx)].
    `top` premultiplies the scene (OCCT's output axes back to the STEP's).
    """
    document, binary = read_glb(data)
    colors = []
    for material in document.get("materials", []):
        factor = material.get("pbrMetallicRoughness", {}).get("baseColorFactor", [0.8, 0.8, 0.8, 1.0])
        colors.append(tuple(round(float(value), 4) for value in factor))
    buckets: dict[tuple, list] = {}

    def visit(index: int, parent: np.ndarray) -> None:
        node = document["nodes"][index]
        world = parent @ _node_matrix(node)
        if "mesh" in node:
            normal_matrix = np.linalg.inv(world[:3, :3]).T
            for primitive in document["meshes"][node["mesh"]]["primitives"]:
                if primitive.get("mode", 4) != 4:
                    continue
                attributes = primitive["attributes"]
                positions = _accessor(document, binary, attributes["POSITION"]).astype(np.float64)
                positions = positions @ world[:3, :3].T + world[:3, 3]
                if "NORMAL" in attributes:
                    normals = _accessor(document, binary, attributes["NORMAL"]).astype(np.float64) @ normal_matrix.T
                    length = np.linalg.norm(normals, axis=1, keepdims=True)
                    normals = normals / np.where(length > 0, length, 1.0)
                else:
                    normals = None
                if "indices" in primitive:
                    indices = _accessor(document, binary, primitive["indices"]).astype(np.uint32)
                else:
                    indices = np.arange(len(positions), dtype=np.uint32)
                if np.linalg.det(world[:3, :3]) < 0:  # a mirrored instance: keep the faces facing out
                    indices = indices.reshape(-1, 3)[:, ::-1].reshape(-1)
                color = colors[primitive["material"]] if "material" in primitive else (0.8, 0.8, 0.8, 1.0)
                buckets.setdefault(color, []).append((positions, normals, indices))
        for child in node.get("children", []):
            visit(child, world)

    start = top if top is not None else np.eye(4)
    scene = document.get("scenes", [{}])[document.get("scene", 0)] if document.get("scenes") else {}
    for root in scene.get("nodes", range(len(document.get("nodes", [])))):
        visit(root, start)
    merged = []
    for color, pieces in buckets.items():
        offset, positions, normals, indices = 0, [], [], []
        for p, n, i in pieces:
            positions.append(p)
            normals.append(n if n is not None else np.zeros_like(p))
            indices.append(i + offset)
            offset += len(p)
        merged.append(
            (
                color,
                np.concatenate(positions).astype(np.float32),
                np.concatenate(normals).astype(np.float32),
                np.concatenate(indices).astype(np.uint32),
            )
        )
    merged.sort(key=lambda item: item[0])
    return merged


def _decoded(document: dict, binary: bytes, primitive: dict) -> np.ndarray:
    """A primitive's positions (float) in its node's frame, Draco or plain/quantized."""
    draco = primitive.get("extensions", {}).get("KHR_draco_mesh_compression")
    if draco:
        import DracoPy

        view = document["bufferViews"][draco["bufferView"]]
        start = view.get("byteOffset", 0)
        return np.asarray(DracoPy.decode(binary[start : start + view["byteLength"]]).points, dtype=np.float64)
    return _accessor(document, binary, primitive["attributes"]["POSITION"]).astype(np.float64)


def node_boxes(data: bytes) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    """{instance name: (min, max)} of a board GLB's instances, in the STEP frame (mm, Z up)."""
    document, binary = read_glb(data)
    nodes = document["nodes"]
    root = next(i for i, node in enumerate(nodes) if node.get("name") == "boarddd-board")
    boxes: dict[str, tuple[np.ndarray, np.ndarray]] = {}

    def visit(index: int, parent: np.ndarray, name: str) -> None:
        node = nodes[index]
        world = parent @ _node_matrix(node)
        if "mesh" in node:
            for primitive in document["meshes"][node["mesh"]]["primitives"]:
                points = _decoded(document, binary, primitive) @ world[:3, :3].T + world[:3, 3]
                low, high = points.min(axis=0), points.max(axis=0)
                if name in boxes:
                    low, high = np.minimum(low, boxes[name][0]), np.maximum(high, boxes[name][1])
                boxes[name] = (low, high)
        for child in node.get("children", []):
            visit(child, world, name)

    for child in nodes[root].get("children", []):
        visit(child, np.eye(4), nodes[child].get("name", ""))
    return boxes


# ─── Tessellating with OpenCascade ───────────────────────────────────────────


def _mesh_doc(doc: occ.Doc, lod: Lod) -> list:
    """Mesh every free shape of a document at `lod` and read back its triangles (mm, Z up)."""
    from OCP.BRepMesh import BRepMesh_IncrementalMesh
    from OCP.collections import IndexedDataMap_TCollection_AsciiString_TCollection_AsciiString
    from OCP.Message import Message_ProgressRange
    from OCP.RWGltf import RWGltf_CafWriter
    from OCP.RWMesh import RWMesh_CoordinateSystem
    from OCP.TCollection import TCollection_AsciiString

    for label in occ.free_shapes(doc):
        BRepMesh_IncrementalMesh(occ.shape_of(label), lod.linear, False, lod.angular, False)
    with tempfile.TemporaryDirectory() as folder:
        path = os.path.join(folder, "part.glb")
        writer = RWGltf_CafWriter(TCollection_AsciiString(path), True)
        converter = writer.ChangeCoordinateSystemConverter()
        converter.SetInputLengthUnit(0.001)
        converter.SetInputCoordinateSystem(RWMesh_CoordinateSystem.RWMesh_CoordinateSystem_Zup)
        converter.SetOutputLengthUnit(0.001)
        converter.SetOutputCoordinateSystem(RWMesh_CoordinateSystem.RWMesh_CoordinateSystem_Zup)
        info = IndexedDataMap_TCollection_AsciiString_TCollection_AsciiString()
        if not writer.Perform(doc.doc, info, Message_ProgressRange()):
            raise ValueError("glTF export failed")
        data = Path(path).read_bytes()
    return _primitives_of(data)


_CACHE_NAME = "board-glb-{lod}.npz"


def _cache_key(identity: str, lods) -> str:
    return Store.key("board-glb", VERSION, identity, tuple((lod.name, lod.linear, lod.angular) for lod in lods))


def _save(primitives: list) -> bytes:
    import io

    arrays = {}
    for number, (color, positions, normals, indices) in enumerate(primitives):
        arrays[f"c{number}"] = np.asarray(color, dtype=np.float64)
        arrays[f"p{number}"] = positions
        arrays[f"n{number}"] = normals
        arrays[f"i{number}"] = indices
    out = io.BytesIO()
    np.savez_compressed(out, count=np.array([len(primitives)]), **arrays)
    return out.getvalue()


def _load(data: bytes) -> list:
    import io

    found = np.load(io.BytesIO(data))
    return [
        (tuple(float(v) for v in found[f"c{n}"]), found[f"p{n}"], found[f"n{n}"], found[f"i{n}"])
        for n in range(int(found["count"][0]))
    ]


def _tessellate(step_bytes: bytes, identity: str, lods, store: Store | None) -> dict[str, _Part]:
    """A product cut out of a board, at every level of detail (coarse to fine on one document)."""
    key = _cache_key(identity, lods) if store is not None else None
    if key is not None:
        parts = {}
        for lod in lods:
            data = store.read_bytes(key, _CACHE_NAME.format(lod=lod.name))
            if data is None:
                break
            parts[lod.name] = _Part(_load(data))
        else:
            return parts
    doc = occ.read(step_bytes)
    parts = {}
    # Coarse first: BRepMesh refines an existing mesh when asked for a smaller deflection.
    for lod in sorted(lods, key=lambda each: -each.linear):
        parts[lod.name] = _Part(_mesh_doc(doc, lod))
        if key is not None:
            store.write_bytes(key, _CACHE_NAME.format(lod=lod.name), _save(parts[lod.name].primitives))
    return parts


# ─── The board ───────────────────────────────────────────────────────────────


def _text_root(found):
    from .split import _text_root as root_of

    return root_of(found)


def _from_text(data: bytes, lods, store, progress) -> tuple[list, dict] | None:
    """(instances, products) from the board's text, or None when it is not a designator assembly."""
    index = stepindex.Index(data)
    found = stepindex.structure(index)
    if not found.has_geometry or found.root is None:
        return None
    root = _text_root(found)
    occurrences = found.children.get(root, [])
    if not occurrences:
        return None
    instances, products, identities = [], {}, {}
    distinct = []
    for occurrence in occurrences:
        if occurrence.child not in identities:
            identities[occurrence.child] = stepindex.identity(found, occurrence.child)
            distinct.append(occurrence.child)
    total = len(distinct)
    for number, definition in enumerate(distinct):
        identity = identities[definition]
        if identity not in products:
            if progress:
                progress(number / max(total, 1), f"model {number + 1} of {total}")
            carved = stepindex.carve(found, definition)
            products[identity] = {
                "name": found.names.get(definition, "").strip(),
                "parts": _tessellate(carved, identity, lods, store),
            }
    for occurrence in occurrences:
        identity = identities[occurrence.child]
        instances.append(
            {
                "name": occurrence.name,
                "product": identity,
                "matrix": np.asarray(occurrence.placement, dtype=np.float64),
            }
        )
    return instances, products


def _from_whole(data: bytes, lods, progress) -> tuple[list, dict]:
    """Read whole by OpenCascade: one node per top-level instance (or per free shape)."""
    from .split import _root

    if progress:
        progress(0.0, "reading the whole file")
    doc = occ.read(data)
    root = _root(doc)
    labels = occ.components(root) if root is not None else []
    if not labels:
        labels = occ.free_shapes(doc)
    instances, products = [], {}
    seen: dict[str, object] = {}
    for label in labels:
        product = occ.referred(label)
        key = occ.label_entry(product)
        seen.setdefault(key, product)
        instances.append(
            {
                "name": occ.label_name(label) or occ.label_name(product),
                "product": key,
                "matrix": occ.location_matrix(label),
            }
        )
    for key, product in seen.items():
        products[key] = {"name": occ.label_name(product), "parts": {}}
    from OCP.XCAFDoc import XCAFDoc_Editor

    for key, product in seen.items():
        if occ.shape_of(product).IsNull():
            continue
        # The product alone, in its own frame, names and colors kept; coarse first.
        single = occ.new_doc()
        XCAFDoc_Editor.Extract_s(product, single.shapes.Label(), False)
        for lod in sorted(lods, key=lambda each: -each.linear):
            products[key]["parts"][lod.name] = _Part(_mesh_doc(single, lod))
    return instances, products


# ─── Writing ─────────────────────────────────────────────────────────────────

#: mm, Z up -> metres, Y up (a column-major glTF matrix).
_ROOT_MATRIX = [0.001, 0, 0, 0, 0, 0, -0.001, 0, 0, 0.001, 0, 0, 0, 0, 0, 1]


def _quantize(positions: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """int16 positions plus the (scale, offset) that gives them back, per axis."""
    low = positions.min(axis=0).astype(np.float64)
    high = positions.max(axis=0).astype(np.float64)
    span = np.maximum(high - low, 1e-9)
    scale = span / 65534.0
    q = np.round((positions - low) / scale - 32767.0).astype(np.int16)
    offset = low + 32767.0 * scale
    return q, scale, offset


class _Writer:
    def __init__(self):
        self.binary = bytearray()
        self.views: list = []
        self.accessors: list = []

    def add(self, array: np.ndarray, component: int, kind: str, target: int, normalized=False, bounds=False):
        while len(self.binary) % 4:
            self.binary.append(0)
        raw = np.ascontiguousarray(array).tobytes()
        view = {"buffer": 0, "byteOffset": len(self.binary), "byteLength": len(raw), "target": target}
        if kind != "SCALAR":
            # Vertex attributes: strides are multiples of 4 (int16 x3 is padded to 8 bytes).
            itemsize = array.dtype.itemsize * array.shape[1]
            if itemsize % 4:
                padded = np.zeros((array.shape[0], (itemsize + 3) // 4 * 4 // array.dtype.itemsize), dtype=array.dtype)
                padded[:, : array.shape[1]] = array
                raw = padded.tobytes()
                view["byteLength"] = len(raw)
                view["byteStride"] = padded.dtype.itemsize * padded.shape[1]
        self.binary += raw
        self.views.append(view)
        accessor = {
            "bufferView": len(self.views) - 1,
            "componentType": component,
            "count": int(array.shape[0]),
            "type": kind,
        }
        if normalized:
            accessor["normalized"] = True
        if bounds:
            accessor["min"] = [int(v) if component != 5126 else float(v) for v in array.min(axis=0)]
            accessor["max"] = [int(v) if component != 5126 else float(v) for v in array.max(axis=0)]
        self.accessors.append(accessor)
        return len(self.accessors) - 1


def _draco(positions: np.ndarray, normals: np.ndarray, indices: np.ndarray) -> tuple[bytes, int]:
    """KHR_draco_mesh_compression bytes and the vertex count they decode to (attribute ids: DRACO_IDS)."""
    import DracoPy

    encoded = DracoPy.encode(
        positions.astype(np.float32),
        faces=indices.reshape(-1, 3).astype(np.uint32),
        normals=normals.astype(np.float64),
        quantization_bits=DRACO_POSITION_BITS,
        normal_quantization_bits=DRACO_NORMAL_BITS,
        compression_level=7,
        preserve_order=False,
    )
    return encoded, len(DracoPy.decode(encoded).points)


#: The unique ids DracoPy gives the attributes, which the glTF primitive must name: it adds the
#: normals first. A viewer reading them the other way round draws the normals as positions (a
#: unit cube per part), so the browser tests check real vertices, not the accessors' bounds.
DRACO_IDS = {"POSITION": 1, "NORMAL": 0}
#: Draco quantization: 14 bits of a part's own extent (a 10 mm part: 0.6 um), normals 10 bits.
DRACO_POSITION_BITS = 14
DRACO_NORMAL_BITS = 10


def _write(instances: list, products: dict, lod: str, meta: dict, compression: str) -> tuple[bytes, dict]:
    writer = _Writer()
    materials, material_of = [], {}
    meshes, mesh_of, dequant_of = [], {}, {}
    nodes = [{"name": "boarddd-board", "matrix": _ROOT_MATRIX, "children": [], "extras": meta}]
    drawn = stored = 0
    for key, product in products.items():
        primitives = []
        part = product["parts"].get(lod)
        if part is None or not part.primitives:
            continue
        everything = np.concatenate([p for _, p, _, _ in part.primitives])
        _, scale, offset = _quantize(everything)
        for color, positions, normals, indices in part.primitives:
            if not len(indices):
                continue
            if color not in material_of:
                material_of[color] = len(materials)
                rgb = [float(v) for v in color[:3]]
                materials.append(
                    {
                        "pbrMetallicRoughness": {
                            "baseColorFactor": [*rgb, float(color[3])],
                            "metallicFactor": 0.1,
                            "roughnessFactor": 0.6,
                        },
                        "doubleSided": True,
                        "extras": {"step_color": rgb},
                        **({"alphaMode": "BLEND"} if color[3] < 0.999 else {}),
                    }
                )
            if compression == "draco":
                encoded, count = _draco(positions, normals, indices)
                while len(writer.binary) % 4:
                    writer.binary.append(0)
                writer.views.append({"buffer": 0, "byteOffset": len(writer.binary), "byteLength": len(encoded)})
                writer.binary += encoded
                view = len(writer.views) - 1

                def bare(component, kind, count, **more):
                    writer.accessors.append({"componentType": component, "count": count, "type": kind, **more})
                    return len(writer.accessors) - 1

                primitive = {
                    "attributes": {
                        "POSITION": bare(
                            5126,
                            "VEC3",
                            count,
                            min=[float(v) for v in positions.min(axis=0)],
                            max=[float(v) for v in positions.max(axis=0)],
                        ),
                        "NORMAL": bare(5126, "VEC3", count),
                    },
                    "indices": bare(5125, "SCALAR", int(len(indices))),
                    "material": material_of[color],
                    "extensions": {
                        "KHR_draco_mesh_compression": {"bufferView": view, "attributes": dict(DRACO_IDS)}
                    },
                }
            else:
                q = np.round((positions - offset) / scale).astype(np.int16)
                n = np.round(np.clip(normals, -1.0, 1.0) * 127.0).astype(np.int8)
                small = len(positions) < 65536
                primitive = {
                    "attributes": {
                        "POSITION": writer.add(q, 5122, "VEC3", 34962, bounds=True),
                        "NORMAL": writer.add(n, 5120, "VEC3", 34962, normalized=True),
                    },
                    "indices": writer.add(
                        indices.astype(np.uint16 if small else np.uint32), 5123 if small else 5125, "SCALAR", 34963
                    ),
                    "material": material_of[color],
                }
            primitives.append(primitive)
            stored += len(indices) // 3
        if not primitives:
            continue
        mesh_of[key] = len(meshes)
        dequant_of[key] = (
            [
                float(scale[0]),
                0,
                0,
                0,
                0,
                float(scale[1]),
                0,
                0,
                0,
                0,
                float(scale[2]),
                0,
                float(offset[0]),
                float(offset[1]),
                float(offset[2]),
                1,
            ]
            if compression != "draco"
            else None
        )
        meshes.append(
            {
                "name": product["name"],
                "primitives": primitives,
                "extras": {"triangles": sum(len(i) // 3 for _, _, _, i in part.primitives)},
            }
        )
    for instance in instances:
        key = instance["product"]
        if key not in mesh_of:
            continue
        node = {
            "name": instance["name"],
            "matrix": [float(v) for v in instance["matrix"].T.reshape(-1)],
            "extras": {"product": products[key]["name"]},
        }
        nodes[0]["children"].append(len(nodes))
        if dequant_of[key] is None:
            node["mesh"] = mesh_of[key]
            nodes.append(node)
        else:
            # The dequantization (KHR_mesh_quantization) on a node of its own below the instance.
            node["children"] = [len(nodes) + 1]
            nodes.append(node)
            nodes.append({"matrix": dequant_of[key], "mesh": mesh_of[key]})
        drawn += meshes[mesh_of[key]]["extras"]["triangles"]
    extension = "KHR_draco_mesh_compression" if compression == "draco" else "KHR_mesh_quantization"
    document = {
        "asset": {"version": "2.0", "generator": f"boarddd {VERSION}"},
        "extensionsUsed": [extension],
        "extensionsRequired": [extension],
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": nodes,
        "meshes": meshes,
        "materials": materials,
        "accessors": writer.accessors,
        "bufferViews": writer.views,
        "buffers": [{"byteLength": len(writer.binary)}],
    }
    while len(writer.binary) % 4:
        writer.binary.append(0)
    document["buffers"][0]["byteLength"] = len(writer.binary)
    text = json.dumps(document, separators=(",", ":")).encode("utf-8")
    text += b" " * (-len(text) % 4)
    glb = struct.pack("<4sII", b"glTF", 2, 12 + 8 + len(text) + 8 + len(writer.binary))
    glb += struct.pack("<I4s", len(text), b"JSON") + text
    glb += struct.pack("<I4s", len(writer.binary), b"BIN\x00") + bytes(writer.binary)
    return glb, {"drawn": drawn, "stored": stored}


def convert(
    source: str | os.PathLike | bytes,
    *,
    lods=LODS,
    cache: str | os.PathLike | Store | None = None,
    compression: str = "draco",
    progress=None,
) -> Result:
    """
    The board STEP as one GLB per level of detail (see the module docstring).

    `cache`: a model cache (`boarddd.step.cache`) for the per-product tessellations, so a model
    seen on an earlier board is not meshed again. `progress(fraction, text)` is called as products
    are worked out. `compression`: "draco" (KHR_draco_mesh_compression; needs DracoPy, a viewer needs a
    Draco decoder) or "quantize" (KHR_mesh_quantization; no decoder).
    """
    started = time.perf_counter()
    data = source if isinstance(source, (bytes, bytearray)) else Path(source).read_bytes()
    data = bytes(data)
    store = open_store(cache)
    found = None
    warnings: list[str] = []
    try:
        found = _from_text(data, lods, store, progress)
    except (ValueError, KeyError, IndexError) as error:
        warnings.append(f"read whole: {error}")
    source_kind = "text"
    if found is None:
        found = _from_whole(data, lods, progress)
        source_kind = "whole"
    instances, products = found
    glbs, triangles = {}, {}
    meta = {
        "format": VERSION,
        "compression": compression,
        "units": "mm",
        "up": "z",
        "source_sha256": hashlib.sha256(data).hexdigest(),
    }
    for lod in lods:
        glbs[lod.name], triangles[lod.name] = _write(
            instances, products, lod.name, {**meta, "lod": lod.name}, compression
        )
    if progress:
        progress(1.0, "done")
    return Result(
        version=VERSION,
        glbs=glbs,
        nodes=len(instances),
        products=len(products),
        triangles=triangles,
        seconds=round(time.perf_counter() - started, 3),
        source=source_kind,
        warnings=warnings,
    )


def _main(argv: list[str]) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m boarddd.step.glb", description="Board STEP -> GLB per level of detail"
    )
    parser.add_argument("step")
    parser.add_argument("--out", required=True, help="folder for <lod>.glb and summary.json")
    parser.add_argument("--cache", default=None)
    parser.add_argument("--compression", default="draco", choices=("draco", "quantize"))
    args = parser.parse_args(argv)

    def say(fraction, text):
        print(json.dumps({"progress": round(fraction, 3), "stage": text}), flush=True)

    result = convert(args.step, cache=args.cache, compression=args.compression, progress=say)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for name, data in result.glbs.items():
        (out / f"{name}.glb").write_bytes(data)
    summary = {k: v for k, v in result.__dict__.items() if k != "glbs"}
    summary["sizes"] = {name: len(data) for name, data in result.glbs.items()}
    (out / "summary.json").write_text(json.dumps(summary, indent=1))
    print(json.dumps({"done": summary}), flush=True)
    return 0


if __name__ == "__main__":
    import sys

    raise SystemExit(_main(sys.argv[1:]))
