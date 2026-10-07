"""
A fab package (a folder or a zip of Gerbers, drills, a job file, placements and a BOM) as a board.

`read_package(path)` is the one entry point: it opens the package (`archive` for a zip), sorts
its files (`classify`), and assembles a :class:`boarddd.model.Board` from what the readers find:

=====================  ==================================================================
board field            read from
=====================  ==================================================================
name, revision         the gbrjob's ``ProjectId`` (KiCad's ``rev?`` placeholder is no revision)
source                 every file, with its role, side and SHA-256; the gbrjob's generator/date
outline                the outline Gerber (`outline`), the board picked by the gbrjob's size
stackup                the gbrjob: thickness, copper count, finish, ``MaterialStackup``
layers                 every Gerber and drill file: KiCad layer ids from the X2 file function
drills                 the Excellon files (`excellon`), each hole once
components             the pick-and-place file(s) (`pos`), enriched from the BOM (`bom`)
=====================  ==================================================================

What a fab package cannot say is left empty and not guessed: footprints, pads, 3D models, the
aux/grid origins (Gerbers carry no origin), net names. A KiCad reader (`boarddd.io.kicad`)
fills those from the design itself.

boarddd's own module (phase F1); the readers it calls are copied from magpie.
"""

from __future__ import annotations

import hashlib
import json
import re
import zipfile
from collections.abc import Iterable, Mapping
from pathlib import Path

from boarddd import model as m
from boarddd.io import archive, classify, excellon, gbrjob, outline
from boarddd.io import bom as bom_reader
from boarddd.io import pos as pos_reader
from boarddd.io.classify import FabFile

__all__ = ["read_package", "read_files", "package_files", "layer_id"]

READER = "boarddd.io.package"
NDIGITS = 6

#: Source-file role per classify kind.
_ROLE = {
    FabFile.GERBER_COPPER: "copper",
    FabFile.GERBER_MASK: "mask",
    FabFile.GERBER_PASTE: "paste",
    FabFile.GERBER_SILK: "silk",
    FabFile.GERBER_EDGE: "outline",
    FabFile.GERBER_FAB: "fab",
    FabFile.GERBER_DOC: "other",
    FabFile.GERBER_JOB: "job",
    FabFile.DRILL: "drill",
    FabFile.DRILL_MAP: "drill_map",
    FabFile.PLACEMENT: "placement",
    FabFile.BOM: "bom",
    FabFile.NETLIST: "netlist",
    FabFile.MODEL3D: "model",
    FabFile.MODEL_GLB: "model",
}
#: Drawable-layer role per classify kind (drills are layers too).
_LAYER_ROLE = {
    FabFile.GERBER_COPPER: "copper",
    FabFile.GERBER_MASK: "mask",
    FabFile.GERBER_PASTE: "paste",
    FabFile.GERBER_SILK: "silk",
    FabFile.GERBER_EDGE: "outline",
    FabFile.GERBER_FAB: "fab",
    FabFile.GERBER_DOC: "user",
    FabFile.DRILL: "drill",
}
#: Non-copper layers come after the N copper layers, in this order (the golden board's rule):
#: outline, paste, silk, mask, then the drills (plated first), then fab and user drawings.
_DRAW_ORDER = ["outline", "paste", "silk", "mask", "copper", "drill"]
_AFTER_DRILLS = ["PTH", "NPTH", "fab", "user"]
_KICAD_PREFIX = {"top": "F", "bottom": "B"}
_KICAD_SUFFIX = {"mask": "Mask", "paste": "Paste", "silk": "Silkscreen", "fab": "Fab"}
_DRILL_FUNCTION = {"viadrill": "via", "componentdrill": "component", "mechanicaldrill": "mechanical"}
_TF_POLARITY = re.compile(r"%TF\.FilePolarity,(\w+)\*%")
_DRILL_FILE_FUNCTION = re.compile(r"TF\.FileFunction,([^\r\n*]+)")


def r(value: float) -> float:
    value = round(float(value), NDIGITS)
    return 0.0 if value == 0 else value


# ---------------------------------------------------------------------------------------------------------------------
# entry points


def package_files(path: str | Path) -> dict[str, bytes]:
    """{relpath: bytes} of a package folder (recursively; dot-files and dot-dirs skipped) or zip."""
    path = Path(path)
    if path.is_dir():
        out = {}
        for file in sorted(path.rglob("*")):
            rel = file.relative_to(path).as_posix()
            if file.is_file() and archive.safe_relpath(rel) == rel:
                out[rel] = file.read_bytes()
        return out
    with zipfile.ZipFile(path) as zf:
        return {member.relpath: member.data for member in archive.members(zf)}


