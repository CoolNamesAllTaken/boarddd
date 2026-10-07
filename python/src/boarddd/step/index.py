"""
A board STEP read as text: its assembly, and any one product cut out as a STEP file of its own.

OpenCascade reads a whole file before it hands back anything, and on a board most of the file
is not the components: the 263 MB vme-wren export with tracks and zones is 90 % copper, and
OCCT took 460 s and 2.5 GB to read it. The components are a small part of the text, and the
same stock models come back on board after board. So the splitter reads the structure from
the text (cheap: an index of where each entity starts), and hands OpenCascade one product at a
time, cut out with everything it reaches and nothing else (`carve`). A product is known by a
hash of that cut-out text with the entity numbers taken out (`identity`), which is the same on
every board the same model was exported to, so measurements, fingerprints and exports are
computed once per model and not once per board.

What a product needs to stand alone:

* everything it reaches: its definition, shape representation and geometry, contexts and
  units; for an assembly (a KiCad footprint holds its model as an instance) its child
  occurrences, their placements and their products, recursively;
* its colours, which point into it from outside: the styled items whose item is in the cut,
  gathered into a new presentation entity (OCCT reads colours from there);
* the application protocol definition, so the header and the schema agree.

Entity numbers are kept, and the new presentation entity takes the next free one.

Pure: no OpenCascade, no filesystem. Works on the file's bytes (latin-1 text). Needs numpy (the
`[step]` extra). `boarddd.step.text` is the stdlib reader of the same structure (refdes, placements,
extents) for when the extra is not installed.

Source: magpie `step/steptext.py` at internal `claud/magpie` `3a0374d3` (boarddd phase F4), renamed
`index` beside F1's `text`.
"""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass, field

from ._extra import require

require(__name__, ("numpy",))

import numpy as np  # noqa: E402

__all__ = ["Index", "Occurrence", "Structure", "structure", "carve", "identity", "axis_matrix"]

_START = re.compile(rb"#(\d{1,18})\s*=\s*")
_REF_BYTES = re.compile(rb"#(\d{1,18})(?!\d)")


def _scan(data: bytes, begin: int, end: int):
    """
    Entity starts and every reference in the DATA section, with numpy over the raw bytes: a
    263 MB file has 25 million references, which as Python objects would be gigabytes.

    Returns (ids, statement starts, body starts, reference positions, referenced numbers).
    A '#' starts a statement when the last non-blank before it is ';' (DATA; ends that way
    too); everything else is a reference.
    """
    raw = np.frombuffer(data, dtype=np.uint8)
    last = len(raw) - 1
    # Byte positions fit in 32 bits below 2 GB (the size guard is below that): half the memory.
    small = np.int32 if len(raw) < 2**31 - 1 else np.int64
    hashes = (np.flatnonzero(raw[begin:end] == ord("#")) + begin).astype(small)
    # Not inside a string ('Context #869' is a name, not a reference): inside means an odd
    # number of quotes before it, which an escaped '' does not change.
    quotes = (np.flatnonzero(raw[begin:end] == ord("'")) + begin).astype(small)
    hashes = hashes[np.searchsorted(quotes, hashes) % 2 == 0]
    del quotes
    numbers = np.zeros(len(hashes), dtype=np.int64)
    length = np.zeros(len(hashes), dtype=np.int8)
    running = np.ones(len(hashes), dtype=bool)
    for step in range(1, 19):
        values = raw[np.minimum(hashes + step, last)]
        running &= (values >= ord("0")) & (values <= ord("9"))
        if not running.any():
            break
        numbers = np.where(running, numbers * 10 + (values.astype(np.int64) - ord("0")), numbers)
        length += running
    good = length > 0
    hashes, numbers, length = hashes[good], numbers[good], length[good]

    def blank(values):
        return (values == 32) | (values == 10) | (values == 13) | (values == 9)

    # A statement's '#' follows a blank or ';'; a reference's follows '(' or ','. Only the
    # candidates get the look either side, a few bytes each way (writers put a newline and a
    # few spaces there), never whole-file arrays: 8 bytes per byte of a 263 MB file.
    candidate = np.flatnonzero(blank(raw[np.maximum(hashes - 1, 0)]) | (raw[np.maximum(hashes - 1, 0)] == ord(";")))
    spots, lengths = hashes[candidate], length[candidate]
    before = np.full(len(spots), -1, dtype=np.int64)
    for step in range(1, 33):
        at = np.maximum(spots - step, 0)
        found = (before < 0) & ~blank(raw[at])
        before[found] = at[found]
    starts_here = (before >= 0) & (raw[np.maximum(before, 0)] == ord(";"))
    after = np.full(len(spots), -1, dtype=np.int64)
    for step in range(0, 32):
        at = np.minimum(spots + 1 + lengths + step, last)
        found = (after < 0) & ~blank(raw[at])
        after[found] = at[found]
    starts_here &= (after >= 0) & (raw[np.maximum(after, 0)] == ord("="))
    chosen = candidate[starts_here]
    ids = numbers[chosen]
    starts = hashes[chosen].astype(np.int64)
    body = after[starts_here] + 1
    begun = np.zeros(len(body), dtype=bool)
    for _step in range(32):
        moving = ~begun & blank(raw[np.minimum(body, last)])
        if not moving.any():
            break
        body = body + moving
    bodies = body
    return ids, starts, bodies, hashes, numbers


