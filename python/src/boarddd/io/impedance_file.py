"""
An impedance file shipped with a board: the controlled lines, by class, in CSV, JSON or a simple spreadsheet
(docs/impedance-file.md). One row (or object) per class, or per class and layer:

    class,target,tolerance,kind,nets,layer,width,gap
    USB,90,10,diff,USB_D{P,N},F.Cu,0.2,0.15
    RF,50,5,single,RF_*,,,

Columns are found by name, in any order and case: class (name, net class, group), target (ohms, impedance, z, z0,
zdiff), tolerance (tol, ±), kind (type: single / se / diff / differential / dp / pair), nets (net, patterns,
signals: boarddd.nets patterns, separated by spaces, ';' or '|'), layer, structure, width, gap (spacing),
coplanar_gap (ground gap), reference (ref). Only target is required. Figures may carry their unit ("90R",
"90 ohm", "±10 %", "5 ohm", "0.2 mm", "8 mil"). A fab's impedance_control.xlsx with a header row of those
names (above it, a title is fine) reads the same; its rows rarely name nets, so they become targets by layer.

``read_impedance_file`` gives NetClasses (source 'file') with ``patterns``; with ``names`` it fills their nets
(and a class without a kind whose nets are all pairs becomes differential).
"""

from __future__ import annotations

import csv
import io
import json
import re

from boarddd import model as m

__all__ = ["read_impedance_file", "is_impedance_file_name", "COLUMNS"]

#: Column name -> field (names compared lower-case, without spaces, dots, dashes, underscores or brackets).
COLUMNS = {
    "class": "class",
    "name": "class",
    "netclass": "class",
    "group": "class",
    "label": "class",
    "target": "target",
    "targetohms": "target",
    "ohms": "target",
    "ohm": "target",
    "impedance": "target",
    "impedanceohm": "target",
    "impedanceohms": "target",
    "z": "target",
    "z0": "target",
    "zo": "target",
    "zdiff": "target",
    "zse": "target",
    "targetimpedance": "target",
    "requiredimpedance": "target",
    "tolerance": "tolerance",
    "tol": "tolerance",
    "±": "tolerance",
    "tolerancepct": "tolerance",
    "+/": "tolerance",
    "kind": "kind",
    "type": "kind",
    "mode": "kind",
    "singlediff": "kind",
    "sediff": "kind",
    "impedancetype": "kind",
    "nets": "nets",
    "net": "nets",
    "patterns": "nets",
    "pattern": "nets",
    "netpatterns": "nets",
    "signals": "nets",
    "signal": "nets",
    "netnames": "nets",
    "netname": "nets",
    "layer": "layer",
    "layers": "layer",
    "signallayer": "layer",
    "copperlayer": "layer",
    "tracelayer": "layer",
    "structure": "structure",
    "linetype": "structure",
    "width": "width",
    "tracewidth": "width",
    "linewidth": "width",
    "w": "width",
    "linewidthmm": "width",
    "gap": "gap",
    "spacing": "gap",
    "space": "gap",
    "s": "gap",
    "pairgap": "gap",
    "linespacing": "gap",
    "coplanargap": "coplanar_gap",
    "groundgap": "coplanar_gap",
    "gndgap": "coplanar_gap",
    "cpwgap": "coplanar_gap",
    "coplanarspacing": "coplanar_gap",
    "reference": "reference",
    "ref": "reference",
    "references": "reference",
    "referencelayer": "reference",
    "referencelayers": "reference",
    "refplane": "reference",
    "refplanes": "reference",
}
_NAME = re.compile(r"(^|[-_ .])(impedance|imp|zcontrol|controlled)", re.I)
_STRUCTURES = {
    "microstrip": "microstrip",
    "ms": "microstrip",
    "stripline": "stripline",
    "sl": "stripline",
    "coplanar": "coplanar",
    "cpw": "coplanar",
    "cpwg": "coplanar_grounded",
    "gcpw": "coplanar_grounded",
    "groundedcoplanar": "coplanar_grounded",
    "coplanargrounded": "coplanar_grounded",
}
_DIFF = ("diff", "differential", "dp", "pair", "edgecoupled", "edge", "coupled", "broadside", "d")
_SINGLE = ("single", "se", "singleended", "s", "single-ended")


def is_impedance_file_name(name: str) -> bool:
    """Whether a file name looks like an impedance file (impedance.csv, board-impedance.json, Z_control.xlsx...)."""
    base = name.replace("\\", "/").rsplit("/", 1)[-1]
    return bool(_NAME.search(base)) and base.lower().endswith((".csv", ".json", ".xlsx", ".tsv", ".txt"))


def _key(text) -> str:
    return re.sub(r"[\s._\-()\[\]%Ωω]", "", str(text or "").lower())