def read_package(path: str | Path, *, name: str | None = None, convention: str = "") -> m.Board:
    """The board a fab package (folder or .zip) describes. See the module docstring for what comes from where."""
    path = Path(path)
    return read_files(package_files(path), name=name or path.stem, convention=convention)


def read_files(files: Mapping[str, bytes], *, name: str | None = None, convention: str = "") -> m.Board:
    """`read_package` on files already in memory: {relpath: bytes}."""
    warnings: list[str] = []
    found = {rel: classify.classify(rel, data, convention) for rel, data in files.items()}
    by_kind: dict[str, list[str]] = {}
    for rel in sorted(found):
        by_kind.setdefault(found[rel].kind, []).append(rel)

    job = _job(files, by_kind.get(FabFile.GERBER_JOB, []), warnings)
    specs = job.get("GeneralSpecs") if isinstance(job.get("GeneralSpecs"), dict) else {}
    project = specs.get("ProjectId") if isinstance(specs.get("ProjectId"), dict) else {}
    job_text = json.dumps(job) if job else ""

    layers = _layers(files, found, job, warnings)
    stackup = _stackup(job, layers)
    copper = [la for la in layers if la.role == "copper"]
    if stackup.copper_layers is None and copper:
        stackup.copper_layers = len(copper)

    board = m.Board(
        name=str(project.get("Name") or "").strip() or name or "board",
        revision=_revision(project.get("Revision")),
        source=m.Source(
            kind="gerber",
            files=_source_files(files, found, layers),
            generator=_generator(job),
            reader=READER,
            created=str((job.get("Header") or {}).get("CreationDate") or "") or None,
        ),
        stackup=stackup,
        layers=layers,
    )
    if gbrjob.looks_like_panel(gbrjob.project_name(job_text)):
        warnings.append(f"the job file names a panel ({gbrjob.project_name(job_text)}); read as one board")

    board.outline = _outline(files, layers, gbrjob.board_size(job_text), warnings)
    board.drills = _drills(files, layers, warnings)
    board.components = _components(files, by_kind.get(FabFile.PLACEMENT, []), warnings)
    _apply_bom(board.components, files, by_kind.get(FabFile.BOM, []), warnings)
    board.warnings = warnings
    return board


# ---------------------------------------------------------------------------------------------------------------------
# job file, layers, stackup


def _text(data: bytes) -> str:
    return data.decode("utf-8", errors="replace")


def _job(files: Mapping[str, bytes], paths: list[str], warnings: list[str]) -> dict:
    for rel in paths:
        try:
            data = json.loads(_text(files[rel]))
        except ValueError:
            warnings.append(f"{rel}: not a readable job file")
            continue
        if isinstance(data, dict):
            if len(paths) > 1:
                warnings.append(f"{len(paths)} job files; read {rel}")
            return data
    return {}


def _revision(value) -> str | None:
    text = str(value or "").strip()
    return None if not text or text == "rev?" else text  # KiCad writes "rev?" when the board has none


def _generator(job: dict) -> str | None:
    gen = (job.get("Header") or {}).get("GenerationSoftware") or {}
    if not isinstance(gen, dict):
        return None
    words = [str(gen.get(k)) for k in ("Vendor", "Application", "Version") if gen.get(k)]
    return " ".join(words) or None


def layer_id(role: str, side: str, copper: int | None, count: int | None, *, plated: bool | None = None) -> str:
    """KiCad's canonical layer name for a layer, where there is one ('F.Cu', 'In2.Cu', 'B.Mask', 'Edge.Cuts', 'PTH')."""
    if role == "copper":
        if copper == 1 or (copper is None and side == "top"):
            return "F.Cu"
        if (count and copper == count) or (copper is None and side == "bottom"):
            return "B.Cu"
        if copper:
            return f"In{copper - 1}.Cu"
        return "Cu"
    if role == "outline":
        return "Edge.Cuts"
    if role == "drill":
        return "PTH" if plated else "NPTH" if plated is False else "Drill"
    if role in _KICAD_SUFFIX and side in _KICAD_PREFIX:
        return f"{_KICAD_PREFIX[side]}.{_KICAD_SUFFIX[role]}"
    return role


