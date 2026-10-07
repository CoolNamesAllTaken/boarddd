"""
Measurements of one component's solid, in the component frame, in millimetres.

The component frame: origin at the footprint anchor on the board surface, +Z up off the board
(a bottom-side part is turned over, so its +Z points away from the board too), X along the
footprint's zero rotation. Everything here is computed from the exact B-rep (OpenCascade's
GProp integrals and bounding boxes), not from a mesh, so a coarser or finer tessellation does
not change it. The one exception is the pick-surface outline, which is a polygon by nature;
its edges are discretized at PICK_DEFLECTION_MM.

The pick surface is what a nozzle lands on: the largest planar face pointing +Z at or near
the top of the part. A nozzle face has to fit inside it, so we also report the largest
circle and axis-aligned rectangle that fit, centered on the component origin (where the
machine picks by default) and on the face's own centroid.

Source: magpie `step/measure.py` at internal `claud/magpie` `3a0374d3` (boarddd phase F4).
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field

from ._extra import require

require(__name__)

from . import occ  # noqa: E402

__all__ = [
    "Measurements",
    "PickSurface",
    "Fit",
    "measure",
    "face_type_counts",
    "PICK_NEAR_MM",
    "PICK_NORMAL_MIN",
    "PICK_DEFLECTION_MM",
]

#: How far below the very top a +Z face may sit and still be the pick surface: a chip's
#: terminations stand a few hundredths proud of its body, and the body is what gets picked.
PICK_NEAR_MM = 0.15
#: ...or this share of the part's height, for tall parts with a lip or a raised marking.
PICK_NEAR_SHARE = 0.05
#: cos(8 degrees): planar faces whose normal is within this of +Z count as facing up.
PICK_NORMAL_MIN = math.cos(math.radians(8.0))
#: Faces within this of each other in z are one surface: a body top split by a marking, or a
#: cathode band recessed 0.01 mm into it, is still sealed by a nozzle laid across it.
PICK_COPLANAR_MM = 0.02
PICK_DEFLECTION_MM = 0.005


@dataclass
class Fit:
    """The largest circle and centered axis-aligned rectangle inside the pick surface."""

    center: tuple[float, float]
    #: Diameter of the largest circle centered here; 0 when the center is off the surface.
    circle_d: float
    #: (width along X, depth along Y) of the largest-area centered rectangle.
    rect: tuple[float, float]


@dataclass
class PickSurface:
    z: float
    area: float
    centroid: tuple[float, float]
    #: Polygons as [[exterior ring], [hole ring], ...] with rings as [(x, y), ...].
    outline: list
    at_origin: Fit
    at_centroid: Fit
    #: How far below the part's top the surface is (0 for a flat-topped part).
    below_top: float


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
    pick: PickSurface | None
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

        def fit(found):
            return Fit(center=tuple(found["center"]), circle_d=found["circle_d"], rect=tuple(found["rect"]))

        pick = data.get("pick")
        if pick is not None:
            pick = PickSurface(
                z=pick["z"],
                area=pick["area"],
                centroid=tuple(pick["centroid"]),
                outline=[[[tuple(point) for point in ring] for ring in polygon] for polygon in pick["outline"]],
                at_origin=fit(pick["at_origin"]),
                at_centroid=fit(pick["at_centroid"]),
                below_top=pick["below_top"],
            )
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
            pick=pick,
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

        pick = self.pick
        if pick is not None:
            pick = PickSurface(
                z=_r(pick.z + dz),
                area=pick.area,
                centroid=pick.centroid,
                outline=pick.outline,
                at_origin=pick.at_origin,
                at_centroid=pick.at_centroid,
                below_top=pick.below_top,
            )
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
            pick=pick,
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

    pick = pick_surface(shape, high[2])
    if pick is None:
        flags.append("no_planar_top")
    elif pick.below_top > PICK_NEAR_MM:
        flags.append("pick_below_top")
    if pick is not None and pick.at_origin.circle_d == 0:
        flags.append("origin_off_pick_surface")
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
        pick=pick,
        flags=flags,
    )


# ─── Pick surface ────────────────────────────────────────────────────────────


def _up_faces(shape):
    """Every planar face whose outward normal points +Z, as (z, area, face)."""
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.BRepGProp import BRepGProp, BRepGProp_Face
    from OCP.GeomAbs import GeomAbs_Plane
    from OCP.gp import gp_Pnt, gp_Vec
    from OCP.GProp import GProp_GProps
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    found = []
    explorer = TopExp_Explorer(shape, TopAbs_FACE)
    while explorer.More():
        face = TopoDS.Face(explorer.Current())
        explorer.Next()
        adaptor = BRepAdaptor_Surface(face, True)
        if adaptor.GetType() != GeomAbs_Plane:
            continue
        # BRepGProp_Face reverses the normal for a reversed face, so this is the outward one.
        probe = BRepGProp_Face(face)
        u0, u1, v0, v1 = probe.Bounds()
        point, normal = gp_Pnt(), gp_Vec()
        probe.Normal((u0 + u1) / 2, (v0 + v1) / 2, point, normal)
        if normal.Magnitude() < 1e-12 or normal.Z() / normal.Magnitude() < PICK_NORMAL_MIN:
            continue
        props = GProp_GProps()
        BRepGProp.SurfaceProperties_s(face, props)
        found.append((props.CentreOfMass().Z(), props.Mass(), face))
    return found


def _face_polygon(face):
    """A planar face's region in XY, from a fine tessellation of it."""
    from OCP.BRep import BRep_Tool
    from OCP.BRepMesh import BRepMesh_IncrementalMesh
    from OCP.TopLoc import TopLoc_Location
    from shapely.geometry import Polygon
    from shapely.ops import unary_union

    BRepMesh_IncrementalMesh(face, PICK_DEFLECTION_MM, False, 0.1, False)
    location = TopLoc_Location()
    mesh = BRep_Tool.Triangulation_s(face, location)
    if mesh is None:
        return None
    trsf = location.Transformation()
    nodes = []
    for index in range(1, mesh.NbNodes() + 1):
        point = mesh.Node(index).Transformed(trsf)
        nodes.append((point.X(), point.Y()))
    triangles = []
    for index in range(1, mesh.NbTriangles() + 1):
        a, b, c = mesh.Triangle(index).Get()
        triangle = Polygon([nodes[a - 1], nodes[b - 1], nodes[c - 1]])
        if triangle.area > 1e-12:
            triangles.append(triangle)
    if not triangles:
        return None
    # A tiny buffer out and back welds triangles that share an edge only to rounding.
    return unary_union(triangles).buffer(1e-6).buffer(-1e-6)


