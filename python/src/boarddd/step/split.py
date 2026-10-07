"""
Split an assembled-board STEP into measured, fingerprinted components.

How a board STEP is laid out (KiCad 10; see `boarddd.step.text` for the entity-level view):

    <board> assembly
      ├─ instance 'C1'            -> product 'C_0603_1608Metric'   (an assembly: the footprint)
      │                                └─ instance '=>[0:1:1:3]' -> the model's solids
      ├─ instance 'C2'            -> the same product, placed elsewhere
      └─ instance '=>[0:1:1:10]'  -> product '<board>_PCB'          (the board body)

The instance name is the designator. The product is shared by every instance of the same
footprint+model, so each distinct product is measured, fingerprinted and exported once, and
every instance refers to it. The instance's placement is the footprint's position, rotation
and side *plus* the model's own offset and rotation, folded together: KiCad does not keep them
apart. With the pick-and-place file the two can be separated (the pos file has the anchor and
the footprint rotation alone), and the component frame is then exact; without it the frame
is the placement, and a model with an offset is measured about its own origin.

Component frame: origin at the footprint anchor on the board surface of its side, +Z away
from the board, X along the footprint's zero rotation. A bottom-side part's frame is turned
over about X, which is how KiCad places it (Rz(rot) * Rx(180)), so its measurements read the
same as the same part on top.

Reading. A board is read from its text when it can be (`index`): the structure above from a
byte-offset index of the entities, each distinct product cut out into a STEP of its own and
worked out alone (`work`, in a process pool), known by a hash of its cut-out text that is the
same on every board, so a model seen before comes from the model cache (`cache`). The board's
tracks, zones and films are never read. Anything that is not an assembly with designators
(a library part, one exported component, an odd exporter) is read whole by OpenCascade, the
original way; both give the same results.

Altium writes the same tree differently: the root product is `PCB`, the board body's product
is `Board`, each component is a product named by its designator under a numbered (or blank)
instance, and Altium 24 puts that product at the origin with the model placed inside it at
its board position. So a designator may come from the product, a board body may be called
Board, and where an instance at the origin says nothing, its solids' placements say where
the component is (`_frame_hint`): for the fit to the pick-and-place file, and for its frame
when there is none (flagged `frame_from_solids`). Connectors that carry a mating plug sit far
from their placements; the fit then goes by the shift most components agree on.

Other exporters seen in the wild name things differently; `_designator` takes the instance
name when it looks like a designator, then the product name, then a numbered fallback, and
says which it used.

The pick-and-place file is read by `boarddd.io.pos` and fitted with `boarddd.step.registration`
(magpie had a second, cut-down fit of its own here, `split_pos`); `PosFit` is that registration
in the form the splitter uses.

Source: magpie `step/split.py` at internal `claud/magpie` `3a0374d3` (boarddd phase F4). Changes:
`split_pos` replaced by `io.pos` + `registration`; the footprint check's contacts are a hook
(`work.register_hook`); settings renamed `BOARDDD_*` (magpie's `MAGPIE_*` still read).
"""

from __future__ import annotations

import hashlib
import math
import os
import re
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from ._extra import require, setting

require(__name__)

import numpy as np  # noqa: E402

from ..io import pos as pos_reader  # noqa: E402
from . import index as stepindex  # noqa: E402
from . import occ, registration, work  # noqa: E402
from .cache import Store, open_store  # noqa: E402
from .fingerprint import Fingerprint, fingerprint  # noqa: E402
from .measure import Measurements, bbox, measure  # noqa: E402

__all__ = ["split", "Assembly", "Board", "Component", "Model", "Placement", "PosFit", "parse_pos", "fit_pos"]


# ─── The pick-and-place file ─────────────────────────────────────────────────


@dataclass(frozen=True)
class Placement:
    ref: str
    x: float
    y: float
    rot: float
    side: str  # 'top' or 'bottom'
    value: str = ""
    package: str = ""


def parse_pos(text: str) -> dict[str, Placement]:
    """Any pick-and-place file `boarddd.io.pos` reads, by designator."""
    return {
        row.reference: Placement(
            ref=row.reference,
            x=float(row.x),
            y=float(row.y),
            rot=float(row.rotation),
            side="bottom" if row.side == "bottom" else "top",
            value=row.value,
            package=row.footprint,
        )
        for row in pos_reader.parse(text)
    }


#: The STEP and the pos file come from one export and agree to the micron, so a component whose
#: model sits this far from its anchor is left out of the fit (registration's default floor,
#: GOOD_MM, is for models from elsewhere: left in, one SOT-23 offset by (0.5, 0.2) mm moved
#: the other ten components by 0.05 mm).
OUTLIER_FLOOR_MM = 0.01


@dataclass
class PosFit:
    """Carries pos-file XY into the STEP frame: flip (y -> -y), then rotate, then shift."""

    rot_deg: float = 0.0
    flip: bool = False
    dx: float = 0.0
    dy: float = 0.0
    residual_mm: float = 0.0
    matched: int = 0
    #: Components left out of the fit because their model sits away from their anchor.
    elsewhere: list[tuple[str, float]] = field(default_factory=list)
    ok: bool = False
    #: registration's method: 'designators' (least squares, trimmed) or 'consensus' (voted).
    method: str = ""
    #: registration's verdict, for reports.
    reason: str = ""

    def apply(self, x: float, y: float) -> tuple[float, float]:
        return registration.apply({"rot_deg": self.rot_deg, "flip": self.flip, "dx": self.dx, "dy": self.dy}, x, y)

    def angle(self, rot: float) -> float:
        """A pos-file rotation as an angle in the STEP frame."""
        return ((-rot if self.flip else rot) + self.rot_deg) % 360.0