def _first(values: np.ndarray) -> np.ndarray:
    """`values` without repeats, each kept where it first appears."""
    if values.size == 0:
        return values
    _unique, first = np.unique(values, return_index=True)
    return values[np.sort(first)]


_REF = re.compile(r"#(\d{1,18})(?!\d)")
_STRING = re.compile(r"'((?:[^']|'')*)'")
_NUMBER = re.compile(r"-?\d+\.?\d*(?:[eE][-+]?\d+)?")
_TYPE = re.compile(r"\s*([A-Z_0-9]+)\s*\(")


class Index:
    """Where each entity's statement is in the file; entities are parsed only when asked for."""

    def __init__(self, data: bytes):
        self.data = data
        begin = data.find(b"DATA;")
        end = data.rfind(b"ENDSEC;")
        if begin < 0 or end < begin:
            raise ValueError("not a STEP file: no DATA section")
        self.header = data[:begin]
        self.data_end = end
        self.ids, self.starts, self.bodies, ref_positions, ref_numbers = _scan(data, begin, end)
        if len(self.ids) > 1 and np.any(np.diff(self.ids) <= 0):
            order = np.argsort(self.ids, kind="stable")
            self.order = order
        else:
            self.order = None
        self.next_starts = np.append(self.starts[1:], end)
        self._parsed: dict[int, tuple[str, str]] = {}
        self._csr(ref_positions, ref_numbers)

    def _csr(self, positions: np.ndarray, numbers: np.ndarray) -> None:
        """
        Every reference in the file, once: entity i refers to `targets[offsets[i]:offsets[i+1]]`
        (entity indices, in the order written). Walks of the graph are then array operations.
        """
        owner = (np.searchsorted(self.starts, positions, side="right") - 1).astype(np.int32)
        # The '#12' that starts entity 12's own statement is its name, not a reference.
        keep = (owner >= 0) & (positions >= self.bodies[np.clip(owner, 0, None)])
        owner, numbers = owner[keep], numbers[keep]
        if self.order is not None:
            sorted_ids = self.ids[self.order]
            at = np.clip(np.searchsorted(sorted_ids, numbers), 0, len(sorted_ids) - 1)
            target = np.where(sorted_ids[at] == numbers, self.order[at], -1)
        else:
            at = np.clip(np.searchsorted(self.ids, numbers), 0, len(self.ids) - 1)
            target = np.where(self.ids[at] == numbers, at, -1)
        valid = target >= 0
        self.targets = target[valid].astype(np.int32 if len(self.ids) < 2**31 - 1 else np.int64)
        self.offsets = np.searchsorted(owner[valid], np.arange(len(self.ids) + 1))

    def position(self, identifier: int) -> int | None:
        return self._position(identifier)

    def walk(self, starts) -> np.ndarray:
        """
        Entity indices reachable from `starts` (indices), breadth first, references in the
        order written: a deterministic order that does not depend on the entities' numbers.
        """
        visited = np.zeros(len(self.ids), dtype=bool)
        return self.walk_more(starts, visited)

    def walk_more(self, starts, visited: np.ndarray) -> np.ndarray:
        frontier = _first(np.asarray(starts, dtype=np.int64))
        frontier = frontier[~visited[frontier]]
        found = []
        while frontier.size:
            visited[frontier] = True
            found.append(frontier)
            low, high = self.offsets[frontier], self.offsets[frontier + 1]
            counts = high - low
            if not counts.sum():
                break
            spans = np.repeat(low - np.concatenate(([0], np.cumsum(counts)[:-1])), counts) + np.arange(counts.sum())
            following = self.targets[spans]
            following = following[~visited[following]]
            frontier = _first(following)
        return np.concatenate(found) if found else np.zeros(0, dtype=np.int64)

    def __len__(self) -> int:
        return len(self.ids)

    def _position(self, identifier: int) -> int | None:
        if self.order is not None:
            sorted_ids = self.ids[self.order]
            at = int(np.searchsorted(sorted_ids, identifier))
            if at < len(sorted_ids) and sorted_ids[at] == identifier:
                return int(self.order[at])
            return None
        at = int(np.searchsorted(self.ids, identifier))
        return at if at < len(self.ids) and self.ids[at] == identifier else None

    def __contains__(self, identifier: int) -> bool:
        return self._position(identifier) is not None

    def statement(self, identifier: int) -> bytes:
        """`#12 = TYPE(...);` as written, without the line break after it."""
        at = self._position(identifier)
        chunk = self.data[self.starts[at] : self.next_starts[at]]
        return chunk[: chunk.rfind(b";") + 1]

    def entity(self, identifier: int) -> tuple[str, str]:
        """(TYPE or '' for a complex entity, the text after '=' without the ';')."""
        found = self._parsed.get(identifier)
        if found is None:
            at = self._position(identifier)
            if at is None:
                return "", ""
            chunk = self.data[self.bodies[at] : self.next_starts[at]]
            body = chunk[: chunk.rfind(b";")].decode("latin-1")
            kind = _TYPE.match(body)
            found = ((kind.group(1) if kind and not body.lstrip().startswith("(") else ""), body)
            self._parsed[identifier] = found
        return found

    def kind(self, identifier: int) -> str:
        return self.entity(identifier)[0]

    def refs(self, identifier: int) -> list[int]:
        at = self._position(identifier)
        if at is None:
            return []
        return [int(self.ids[i]) for i in self.targets[self.offsets[at] : self.offsets[at + 1]]]

    def _heads(self) -> np.ndarray:
        """The first eight bytes of every entity's body, as one integer each, made once."""
        if getattr(self, "_head_keys", None) is None:
            raw = np.frombuffer(self.data, dtype=np.uint8)
            last = len(raw) - 1
            keys = np.zeros(len(self.bodies), dtype=np.uint64)
            for offset in range(8):
                keys |= raw[np.minimum(self.bodies + offset, last)].astype(np.uint64) << np.uint64(8 * offset)
            self._head_keys = keys
        return self._head_keys

    def of_type(self, name: str) -> list[int]:
        """Every entity of one simple type: its first eight bytes, then the rest of the name checked."""
        encoded = name.encode()
        head = encoded[:8].ljust(8, b"\x00")
        key = np.uint64(int.from_bytes(head, "little"))
        keys = self._heads()
        if len(encoded) < 8:
            mask = np.uint64((1 << (8 * len(encoded))) - 1)
            candidates = np.flatnonzero((keys & mask) == key)
        else:
            candidates = np.flatnonzero(keys == key)
        found = []
        data, bodies = self.data, self.bodies
        for position in candidates:
            start = int(bodies[position])
            if data.startswith(encoded, start) and data[start + len(encoded) : start + len(encoded) + 1] in (
                b"(",
                b" ",
                b"\n",
                b"\r",
            ):
                found.append(int(self.ids[position]))
        return found

    def complex_containing(self, name: str) -> list[int]:
        """Complex entities (`#7 = ( A(...) B(...) )`) whose text names `name`."""
        pattern = re.compile(rb"#(\d{1,18})\s*=\s*\(")
        needle = name.encode()
        found = []
        begin = self.data.find(b"DATA;")
        for match in pattern.finditer(self.data, begin, self.data_end):
            identifier = int(match.group(1))
            at = self._position(identifier)
            if at is not None and needle in self.data[self.bodies[at] : self.next_starts[at]]:
                found.append(identifier)
        return found

    def max_id(self) -> int:
        return int(self.ids.max()) if len(self.ids) else 0