def _rings(geometry) -> list:
    polygons = list(geometry.geoms) if hasattr(geometry, "geoms") else [geometry]
    out = []
    for polygon in polygons:
        if polygon.is_empty or polygon.geom_type != "Polygon":
            continue
        rings = [polygon.exterior] + list(polygon.interiors)
        out.append([[(round(x, 4), round(y, 4)) for x, y in ring.coords] for ring in rings])
    return out


def fit_inside(region, center: tuple[float, float]) -> Fit:
    """The largest circle and the largest-area axis-aligned rectangle centered at `center`."""
    import numpy as np
    import shapely
    from shapely.geometry import Point

    point = Point(center)
    if not region.contains(point):
        return Fit(center=(round(center[0], 4), round(center[1], 4)), circle_d=0.0, rect=(0.0, 0.0))
    radius = region.boundary.distance(point)
    shapely.prepare(region)
    min_x, min_y, max_x, max_y = region.bounds
    reach = math.hypot(max_x - min_x, max_y - min_y)
    cx, cy = center

    def largest(degrees: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """For each diagonal angle, the biggest centered rectangle: (area, width, depth)."""
        ux, uy = np.cos(np.radians(degrees)), np.sin(np.radians(degrees))
        low, high = np.zeros_like(degrees), np.full_like(degrees, reach)
        # Bisect every angle at once: one vectorized containment test per step.
        for _ in range(28):
            middle = (low + high) / 2
            boxes = shapely.box(cx - middle * ux, cy - middle * uy, cx + middle * ux, cy + middle * uy)
            fits = shapely.contains(region, boxes)
            low, high = np.where(fits, middle, low), np.where(fits, high, middle)
        return 4 * low * low * ux * uy, 2 * low * ux, 2 * low * uy

    # Sweep the diagonal's angle (the aspect ratio) coarsely, then refine about the best.
    coarse = np.arange(2.0, 90.0, 2.0)
    area, width, depth = largest(coarse)
    around = coarse[int(np.argmax(area))] + np.arange(-2.0, 2.05, 0.1)
    around = around[(around > 0.0) & (around < 90.0)]
    fine_area, fine_width, fine_depth = largest(around)
    if fine_area.max() >= area.max():
        area, width, depth = fine_area, fine_width, fine_depth
    best = int(np.argmax(area))
    return Fit(
        center=(round(cx, 4), round(cy, 4)),
        circle_d=round(2 * radius, 4),
        rect=(round(float(width[best]), 4), round(float(depth[best]), 4)),
    )


def pick_surface(shape, top: float) -> PickSurface | None:
    """The largest +Z planar surface at or near `top`, or None when there is none."""
    from shapely.ops import unary_union

    faces = _up_faces(shape)
    if not faces:
        return None
    height_allowance = max(PICK_NEAR_MM, PICK_NEAR_SHARE * max(top, 0.0))
    near = [entry for entry in faces if entry[0] >= top - height_allowance]
    if not near:
        return None

    # Group coplanar faces; the pick surface is the largest connected region of one group.
    groups: list[list] = []
    for entry in sorted(near, key=lambda item: -item[0]):
        for group in groups:
            if abs(group[0][0] - entry[0]) <= PICK_COPLANAR_MM:
                group.append(entry)
                break
        else:
            groups.append([entry])

    best = None
    for group in groups:
        polygons = [
            polygon
            for polygon in (_face_polygon(face) for _z, _a, face in group)
            if polygon is not None and not polygon.is_empty
        ]
        if not polygons:
            continue
        merged = unary_union(polygons)
        parts = list(merged.geoms) if hasattr(merged, "geoms") else [merged]
        region = max(parts, key=lambda part: part.area)
        z = max(entry[0] for entry in group)
        if best is None or region.area > best[1].area:
            best = (z, region)
    if best is None:
        return None

    z, region = best
    # Drop the repeated vertices a triangle union leaves along straight edges.
    region = region.simplify(0.0005, preserve_topology=True)
    centroid = region.centroid
    return PickSurface(
        z=_r(z),
        area=_r(region.area),
        centroid=(_r(centroid.x, 4), _r(centroid.y, 4)),
        outline=_rings(region),
        at_origin=fit_inside(region, (0.0, 0.0)),
        at_centroid=fit_inside(region, (centroid.x, centroid.y)),
        below_top=_r(top - z),
    )