def fit_pos(step_points: dict[str, tuple[float, float]], placements: dict[str, Placement]) -> PosFit:
    """Fit pos-file positions onto STEP placement origins by designator (`registration.register`)."""
    found = registration.register(
        {ref: (p.x, p.y, p.side) for ref, p in placements.items()},
        {ref: (x, y, "") for ref, (x, y) in step_points.items()},
        outlier_floor_mm=OUTLIER_FLOOR_MM,
    )
    t = found.transform
    by_name = found.method in ("designators", "consensus")
    return PosFit(
        rot_deg=float(t["rot_deg"]),
        flip=bool(t["flip"]),
        dx=float(t["dx"]),
        dy=float(t["dy"]),
        residual_mm=found.residual_mm,
        matched=len(found.matched),
        elsewhere=[(ref, round(mm, 4)) for ref, mm in found.elsewhere],
        ok=found.ok and by_name and found.residual_mm <= registration.GOOD_MM,
        method=found.method,
        reason=found.reason,
    )


#: KiCad's names for the board's own bodies (substrate, copper, mask, silkscreen).
BOARD_NODE = re.compile(r"^=>\[")
BOARD_NAME = re.compile(r"(^|[^a-z])(pcb|board|substrate)([^a-z]|$)", re.I)
#: R12, U1A, J1_2, Q3.1: letters, a number, and at most a short unit suffix.
DESIGNATOR = re.compile(r"^[A-Za-z]{1,5}\d{1,5}([._-]?[A-Za-z0-9]{1,3})?$")
#: Placement z axes closer than this to vertical are taken to say the side.
VERTICAL = 0.5
#: A local transform within this of another is the same model placement (for de-duplication).
LOCAL_ROUND = 5


def _rz(angle_deg: float) -> np.ndarray:
    angle = math.radians(angle_deg)
    matrix = np.eye(4)
    matrix[0, 0], matrix[0, 1] = math.cos(angle), -math.sin(angle)
    matrix[1, 0], matrix[1, 1] = math.sin(angle), math.cos(angle)
    return matrix


_RX180 = np.diag([1.0, -1.0, -1.0, 1.0])


def _rigid_inverse(matrix: np.ndarray) -> np.ndarray:
    inverse = np.eye(4)
    inverse[:3, :3] = matrix[:3, :3].T
    inverse[:3, 3] = -matrix[:3, :3].T @ matrix[:3, 3]
    return inverse


def _rounded(matrix: np.ndarray) -> tuple:
    return tuple(round(float(value), LOCAL_ROUND) + 0.0 for value in matrix[:3, :].ravel())


_TIMESTAMP = re.compile(rb"(FILE_NAME\('[^']*',')\d{4}-\d\d-\d\dT[\d:]+(')")


_OCCURRENCE_ID = re.compile(rb"(NEXT_ASSEMBLY_USAGE_OCCURRENCE\(')\d+(')")


def _steady(data: bytes) -> bytes:
    """
    A STEP export made the same every time, so the same model always exports the same bytes:
    the timestamp fixed, and the occurrence ids OCCT's writer takes from a counter that runs
    on through the whole process numbered in the file instead.
    """
    data = _TIMESTAMP.sub(rb"\g<1>2000-01-01T00:00:00\g<2>", data, count=1)
    counter = iter(range(1, 1 << 30))
    return _OCCURRENCE_ID.sub(lambda match: match.group(1) + str(next(counter)).encode() + match.group(2), data)


def _location(matrix: np.ndarray):
    from OCP.TopLoc import TopLoc_Location

    return TopLoc_Location(occ.matrix_trsf(matrix))


@dataclass
class Board:
    """The board body, and the surfaces components are measured from."""

    name: str
    top_z: float
    bottom_z: float
    bbox_min: tuple[float, float, float]
    bbox_max: tuple[float, float, float]
    #: How it was found: 'kicad' (a `=>[` board node), 'name' (called PCB/board), or
    #: 'placements' (no body found; the commonest mounting heights per side).
    source: str
    bodies: list[str] = field(default_factory=list)

    @property
    def thickness(self) -> float:
        return round(self.top_z - self.bottom_z, 6)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "top_z": self.top_z,
            "bottom_z": self.bottom_z,
            "thickness": self.thickness,
            "bbox_min": self.bbox_min,
            "bbox_max": self.bbox_max,
            "source": self.source,
            "bodies": self.bodies,
        }