def _layers(files: Mapping[str, bytes], found: dict, job: dict, warnings: list[str]) -> list[m.Layer]:
    specs = job.get("GeneralSpecs") if isinstance(job.get("GeneralSpecs"), dict) else {}
    attributes = {
        str(f.get("Path")): f for f in (job.get("FilesAttributes") or []) if isinstance(f, dict) and f.get("Path")
    }
    numbers = [c.copper_layer for c in found.values() if c.kind == FabFile.GERBER_COPPER and c.copper_layer]
    count = specs.get("LayerNumber") if isinstance(specs.get("LayerNumber"), int) else None
    count = count or (max(numbers) if numbers else None)
    n = count or 0

    layers: list[m.Layer] = []
    seen: set[str] = set()
    for rel in sorted(found):
        kind = found[rel]
        role = _LAYER_ROLE.get(kind.kind)
        if role is None:
            continue
        head = _text(files[rel][:8192])
        side = kind.side or "none"
        copper = kind.copper_layer
        if role == "copper" and side in ("", "none"):
            side = "top" if copper == 1 else "bottom" if count and copper == count else "inner"
        if role == "copper" and copper is None and side == "bottom" and count:
            copper = count
        plated = None
        function = kind.function or None
        polarity = "positive"
        if role == "drill":
            match = _DRILL_FILE_FUNCTION.search(head)
            function = match.group(1).strip() if match else None
            plated = _plated(function, rel)
            side = "none"
        else:
            match = _TF_POLARITY.search(head)
            said = match.group(1) if match else (attributes.get(rel.rsplit("/", 1)[-1]) or {}).get("FilePolarity", "")
            polarity = "negative" if str(said).lower() == "negative" else "positive"
        if role in ("outline", "user"):
            side = "none" if role == "outline" or side not in ("top", "bottom") else side
        lid = layer_id(role, side, copper, count, plated=plated)
        if role == "user":
            lid = Path(rel).stem
        base, k = lid, 2
        while lid in seen:
            lid, k = f"{base}-{k}", k + 1
        seen.add(lid)
        if role == "copper":
            order = copper or (1 if side == "top" else n or 1)
        elif role == "drill":
            order = n + 1 + len(_DRAW_ORDER) + _AFTER_DRILLS.index("NPTH" if plated is False else "PTH")
        elif role in ("fab", "user"):
            order = n + 1 + len(_DRAW_ORDER) + _AFTER_DRILLS.index(role)
        else:
            order = n + 1 + _DRAW_ORDER.index(role)
        layers.append(
            m.Layer(
                id=lid,
                role=role,
                side=side if side in ("top", "bottom", "inner") else "none",
                order=order,
                files=[rel],
                format="excellon" if role == "drill" else "gerber",
                polarity=polarity,
                function=function,
                plated=plated,
            )
        )
    if not any(la.role == "copper" for la in layers):
        warnings.append("no copper layer found")
    return sorted(layers, key=lambda la: (la.order, la.side != "top", la.id))


def _plated(function: str | None, rel: str) -> bool | None:
    words = (function or "").replace(" ", "").lower().split(",")
    if words[0] == "plated":
        return True
    if words[0] == "nonplated":
        return False
    name = rel.rsplit("/", 1)[-1].lower()
    if re.search(r"(^|[-_. ])(npth|non[-_]?plated|unplated)([-_. ]|$)", name):
        return False
    if re.search(r"(^|[-_. ])(pth|plated)([-_. ]|$)", name):
        return True
    return None


def _stackup(job: dict, layers: list[m.Layer]) -> m.Stackup:
    """The gbrjob's GeneralSpecs and MaterialStackup, linked to the package's layers."""
    copper_ids = [la.id for la in sorted((la for la in layers if la.role == "copper"), key=lambda la: la.order)]
    return gbrjob.read_stackup(job, copper_ids, lambda kind, side: layer_id(kind, side, None, None))


def _source_files(files: Mapping[str, bytes], found: dict, layers: list[m.Layer]) -> list[m.SourceFile]:
    side_of = {f: la.side for la in layers for f in la.files}
    out = []
    for rel in sorted(files):
        side = side_of.get(rel)
        out.append(
            m.SourceFile(
                path=rel,
                role=_ROLE.get(found[rel].kind, "other"),
                side=side if side in ("top", "bottom", "inner") else None,
                sha256=hashlib.sha256(files[rel]).hexdigest(),
            )
        )
    return out


# ---------------------------------------------------------------------------------------------------------------------
# outline, drills


def _signed_area(points: list[tuple[float, float]]) -> float:
    return sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(points, points[1:] + points[:1], strict=True)) / 2


def _wound(points: Iterable[tuple[float, float]], ccw: bool) -> list[tuple[float, float]]:
    pts = [(r(x), r(y)) for x, y in points]
    return pts if (_signed_area(pts) > 0) == ccw else pts[::-1]