def _strings(body: str) -> list[str]:
    return [value.replace("''", "'") for value in _STRING.findall(body)]


def _numbers(body: str) -> list[float]:
    return [float(value) for value in _NUMBER.findall(body.split("(", 1)[-1])]


# ─── Placements ──────────────────────────────────────────────────────────────


def _unit(vector) -> np.ndarray | None:
    vector = np.asarray(vector, dtype=float)
    length = float(np.linalg.norm(vector))
    return vector / length if length > 1e-12 else None


def axis_matrix(index: Index, axis_id: int) -> np.ndarray:
    """An AXIS2_PLACEMENT_3D as a 4x4 matrix: columns x, y, z and the origin."""
    kind, body = index.entity(axis_id)
    matrix = np.eye(4)
    if kind != "AXIS2_PLACEMENT_3D":
        return matrix
    refs = [int(value) for value in _REF.findall(body)]

    def triple(identifier):
        values = _numbers(index.entity(identifier)[1])[:3]
        return np.array(values + [0.0] * (3 - len(values)))

    origin = triple(refs[0]) if refs else np.zeros(3)
    z = _unit(triple(refs[1])) if len(refs) > 1 else np.array([0.0, 0.0, 1.0])
    reference = triple(refs[2]) if len(refs) > 2 else np.array([1.0, 0.0, 0.0])
    z = z if z is not None else np.array([0.0, 0.0, 1.0])
    # As OpenCascade builds a gp_Ax2: x is the reference direction with its z part taken out.
    x = _unit(reference - z * float(reference @ z))
    if x is None:
        x = _unit(np.cross(z, [1.0, 0.0, 0.0])) if abs(z[0]) < 0.9 else _unit(np.cross(z, [0.0, 1.0, 0.0]))
    y = np.cross(z, x)
    matrix[:3, 0], matrix[:3, 1], matrix[:3, 2], matrix[:3, 3] = x, y, z, origin
    return matrix