class Model:
    """
    One distinct solid in the component frame: a product, placed by one local transform.

    Made in one of two ways. From a label of a document that holds the whole board (the file
    read by OpenCascade, the original way), measured here. Or from `computed`: measurements and
    fingerprint worked out in the seat frame by `work.compute`, from the product cut out of the
    board's text, perhaps on another board and kept in the model cache; they are lifted onto
    this board's seat. Then the label is only read (from the cut-out STEP) when something needs
    the solid itself: a drawing, the footprint check's contacts, an export, none of which are
    read from the cache.
    """

    def __init__(
        self,
        assembly: Assembly,
        key: str,
        label,
        product: str,
        local: np.ndarray,
        seat: float = 0.0,
        *,
        computed: dict | None = None,
        loader=None,
        store=None,
        store_key: str | None = None,
    ):
        self._assembly = assembly
        self.key = key
        self._label = label
        self._loader = loader
        self._shape = None
        self.product = product
        #: Product frame -> component frame. Identity for a model drawn about its anchor.
        self.local = local
        #: Where exported STEP/GLB put the model: the component frame measured from the seat
        #: rather than the substrate, so a model's file does not depend on the board's copper
        #: and pad thickness and a library model exports as itself.
        self.export_local = np.array(local)
        self.export_local[2, 3] -= seat
        #: The model cache and this model's entry in it, when there is one.
        self.store = store
        self.store_key = store_key
        started = time.perf_counter()
        if computed is None:
            self.measurements: Measurements | None = measure(self.shape)
            if self.measurements is not None:
                # The board's seat (KiCad: copper and pad), so `own_height` is the part's own.
                self.measurements.seat = round(float(seat), 6)
            self.fingerprint: Fingerprint | None = (
                fingerprint(self.shape, self.measurements, seat, self.label) if self.measurements else None
            )
        else:
            measured = computed.get("measurements")
            self.measurements = None
            self.fingerprint = None
            if measured is not None:
                self.measurements = Measurements.from_dict(measured).lifted(seat)
                self.measurements.seat = round(float(seat), 6)
                found = Fingerprint.from_dict(computed["fingerprint"])
                self.fingerprint = Fingerprint(**{**found.__dict__, "seat": seat})
        self.seconds = time.perf_counter() - started
        #: What registered hooks (`work.register_hook`) worked out for this model.
        self.extras: dict = {}
        self._step: bytes | None = None
        self._glb: bytes | None = None

    @property
    def label(self):
        if self._label is None and self._loader is not None:
            self._label = self._loader()
        return self._label

    @property
    def shape(self):
        if self._shape is None:
            self._shape = occ.shape_of(self.label).Moved(_location(self.local))
        return self._shape

    @property
    def is_identity(self) -> bool:
        return np.allclose(self.export_local, np.eye(4), atol=1e-9)

    def _doc(self):
        """A new document holding this model alone, in the component frame, names and colors kept."""
        from OCP.TCollection import TCollection_ExtendedString
        from OCP.TDataStd import TDataStd_Name
        from OCP.XCAFDoc import XCAFDoc_Editor

        target = occ.new_doc()
        XCAFDoc_Editor.Extract_s(self.label, target.shapes.Label(), False)
        copied = occ.free_shapes(target)
        if not copied:
            raise ValueError(f"could not copy {self.product}")
        if not self.is_identity:
            wrapper = target.shapes.NewShape()
            target.shapes.AddComponent(wrapper, copied[0], _location(self.export_local))
            TDataStd_Name.Set_s(wrapper, TCollection_ExtendedString(self.product or "component"))
            target.shapes.UpdateAssemblies()
        return target

    def _stored(self, name: str) -> bytes | None:
        return self.store.read_bytes(self.store_key, name) if self.store is not None and self.store_key else None

    def _keep(self, name: str, data: bytes) -> None:
        if self.store is not None and self.store_key:
            self.store.write_bytes(self.store_key, name, data)

    def step_bytes(self) -> bytes:
        if self._step is None:
            self._step = self._stored("model.step")
        if self._step is None:
            self._step = self._export_step()
            self._keep("model.step", self._step)
        return self._step

    def glb_bytes(self) -> bytes:
        """Binary glTF for viewers: metres, Y up (glTF's convention), from the component frame."""
        if self._glb is None:
            self._glb = self._stored("model.glb")
        if self._glb is None:
            self._glb = self._export_glb()
            self._keep("model.glb", self._glb)
        return self._glb

    def _export_step(self) -> bytes:
        if True:
            from OCP.STEPCAFControl import STEPCAFControl_Writer
            from OCP.STEPControl import STEPControl_AsIs

            document = self._doc()
            writer = STEPCAFControl_Writer()
            writer.SetNameMode(True)
            writer.SetColorMode(True)
            if not writer.Transfer(document.doc, STEPControl_AsIs):
                raise ValueError(f"STEP transfer failed for {self.product}")
            with tempfile.TemporaryDirectory() as folder:
                path = os.path.join(folder, "model.step")
                writer.Write(path)
                return _steady(Path(path).read_bytes())

    def _export_glb(self) -> bytes:
        if True:
            from OCP.BRepMesh import BRepMesh_IncrementalMesh
            from OCP.collections import IndexedDataMap_TCollection_AsciiString_TCollection_AsciiString
            from OCP.Message import Message_ProgressRange
            from OCP.RWGltf import RWGltf_CafWriter
            from OCP.RWMesh import RWMesh_CoordinateSystem
            from OCP.TCollection import TCollection_AsciiString

            document = self._doc()
            size = self.measurements.size if self.measurements else (1.0, 1.0, 1.0)
            deflection = max(0.005, 0.002 * math.sqrt(sum(value * value for value in size)))
            for label in occ.free_shapes(document):
                BRepMesh_IncrementalMesh(occ.shape_of(label), deflection, False, 0.35, False)
            with tempfile.TemporaryDirectory() as folder:
                path = os.path.join(folder, "model.glb")
                writer = RWGltf_CafWriter(TCollection_AsciiString(path), True)
                converter = writer.ChangeCoordinateSystemConverter()
                converter.SetInputLengthUnit(0.001)
                converter.SetInputCoordinateSystem(RWMesh_CoordinateSystem.RWMesh_CoordinateSystem_Zup)
                info = IndexedDataMap_TCollection_AsciiString_TCollection_AsciiString()
                if not writer.Perform(document.doc, info, Message_ProgressRange()):
                    raise ValueError(f"glTF export failed for {self.product}")
                return Path(path).read_bytes()

    @property
    def model_height(self) -> float | None:
        """The model's own height: its top above its own origin plane (the seat)."""
        if self.measurements is None:
            return None
        return round(self.measurements.height - float(self.local[2, 3]), 6)

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "product": self.product,
            "model_height": self.model_height,
            "local_transform": [[round(float(v), 6) for v in row] for row in self.local],
            "measurements": self.measurements.to_dict() if self.measurements else None,
            "fingerprint": self.fingerprint.to_dict() if self.fingerprint else None,
        }


