"""
Where every component sits, read out of a STEP file's text.

A KiCad STEP export of an assembled board is an assembly: one product per distinct footprint,
instanced once per component. The instance is where the reference designator lives --

    #12885 = NEXT_ASSEMBLY_USAGE_OCCURRENCE('17','C1','',#5,#311,$);

-- and it is the only place it lives. The PRODUCT entries are deduplicated prototypes, so all
thirty-nine capacitors on one real board share one PRODUCT called `C_0201_0603Metric`. That matters
more than it sounds: the browser-side tessellator we use resolves instance labels back to their
prototype and hands us thirty-nine meshes all called the same thing. This module is how a mesh
gets a name again.

Each instance's placement is reached by a short walk:

    NEXT_ASSEMBLY_USAGE_OCCURRENCE  <- PRODUCT_DEFINITION_SHAPE
                                    -> CONTEXT_DEPENDENT_SHAPE_REPRESENTATION
                                    -> (REPRESENTATION_RELATIONSHIP ... ITEM_DEFINED_TRANSFORMATION)
                                    -> AXIS2_PLACEMENT_3D  ->  point, z axis, x axis

The z axis is the side: KiCad writes (0,0,-1) for a part on the bottom. The point is where the
component was placed, in the board's own frame -- which is NOT the frame the pick-and-place
file uses, hence `boarddd.step.registration`.

Where a component's placement is is not where its solids are. The placement is where the
model's own origin lands, and a library part need not be drawn around its origin: the KiCad
library's U.FL is modeled 55 mm away from its, and the footprint carries an offset to drag it
back onto the pad. KiCad folds that offset into the placement, so the U.FL on one real board
is placed 43 mm off the edge of the board while its body sits exactly where it should. A
sub-board is the same story for a different reason -- its solids are in its own page frame,
and its origin is the corner of its own outline, 14 mm from the middle of the module.

So `instances(text, geometry=True)` also reads where the solids are, by walking the assembly
tree and transforming each product's vertices into the board's frame. Vertices and not every
CARTESIAN_POINT: surfaces and curves carry placement points of their own, which sit wherever
the authoring tool left them -- the U.FL has one at its own origin, 55 mm from anything you
could see -- and a box drawn around those is not a box around the part.

Two things this has to survive, both seen in real exports:

* Entities wrap across lines, so the file cannot be read a line at a time. It is split on `;`.
* A vendor-supplied component model can be an assembly in its own right, with its own internal
  instances (BODY-QFN, PIN1-ID). Those are instances of a component, not of the board, so only
  the ones whose parent is the root product count. One real board has 125 occurrences
  in one revision and 72 in the next, entirely because of one TI part.

Pure: no settings, no filesystem.

Source: `magpie/step/stepmeta.py` at magpie commit 3a0374d3 (copied for boarddd phase F1; magpie's later
changes are re-diffed before magpie switches to this module).
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

__all__ = [
    "Extent",
    "Instance",
    "SIDE_TOP",
    "SIDE_BOTTOM",
    "SURFACE_TOLERANCE_MM",
    "entities",
    "instances",
    "board_nodes",
    "board_z_fallback",
    "product_names",
    "shape_reps",
]

SIDE_TOP, SIDE_BOTTOM = "top", "bottom"

#: How far a placement origin may sit outside its own solids and still be read as the plane
#: they were mounted on. Normally it is inside them; the allowance is for a part drawn about
#: one of its own edges, whose origin lands just outside -- one real module's is 0.1 mm out.
#: A model with a large offset baked in is nowhere near, and its placement z is not a surface.
SURFACE_TOLERANCE_MM = 1.0

#: KiCad names the board's own bodies -- substrate, mask, silkscreen, pads -- with an
#: OpenCascade label path rather than a designator, because they are not components and it has
#: nothing else to call them. They are recognized and set aside, not discarded: their count is
#: a useful cross-check, and the browser uses their meshes to find the board's surfaces.
BOARD_NODE = re.compile(r"^=>\[")

#: An entity's `#id`. Bounded: Python refuses to turn more than 4300 digits into an int, so
#: `#999...9` five thousand digits long was a ValueError -- a 500 -- rather than an entity
#: nothing refers to. No real file has an id past eighteen digits, and one that does is not
#: an entity to this module.
_ID = r"#(\d{1,18})(?!\d)"
_ENTITY = re.compile(r"^\s*" + _ID + r"\s*=\s*(.*)$", re.S)
_TYPE = re.compile(r"^\s*([A-Z_0-9]+)\s*\(")
_REF = re.compile(_ID)
#: Quoted STEP strings. Backslash escapes are not unescaped: designators do not contain them,
#: and a half-applied unescaping would be worse than none.
_STRING = re.compile(r"'((?:[^']|'')*)'")
_NUMBER = re.compile(r"-?\d+\.?\d*(?:[eE][-+]?\d+)?")


@dataclass(frozen=True)
class Extent:
    """The box everything under one occurrence occupies, in the board's own frame."""

    min: tuple[float, float, float]
    max: tuple[float, float, float]
    #: How many solids went into it. A chip resistor brings one; a radio module can bring 128.
    #: The count is what says a box is worth sweeping rather than pointing at.
    solids: int

    @property
    def center(self) -> tuple[float, float, float]:
        return ((self.min[0] + self.max[0]) / 2, (self.min[1] + self.max[1]) / 2, (self.min[2] + self.max[2]) / 2)