# ─── Structure ───────────────────────────────────────────────────────────────


@dataclass
class Occurrence:
    """One NEXT_ASSEMBLY_USAGE_OCCURRENCE: an instance of `child` placed in `parent`."""

    id: int
    name: str
    parent: int  # PRODUCT_DEFINITION ids
    child: int
    placement: np.ndarray = field(default_factory=lambda: np.eye(4))


@dataclass
class Structure:
    index: Index
    occurrences: list[Occurrence]
    #: PRODUCT_DEFINITION id -> its occurrences, in file order.
    children: dict[int, list[Occurrence]]
    #: PRODUCT_DEFINITION id -> product name.
    names: dict[int, str]
    #: PRODUCT_DEFINITION id -> its PRODUCT_DEFINITION_SHAPE ids; same for occurrences.
    shapes_of: dict[int, list[int]]
    #: PRODUCT_DEFINITION_SHAPE id -> SHAPE_DEFINITION_REPRESENTATION ids.
    representations: dict[int, list[int]]
    #: PRODUCT_DEFINITION_SHAPE id (of an occurrence) -> CONTEXT_DEPENDENT_SHAPE_REPRESENTATION ids.
    contextual: dict[int, list[int]]
    #: styled item id -> the item it styles.
    styled: dict[int, int]
    roots: list[int]
    has_geometry: bool
    #: (styled item, what it styles) as entity indices.
    styled_index: list = field(default_factory=list)
    #: entity index -> type, for the few types the identity treats specially.
    special: dict = field(default_factory=dict)
    #: APPLICATION_PROTOCOL_DEFINITION ids, which every cut-out carries.
    protocols: list = field(default_factory=list)

    @property
    def root(self) -> int | None:
        if not self.roots:
            return None
        return max(self.roots, key=lambda pd: (len(self.children.get(pd, [])), -pd))


def _transform(index: Index, cdsr: int) -> np.ndarray | None:
    """The placement a CONTEXT_DEPENDENT_SHAPE_REPRESENTATION gives: item 2 from item 1."""
    refs = index.refs(cdsr)
    if not refs:
        return None
    for ref in index.refs(refs[0]):
        if index.kind(ref) == "ITEM_DEFINED_TRANSFORMATION":
            axes = [value for value in index.refs(ref) if index.kind(value) == "AXIS2_PLACEMENT_3D"]
            if len(axes) >= 2:
                first, second = axis_matrix(index, axes[0]), axis_matrix(index, axes[1])
                inverse = np.eye(4)
                inverse[:3, :3] = first[:3, :3].T
                inverse[:3, 3] = -first[:3, :3].T @ first[:3, 3]
                return second @ inverse
            if axes:
                return axis_matrix(index, axes[-1])
    return None