def _num(text) -> tuple[float | None, str]:
    """A figure and the unit text after it ('90R' -> (90, 'r'), '±10 %' -> (10, '%'))."""
    s = str(text if text is not None else "").strip()
    mt = re.search(r"[-+]?\d+(?:[.,]\d+)?", s)
    if not mt:
        return None, ""
    return float(mt.group(0).replace(",", ".")), s[mt.end() :].strip().lower()


def _length(text) -> float | None:
    v, unit = _num(text)
    if v is None or v <= 0:
        return None
    if unit.startswith(("mil", "th")):
        return round(v * 0.0254, 6)
    if unit.startswith(("um", "µm", "micron")):
        return round(v / 1000, 6)
    if unit.startswith(("in", '"')):
        return round(v * 25.4, 6)
    return v


def _split_patterns(text) -> list[str]:
    out, cur, depth = [], "", 0
    for c in str(text or ""):
        if c in "{[":
            depth += 1
        elif c in "}]":
            depth = max(0, depth - 1)
        if depth == 0 and (c.isspace() or c in ";|,"):
            if cur:
                out.append(cur)
            cur = ""
            continue
        cur += c
    if cur:
        out.append(cur)
    return out


def _layer(text, layers: list[str] | None) -> str | None:
    s = str(text or "").strip()
    if not s:
        return None
    k = s.lower().replace(" ", "")
    count = len(layers) if layers else None
    if k in ("top", "front", "f.cu", "fcu", "toplayer", "top_layer", "l1", "1", "gtl"):
        return "F.Cu"
    if k in ("bottom", "back", "b.cu", "bcu", "bottomlayer", "bottom_layer", "gbl"):
        return "B.Cu"
    mt = re.fullmatch(r"(?:l|layer|sig|signal)?(\d+)", k)
    if mt:
        n = int(mt.group(1))
        if n == 1:
            return "F.Cu"
        if count and n == count:
            return "B.Cu"
        if count and 1 < n < count:
            return f"In{n - 1}.Cu"
        return f"L{n}"
    mt = re.fullmatch(r"in(\d+)(?:\.?cu)?", k)
    if mt:
        return f"In{int(mt.group(1))}.Cu"
    return s


_UNIT_IN_HEADER = re.compile(r"\((mm|mils?|th|um|µm|in|ohms?|Ω|%)\)|\b(mm|mils?|ohms?)$", re.I)


def _field(header) -> tuple[str | None, str]:
    """A header cell's field and the unit it states ('Line width (mil)' -> ('width', 'mil'))."""
    text = str(header or "").strip()
    unit = _UNIT_IN_HEADER.search(text)
    field = COLUMNS.get(_key(text))
    if field is None and unit:
        field = COLUMNS.get(_key(text[: unit.start()] + text[unit.end() :]))
    return field, (unit.group(1) or unit.group(2) or "").lower() if unit else ""


def _rows_from_table(rows: list[list[str]]) -> tuple[list[dict], list[str]]:
    """Find the header row (most known names among the first 20 rows, with a target column) and read the rows."""
    best, best_score = None, 0
    for i, row in enumerate(rows[:20]):
        fields = [_field(c)[0] for c in row]
        score = len({f for f in fields if f})
        if "target" in fields and score > best_score:
            best, best_score = i, score
    if best is None:
        return [], ["No column for the target impedance (target, ohms, impedance, Z0, Zdiff)."]
    header = [_field(c) for c in rows[best]]
    zdiff = any(_key(c) == "zdiff" for c in rows[best])
    out = []
    for row in rows[best + 1 :]:
        item = {}
        for (field, unit), cell in zip(header, row, strict=False):
            text = str(cell or "").strip()
            if field and text and field not in item:
                bare = re.fullmatch(r"[-+±]?\d+(?:[.,]\d+)?", text) is not None
                item[field] = f"{text} {unit}" if unit and bare and unit not in ("%",) else text
        if item.get("target"):
            if zdiff and "kind" not in item:
                item["kind"] = "differential"
            out.append(item)
    return out, []


def _rows(data: bytes, name: str) -> tuple[list[dict], list[str]]:
    head = data[:4]
    if head[:2] == b"PK":
        from boarddd.io.bom import _sheets

        best: tuple[list[dict], list[str]] = ([], ["The spreadsheet has no sheet with a target column."])
        for sheet in _sheets(data, name or "impedance.xlsx"):
            rows = [
                ["" if c is None else (str(int(c)) if isinstance(c, float) and c.is_integer() else str(c)) for c in r]
                for r in sheet
            ]
            got = _rows_from_table(rows)
            if len(got[0]) > len(best[0]):
                best = got
        return best
    text = data.decode("utf-8-sig", errors="replace")
    stripped = text.lstrip()
    if stripped.startswith(("{", "[")):
        doc = json.loads(stripped)
        items = (doc.get("classes") or doc.get("targets") or []) if isinstance(doc, dict) else doc
        out = []
        for obj in items if isinstance(items, list) else []:
            if not isinstance(obj, dict):
                continue
            item = {}
            for k, v in obj.items():
                field = COLUMNS.get(_key(k))
                if field and v not in (None, "", []):
                    item[field] = v
            if item.get("target") is not None:
                out.append(item)
        return out, [] if out else ["No class with a target in the JSON."]
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    return _rows_from_table(list(csv.reader(io.StringIO(text), dialect)))


