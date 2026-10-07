"""
Fingerprints that tell 3D model versions apart.

Three keys, each a short hex digest, from the exact B-rep (never a mesh, so tessellation does
not move them):

* `shape_key`: what the solid is, whatever frame it is in. Volume, surface area, the principal
  moments of inertia, and how many faces of each surface type, each rounded to SIG_FIGS
  significant figures. Two instances of one model agree; the same model turned 90 degrees in
  its footprint also agrees.
* `frame_key`: the shape key plus where the solid sits in the component frame (bounding box
  and centroid, rounded to FRAME_MM, and the central inertia tensor, whose products of
  inertia tell a model from its mirror image: a pin header with pin 1 left or right has the
  same box and principal moments). Two versions that differ only by a model offset or a
  rotation agree on the shape key and not on this one, and for placement that matters.
  Heights are taken from the seat (where the board puts every model's origin, `seat`), not
  from the substrate: KiCad lifts models by the copper and pad thickness, which is the
  board's business, so a library model and the same model on a board agree.
* `brep_key`: every face as (surface type, area, centroid) in the component frame, sorted
  and hashed. It survives re-export (entity numbering and face order do not enter), and
  changes when any face does, so a fillet added to one corner is a different model even
  when the volume barely moves.

A fourth, `color_key`, is the set of colors the model carries. It is not geometry and `same()`
ignores it, but it is how two stock models with identical solids tell apart: KiCad's
C_0603_1608Metric and L_0603_1608Metric differ only in the body's color.

Rounding to a grid always has edges: a value sitting exactly on a rounding boundary can land
either side after a re-export. Keys are for indexing; `same(a, b)` compares the raw values
with a relative tolerance and is what decides.

Source: magpie `step/fingerprint.py` at internal `claud/magpie` `3a0374d3` (boarddd phase F4).
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass

from ._extra import require

require(__name__)

from . import occ  # noqa: E402
from .measure import Measurements  # noqa: E402

__all__ = ["Fingerprint", "fingerprint", "same", "SIG_FIGS", "FRAME_MM", "BREP_MM"]

SIG_FIGS = 4
FRAME_MM = 0.01
BREP_MM = 0.001
#: Face areas: B-spline face integrals wobble in the sixth figure between exports.
BREP_AREA_MM2 = 0.001
#: Inertia tensor resolution, as a share of the largest moment. A mirror image shows only in
#: the products of inertia, and faintly on a long part: the 1x34 SMD pin header with pin 1
#: left or right differs by Ixy = +-52 against a largest moment of 416,000 (1.2e-4 each way).
TENSOR_SHARE = 1e-4
#: `same()` tolerance on volume, area and moments.
RELATIVE = 2e-3


def _sig(value: float, figures: int = SIG_FIGS) -> float:
    if value == 0 or not math.isfinite(value):
        return 0.0
    return round(value, figures - 1 - int(math.floor(math.log10(abs(value))))) + 0.0


#: Grid cells are shifted off the round numbers by this share of a cell. CAD dimensions
#: are multiples of 0.0005 mm, so an unshifted 0.001 grid puts face centroids like 1.2525
#: exactly on a cell edge, where a re-export's last bit decides the cell.
GRID_SHIFT = 0.2371


def _grid(value: float, step: float) -> float:
    return round(math.floor(value / step + GRID_SHIFT) * step, 6) + 0.0


def _digest(payload) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]


@dataclass(frozen=True)
class Fingerprint:
    shape_key: str
    frame_key: str
    brep_key: str
    volume: float
    area: float
    principal_moments: tuple[float, float, float]
    face_types: dict
    #: In the seat frame (z from the seat), so they compare across boards.
    bbox_min: tuple[float, float, float]
    bbox_max: tuple[float, float, float]
    seat: float = 0.0
    #: Central inertia tensor in the component frame (xx, yy, zz, xy, xz, yz).
    inertia: tuple = ()
    color_key: str = ""
    colors: tuple = ()

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> Fingerprint:
        tupled = ("principal_moments", "bbox_min", "bbox_max", "inertia")
        values = {key: (tuple(value) if key in tupled else value) for key, value in data.items()}
        values["colors"] = tuple(tuple(color) for color in data.get("colors", ()))
        values["face_types"] = dict(data["face_types"])
        return cls(**values)


def _brep_faces(shape, seat: float = 0.0) -> list:
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    faces = []
    explorer = TopExp_Explorer(shape, TopAbs_FACE)
    while explorer.More():
        face = TopoDS.Face(explorer.Current())
        explorer.Next()
        kind = BRepAdaptor_Surface(face, False).GetType().name.removeprefix("GeomAbs_")
        props = GProp_GProps()
        BRepGProp.SurfaceProperties_s(face, props)
        middle = props.CentreOfMass()
        faces.append(
            (
                kind,
                _grid(props.Mass(), BREP_AREA_MM2),
                _grid(middle.X(), BREP_MM),
                _grid(middle.Y(), BREP_MM),
                _grid(middle.Z() - seat, BREP_MM),
            )
        )
    faces.sort()
    return faces


def _tensor_cells(tensor, moments) -> list[float]:
    """The inertia tensor on a grid of TENSOR_SHARE of the largest moment."""
    scale = max(max(moments, default=0.0), 1e-12) * TENSOR_SHARE
    return [_grid(value / scale, 1.0) for value in tensor]


def fingerprint(shape, measured: Measurements, seat: float = 0.0, label=None) -> Fingerprint:
    """Fingerprint a shape placed in the component frame, reusing its measurements."""
    occ.load()
    low = measured.bbox_min[:2] + (round(measured.bbox_min[2] - seat, 6),)
    high = measured.bbox_max[:2] + (round(measured.bbox_max[2] - seat, 6),)
    centroid = measured.centroid[:2] + (measured.centroid[2] - seat,)
    palette = sorted(occ.colors(label)) if label is not None else []
    invariant = {
        "volume": _sig(measured.volume),
        "area": _sig(measured.area),
        "moments": [_sig(value) for value in measured.principal_moments],
        "faces": measured.face_types,
    }
    framed = dict(
        invariant,
        bbox=[_grid(value, FRAME_MM) for value in low + high],
        centroid=[_grid(value, FRAME_MM) for value in centroid],
        inertia=_tensor_cells(measured.inertia, measured.principal_moments),
    )
    return Fingerprint(
        shape_key=_digest(invariant),
        frame_key=_digest(framed),
        brep_key=_digest(_brep_faces(shape, seat)),
        volume=measured.volume,
        area=measured.area,
        principal_moments=measured.principal_moments,
        inertia=measured.inertia,
        color_key=_digest(palette) if palette else "",
        colors=tuple(palette),
        face_types=measured.face_types,
        bbox_min=low,
        bbox_max=high,
        seat=seat,
    )


def _close(a: float, b: float, relative: float, absolute: float = 1e-6) -> bool:
    return abs(a - b) <= max(absolute, relative * max(abs(a), abs(b)))


def same(
    a: Fingerprint, b: Fingerprint, *, frame: bool = True, relative: float = RELATIVE, frame_mm: float = 0.02
) -> bool:
    """
    Whether two fingerprints are the same model, with tolerances instead of rounding.

    Face-type counts must match exactly: a re-export does not add a face. With `frame`, the
    bounding boxes must also agree to `frame_mm`.
    """
    if a.face_types != b.face_types:
        return False
    if not (_close(a.volume, b.volume, relative) and _close(a.area, b.area, relative)):
        return False
    scale = max(max(a.principal_moments), max(b.principal_moments), 1e-12)
    if any(abs(x - y) > relative * scale for x, y in zip(a.principal_moments, b.principal_moments, strict=False)):
        return False
    if frame:
        if (
            a.inertia
            and b.inertia
            and any(abs(x - y) > TENSOR_SHARE * scale for x, y in zip(a.inertia, b.inertia, strict=False))
        ):
            return False  # a mirror image, or the same model turned
        corners = zip(a.bbox_min + a.bbox_max, b.bbox_min + b.bbox_max, strict=False)
        if any(abs(x - y) > frame_mm for x, y in corners):
            return False
    return True