@dataclass
class Component:
    """One placed component."""

    ref: str
    product: str
    side: str
    #: Component frame -> board (STEP) frame, 4x4.
    transform: np.ndarray
    #: The STEP's own placement of the instance (model frame -> board frame).
    placement: np.ndarray
    #: None when the file carries no geometry for it (see `_text_only`).
    model: Model | None
    #: Where the frame came from: 'pos' (anchor and rotation from the pick-and-place file)
    #: or 'step' (the placement itself, model offset included).
    frame_source: str
    #: Where the designator came from: 'instance', 'product' or 'generated'.
    name_source: str
    #: The model's origin above (+) or below the board surface, in mm. KiCad sits models on
    #: the copper, a few hundredths above the substrate.
    seat_z: float = 0.0
    pos: dict | None = None
    flags: list[str] = field(default_factory=list)
    #: For an instance at the origin whose solids place themselves (Altium): where they sit
    #: in the component frame, which `offset` reports. None otherwise.
    apparent: np.ndarray | None = None

    @property
    def measurements(self) -> Measurements | None:
        return self.model.measurements if self.model else None

    @property
    def fingerprint(self) -> Fingerprint | None:
        return self.model.fingerprint if self.model else None

    @property
    def rotation_deg(self) -> float:
        return round(math.degrees(math.atan2(self.transform[1, 0], self.transform[0, 0])) % 360.0, 4)

    @property
    def offset(self) -> tuple[float, float, float]:
        """Where the model's own origin sits in the component frame (its baked-in offset)."""
        if self.apparent is not None:
            return tuple(round(float(value), 6) for value in self.apparent[:3, 3])
        if self.model is None:
            return (0.0, 0.0, 0.0)
        return tuple(round(float(value), 6) for value in self.model.local[:3, 3])

    def step_bytes(self) -> bytes:
        if self.model is None:
            raise ValueError(f"{self.ref} has no geometry in this file")
        return self.model.step_bytes()

    def glb_bytes(self) -> bytes:
        if self.model is None:
            raise ValueError(f"{self.ref} has no geometry in this file")
        return self.model.glb_bytes()

    def to_dict(self) -> dict:
        origin = self.transform[:3, 3]
        return {
            "ref": self.ref,
            "product": self.product,
            "side": self.side,
            "x": round(float(origin[0]), 6),
            "y": round(float(origin[1]), 6),
            "z": round(float(origin[2]), 6),
            "rotation_deg": self.rotation_deg,
            "transform": [[round(float(v), 9) for v in row] for row in self.transform],
            "model_offset": self.offset,
            "seat_z": round(self.seat_z, 6),
            "frame_source": self.frame_source,
            "name_source": self.name_source,
            "model": self.model.key if self.model else None,
            "pos": self.pos,
            "flags": self.flags,
        }


@dataclass
class Assembly:
    board: Board | None
    components: dict[str, Component]
    models: dict[str, Model]
    pos_fit: PosFit | None = None
    #: Where the board seats models above its surface (KiCad: copper plus pad thickness).
    seat_z: float = 0.0
    #: Designators in the pos file with no solid in the STEP (no 3D model, or DNP).
    missing_models: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    timings: dict[str, float] = field(default_factory=dict)
    _doc: object = None
    #: 'text' (structure from the text, products cut out and worked out alone) or 'occt'.
    read_from: str = ""
    #: Models in all, from the cache, and worked out, for the text route.
    stats: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        fit = self.pos_fit
        return {
            "board": self.board.to_dict() if self.board else None,
            "components": {ref: component.to_dict() for ref, component in self.components.items()},
            "models": {key: model.to_dict() for key, model in self.models.items()},
            "pos_fit": None
            if fit is None
            else {
                "ok": fit.ok,
                "rot_deg": fit.rot_deg,
                "flip": fit.flip,
                "dx": round(fit.dx, 6),
                "dy": round(fit.dy, 6),
                "residual_mm": round(fit.residual_mm, 6),
                "matched": fit.matched,
                "elsewhere": fit.elsewhere,
            },
            "seat_z": self.seat_z,
            "missing_models": self.missing_models,
            "warnings": self.warnings,
            "timings": {key: round(value, 3) for key, value in self.timings.items()},
        }


# ─── Walking the tree ────────────────────────────────────────────────────────


def _root(doc: occ.Doc):
    """The board assembly: the free shape with most components, unwrapped of single-child shells."""
    roots = occ.free_shapes(doc)
    if not roots:
        return None
    root = max(roots, key=lambda label: len(occ.components(label)))
    for _ in range(4):
        children = occ.components(root)
        if len(children) != 1:
            break
        inner = occ.referred(children[0])
        if len(occ.components(inner)) <= 1 or not np.allclose(occ.location_matrix(children[0]), np.eye(4)):
            break
        root = inner
    return root


def _designator(instance: str, product: str, taken: set[str], index: int) -> tuple[str, str]:
    instance, product = instance.strip(), product.strip()
    if (
        instance
        and not BOARD_NODE.match(instance)
        and instance != product
        and not re.fullmatch(r"(NAUO|SOLID|Component|Body)?[\s_#:-]*\d*", instance, re.I)
    ):
        name, source = instance, "instance"
    elif product and DESIGNATOR.match(product) and product not in taken:
        name, source = product, "product"
    else:
        name, source = f"{product or 'component'}#{index}", "generated"
    unique, count = name, 1
    while unique in taken:
        count += 1
        unique = f"{name}#{count}"
    return unique, source


def _children_placements(handle) -> list:
    """The placements of a product's own instances, in the product's frame."""
    if isinstance(handle, _TextProduct):
        return [o.placement for o in handle.found.children.get(handle.definition, [])]
    return [occ.location_matrix(instance) for instance in occ.components(handle)]


def _frame_hint(entry) -> np.ndarray | None:
    """
    Where a component is when its own placement says nothing: an instance at the origin whose
    product places its solids itself (Altium puts each designator's product at the origin and
    the bodies inside it at their board positions). The first solid's orientation, at the
    middle of where the solids are placed. None for a placement that says where it is.
    """
    if entry.get("model_file") or not np.allclose(entry["placement"], np.eye(4), atol=1e-9):
        return None  # a model file keeps the frame it was drawn in
    inner = _children_placements(entry["product_label"])
    if not inner:
        return None
    hint = entry["placement"] @ inner[0]
    hint[:3, 3] = np.mean([entry["placement"] @ matrix for matrix in inner], axis=0)[:3, 3]
    return hint


def _where(entry) -> np.ndarray:
    """The placement that says where a component is (its own, or the hint from its solids)."""
    hint = entry.get("frame_hint")
    return hint if hint is not None else entry["placement"]


def _vertical(placement: np.ndarray) -> float:
    return float(placement[2, 2])


