"""Cut a small, deterministic excerpt out of a KiCad assembly STEP: a few components, verbatim.

    python make_excerpt.py FULL.step OUT.step REF [REF ...]

Keeps the root product and its shape representation (with every component's placement axis),
the board's substrate, and for each named component its whole occurrence chain: the
NEXT_ASSEMBLY_USAGE_OCCURRENCE, its PRODUCT_DEFINITION_SHAPE and CONTEXT_DEPENDENT_SHAPE_REPRESENTATION,
the transformation and placements, the product and its shape representations -- and the
occurrences inside a component that is an assembly of its own (a vendor model's Body/Pins).
Entities keep their ids and their text. The solids are kept but every CLOSED_SHELL is
replaced by an empty one, so the faces, edges and points that make up most of a STEP file are
left out: the excerpt has the assembly and stubs where the shells would be. The FILE_NAME
header is rewritten without a timestamp, so the output depends only on the input.

Stdlib only; written for boarddd's STEP reader tests (boarddd.step.text, .registration).
"""

from __future__ import annotations

import re
import sys

ENTITY = re.compile(r"^\s*#(\d+)\s*=\s*(.*)$", re.S)
TYPE = re.compile(r"^\s*([A-Z_0-9]+)\s*\(")
REF = re.compile(r"#(\d+)(?!\d)")
STRING = re.compile(r"'((?:[^']|'')*)'")


def parse(text: str) -> tuple[str, dict[int, str], dict[int, str]]:
    head, _, rest = text.partition("DATA;")
    body = rest.split("ENDSEC;", 1)[0]
    raw: dict[int, str] = {}
    kind: dict[int, str] = {}
    for statement in body.split(";"):
        match = ENTITY.match(statement)
        if not match:
            continue
        ident = int(match.group(1))
        raw[ident] = statement.strip()
        found = TYPE.match(match.group(2))
        kind[ident] = found.group(1) if found else ""
    return head, raw, kind


def refs(statement: str) -> list[int]:
    return [int(v) for v in REF.findall(statement.split("=", 1)[1])]


def main(argv: list[str]) -> int:
    source, out, *keep = argv
    head, raw, kind = parse(open(source, encoding="utf-8", errors="replace").read())
    back: dict[int, list[int]] = {}
    for ident, statement in raw.items():
        for target in refs(statement):
            back.setdefault(target, []).append(ident)

    nauo = {i: STRING.findall(raw[i]) for i in raw if kind[i] == "NEXT_ASSEMBLY_USAGE_OCCURRENCE"}
    parent = {i: refs(raw[i])[0] for i in nauo}
    child = {i: refs(raw[i])[1] for i in nauo}
    children = set(child.values())
    root = next(p for p in parent.values() if p not in children)
    names = {}
    for ident in raw:
        if kind[ident] == "PRODUCT_DEFINITION":
            formation = refs(raw.get(refs(raw[ident])[0], "#0 = X()"))
            product = raw.get(formation[0], "") if formation else ""
            found = STRING.search(product.split("=", 1)[-1])
            names[ident] = found.group(1) if found else ""

    def wanted(occurrence: int) -> bool:
        ref = nauo[occurrence][1] if len(nauo[occurrence]) > 1 else ""
        return ref in keep or (ref.startswith("=>[") and names.get(child[occurrence], "").endswith("_PCB"))

    chosen = [i for i in nauo if parent[i] == root and wanted(i)]
    missing = set(keep) - {nauo[i][1] for i in chosen}
    if missing:
        raise SystemExit(f"not in {source}: {sorted(missing)}")
    # the occurrences inside a chosen component (a vendor model that is an assembly)
    queue = list(chosen)
    while queue:
        occurrence = queue.pop()
        inner = [i for i in nauo if parent[i] == child[occurrence] and i not in chosen]
        chosen += inner
        queue += inner

    seeds = {root}
    for occurrence in chosen:
        seeds.add(occurrence)
        for pds in back.get(occurrence, []):  # PRODUCT_DEFINITION_SHAPE -> the occurrence
            if kind[pds] != "PRODUCT_DEFINITION_SHAPE":
                continue
            seeds.add(pds)
            seeds.update(b for b in back.get(pds, []) if kind[b] == "CONTEXT_DEPENDENT_SHAPE_REPRESENTATION")
    definitions = {root} | {child[o] for o in chosen}
    for definition in definitions:  # a product's own shape: PDS <- SHAPE_DEFINITION_REPRESENTATION
        for pds in back.get(definition, []):
            if kind[pds] == "PRODUCT_DEFINITION_SHAPE":
                seeds.add(pds)
                seeds.update(b for b in back.get(pds, []) if kind[b] == "SHAPE_DEFINITION_REPRESENTATION")

    kept: set[int] = set()
    stubbed: set[int] = set()
    queue = list(seeds)
    while queue:
        ident = queue.pop()
        if ident in kept or ident not in raw:
            continue
        kept.add(ident)
        if kind[ident] in ("CLOSED_SHELL", "OPEN_SHELL"):
            stubbed.add(ident)
            continue
        if kind[ident] in ("SHAPE_REPRESENTATION", "ADVANCED_BREP_SHAPE_REPRESENTATION"):
            # a representation's relationships to other representations point INTO it
            queue += [b for b in back.get(ident, []) if kind[b] == "SHAPE_REPRESENTATION_RELATIONSHIP"]
        if ident in nauo and ident not in chosen:
            continue
        queue += refs(raw[ident])
    # an occurrence nobody chose stays out, and with it nothing it alone reaches
    kept -= {i for i in nauo if i not in chosen}

    lines = []
    for ident in sorted(kept):
        statement = raw[ident]
        if ident in stubbed:
            statement = f"#{ident} = {kind[ident]}('',())"
        lines.append(statement + ";")
    head = re.sub(
        r"FILE_NAME\(.*?\);",
        "FILE_NAME('excerpt.step','',('Pcbnew'),('Kicad'),'Open CASCADE STEP processor',"
        "'KiCad to STEP converter','Unknown');",
        head,
        count=1,
        flags=re.S,
    )
    with open(out, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(head.rstrip() + "\nDATA;\n" + "\n".join(lines) + "\nENDSEC;\nEND-ISO-10303-21;\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