@dataclass(frozen=True)
class Instance:
    """One placed component, as the STEP file describes it."""

    seq: str
    ref: str
    x: float
    y: float
    z: float
    #: The placement's z axis. (0, 0, -1) means the part is on the underside.
    z_dir: tuple[float, float, float]
    #: The placement's reference direction, which encodes rotation. Carried but not
    #: interpreted -- meshes arrive already transformed, so nothing here needs the angle yet,
    #: and KiCad's convention differs between the two sides.
    x_dir: tuple[float, float, float]
    #: What this occurrence is an instance OF: `C_0603_1608Metric`, `myboard_PCB`. The
    #: prototype's name, so every one of the thirty-nine capacitors carries the same one -- it
    #: says what a thing is, never which one it is. It is how the board's substrate is told
    #: from the films over it, which are all board bodies and only one of which is the board.
    product: str = ""
    #: Where this occurrence's solids actually are, when the geometry was read -- that is,
    #: `instances(text, geometry=True)`. None when it was not asked for, and None when the
    #: occurrence has no geometry under it at all.
    extent: Extent | None = None
    #: True when this occurrence is an assembly in its own right: a module, a sub-board, a
    #: vendor model with a body and a pin-1 marker inside it. It matters to whoever is
    #: matching solids to designators, because the CAD kernel hands such a thing back as many
    #: separate solids and never as one.
    assembly: bool = False

    @property
    def side(self) -> str:
        """
        Which face this was placed on, or `''` when the file does not say clearly.

        Usually the placement's z axis settles it: KiCad writes (0,0,-1) for a part on the
        underside, and on two real boards every bottom-side part is marked that way. But the
        axis describes the whole rotation, not just the side, and a model whose own frame was
        authored lying down comes through with z pointing along the board -- two of one real
        board's sixty-six components do exactly that. Those get no answer here rather than a
        coin-flip, because the pick-and-place file already knows the side for every component
        in the STEP and is the better authority. See registration, which checks the two agree.
        """
        vertical = self.z_dir[2]
        if vertical > 0.5:
            return SIDE_TOP
        if vertical < -0.5:
            return SIDE_BOTTOM
        return ""

    @property
    def is_board(self) -> bool:
        return bool(BOARD_NODE.match(self.ref))

    @property
    def where(self) -> tuple[float, float]:
        """
        Where to look for this component's solids on the board.

        The middle of them when the geometry was read, and the placement origin otherwise.
        The two are the same point for almost every component, and 55 mm apart for a model
        with a large offset baked into it -- which is a component nothing could find by
        looking near its origin, because there is nothing there.
        """
        if self.extent is None:
            return self.x, self.y
        middle = self.extent.center
        return middle[0], middle[1]

    @property
    def surface(self) -> float | None:
        """
        The plane this component was mounted on, or None when its placement z is not one.

        A placement's z is the board surface under that component, which is a better answer
        than one plane fitted to the whole board: it is per component, it survives a board
        that is not flat, and it needs no guessing. That holds as long as the placement is
        where the part is. When the model carries a large offset the origin is somewhere off
        the board entirely, its z is 23 mm in the air, and the honest answer is that this file
        does not say -- see SURFACE_TOLERANCE_MM.
        """
        if self.extent is None:
            return self.z
        low, high = self.extent.min, self.extent.max
        near = (
            low[0] - SURFACE_TOLERANCE_MM <= self.x <= high[0] + SURFACE_TOLERANCE_MM
            and low[1] - SURFACE_TOLERANCE_MM <= self.y <= high[1] + SURFACE_TOLERANCE_MM
        )
        return self.z if near else None