def _find_board(entries) -> tuple[list, str]:
    """Which root-level instances are the board's own bodies, and how that was decided."""
    kicad = [entry for entry in entries if BOARD_NODE.match(entry["name"])]
    if kicad:
        return kicad, "kicad"
    named = [
        entry
        for entry in entries
        if (BOARD_NAME.search(entry["product"]) or BOARD_NAME.search(entry["name"]))
        and not DESIGNATOR.match(entry["name"])
    ]
    if named:
        return named, "name"
    return [], ""


def _product_shape(handle):
    """The product's shape, from a document label or a product read from the board's text."""
    if isinstance(handle, _TextProduct):
        handle = handle.label()
    return occ.shape_of(handle)


def _body_box(entry):
    """A board body's box in the board frame; for a product read from text, kept in the cache."""
    handle = entry["product_label"]
    store = getattr(handle, "store", None) if isinstance(handle, _TextProduct) else None
    key = Store.key("body", handle.identity, _rounded(entry["placement"])) if store is not None else None
    if key is not None:
        kept = store.read_json(key, "box.json")
        if kept is not None:
            return None if kept["box"] is None else (tuple(kept["box"][0]), tuple(kept["box"][1]))
    found = bbox(_product_shape(handle).Moved(_location(entry["placement"])))
    if key is not None:
        store.write_json(key, "box.json", {"box": None if found is None else [list(found[0]), list(found[1])]})
    return found


def _board_from(bodies: list, source: str, doc) -> Board | None:
    boxes = []
    measured = bodies
    if bodies and isinstance(bodies[0]["product_label"], _TextProduct):
        # Read from the text, only the substrate is read at all: the copper, mask and silk
        # films of a full export are most of the file and say nothing the substrate does not.
        substrate = [entry for entry in bodies if entry["product"].upper().endswith("PCB")]
        measured = substrate or bodies
    for entry in measured:
        found = _body_box(entry)
        if found is not None:
            boxes.append((entry, found))
    if not boxes:
        return None

    def area(item):
        (low, high) = item[1]
        return (high[0] - low[0]) * (high[1] - low[1])

    # The substrate: the one KiCad calls *_PCB, else the one with the largest footprint
    # that is still a plate (copper and mask films are as wide but far thinner).
    substrate = next((item for item in boxes if item[0]["product"].upper().endswith("PCB")), None)
    if substrate is None:
        plates = [item for item in boxes if item[1][1][2] - item[1][0][2] >= 0.2] or boxes
        substrate = max(plates, key=area)
    low, high = substrate[1]
    return Board(
        name=substrate[0]["product"] or substrate[0]["name"],
        top_z=round(high[2], 6),
        bottom_z=round(low[2], 6),
        bbox_min=tuple(round(v, 6) for v in low),
        bbox_max=tuple(round(v, 6) for v in high),
        source=source,
        bodies=[
            entry["product"] or entry["name"]
            for entry in (bodies if measured is not bodies else [e for e, _b in boxes])
        ],
    )


def _board_from_placements(entries) -> Board | None:
    """No board body: the commonest mounting height per side is that side's surface."""
    from collections import Counter

    tops = Counter(round(float(e["placement"][2, 3]), 3) for e in entries if _vertical(e["placement"]) > VERTICAL)
    bottoms = Counter(round(float(e["placement"][2, 3]), 3) for e in entries if _vertical(e["placement"]) < -VERTICAL)
    if not tops and not bottoms:
        return None
    top = tops.most_common(1)[0][0] if tops else None
    bottom = bottoms.most_common(1)[0][0] if bottoms else None
    if top is None:
        top = bottom + 1.6
    if bottom is None or not 0.2 <= top - bottom <= 6.0:
        bottom = top - 1.6
    return Board(
        name="", top_z=top, bottom_z=bottom, bbox_min=(0.0, 0.0, bottom), bbox_max=(0.0, 0.0, top), source="placements"
    )


#: Above this a STEP file is refused: `BOARDDD_STEP_MAX_MB` (default 2048).
MAX_MB_ENV = "BOARDDD_STEP_MAX_MB"
#: Above this many entities, likewise: `BOARDDD_STEP_MAX_ENTITIES` (default 40 million).
MAX_ENTITIES_ENV = "BOARDDD_STEP_MAX_ENTITIES"
#: A file that cannot be read from its text (not an assembly with designators) is read whole
#: by OpenCascade, at about 10x its size in memory: `BOARDDD_STEP_MAX_OCCT_MB` (default 400).
MAX_OCCT_MB_ENV = "BOARDDD_STEP_MAX_OCCT_MB"


class StepTooLarge(ValueError):
    """The file is past a size guard; the message says which and how to raise it."""


def _limit(name: str, default: float) -> float:
    try:
        return float(setting(name.removeprefix("BOARDDD_"), default))
    except ValueError:
        return default


class _TextProduct:
    """A product of a board read as text: known by its identity, cut out and read alone when needed."""

    def __init__(self, found, definition: int, store, documents: _TextDocuments | None = None):
        self.documents = documents
        self.found = found
        self.definition = definition
        self.store = store
        self._identity: str | None = None
        self._step: bytes | None = None
        self._label = None
        self._document = None

    @property
    def identity(self) -> str:
        if self._identity is None:
            self._identity = stepindex.identity(self.found, self.definition)
        return self._identity

    def step(self) -> bytes:
        if self._step is None:
            key = Store.key("product", self.identity) if self.store is not None else None
            if key is not None:
                self._step = self.store.read_bytes(key, "product.step")
            if self._step is None:
                self._step = stepindex.carve(self.found, self.definition)
                if key is not None:
                    self.store.write_bytes(key, "product.step", self._step)
        return self._step

    def label(self):
        if self._label is None:
            self._document = occ.read(self.step())
            if self.documents is not None:
                self.documents.documents.append(self._document)
            roots = occ.free_shapes(self._document)
            self._label = max(roots, key=lambda label: len(occ.components(label)))
        return self._label


class _ColorTools:
    """`GetColor` across the documents of a board read as text, one per product read so far."""

    def __init__(self, documents: list):
        self.documents = documents

    def GetColor(self, *args):  # noqa: N802 (OCCT's name, for callers of ColorTool)
        return any(document.colors.GetColor(*args) for document in self.documents)