def structure(index: Index) -> Structure:
    """The assembly tree and what the carving needs, from a few scans of the text."""
    occurrences = []
    for identifier in index.of_type("NEXT_ASSEMBLY_USAGE_OCCURRENCE"):
        body = index.entity(identifier)[1]
        names = _strings(body)
        refs = [int(value) for value in _REF.findall(body)]
        if len(refs) < 2:
            continue
        occurrences.append(Occurrence(identifier, (names[1] if len(names) > 1 else "").strip(), refs[0], refs[1]))
    shapes_of: dict[int, list[int]] = {}
    for identifier in index.of_type("PRODUCT_DEFINITION_SHAPE"):
        refs = index.refs(identifier)
        if refs:
            shapes_of.setdefault(refs[-1], []).append(identifier)
    representations: dict[int, list[int]] = {}
    for identifier in index.of_type("SHAPE_DEFINITION_REPRESENTATION"):
        refs = index.refs(identifier)
        if refs:
            representations.setdefault(refs[0], []).append(identifier)
    contextual: dict[int, list[int]] = {}
    for identifier in index.of_type("CONTEXT_DEPENDENT_SHAPE_REPRESENTATION"):
        refs = index.refs(identifier)
        if len(refs) >= 2:
            contextual.setdefault(refs[1], []).append(identifier)
    for occurrence in occurrences:
        for pds in shapes_of.get(occurrence.id, []):
            for cdsr in contextual.get(pds, []):
                placed = _transform(index, cdsr)
                if placed is not None:
                    occurrence.placement = placed
                    break
    children: dict[int, list[Occurrence]] = {}
    for occurrence in occurrences:
        children.setdefault(occurrence.parent, []).append(occurrence)
    names: dict[int, str] = {}
    for identifier in index.of_type("PRODUCT_DEFINITION"):
        refs = index.refs(identifier)
        formation = index.refs(refs[0]) if refs else []
        if formation and index.kind(formation[0]) == "PRODUCT":
            found = _strings(index.entity(formation[0])[1])
            names[identifier] = found[0] if found else ""
        else:
            names[identifier] = ""
    styled: dict[int, int] = {}
    for kind in ("STYLED_ITEM", "OVER_RIDING_STYLED_ITEM"):
        for identifier in index.of_type(kind):
            refs = index.refs(identifier)
            if refs:
                # The styled item is the last reference (OVER_RIDING adds the over-ridden one after it).
                styled[identifier] = refs[-1] if kind == "STYLED_ITEM" else refs[-2]
    parents = {occurrence.parent for occurrence in occurrences}
    used = {occurrence.child for occurrence in occurrences}
    roots = sorted(parents - used) if occurrences else sorted(names)
    has_geometry = bool(index.of_type("ADVANCED_FACE") or index.of_type("FACE_SURFACE"))
    styled_index = [
        (index.position(item), index.position(target))
        for item, target in styled.items()
        if index.position(item) is not None and index.position(target) is not None
    ]
    special = {index.position(o.id): "NEXT_ASSEMBLY_USAGE_OCCURRENCE" for o in occurrences}
    special.update({index.position(i): "COLOUR_RGB" for i in index.of_type("COLOUR_RGB")})
    return Structure(
        index,
        occurrences,
        children,
        names,
        shapes_of,
        representations,
        contextual,
        styled,
        roots,
        has_geometry,
        styled_index,
        special,
        index.of_type("APPLICATION_PROTOCOL_DEFINITION"),
    )


# ─── Carving ─────────────────────────────────────────────────────────────────


def _starts(found: Structure, product: int) -> list[int]:
    """The entities a product's walk starts from (ids): its definition, its shape's definition
    and representation, and for an assembly its occurrences with theirs, recursively. These are
    the links that point at the product rather than from it."""
    starts, pending, done = [], [product], set()
    while pending:
        definition = pending.pop()
        if definition in done:
            continue
        done.add(definition)
        starts.append(definition)
        for pds in found.shapes_of.get(definition, []):
            starts.append(pds)
            starts.extend(found.representations.get(pds, []))
        for occurrence in found.children.get(definition, []):
            starts.append(occurrence.id)
            for pds in found.shapes_of.get(occurrence.id, []):
                starts.append(pds)
                starts.extend(found.contextual.get(pds, []))
            pending.append(occurrence.child)
    return starts


def _members(found: Structure, product: int) -> tuple[np.ndarray, list[int]]:
    """
    (every entity the product needs, as indices in walk order; the styled items colouring it,
    as ids). The walk is breadth first from the product's start entities, then each style's
    chain in the order of what it styles.
    """
    index = found.index
    positions = [index.position(identifier) for identifier in _starts(found, product)]
    visited = np.zeros(len(index.ids), dtype=bool)
    main = index.walk_more([p for p in positions if p is not None], visited)
    rank = np.full(len(index.ids), -1, dtype=np.int64)
    rank[main] = np.arange(len(main))
    styles = []
    for style, target in found.styled_index:
        if visited[target]:
            styles.append((rank[target], style))
    styles.sort()
    # Their chains in one walk, started in that order: as deterministic, and one walk.
    order = np.concatenate([main, index.walk_more([style for _rank, style in styles], visited)])
    return order, [int(index.ids[style]) for _rank, style in styles]