def entities(text: str) -> dict[int, tuple[str, str]]:
    """
    ``{id: (TYPE, arguments)}`` for every entity in the data section.

    Split on `;` rather than read line by line: a single entity routinely spans several lines,
    and the placement chain below is exactly the kind of long entity that gets wrapped.
    """
    body = text.split("DATA;", 1)[-1].split("ENDSEC;", 1)[0]

    found: dict[int, tuple[str, str]] = {}
    for statement in body.split(";"):
        match = _ENTITY.match(statement)
        if not match:
            continue
        identifier, rest = int(match.group(1)), match.group(2)
        kind = _TYPE.match(rest)
        # A complex entity -- `#7 = ( A(...) B(...) )` -- has no single type. The walk below
        # reaches those by reference and reads them whole, so recording them untyped is enough.
        found[identifier] = ((kind.group(1) if kind else ""), rest)
    return found


def product_names(found: dict[int, tuple[str, str]]) -> dict[int, str]:
    """
    ``{product definition id: the product's name}``.

    Two links out from each definition -- to its formation, and from there to the product that
    carries the name. Only the definitions are walked, of which a board has a hundred, so this
    costs nothing next to reading the file.
    """
    names: dict[int, str] = {}
    for identifier, (kind, args) in found.items():
        if kind != "PRODUCT_DEFINITION":
            continue
        links = _refs(args)
        if not links:
            continue
        formation = _refs(found.get(links[0], ("", ""))[1])
        product = formation[0] if formation else None
        if product is None or found.get(product, ("", ""))[0] != "PRODUCT":
            continue
        found_name = _STRING.search(found[product][1])
        names[identifier] = found_name.group(1).replace("''", "'") if found_name else ""
    return names


def _strings(args: str) -> list[str]:
    return [value.replace("''", "'") for value in _STRING.findall(args)]


def _refs(args: str) -> list[int]:
    return [int(value) for value in _REF.findall(args)]


def _triple(found: dict[int, tuple[str, str]], identifier: int | None) -> tuple[float, ...]:
    """The numbers out of a CARTESIAN_POINT or DIRECTION, padded to three."""
    if identifier is None or identifier not in found:
        return (0.0, 0.0, 0.0)
    numbers = [float(value) for value in _NUMBER.findall(found[identifier][1].split("(", 1)[-1])]
    numbers = numbers[:3] + [0.0] * (3 - len(numbers))
    return tuple(numbers)


def _placement(found: dict[int, tuple[str, str]], axis_id: int):
    """position, z direction, x direction from an AXIS2_PLACEMENT_3D."""
    kind, args = found.get(axis_id, ("", ""))
    if kind != "AXIS2_PLACEMENT_3D":
        return None

    parts = _refs(args)
    point = _triple(found, parts[0] if parts else None)
    # Both directions are optional in STEP; their defaults are the identity orientation.
    z_dir = _triple(found, parts[1]) if len(parts) > 1 else (0.0, 0.0, 1.0)
    x_dir = _triple(found, parts[2]) if len(parts) > 2 else (1.0, 0.0, 0.0)
    return point, z_dir, x_dir


def _occurrences(found: dict[int, tuple[str, str]]):
    """Every assembly occurrence as (id, seq, ref, parent_id, child_id)."""
    for identifier, (kind, args) in found.items():
        if kind != "NEXT_ASSEMBLY_USAGE_OCCURRENCE":
            continue
        names = _strings(args)
        links = _refs(args)
        if len(links) < 2:
            continue
        seq = names[0] if names else ""
        ref = names[1] if len(names) > 1 else ""
        yield identifier, seq, ref.strip(), links[0], links[1]