class _TextDocuments:
    """
    What `Assembly._doc` is for a board read from its text: callers that took the board's one
    document for its colour tool (magpie's footprint check) get one that answers for every
    product document, which is where the labels they hold come from.
    """

    def __init__(self):
        self.documents: list = []

    @property
    def colors(self) -> _ColorTools:
        return _ColorTools(self.documents)


def _text_root(found) -> int | None:
    """The board's product definition, unwrapped of single-child shells (as `_root`)."""
    root = found.root
    for _ in range(4):
        children = found.children.get(root, [])
        if len(children) != 1:
            break
        inner = children[0].child
        if len(found.children.get(inner, [])) <= 1 or not np.allclose(children[0].placement, np.eye(4)):
            break
        root = inner
    return root


def _read_text(data: bytes, store, documents=None):
    """(structure, entries) when the board can be read from its text, else None."""
    index = stepindex.Index(data)
    if len(index) > _limit(MAX_ENTITIES_ENV, 40e6):
        raise StepTooLarge(
            f"STEP file has {len(index):,} entities, more than {MAX_ENTITIES_ENV} "
            f"allows ({_limit(MAX_ENTITIES_ENV, 40e6):,.0f})"
        )
    found = stepindex.structure(index)
    if not found.has_geometry or found.root is None:
        return None
    root = _text_root(found)
    occurrences = found.children.get(root, [])
    # KiCad names the instance by its designator; Altium names the product by it and numbers
    # (or leaves blank) the instances.
    if not any(
        (DESIGNATOR.match(o.name) and not BOARD_NODE.match(o.name))
        or DESIGNATOR.match(found.names.get(o.child, "").strip())
        for o in occurrences
    ):
        return None
    products: dict[int, _TextProduct] = {}
    entries = []
    for occurrence in occurrences:
        product = products.setdefault(occurrence.child, _TextProduct(found, occurrence.child, store, documents))
        entries.append(
            {
                "name": occurrence.name,
                "product": found.names.get(occurrence.child, ""),
                "product_label": product,
                "placement": occurrence.placement,
            }
        )
    return found, entries