def _outline(files, layers, size, warnings) -> m.Outline | None:
    edges = [la for la in layers if la.role == "outline"]
    for layer in edges:
        text = _text(files[layer.files[0]])
        found = outline.contours(text)
        board = outline.pick_board(found, *(size or (None, None)))
        if board is None:
            continue
        if len(edges) > 1:
            warnings.append(f"{len(edges)} outline layers; the board edge is from {layer.files[0]}")
        return m.Outline(
            board=_wound(board.points, True),
            cutouts=[_wound(c.points, False) for c in outline.cutouts(found, board)],
        )
    if edges:
        warnings.append("the outline layer has no closed loop")
    if size:
        warnings.append("no outline: the board size from the job file is known, but not where the board is")
    return None


def _drills(files, layers, warnings) -> list[m.Drill]:
    # A hole listed again by a later file (a combined drill file next to a PTH/NPTH pair) is kept
    # once, as excellon.distinct does; two hits in one file (stacked pads) are both kept.
    holes: list[tuple[excellon.Hole, m.Layer]] = []
    seen: set = set()
    again = 0
    for layer in (la for la in layers if la.role == "drill"):
        mine = excellon.parse(_text(files[layer.files[0]]), keep_empty=True)
        keys = [_hole_key(h) for h in mine]
        for hole, key in zip(mine, keys, strict=True):
            if key in seen:
                again += 1
            else:
                holes.append((hole, layer))
        seen.update(keys)
    if again:
        warnings.append(f"{again} holes are listed in more than one drill file; kept once")
    out = []
    for hole, layer in holes:
        out.append(
            m.Drill(
                x=r(hole.x),
                y=r(hole.y),
                diameter=r(hole.diameter),
                plated=hole.plated if layer.plated is None else layer.plated,
                x2=r(hole.x2) if hole.x2 is not None else None,
                y2=r(hole.y2) if hole.y2 is not None else None,
                tool=hole.tool or None,
                function=_DRILL_FUNCTION.get(hole.function.lower()),
                layer=layer.id,
            )
        )
    zero = sum(d.diameter == 0 for d in out)
    if zero:
        warnings.append(f"{zero} holes have a 0 mm drill in the drill file (a placeholder diameter, kept as 0)")
    return out


def _hole_key(hole: excellon.Hole):
    """excellon.distinct's identity: both ends (unordered) and the diameter, to the micron."""
    ends = [(round(hole.x, 3), round(hole.y, 3))]
    if hole.x2 is not None and hole.y2 is not None:
        ends.append((round(hole.x2, 3), round(hole.y2, 3)))
    return tuple(sorted(ends)), round(hole.diameter, 3)


# ---------------------------------------------------------------------------------------------------------------------
# components


def _ref_key(ref: str):
    return (re.sub(r"\d+", "", ref), int(re.sub(r"\D", "", ref)[:18] or 0), ref)


def _components(files, paths: list[str], warnings: list[str]) -> list[m.Component]:
    comps: dict[str, m.Component] = {}
    for rel in paths:
        skipped: list[str] = []
        for row in pos_reader.parse(_text(files[rel]).lstrip("﻿"), skipped):
            if row.reference in comps:
                warnings.append(f"{row.reference} is placed twice; {rel} wins")
            comps[row.reference] = m.Component(
                ref=row.reference,
                side="bottom" if row.side == "bottom" else "top",
                x=r(row.x),
                y=r(row.y),
                rotation=r(row.rotation),
                value=row.value or None,
                footprint=row.footprint or None,
            )
        if skipped:
            warnings.append(f"{rel}: {len(skipped)} rows without a usable position left out ({', '.join(skipped[:5])})")
    return sorted(comps.values(), key=lambda c: _ref_key(c.ref))


def _apply_bom(comps: list[m.Component], files, paths: list[str], warnings: list[str]) -> None:
    by_ref = {c.ref: c for c in comps}
    for rel in paths:
        data = files[rel]
        try:
            if rel.lower().endswith(bom_reader.SPREADSHEET_EXTENSIONS):
                text = bom_reader.spreadsheet_to_csv(data, rel)
            else:
                text = bom_reader.decode(data)
            rows = bom_reader.parse_csv(text)
        except bom_reader.ParseError as exc:
            warnings.append(f"{rel}: BOM not read ({exc})")
            continue
        unplaced = []
        for row in rows:
            for ref in row.designators:
                comp = by_ref.get(ref)
                if comp is None:
                    unplaced.append(ref)
                    continue
                comp.populate = comp.populate and row.populate
                comp.value = comp.value or row.value or None
                if row.mpn and not any(p.mpn == row.mpn for p in comp.mpn):
                    comp.mpn.append(m.PartNumber(mpn=row.mpn, manufacturer=row.manufacturer or None))
                for key, value in (("LCSC", row.lcsc_pn), ("Note", row.note)):
                    if value:
                        comp.attributes[key] = value
        if unplaced:
            warnings.append(f"{rel}: {len(unplaced)} BOM designators have no placement ({', '.join(unplaced[:5])})")