def _root(occurrences) -> int | None:
    """
    The product every top-level occurrence hangs off.

    It is the one that is a parent but never a child. Finding it is what keeps a vendor model's
    own internals -- a QFN's body and its pin-1 dimple, each a perfectly legitimate assembly
    occurrence -- from being counted as components of the board.
    """
    parents = {parent for _id, _seq, _ref, parent, _child in occurrences}
    children = {child for _id, _seq, _ref, _parent, child in occurrences}
    candidates = parents - children
    if len(candidates) == 1:
        return next(iter(candidates))
    if not candidates:
        return None
    # More than one root means an unusual file. The one with the most children is the board;
    # picking deterministically beats guessing differently on each run.
    counts = Counter(parent for _id, _seq, _ref, parent, _child in occurrences)
    return max(candidates, key=lambda parent: (counts[parent], -parent))


def _transforms(found: dict[int, tuple[str, str]]) -> dict[int, int]:
    """
    ``{occurrence id: axis placement id}``.

    The link runs backwards from the shape definition, so the file is indexed once by what each
    CONTEXT_DEPENDENT_SHAPE_REPRESENTATION points at rather than searched per occurrence.
    """
    shapes: dict[int, int] = {}  # occurrence id -> product_definition_shape id
    for identifier, (kind, args) in found.items():
        if kind == "PRODUCT_DEFINITION_SHAPE":
            for ref in _refs(args):
                shapes.setdefault(ref, identifier)

    placements: dict[int, int] = {}
    for _identifier, (kind, args) in found.items():
        if kind != "CONTEXT_DEPENDENT_SHAPE_REPRESENTATION":
            continue
        links = _refs(args)
        if len(links) < 2:
            continue
        relationship, shape = links[0], links[1]

        # The relationship is a complex entity; its ITEM_DEFINED_TRANSFORMATION is the only
        # reference in it that resolves to one.
        transform_id = None
        for ref in _refs(found.get(relationship, ("", ""))[1]):
            if found.get(ref, ("", ""))[0] == "ITEM_DEFINED_TRANSFORMATION":
                transform_id = ref
                break
        if transform_id is None:
            continue

        # Two axis placements: where the part's own origin is, and where it lands on the board.
        # The second is the one that moves.
        axes = [ref for ref in _refs(found[transform_id][1]) if found.get(ref, ("", ""))[0] == "AXIS2_PLACEMENT_3D"]
        if not axes:
            continue
        placements[shape] = axes[-1]

    return {occurrence: placements[shape] for occurrence, shape in shapes.items() if shape in placements}


# ─── Where the solids actually are ───────────────────────────────────────────

_IDENTITY = ((0.0, 0.0, 0.0), ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)))


def _index(found: dict[int, tuple[str, str]]) -> dict[int, list[int]]:
    """``{id: [ids it refers to]}``, once, because the walks below follow references a lot."""
    return {identifier: [int(value) for value in _REF.findall(rest)] for identifier, (_kind, rest) in found.items()}


def shape_reps(found: dict, index: dict[int, list[int]]) -> dict[int, list[int]]:
    """``{product definition id: [shape representation ids]}`` via the definition-shape link."""
    definition_of = {
        identifier: index[identifier][0]
        for identifier, (kind, _rest) in found.items()
        if kind == "PRODUCT_DEFINITION_SHAPE" and index[identifier]
    }
    reps: dict[int, list[int]] = {}
    for identifier, (kind, _rest) in found.items():
        if kind != "SHAPE_DEFINITION_REPRESENTATION" or len(index[identifier]) < 2:
            continue
        definition = definition_of.get(index[identifier][0])
        if definition is not None:
            reps.setdefault(definition, []).append(index[identifier][1])
    return reps