def split(
    source: str | os.PathLike | bytes,
    pos: str | os.PathLike | dict | None = None,
    *,
    cache=None,
    workers: int | None = None,
    text: bool = True,
) -> Assembly:
    """
    Read a board STEP and return its board and components, each measured and fingerprinted.

    `pos` is the pick-and-place file (a path, its text, or already parsed placements). With it
    each component's frame is its footprint anchor and rotation; without it, its placement.

    A board is read from its text when it can be (an assembly with designators: every KiCad
    export, and most others): each distinct product is cut out and worked out on its own, in
    `workers` processes, and kept in the model cache (`cache`: a path or Store; None for
    `$BOARDDD_STEP_CACHE`; False for none), so a model seen on an earlier board is not worked
    out again. Anything else, and `text=False`, is read whole by OpenCascade, as before.
    Results are the same either way, to rounding in the last kept digit.
    """
    timings: dict[str, float] = {}
    started = time.perf_counter()
    if isinstance(source, (bytes, bytearray, memoryview)):
        data = bytes(source)
    else:
        size = os.path.getsize(source)
        if size > _limit(MAX_MB_ENV, 2048) * 1e6:
            raise StepTooLarge(
                f"STEP file is {size / 1e6:.0f} MB, more than {MAX_MB_ENV} allows ({_limit(MAX_MB_ENV, 2048):.0f} MB)"
            )
        data = Path(source).read_bytes()
    if len(data) > _limit(MAX_MB_ENV, 2048) * 1e6:
        raise StepTooLarge(
            f"STEP file is {len(data) / 1e6:.0f} MB, more than {MAX_MB_ENV} allows ({_limit(MAX_MB_ENV, 2048):.0f} MB)"
        )
    store = open_store(cache)
    textual = None
    documents = _TextDocuments()
    if text:
        try:
            textual = _read_text(data, store, documents)
        except StepTooLarge:
            raise
        except ValueError:
            textual = None
    doc = None
    if textual is None:
        if len(data) > _limit(MAX_OCCT_MB_ENV, 400) * 1e6:
            raise StepTooLarge(
                f"STEP file is {len(data) / 1e6:.0f} MB and cannot be read from its text (no "
                f"assembly with designators), so OpenCascade would read it whole, at about ten "
                f"times that in memory; {MAX_OCCT_MB_ENV} allows {_limit(MAX_OCCT_MB_ENV, 400):.0f} MB. "
                f"Export it without tracks and zones, or raise the limit."
            )
        try:
            doc = occ.read(data)
        except ValueError as error:
            if "nothing could be transferred" not in str(error):
                raise
            return _text_only(data, str(error))
    timings["read"] = time.perf_counter() - started

    placements: dict[str, Placement] | None = None
    if isinstance(pos, dict):
        placements = pos
    elif pos is not None:
        text_pos = (
            Path(pos).read_text(errors="replace")
            if (isinstance(pos, os.PathLike) or (isinstance(pos, str) and "\n" not in pos and os.path.exists(pos)))
            else str(pos)
        )
        placements = parse_pos(text_pos)

    assembly = Assembly(board=None, components={}, models={}, _doc=doc)
    assembly.timings = timings
    phase = time.perf_counter()
    if textual is not None:
        _found, entries = textual
        assembly.read_from = "text"
        assembly._doc = documents
    else:
        assembly.read_from = "occt"
        root = _root(doc)
        if root is None:
            assembly.warnings.append("no shapes in the file")
            return assembly
        instances = occ.components(root)
        entries = []
        for instance in instances:
            product_label = occ.referred(instance)
            entries.append(
                {
                    "name": occ.label_name(instance),
                    "product": occ.label_name(product_label),
                    "product_label": product_label,
                    "placement": occ.location_matrix(instance),
                }
            )

    bodies, board_source = _find_board(entries)
    designated = [
        entry
        for entry in entries
        if (DESIGNATOR.match(entry["name"].strip()) and not BOARD_NODE.match(entry["name"]))
        or DESIGNATOR.match(entry["product"].strip())
    ]
    if (
        doc is not None
        and not designated
        and not (board_source == "kicad" and len(entries) > len(bodies))
        and not any(entry["product"].upper().endswith("PCB") for entry in bodies)
    ):
        # A model file, not a board: a library part, or one component exported by `split`.
        # (Inside a KiCad footprint the model instance is also called `=>[...]`.)
        entries = [
            {
                "name": "",
                "product": occ.label_name(root),
                "product_label": root,
                "placement": np.eye(4),
                "model_file": True,
            }
        ]
        bodies, board_source = [], ""
        assembly.warnings.append("no designators and no board body: read as a single model")
    assembly.board = _board_from(bodies, board_source, doc) if bodies else None
    body_ids = {id(entry) for entry in bodies}
    parts = [entry for entry in entries if id(entry) not in body_ids]
    if assembly.board is None and board_source == "" and len(parts) > 1:
        assembly.board = _board_from_placements(parts)
        if assembly.board is not None:
            assembly.warnings.append("no board body found; surfaces guessed from mounting heights")
        else:
            assembly.warnings.append("no board body found and no placements say where it is")

    for entry in parts:
        entry["frame_hint"] = _frame_hint(entry)

    taken: set[str] = set()
    for index, entry in enumerate(parts, 1):
        entry["ref"], entry["name_source"] = _designator(entry["name"], entry["product"], taken, index)
        taken.add(entry["ref"])

    fit: PosFit | None = None
    if placements:
        fit = fit_pos(
            {entry["ref"]: (float(_where(entry)[0, 3]), float(_where(entry)[1, 3])) for entry in parts}, placements
        )
        assembly.pos_fit = fit
        upper = {ref.upper() for ref in taken}
        assembly.missing_models = sorted(ref for ref in placements if ref.upper() not in upper)
        if fit.matched < 2:
            assembly.warnings.append(
                f"only {fit.matched} pick-and-place designators have a solid in the STEP; "
                f"frames taken from the STEP placements"
            )
        elif not fit.ok:
            assembly.warnings.append(
                f"pick-and-place does not fit the STEP (residual {fit.residual_mm:.3f} mm over "
                f"{fit.matched} components); frames taken from the STEP placements"
            )
    timings["walk"] = time.perf_counter() - phase

    phase = time.perf_counter()
    board = assembly.board
    by_upper = {ref.upper(): placement for ref, placement in (placements or {}).items()}
    framed = []
    for entry in parts:
        P = entry["placement"]
        flags: list[str] = []
        placed = by_upper.get(entry["ref"].upper()) if fit is not None and fit.ok else None
        vertical = _vertical(P)

        if placed is not None:
            side = placed.side
            if fit.flip:
                side = "bottom" if side == "top" else "top"
            angle = fit.angle(placed.rot)
            x, y = fit.apply(placed.x, placed.y)
            frame_source = "pos"
        else:
            if entry.get("frame_hint") is not None:
                # Placed at the origin, its solids placed inside it (Altium): frame from those.
                P = entry["frame_hint"]
                vertical = _vertical(P)
                flags.append("frame_from_solids")
            if vertical > VERTICAL:
                side = "top"
            elif vertical < -VERTICAL:
                side = "bottom"
            else:
                # A model authored lying down: its axes do not say the side, its solids do.
                middle = (board.top_z + board.bottom_z) / 2 if board else 0.0
                placed_box = bbox(_product_shape(entry["product_label"]).Moved(_location(entry["placement"])))
                height = (placed_box[0][2] + placed_box[1][2]) / 2 if placed_box else float(P[2, 3])
                side = "bottom" if height < middle else "top"
                flags.append("placement_not_vertical")
            axis = P[:3, 0] if abs(P[2, 0]) < 0.9 else P[:3, 1]
            angle = math.degrees(math.atan2(axis[1], axis[0])) % 360.0
            if abs(P[2, 0]) >= 0.9:
                angle = (angle - 90.0) % 360.0
            x, y = float(P[0, 3]), float(P[1, 3])
            frame_source = "step"

        surface = (board.top_z if side == "top" else board.bottom_z) if board else float(P[2, 3])
        frame = _rz(angle) @ (_RX180 if side == "bottom" else np.eye(4))
        frame[:3, 3] = (x, y, surface)
        P = entry["placement"]
        local = _rigid_inverse(frame) @ P
        # Where the solids sit in the component frame: the same as `local` unless the instance
        # is at the origin and its solids place themselves (Altium), when it is from there.
        entry["apparent"] = _rigid_inverse(frame) @ _where(entry)
        framed.append((entry, P, flags, placed, side, angle, x, y, frame, local, frame_source))

    # Where this board seats its models: KiCad lifts every one by the copper and pad, the
    # same for all. Fingerprints are taken from there, so they compare across boards.
    from collections import Counter

    seats = Counter(
        round(float(entry["apparent"][2, 3]), 4)
        for entry, *_rest in framed
        if np.allclose(entry["apparent"][:3, :3], np.eye(3), atol=1e-6)
    )
    assembly.seat_z = seats.most_common(1)[0][0] if seats else 0.0

    if textual is not None:
        _models_from_text(assembly, framed, store, workers)
    for entry, P, flags, placed, side, angle, x, y, frame, local, frame_source in framed:
        seat = float(entry["apparent"][2, 3])
        if textual is not None:
            model = assembly.models[entry["model_key"]]
        else:
            key = f"{occ.label_entry(entry['product_label'])}@{hashlib.sha1(repr(_rounded(local)).encode()).hexdigest()[:8]}"
            model = assembly.models.get(key)
            if model is None:
                model = Model(assembly, key, entry["product_label"], entry["product"], local, assembly.seat_z)
                assembly.models[key] = model
        if model.measurements is None:
            flags.append("no_geometry")
        if not np.allclose(entry["apparent"][:3, :3], np.eye(3), atol=1e-6):
            flags.append("model_rotated")

        component = Component(
            ref=entry["ref"],
            product=entry["product"],
            side=side,
            transform=frame,
            placement=P,
            model=model,
            frame_source=frame_source,
            name_source=entry["name_source"],
            seat_z=seat,
            flags=flags,
            apparent=entry["apparent"] if entry.get("frame_hint") is not None else None,
        )
        if placed is not None:
            W = _where(entry)
            origin_xy = (float(W[0, 3]), float(W[1, 3]))
            step_angle = math.degrees(math.atan2(W[1, 0], W[0, 0])) % 360.0
            component.pos = {
                "x": placed.x,
                "y": placed.y,
                "rot": placed.rot,
                "side": placed.side,
                # Placement origin vs anchor: the model offset in the board plane, plus the fit's own error.
                "offset_mm": round(math.hypot(origin_xy[0] - x, origin_xy[1] - y), 6),
                "rot_diff_deg": round(((step_angle - angle + 180.0) % 360.0) - 180.0, 4),
            }
        assembly.components[entry["ref"]] = component
    timings["measure"] = time.perf_counter() - phase
    timings["total"] = time.perf_counter() - started
    return assembly


