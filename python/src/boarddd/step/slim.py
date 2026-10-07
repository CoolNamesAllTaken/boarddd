"""
A STEP file with the board's dressing taken out, for the browser's CAD kernel.

KiCad's export of an assembled board is mostly not the components. Besides the substrate it
models the pads, the silkscreen and the solder mask as solids -- every pad a box, every glyph
of every designator a face -- and on three real boards that is 59 to 71 percent of the file.
The 3D view never shows any of it: it paints the gerbers onto a board of its own and keeps the
model's board bodies hidden, using only the substrate's thickness.

What the dressing costs is memory, and the kernel's memory has a ceiling. occt-import-js is
32-bit WebAssembly with a two-gigabyte heap, and reading a STEP file takes it roughly seventy
megabytes per megabyte of text -- measured, not guessed: an 8 MB board reads in 0.66 GB, a
21 MB one in 1.47 GB, and a 36 MB one asks for more than it can have. At the ceiling
the reader does not fail; it hands back every solid with no triangles in it, and a page that
has just grown a two-gigabyte worker is the page a browser kills. That board comes down to
about a third of its size with the dressing gone, which is smaller than boards that already
load.

So this is the STEP equivalent of `boarddd.io.gerber`'s profile stripping: what the renderer
reads is not what was uploaded. The stored bytes are still served verbatim; only the model
the 3D view fetches goes through here.

How it is done, because a STEP file is a graph and not a list. Removing an entity means
removing everything only it reaches, and nothing anything else reaches -- the units, the
contexts, the colours are shared by every product in the file. So:

1. The dressing instances are found the way `boarddd.step.text` finds board bodies: an occurrence under
   the root whose reference is an OpenCascade label rather than a designator, whose product
   is named `<board>_pad`, `_silkscreen` or `_soldermask`. The substrate (`_PCB`) stays.
2. D is everything reachable from those instances' chains (the occurrence, its product and
   shape, and the relationship entities that tie them to the root). K is everything reachable
   from every entity NOT in D. What goes is D minus K -- the dressing's own geometry, and not
   the context it shares with the rest of the board.
3. A STEP file also has entities pointing INTO the geometry from outside it -- the styled
   items that colour a solid, the category and presentation lists that name every product.
   Those are repaired: a reference to a removed entity inside a list is dropped from the
   list; a reference in a scalar position takes its entity with it; a list emptied out takes
   its entity too. Repeated until nothing changes.
4. Last, a sweep for what that orphaned: the colour chains of the removed styled items are
   now reachable from nothing, and are dropped. Checked on the whole file with nothing
   removed, this sweep removes nothing, so it can only take what step 3 left hanging.

If any of it would touch the root product, the file is served as it came, and says so.

Pure: no settings, no filesystem. Entity ids are kept, so nothing downstream that
reads the file by id -- `boarddd.step.text`, for one -- reads it differently.

Source: `magpie/step/stepslim.py` at magpie commit 3a0374d3 (copied for boarddd phase F1; magpie's later
changes are re-diffed before magpie switches to this module).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from boarddd.step import text as stepmeta

__all__ = ["Slimmed", "Product", "DRESSING", "slim", "weigh"]

#: How KiCad names the board's own films. The substrate is `<board>_PCB` and is not matched,
#: because its thickness is the one thing the 3D view measures off the model's board.
DRESSING = re.compile(r"_(pads?|silkscreen|soldermask)$", re.I)

#: Bounded like `boarddd.step.text`'s, which says why.
_REF = re.compile(stepmeta._ID)
_EMPTY_LIST = re.compile(r"\(\s*\)")

#: Entities that point INTO geometry from outside it rather than being part of it: the styled
#: items that colour a solid, the lists that name every product or every styled item in the
#: file. Following their references would make the dressing's geometry look shared -- every
#: face of it is referenced by a styled item -- so they are not followed when working out what
#: the rest of the file reaches. They are repaired afterwards instead. Missing one here is
#: safe: the file keeps more than it needs to, and the kernel reads it all the same.
BACK_REFERENCING = frozenset(
    {
        "STYLED_ITEM",
        "OVER_RIDING_STYLED_ITEM",
        "MECHANICAL_DESIGN_GEOMETRIC_PRESENTATION_REPRESENTATION",
        "PRESENTATION_LAYER_ASSIGNMENT",
        "PRODUCT_RELATED_PRODUCT_CATEGORY",
        "DRAUGHTING_MODEL",
        "SHAPE_ASPECT",
        "PROPERTY_DEFINITION",
        "PROPERTY_DEFINITION_REPRESENTATION",
    }
)


@dataclass(frozen=True)
class Product:
    """One product in the file and how much of the file it is."""

    name: str
    entities: int
    uses: int
    share: float


@dataclass
class Slimmed:
    text: str
    #: The product names taken out, in file order. Empty when nothing was.
    dropped: list[str] = field(default_factory=list)
    #: The references of the instances taken out -- the `=>[0:1:1:36]` labels -- so a manifest
    #: can say which board nodes the served file still has without parsing it again.
    dropped_refs: list[str] = field(default_factory=list)
    removed: int = 0
    total: int = 0
    #: The products that account for most of what is left, heaviest first.
    heaviest: list[Product] = field(default_factory=list)
    #: Why nothing was removed, when nothing was and something should have been.
    reason: str = ""


# ─── The graph ───────────────────────────────────────────────────────────────


def _refs(found: dict) -> dict[int, list[int]]:
    return {identifier: [int(value) for value in _REF.findall(rest)] for identifier, (_kind, rest) in found.items()}


def _reach(refs: dict[int, list[int]], seeds, within=None) -> set[int]:
    """Everything reachable from `seeds` by following references, optionally only through `within`."""
    seen: set[int] = set()
    stack = list(seeds)
    while stack:
        identifier = stack.pop()
        if identifier in seen or identifier not in refs:
            continue
        if within is not None and identifier not in within:
            continue
        seen.add(identifier)
        stack.extend(refs[identifier])
    return seen


def weigh(text: str, limit: int = 8) -> list[Product]:
    """
    The products in a STEP file by how much of it each one is, heaviest first.

    For telling somebody which models to simplify when the file is too big to read. A
    product's weight is every entity its shape reaches, so shared context is counted against
    each product that uses it -- an overestimate of a few dozen entities, on a scale of
    thousands.
    """
    found = stepmeta.entities(text)
    refs = _refs(found)
    return _heaviest(found, refs, stepmeta.product_names(found), limit)


# ─── Taking entities out ─────────────────────────────────────────────────────


def _reference_positions(kind: str, rest: str):
    """
    Every `#ref` in an entity's text with whether it sits inside a list.

    Yields (start, end, identifier, in_list). Depth counts parentheses outside strings; the
    entity's own argument parentheses are depth 1, and a complex entity -- `( A(...) B(...) )`
    -- wraps its members in one more, so its arguments are depth 2.
    """
    scalar_depth = 2 if kind == "" else 1
    depth = 0
    quoted = False
    index = 0
    length = len(rest)
    while index < length:
        char = rest[index]
        if quoted:
            if char == "'":
                # A doubled quote is an escaped quote, not the end of the string.
                if index + 1 < length and rest[index + 1] == "'":
                    index += 1
                else:
                    quoted = False
        elif char == "'":
            quoted = True
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif char == "#":
            match = _REF.match(rest, index)
            if match:
                yield match.start(), match.end(), int(match.group(1)), depth > scalar_depth
                index = match.end()
                continue
        index += 1


def _without(kind: str, rest: str, removed: set[int]) -> str | None:
    """
    The entity's text with references to removed entities taken out of its lists.

    None when the entity cannot survive: a removed reference in a scalar position, or a list
    that would be left empty.
    """
    cuts = []
    for start, end, identifier, in_list in _reference_positions(kind, rest):
        if identifier not in removed:
            continue
        if not in_list:
            return None
        cuts.append((start, end))
    if not cuts:
        return rest

    pieces = []
    cursor = 0
    for start, end in cuts:
        pieces.append(rest[cursor:start])
        cursor = end
    pieces.append(rest[cursor:])
    repaired = "".join(pieces)
    # The commas the cut elements were joined by: `(#1,,#3)`, `(,#3)`, `(#1,)`.
    repaired = re.sub(r",\s*,", ",", repaired)
    repaired = re.sub(r"\(\s*,", "(", repaired)
    repaired = re.sub(r",\s*\)", ")", repaired)
    if len(_EMPTY_LIST.findall(repaired)) > len(_EMPTY_LIST.findall(rest)):
        return None
    return repaired


def _sweep(refs: dict[int, list[int]], original_refs: dict[int, list[int]], remaining: set[int]) -> set[int]:
    """
    What nothing reaches any more.

    The roots are the entities nothing in the FILE AS IT CAME refers to -- the protocol
    definition, the shape-definition links, the category and presentation lists. Everything
    the file needs hangs off one of them, so on a file nothing was removed from this finds
    nothing. Here it finds the style chains of styled items that were removed: referred to
    once, by an entity that is gone, and so neither a root nor reachable from one. (Taking
    the roots from what remains instead would crown every orphan a root and sweep nothing.)
    """
    referenced_originally = set()
    for targets in original_refs.values():
        referenced_originally.update(targets)
    roots = {identifier for identifier in remaining if identifier not in referenced_originally}
    return remaining - _reach(refs, roots, within=remaining)


def _dressing_instances(found: dict, refs: dict, names: dict) -> list[tuple[int, str, int]]:
    """(occurrence id, reference, child product definition id) for each film on the board."""
    occurrences = list(stepmeta._occurrences(found))
    root = stepmeta._root(occurrences)
    dressing = []
    for identifier, _seq, ref, parent, child in occurrences:
        if root is not None and parent != root:
            continue
        if not stepmeta.BOARD_NODE.match(ref):
            continue
        if DRESSING.search(names.get(child, "")):
            dressing.append((identifier, ref, child))
    return dressing


def _chain(found: dict, refs: dict, occurrence: int, child: int, child_shared: bool) -> set[int]:
    """
    The entities that exist only to place this occurrence.

    The occurrence, the definition-shape that names it, the context-dependent representation
    that positions it, the relationship inside that and its transformation; and, unless the
    product is also used by an occurrence that stays, the product's own definition and the
    link to its shape.
    """
    seeds = {occurrence}
    if not child_shared:
        seeds.add(child)
    for identifier, (kind, _rest) in found.items():
        if kind == "PRODUCT_DEFINITION_SHAPE" and refs[identifier] and refs[identifier][0] in seeds:
            seeds.add(identifier)
    definition_shapes = {identifier for identifier in seeds if found[identifier][0] == "PRODUCT_DEFINITION_SHAPE"}
    for identifier, (kind, _rest) in found.items():
        if (
            kind == "CONTEXT_DEPENDENT_SHAPE_REPRESENTATION"
            and len(refs[identifier]) >= 2
            and refs[identifier][1] in definition_shapes
        ):
            seeds.add(identifier)
            relationship = refs[identifier][0]
            seeds.add(relationship)
            for ref in refs.get(relationship, []):
                if found.get(ref, ("", ""))[0] == "ITEM_DEFINED_TRANSFORMATION":
                    seeds.add(ref)
        elif (
            kind == "SHAPE_DEFINITION_REPRESENTATION" and refs[identifier] and refs[identifier][0] in definition_shapes
        ):
            seeds.add(identifier)
    return seeds


def slim(text: str) -> Slimmed:
    """
    The file with its board dressing removed, or the file as it came when there is none.

    Cheap enough to do once per stored model and keep -- a few seconds on a 36 MB file -- and
    not cheap enough to do per request.
    """
    found = stepmeta.entities(text)
    if not found:
        return Slimmed(text=text, reason="not a STEP file")
    refs = _refs(found)
    original_refs = dict(refs)  # the graph as it came; `refs` follows the repairs
    names = stepmeta.product_names(found)
    total = len(found)

    dressing = _dressing_instances(found, refs, names)
    if not dressing:
        return Slimmed(
            text=text, total=total, heaviest=_heaviest(found, refs, names), reason="no board dressing recognized"
        )

    kept_children = {
        child
        for _identifier, _seq, _ref, _parent, child in stepmeta._occurrences(found)
        if _identifier not in {occurrence for occurrence, _r, _c in dressing}
    }
    seeds: set[int] = set()
    for occurrence, _ref, child in dressing:
        seeds |= _chain(found, refs, occurrence, child, child_shared=child in kept_children)

    doomed = _reach(refs, seeds)
    kept = _reach(
        refs,
        [
            identifier
            for identifier, (kind, _rest) in found.items()
            if identifier not in doomed and kind not in BACK_REFERENCING
        ],
    )
    removed = doomed - kept

    # Repair what points into the removed geometry from outside it, until nothing does.
    rewritten: dict[int, str] = {}
    changed = True
    while changed:
        changed = False
        for identifier, (kind, rest) in found.items():
            if identifier in removed:
                continue
            current = rewritten.get(identifier, rest)
            if not any(target in removed for target in refs[identifier]):
                continue
            repaired = _without(kind, current, removed)
            if repaired is None:
                removed.add(identifier)
                rewritten.pop(identifier, None)
                changed = True
            elif repaired != current:
                rewritten[identifier] = repaired
                # The refs index has to follow, or the next round sees the old references.
                refs[identifier] = [int(value) for value in _REF.findall(repaired)]

    remaining = set(found) - removed
    removed |= _sweep(refs, original_refs, remaining)

    occurrences = list(stepmeta._occurrences(found))
    root = stepmeta._root(occurrences)
    root_shapes = stepmeta.shape_reps(found, refs).get(root, []) if root is not None else []
    if root is None or root in removed or any(shape in removed for shape in root_shapes):
        return Slimmed(
            text=text,
            total=total,
            heaviest=_heaviest(found, refs, names),
            reason="removing the board dressing would have taken the board with it",
        )

    head = text.split("DATA;", 1)[0]
    body = []
    for identifier, (_kind, rest) in found.items():
        if identifier in removed:
            continue
        body.append(f"#{identifier} = {rewritten.get(identifier, rest)};\n")
    slimmed = head + "DATA;\n" + "".join(body) + "ENDSEC;\nEND-ISO-10303-21;\n"

    survivors = {identifier: found[identifier] for identifier in found if identifier not in removed}
    survivor_refs = {identifier: refs[identifier] for identifier in survivors}
    return Slimmed(
        text=slimmed,
        dropped=[names.get(child, "") for _occurrence, _ref, child in dressing],
        dropped_refs=[ref for _occurrence, ref, _child in dressing],
        removed=len(removed),
        total=total,
        heaviest=_heaviest(survivors, survivor_refs, stepmeta.product_names(survivors)),
    )


def _heaviest(found: dict, refs: dict, names: dict, limit: int = 8) -> list[Product]:
    uses: dict[int, int] = {}
    for identifier, (kind, _rest) in found.items():
        if kind == "NEXT_ASSEMBLY_USAGE_OCCURRENCE" and len(refs[identifier]) >= 2:
            child = refs[identifier][1]
            uses[child] = uses.get(child, 0) + 1
    total = max(len(found), 1)
    products = []
    for definition, shapes in stepmeta.shape_reps(found, refs).items():
        weight = len(_reach(refs, shapes))
        products.append(
            Product(
                name=names.get(definition, ""),
                entities=weight,
                uses=uses.get(definition, 0),
                share=round(weight / total, 4),
            )
        )
    products.sort(key=lambda product: (-product.entities, product.name))
    return products[:limit]