def _vertices(found: dict, index: dict, rep: int, cache: dict) -> list[tuple[float, ...]]:
    """
    Every vertex of one shape representation, in its own frame.

    Vertices rather than every CARTESIAN_POINT the representation reaches. Surfaces and curves
    carry placement points of their own and those sit wherever the part was authored: the U.FL
    solid has exactly one point at its model origin, 55 mm from the body, and counting it makes
    the box eighteen times too long. A vertex is on the part by definition.

    Cached per representation, because a prototype used thirty-nine times is read once and
    transformed thirty-nine times.
    """
    if rep in cache:
        return cache[rep]
    seen: set[int] = set()
    stack = [rep]
    points: list[tuple[float, ...]] = []
    while stack:
        identifier = stack.pop()
        if identifier in seen or identifier not in found:
            continue
        seen.add(identifier)
        if found[identifier][0] == "VERTEX_POINT":
            for ref in index[identifier]:
                if found.get(ref, ("", ""))[0] == "CARTESIAN_POINT":
                    points.append(_triple(found, ref))
        stack.extend(index[identifier])
    cache[rep] = points
    return points


def _unit(vector: tuple[float, ...]) -> tuple[float, float, float] | None:
    length = math.sqrt(sum(value * value for value in vector[:3]))
    if length < 1e-12:
        return None
    return (vector[0] / length, vector[1] / length, vector[2] / length)


def _axes(z_dir, x_dir) -> tuple:
    """
    The three unit axes of a placement, from STEP's axis and reference direction.

    The reference direction is not required to be perpendicular to the axis -- the standard
    says only that it is not parallel -- so the x axis is what is left of it once the axis has
    been projected out. KiCad writes them perpendicular already; a model that came through
    another tool need not.
    """
    z = _unit(z_dir) or (0.0, 0.0, 1.0)
    along = sum(x_dir[i] * z[i] for i in range(3))
    x = _unit((x_dir[0] - along * z[0], x_dir[1] - along * z[1], x_dir[2] - along * z[2]))
    if x is None:
        # Parallel to the axis, which is not a legal placement. Any perpendicular will do:
        # the orientation is already lost, and the position is still worth having.
        x = _unit((z[1] - z[2], z[2] - z[0], z[0] - z[1])) or (1.0, 0.0, 0.0)
    y = (z[1] * x[2] - z[2] * x[1], z[2] * x[0] - z[0] * x[2], z[0] * x[1] - z[1] * x[0])
    return (x, y, z)


def _frame(found: dict, axis_id: int | None) -> tuple:
    """``(origin, (x, y, z))`` for an AXIS2_PLACEMENT_3D, or the identity when there is none."""
    placed = _placement(found, axis_id) if axis_id is not None else None
    if placed is None:
        return _IDENTITY
    origin, z_dir, x_dir = placed
    return (origin[0], origin[1], origin[2]), _axes(z_dir, x_dir)


def _through(outer: tuple, inner: tuple) -> tuple:
    """The frame of something placed by `inner` inside something placed by `outer`."""
    origin, axes = outer
    inner_origin, inner_axes = inner

    def carried(vector):
        return tuple(vector[0] * axes[0][i] + vector[1] * axes[1][i] + vector[2] * axes[2][i] for i in range(3))

    moved = carried(inner_origin)
    return (
        (origin[0] + moved[0], origin[1] + moved[1], origin[2] + moved[2]),
        tuple(carried(axis) for axis in inner_axes),
    )


#: How deep an assembly may nest before the walk stops going down it. A board is two or three
#: levels (board, module, a vendor model's own internals); a file thirty deep was not written
#: by a CAD tool, and three thousand deep ran the recursion out -- a 500 from a public page.
MAX_ASSEMBLY_DEPTH = 32
#: How much the walk may do before it gives up on the solids: distinct (product, orientation)
#: pairs measured, and vertices transformed. The largest real exports here need about 200 and
#: 40 thousand (the PoE pant; the 24 MB pogo board); memoising shared sub-assemblies keeps the
#: ordinary file far below both, and these are the ceiling for one built to defeat the memo by
#: turning each use about a different axis, which would otherwise be exponential again.
MAX_MEASURED = 20_000
MAX_VERTEX_WORK = 5_000_000


class _TooMuchWork(Exception):
    """The walk passed MAX_MEASURED or MAX_VERTEX_WORK; the file is treated as having no geometry."""