def _models_from_text(assembly: Assembly, framed: list, store, workers: int | None) -> None:
    """
    The board's distinct models, from the cache or worked out (in parallel) and then cached.

    A model is a product placed by one seat-frame transform; its key is the product's text
    identity and that transform, so it is the same key on every board.
    """
    seat = assembly.seat_z
    wanted: dict[str, tuple] = {}
    for entry, _P, _flags, _placed, _side, _angle, _x, _y, _frame, local, _source in framed:
        product = entry["product_label"]
        export_local = np.array(local)
        export_local[2, 3] -= seat
        rounded = _rounded(export_local)
        store_key = Store.key("model", product.identity, rounded)
        entry["model_key"] = f"{product.identity[:12]}@{hashlib.sha1(repr(rounded).encode()).hexdigest()[:8]}"
        wanted.setdefault(entry["model_key"], (product, local, export_local, store_key, entry["product"]))

    computed: dict[str, dict] = {}
    made: dict[str, dict] = {}
    tasks, keys = [], []
    for model_key, (product, _local, export_local, store_key, name) in wanted.items():
        stored = store.read_json(store_key, "model.json") if store is not None else None
        if stored is not None:
            computed[model_key] = stored
            continue
        # Exports and contacts are made with the model, cached or not, so a cached board and an
        # uncached one give the same bytes and the same contacts.
        tasks.append(
            {
                "step": product.step(),
                "local": export_local.tolist(),
                "product": name,
                "eager": work.EAGER + tuple(work.HOOKS),
                "seat": seat,
            }
        )
        keys.append(model_key)
    started = time.perf_counter()
    for model_key, result in zip(keys, work.run(tasks, work.workers_setting(workers)), strict=False):
        product, _local, _export, store_key, _name = wanted[model_key]
        record = {"measurements": result["measurements"], "fingerprint": result["fingerprint"]}
        computed[model_key] = record
        made[model_key] = result
        if store is not None:
            for name in ("step", "glb"):
                if name in result:
                    store.write_bytes(store_key, f"model.{name}", result[name])
            for hook in work.HOOKS:
                if hook in result:
                    store.write_json(store_key, f"{hook}.json", result[hook])
            store.write_json(store_key, "model.json", record)
    assembly.timings["work"] = time.perf_counter() - started
    assembly.stats = {"models": len(wanted), "cached": len(wanted) - len(tasks), "worked": len(tasks)}
    for model_key, (product, local, _export, store_key, name) in wanted.items():
        model = Model(
            assembly,
            model_key,
            None,
            name,
            local,
            seat,
            computed=computed[model_key],
            loader=product.label,
            store=store,
            store_key=store_key,
        )
        result = made.get(model_key, {})
        model._step, model._glb = result.get("step"), result.get("glb")
        for hook in work.HOOKS:
            if hook in result:
                model.extras[hook] = result[hook]
            elif store is not None and store.has(store_key, f"{hook}.json"):
                model.extras[hook] = store.read_json(store_key, f"{hook}.json")
        if result.get("drawing") and model.fingerprint is not None:
            from . import hlr

            hlr.remember(model, result["drawing"])
        assembly.models[model_key] = model


def _text_only(source, why: str) -> Assembly:
    """
    Designators and placements read from the file's text, for a STEP with no usable geometry.

    OpenCascade transfers nothing from a file whose solids have been stripped, but the
    assembly structure is still there to read; `boarddd.step.text` does that walk.
    """
    from . import text as steptext

    text = (
        bytes(source).decode("latin-1")
        if isinstance(source, (bytes, bytearray, memoryview))
        else Path(source).read_text(errors="replace")
    )
    assembly = Assembly(board=None, components={}, models={})
    assembly.warnings.append(f"{why}; designators and placements read from the text only")
    taken: set[str] = set()
    for index, found in enumerate(steptext.instances(text), 1):
        ref, source_name = _designator(found.ref, found.product, taken, index)
        taken.add(ref)
        x_axis = np.array(found.x_dir[:3], dtype=float)
        z_axis = np.array(found.z_dir[:3], dtype=float)
        placement = np.eye(4)
        if np.linalg.norm(x_axis) > 0 and np.linalg.norm(z_axis) > 0:
            z_axis /= np.linalg.norm(z_axis)
            x_axis -= z_axis * float(x_axis @ z_axis)
            x_axis /= np.linalg.norm(x_axis)
            placement[:3, 0], placement[:3, 1], placement[:3, 2] = x_axis, np.cross(z_axis, x_axis), z_axis
        placement[:3, 3] = (found.x, found.y, found.z)
        assembly.components[ref] = Component(
            ref=ref,
            product=found.product,
            side=found.side or "top",
            transform=placement,
            placement=placement,
            model=None,
            frame_source="step",
            name_source=source_name,
            flags=["no_geometry"],
        )
    return assembly
