"""
Measurements of one component's solid, in the component frame, in millimetres.

The component frame: origin at the footprint anchor on the board surface, +Z up off the board
(a bottom-side part is turned over, so its +Z points away from the board too), X along the
footprint's zero rotation. Everything here is computed from the exact B-rep (OpenCascade's
GProp integrals and bounding boxes), not from a mesh, so a coarser or finer tessellation does
not change it.

Source: magpie `step/measure.py` at internal `claud/magpie` `3a0374d3` (boarddd phase F4).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from ._extra import require

require(__name__)

from . import occ  # noqa: E402

__all__ = ["Measurements", "measure", "face_type_counts"]


@dataclass
class Measurements:
    bbox_min: tuple[float, float, float]
    bbox_max: tuple[float, float, float]
    #: Height above the board's top surface: bbox_max z in the component frame.
    height: float
    volume: float
    area: float
    centroid: tuple[float, float, float]
    #: Principal moments of inertia about the centroid (unit density, mm^5), ascending.
    principal_moments: tuple[float, float, float]
    #: The inertia tensor about the centroid in the component frame: (xx, yy, zz, xy, xz, yz).
    #: The products of inertia change sign under a mirror, which the principal moments do not.
    inertia: tuple[float, float, float, float, float, float]
    solids: int
    faces: int
    face_types: dict[str, int]
    flags: list[str] = field(default_factory=list)
    #: Where the board seats the model, above its top surface (KiCad: the copper and pad,
    #: 0.085 mm by default). `height` is measured from the board, so the part's own height is
    #: `height - seat`. 0 until the splitter, which knows each model's seat, fills it in.
    seat: float = 0.0

    @property
    def own_height(self) -> float:
        """The part's own height: its top above its seat."""
        return round(self.height - self.seat, 6)

    @property
    def size(self) -> tuple[float, float, float]:
        return tuple(round(high - low, 6) for low, high in zip(self.bbox_min, self.bbox_max, strict=False))

    def to_dict(self) -> dict:
        data = asdict(self)
        data["size"] = self.size
        data["own_height"] = self.own_height
        return data

    @classmethod
    def from_dict(cls, data: dict) -> Measurements:
        """The inverse of `to_dict` (derived fields are dropped), for the model cache."""

        return cls(
            bbox_min=tuple(data["bbox_min"]),
            bbox_max=tuple(data["bbox_max"]),
            height=data["height"],
            volume=data["volume"],
            area=data["area"],
            centroid=tuple(data["centroid"]),
            principal_moments=tuple(data["principal_moments"]),
            inertia=tuple(data["inertia"]),
            solids=data["solids"],
            faces=data["faces"],
            face_types=dict(data["face_types"]),
            flags=list(data["flags"]),
            seat=data.get("seat", 0.0),
        )

    def lifted(self, dz: float) -> Measurements:
        """
        The same measurements with the solid `dz` higher: from the seat frame, where models are
        measured once for every board, to a board's component frame, where its seat is `dz`.
        Only z moves; the one flag that depends on it is decided again.
        """

        def up(point):
            return point[:2] + (_r(point[2] + dz),)

        low = up(self.bbox_min)
        flags = [flag for flag in self.flags if flag != "below_board_surface"]
        if low[2] < -0.05:
            flags.append("below_board_surface")
        return Measurements(
            bbox_min=low,
            bbox_max=up(self.bbox_max),
            height=_r(self.height + dz),
            volume=self.volume,
            area=self.area,
            centroid=up(self.centroid),
            principal_moments=self.principal_moments,
            inertia=self.inertia,
            solids=self.solids,
            faces=self.faces,
            face_types=self.face_types,
            flags=flags,
            seat=self.seat,
        )


def _r(value: float, places: int = 6) -> float:
    return round(float(value), places) + 0.0


def face_type_counts(shape) -> tuple[int, dict[str, int]]:
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    counts: dict[str, int] = {}
    total = 0
    explorer = TopExp_Explorer(shape, TopAbs_FACE)
    while explorer.More():
        kind = BRepAdaptor_Surface(TopoDS.Face(explorer.Current()), False).GetType()
        name = kind.name.removeprefix("GeomAbs_").lower()
        counts[name] = counts.get(name, 0) + 1
        total += 1
        explorer.Next()
    return total, dict(sorted(counts.items()))


def _count(shape, kind) -> int:
    from OCP.TopExp import TopExp_Explorer

    explorer = TopExp_Explorer(shape, kind)
    found = 0
    while explorer.More():
        found += 1
        explorer.Next()
    return found


def bbox(shape) -> tuple[tuple[float, float, float], tuple[float, float, float]] | None:
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib

    box = Bnd_Box()
    BRepBndLib.AddOptimal_s(shape, box, False, False)
    if box.IsVoid():
        return None
    low, high = box.CornerMin(), box.CornerMax()
    return (low.X(), low.Y(), low.Z()), (high.X(), high.Y(), high.Z())


def measure(shape) -> Measurements | None:
    """Measure a shape already placed in the component frame. None when it has no faces."""
    occ.load()
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    from OCP.TopAbs import TopAbs_SOLID

    box = bbox(shape)
    faces, types = face_type_counts(shape)
    if box is None or not faces:
        return None
    low, high = box
    flags: list[str] = []

    volume = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, volume)
    surface = GProp_GProps()
    BRepGProp.SurfaceProperties_s(shape, surface)
    solids = _count(shape, TopAbs_SOLID)
    if solids == 0:
        flags.append("no_solid")  # shells or loose faces: volume means nothing
    mass = volume.Mass()
    if mass <= 0:
        flags.append("nonpositive_volume")
        center = surface.CentreOfMass()
        moments = (0.0, 0.0, 0.0)
        tensor = (0.0,) * 6
    else:
        center = volume.CentreOfMass()
        principal = volume.PrincipalProperties()
        moments = tuple(sorted(_r(value, 9) for value in principal.Moments()))
        matrix = volume.MatrixOfInertia()
        tensor = tuple(
            _r(matrix.Value(row, column), 9) for row, column in ((1, 1), (2, 2), (3, 3), (1, 2), (1, 3), (2, 3))
        )

    if low[2] < -0.05:
        flags.append("below_board_surface")  # through-hole leads, or a model sunk into the board

    return Measurements(
        bbox_min=tuple(_r(value) for value in low),
        bbox_max=tuple(_r(value) for value in high),
        height=_r(high[2]),
        volume=_r(mass),
        area=_r(surface.Mass()),
        centroid=(_r(center.X()), _r(center.Y()), _r(center.Z())),
        principal_moments=moments,
        inertia=tensor,
        solids=solids,
        faces=faces,
        face_types=types,
        flags=flags,
    )