def _extents(found: dict, occurrences: list, root: int | None, placements: dict[int, int]) -> dict[int, Extent]:
    """
    ``{occurrence id: the box of everything under it}``, in the board's own frame, for the
    board's own occurrences (the ones directly under the root -- the only ones read).

    Each product's vertices are carried through the placements above it, so a sub-board's
    solids arrive in the board's coordinates rather than its own. Occurrences with no geometry
    beneath them are simply absent.

    A product's box is measured once per ORIENTATION and not once per use. The translation of a
    placement only shifts a box -- min and max commute with adding a constant -- but a rotation
    does not, so the box of a rotated product is found from its rotated vertices, exactly as
    before, and remembered under (product, rotation). Walking every use instead was exponential
    in a file where each sub-assembly uses the next twice: 3.8 KB and 20 levels took 14 s on a
    public page, and each two more levels multiplied that by four. A file that still needs more
    than MAX_VERTEX_WORK vertices transformed is left unmeasured, as if it had no solids, rather
    than holding a worker until the timeout; so is one that needs more than MAX_MEASURED.
    """
    if root is None:
        return {}
    index = _index(found)
    reps = shape_reps(found, index)
    children: dict[int, list[tuple[int, int]]] = {}
    for identifier, _seq, _ref, parent, child in occurrences:
        children.setdefault(parent, []).append((identifier, child))

    cache: dict[int, list] = {}
    measured: dict[tuple, tuple[list, list, int]] = {}
    work = [0]

    def measure(product: int, axes: tuple, above: frozenset) -> tuple[list, list, int]:
        """The box of `product` turned by `axes`, about its own origin: (low, high, solids)."""
        key = (product, tuple(round(value, 9) for axis in axes for value in axis))
        if key in measured:
            return measured[key]
        if len(measured) >= MAX_MEASURED:
            raise _TooMuchWork
        low = [math.inf] * 3
        high = [-math.inf] * 3
        solids = 0
        for rep in reps.get(product, ()):
            points = _vertices(found, index, rep, cache)
            if not points:
                continue
            work[0] += len(points)
            if work[0] > MAX_VERTEX_WORK:
                raise _TooMuchWork
            solids += 1
            for point in points:
                for i in range(3):
                    value = point[0] * axes[0][i] + point[1] * axes[1][i] + point[2] * axes[2][i]
                    if value < low[i]:
                        low[i] = value
                    if value > high[i]:
                        high[i] = value
        for identifier, child in children.get(product, ()):
            # A file whose assembly tree refers to itself is not a tree. Walking it would not
            # terminate, and the rest of the file is still worth reading.
            if child in above or len(above) > MAX_ASSEMBLY_DEPTH:
                continue
            offset, turned = _through(((0.0, 0.0, 0.0), axes), _frame(found, placements.get(identifier)))
            child_low, child_high, child_solids = measure(child, turned, above | {child})
            if not child_solids:
                continue
            solids += child_solids
            for i in range(3):
                low[i] = min(low[i], offset[i] + child_low[i])
                high[i] = max(high[i], offset[i] + child_high[i])
        measured[key] = (low, high, solids)
        return measured[key]

    boxes: dict[int, Extent] = {}
    try:
        for identifier, child in children.get(root, ()):
            if child == root:
                continue
            origin, axes = _frame(found, placements.get(identifier))
            low, high, solids = measure(child, axes, frozenset({root, child}))
            if not solids:
                continue
            boxes[identifier] = Extent(
                min=tuple(origin[i] + low[i] for i in range(3)),
                max=tuple(origin[i] + high[i] for i in range(3)),
                solids=solids,
            )
    except _TooMuchWork:
        return {}
    return boxes


def _parse(text: str, geometry: bool = False):
    found = entities(text)
    occurrences = list(_occurrences(found))
    root = _root(occurrences)
    placements = _transforms(found)
    # An occurrence whose child is itself a parent is an assembly: a module, a sub-board, or a
    # vendor model with its own internals.
    parents = {parent for _id, _seq, _ref, parent, _child in occurrences}
    named = product_names(found)
    boxes = _extents(found, occurrences, root, placements) if geometry else {}

    for identifier, seq, ref, parent, child in occurrences:
        if root is not None and parent != root:
            continue  # a component's own internals, not the board's
        placed = _placement(found, placements[identifier]) if identifier in placements else None
        point, z_dir, x_dir = placed or ((0.0, 0.0, 0.0), (0.0, 0.0, 1.0), (1.0, 0.0, 0.0))
        yield Instance(
            seq=seq,
            ref=ref,
            x=point[0],
            y=point[1],
            z=point[2],
            z_dir=z_dir,
            x_dir=x_dir,
            product=named.get(child, ""),
            extent=boxes.get(identifier),
            assembly=child in parents,
        )