def _context(index: Index, members) -> int | None:
    """A geometric representation context in the cut, for the presentation entity."""
    for position in sorted(int(i) for i in members):
        identifier = int(index.ids[position])
        kind, body = index.entity(identifier)
        if kind == "" and "GEOMETRIC_REPRESENTATION_CONTEXT" in body:
            return identifier
    return None


def carve(found: Structure, product: int) -> bytes:
    """The product, standing alone, as STEP bytes OpenCascade reads as one free shape."""
    index = found.index
    order, styles = _members(found, product)
    visited = np.zeros(len(index.ids), dtype=bool)
    visited[order] = True
    extra = index.walk_more([p for p in (index.position(i) for i in found.protocols) if p is not None], visited)
    members = np.sort(np.concatenate([order, extra]))
    data, starts, ends = index.data, index.starts, index.next_starts
    lines = []
    for position in members:
        chunk = data[starts[position] : ends[position]]
        lines.append(chunk[: chunk.rfind(b";") + 1])
    context = _context(index, order)
    if styles and context is not None:
        listed = ",".join(f"#{identifier}" for identifier in styles)
        lines.append(
            f"#{index.max_id() + 1} = MECHANICAL_DESIGN_GEOMETRIC_PRESENTATION_REPRESENTATION("
            f"'',({listed}),#{context});".encode()
        )
    return index.header + b"DATA;\n" + b"\n".join(lines) + b"\nENDSEC;\nEND-ISO-10303-21;\n"


_BLANKS = re.compile(rb"\s+")
_STRING_BYTES = re.compile(rb"'(?:[^']|'')*'")


def _without_refs(piece: bytes) -> bytes:
    """An entity's text with blanks and reference numbers taken out, outside its strings only."""
    parts, last = [], 0
    for match in _STRING_BYTES.finditer(piece):
        parts.append(_BLANKS.sub(b"", _REF_BYTES.sub(b"#", piece[last : match.start()])))
        parts.append(match.group(0))
        last = match.end()
    parts.append(_BLANKS.sub(b"", _REF_BYTES.sub(b"#", piece[last:])))
    return b"".join(parts)


def identity(found: Structure, product: int) -> str:
    """
    A hash of what the product is, the same on every board it is exported to.

    Two parts, which together fix the cut-out text up to entity numbering: the entities in
    walk order with their numbers and blanks taken out (writers wrap lines by how wide the
    numbers are), and what each one refers to, as positions in that order. Occurrence names
    inside the product are blanked (KiCad names a footprint's model instance by its label
    path, `=>[0:1:1:3]`, a different number on every board) and colours are rounded (some
    boards write them through float32: 0.1449999932 against 0.1450000016).
    """
    index = found.index
    order, _styles = _members(found, product)
    rank = np.full(len(index.ids), -1, dtype=np.int64)
    rank[order] = np.arange(len(order))
    data, bodies, ends = index.data, index.bodies, index.next_starts
    pieces = []
    special = found.special
    for position in order:
        position = int(position)
        piece = data[bodies[position] : ends[position]]
        kind = special.get(position)
        if kind == "NEXT_ASSEMBLY_USAGE_OCCURRENCE":
            piece = _STRING_BYTES.sub(b"''", piece)
        elif kind == "COLOUR_RGB":
            values = _numbers(piece.decode("latin-1"))[-3:]
            piece = ("COLOUR_RGB(" + ",".join(f"{value:.4f}" for value in values) + ");").encode()
        # Blanks and numbers in strings stay; numbers outside them go (they are references).
        pieces.append(_without_refs(piece))
    text = b"\x00".join(pieces)
    low, high = index.offsets[order], index.offsets[order + 1]
    counts = high - low
    spans = np.repeat(low - np.concatenate(([0], np.cumsum(counts)[:-1])), counts) + np.arange(int(counts.sum()))
    structure_ = np.concatenate([counts, rank[index.targets[spans]]]).astype("<i8")
    digest = hashlib.sha256(text)
    digest.update(structure_.tobytes())
    return digest.hexdigest()[:32]


del math