def read_impedance_file(
    source, *, name: str = "", names: list[str] | None = None, board=None, layers: list[str] | None = None
) -> tuple[list[m.NetClass], list[str]]:
    """
    An impedance file (path, bytes or text) -> (NetClasses with source 'file', warnings). ``names``: the board's
    net names, to fill each class's nets from its patterns; ``board``: board@1 for its Net.pair; ``layers``: its
    copper layer ids top to bottom, to read 'L3' or '3' as an inner layer.
    """
    from pathlib import Path

    from boarddd.io.kicad.project import impedance_from_name
    from boarddd.nets import match_nets

    if isinstance(source, (bytes, bytearray)):
        data = bytes(source)
    elif isinstance(source, str) and ("\n" in source or source.lstrip().startswith(("{", "["))):
        data = source.encode("utf-8")
    else:
        data = Path(source).read_bytes()
        name = name or Path(source).name
    try:
        items, warnings = _rows(data, name)
    except (ValueError, json.JSONDecodeError) as exc:
        return [], [f"{name or 'impedance file'}: {exc}"]
    classes: dict[str, m.NetClass] = {}
    explicit: dict[str, bool] = {}
    for i, item in enumerate(items):
        target, unit = _num(item.get("target"))
        if target is None or not 5 <= target <= 500:
            warnings.append(f"row {i + 1}: target {item.get('target')!r} is not an impedance")
            continue
        label = (
            str(item.get("class") or "").strip()
            or f"Z{target:g}{'_' + str(item['layer']) if item.get('layer') else ''}"
        )
        kind_text = _key(item.get("kind"))
        kind = (
            "differential"
            if kind_text in _DIFF or kind_text.startswith("diff")
            else "single"
            if kind_text in _SINGLE or kind_text.startswith("single")
            else None
        )
        by_name = impedance_from_name(label)
        if kind is None and by_name is not None:
            kind = by_name.kind
        tol_v, tol_unit = _num(item.get("tolerance"))
        tolerance = None
        if tol_v is not None and tol_v > 0:
            tolerance = round(abs(tol_v) * 100 / target, 6) if tol_unit.startswith(("ohm", "ω", "r")) else abs(tol_v)
        structure = _STRUCTURES.get(_key(item.get("structure"))) or (by_name.structure if by_name else None)
        pats = item.get("nets")
        patterns = [str(p) for p in pats] if isinstance(pats, list) else _split_patterns(pats)
        refs = _split_patterns(item.get("reference")) if item.get("reference") else []
        layer = _layer(item.get("layer"), layers)
        nc = classes.get(label)
        if nc is None:
            nc = classes[label] = m.NetClass(
                name=label,
                impedance=m.ImpedanceTarget(
                    kind=kind or "single", target=target, tolerance_pct=tolerance, structure=structure, source="file"
                ),
            )
            explicit[label] = kind is not None
        for p in patterns:
            if p not in nc.patterns:
                nc.patterns.append(p)
        width, gap = _length(item.get("width")), _length(item.get("gap"))
        if layer or width or gap:
            nc.impedance.layers.append(
                m.ImpedanceLayer(
                    layer=layer or "",
                    width=width,
                    gap=gap if (kind or nc.impedance.kind) == "differential" or gap else None,
                    coplanar_gap=_length(item.get("coplanar_gap")),
                    ref_top=_layer(refs[0], layers) if refs else None,
                    ref_bottom=_layer(refs[1], layers) if len(refs) > 1 else None,
                )
            )
        if gap is not None and nc.diff_pair_gap is None:
            nc.diff_pair_gap = gap
        if width is not None and nc.track_width is None:
            nc.track_width = width
    out = list(classes.values())
    if names is not None:
        for nc in out:
            if not nc.patterns:
                continue
            found = match_nets(list(names), nc.patterns, board=board)
            nc.nets = found["nets"]
            paired = {n for p in found["pairs"] for n in p}
            if not explicit[nc.name] and nc.nets and set(nc.nets) <= paired:
                nc.impedance.kind = "differential"
            for p in found["patterns"]:
                if p["error"]:
                    warnings.append(f"{nc.name}: pattern {p['text']!r}: {p['error']}")
                elif not p["nets"]:
                    warnings.append(f"{nc.name}: pattern {p['text']!r} matches no net")
    for nc in out:
        if nc.impedance.kind == "differential" and nc.track_width is not None and nc.diff_pair_width is None:
            nc.diff_pair_width, nc.track_width = nc.track_width, None
        for la in nc.impedance.layers:
            if nc.impedance.kind != "differential":
                la.gap = None
    return out, warnings