def instances(text: str, *, geometry: bool = False) -> list[Instance]:
    """
    Every placed component, in file order. Board bodies are excluded.

    `geometry` also reads where each one's solids are, which costs another walk of the file --
    about a third again on top of the parse. Worth it for anything that has to find a
    component's solids and pointless for anything that only wants its placement, which is why
    it is asked for rather than always done. See the module docstring for what it is for.
    """
    return [item for item in _parse(text, geometry) if item.ref and not item.is_board]


def board_nodes(text: str, *, geometry: bool = False) -> list[Instance]:
    """The board's own bodies -- substrate, mask, silkscreen, pads."""
    return [item for item in _parse(text, geometry) if item.is_board]


#: A part mounted further than this from the board's face was not mounted ON that face: a
#: daughterboard on standoffs, a part on a spacer. A pad and a mask put a real part about 0.1 mm
#: off the substrate; this allows a thick pad and nothing more.
FACE_MM = 0.5
#: Two faces closer or further apart than this were not both measured off the board.
BOARD_MM = (0.2, 6.0)
#: What a board is when one face is known and the other is not worth believing.
NOMINAL_MM = 1.6


def board_z_fallback(found: list[Instance], substrate: Extent | None = None) -> dict:
    """
    Where the board's surfaces probably are, judged only by where parts were mounted.

    Used when the board's own solid cannot be identified -- a STEP from somewhere other than
    KiCad, where the bodies are not labeled the way we recognize -- and as the board to draw
    before the model has loaded. Components sit ON a surface, so the commonest mounting height
    per side is that surface, and the gap between the two is the board's thickness. A guess,
    and reported as one: heights measured against it inherit whatever the guess got wrong.

    The commonest height on a face can be one part, and one part can be anywhere. The
    parachute test fixture (design 16) has seventy parts underneath and exactly one on top --
    a daughterboard on 11 mm standoffs -- and "the commonest height on top" put the board's top
    face 12.6 mm up, where it was drawn, a centimetre under every part on it. So:

    * given the `substrate` (the model's own board body, which the server can usually name),
      a face's height is taken only from parts mounted within FACE_MM of that face, and is
      the face itself when none were;
    * without it, two faces a board's thickness cannot span are not both believed: the face
      more parts were mounted on is, and the other is put NOMINAL_MM away.
    """

    def commonest(values: list[float]) -> tuple[float | None, int]:
        if not values:
            return None, 0
        # Binned at 10 microns: mounting heights agree exactly in principle, and to within
        # rounding in practice.
        counts = Counter(round(value, 2) for value in values)
        value, count = max(counts.items(), key=lambda pair: (pair[1], -abs(pair[0])))
        return value, count

    # Components whose side the file does not state are left out of both groups rather than
    # assumed onto one: a handful of them landing in the wrong pile would move the mode, and
    # this figure is already the fallback for when nothing better is available.
    tops = [item.z for item in found if item.side == SIDE_TOP]
    bottoms = [item.z for item in found if item.side == SIDE_BOTTOM]

    if substrate is not None:
        face_top, face_bottom = substrate.max[2], substrate.min[2]
        top, _ = commonest([z for z in tops if abs(z - face_top) <= FACE_MM])
        bottom, _ = commonest([z for z in bottoms if abs(z - face_bottom) <= FACE_MM])
        top = round(face_top, 3) if top is None else top
        bottom = round(face_bottom, 3) if bottom is None else bottom
    else:
        (top, on_top), (bottom, underneath) = commonest(tops), commonest(bottoms)
        if (
            top is not None
            and bottom is not None
            and on_top != underneath
            and not BOARD_MM[0] <= top - bottom <= BOARD_MM[1]
        ):
            if on_top > underneath:
                bottom = round(top - NOMINAL_MM, 3)
            else:
                top = round(bottom + NOMINAL_MM, 3)

    thickness = round(top - bottom, 3) if top is not None and bottom is not None else None
    return {"top_z": top, "bottom_z": bottom, "thickness_mm": thickness}
