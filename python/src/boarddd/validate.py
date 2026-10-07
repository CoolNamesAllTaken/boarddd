"""Validate a board.json document (plain JSON values) against boarddd/board@1.

A small checker for the JSON Schema subset ``boarddd._codegen`` emits, plus the model rules a schema
can't express. src/model/index.js implements the same checks with the same messages; the shared
fixtures in fixtures/model/ keep the two in step. No dependencies.
"""

from __future__ import annotations

import json
import math
import re
from functools import cache
from pathlib import Path
from typing import Any


@cache
def schema() -> dict[str, Any]:
    """The committed schema (generated from boarddd.model; identical to schema/board.schema.json)."""
    from . import _codegen

    return _codegen.schema()


def _type_ok(t: str, v: Any) -> bool:
    if t == "null":
        return v is None
    if t == "boolean":
        return isinstance(v, bool)
    if t == "integer":
        return isinstance(v, int) and not isinstance(v, bool) or (isinstance(v, float) and v.is_integer())
    if t == "number":
        return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
    if t == "string":
        return isinstance(v, str)
    if t == "array":
        return isinstance(v, list)
    if t == "object":
        return isinstance(v, dict)
    raise ValueError(f"unknown type {t}")


def _ptr(path: str, key: Any) -> str:
    return f"{path}/{str(key).replace('~', '~0').replace('/', '~1')}"


def _fmt(v: Any) -> str:
    return json.dumps(v, separators=(", ", ": "))


def _check(s: dict[str, Any], v: Any, path: str, root: dict[str, Any], errors: list[str]) -> None:
    if "$ref" in s:
        _check(root["$defs"][s["$ref"].removeprefix("#/$defs/")], v, path, root, errors)
    if "anyOf" in s:
        branches = s["anyOf"]
        results = []
        for b in branches:
            errs: list[str] = []
            _check(b, v, path, root, errs)
            if not errs:
                break
            results.append(errs)
        else:
            # nullable: report the non-null branch's errors (more useful than "matches nothing")
            non_null = [r for b, r in zip(branches, results, strict=True) if b != {"type": "null"}]
            errors.extend(non_null[0] if len(non_null) == 1 else [f"{path or '/'}: matches none of the allowed shapes"])
    if "const" in s and v != s["const"]:
        errors.append(f"{path or '/'}: must be {_fmt(s['const'])}")
        return
    if "type" in s:
        types = s["type"] if isinstance(s["type"], list) else [s["type"]]
        if not any(_type_ok(t, v) for t in types):
            errors.append(f"{path or '/'}: expected {' or '.join(types)}")
            return
    if "enum" in s and v not in s["enum"]:
        errors.append(f"{path or '/'}: must be one of {_fmt(s['enum'])}")
        return
    if isinstance(v, str) and "pattern" in s and not re.search(s["pattern"], v):
        errors.append(f"{path or '/'}: does not match {s['pattern']}")
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        if "minimum" in s and v < s["minimum"]:
            errors.append(f"{path or '/'}: must be >= {_fmt(s['minimum'])}")
        if "maximum" in s and v > s["maximum"]:
            errors.append(f"{path or '/'}: must be <= {_fmt(s['maximum'])}")
        if "exclusiveMinimum" in s and v <= s["exclusiveMinimum"]:
            errors.append(f"{path or '/'}: must be > {_fmt(s['exclusiveMinimum'])}")
    if isinstance(v, list):
        if "minItems" in s and len(v) < s["minItems"]:
            errors.append(f"{path or '/'}: must have at least {s['minItems']} items")
        if "maxItems" in s and len(v) > s["maxItems"]:
            errors.append(f"{path or '/'}: must have at most {s['maxItems']} items")
        prefix = s.get("prefixItems", [])
        for i, item in enumerate(v):
            if i < len(prefix):
                _check(prefix[i], item, _ptr(path, i), root, errors)
            elif isinstance(s.get("items"), dict):
                _check(s["items"], item, _ptr(path, i), root, errors)
    if isinstance(v, dict):
        props = s.get("properties", {})
        for name in s.get("required", []):
            if name not in v:
                errors.append(f"{path or '/'}: missing required property '{name}'")
        extra = s.get("additionalProperties", True)
        for k, item in v.items():
            if k in props:
                _check(props[k], item, _ptr(path, k), root, errors)
            elif extra is False:
                errors.append(f"{path or '/'}: unknown property '{k}'")
            elif isinstance(extra, dict):
                _check(extra, item, _ptr(path, k), root, errors)


def _rules(b: dict[str, Any], errors: list[str]) -> None:
    """Model rules beyond the schema (run only on schema-valid documents)."""
    seen: dict[str, int] = {}
    for i, c in enumerate(b.get("components", [])):
        if c["ref"] in seen:
            errors.append(f"/components/{i}/ref: duplicate reference '{c['ref']}' (also /components/{seen[c['ref']]})")
        seen.setdefault(c["ref"], i)
    fps = b.get("footprints", {})
    if fps:
        for i, c in enumerate(b.get("components", [])):
            if c.get("footprint") is not None and c["footprint"] not in fps:
                errors.append(f"/components/{i}/footprint: '{c['footprint']}' is not in /footprints")
    for key, fp in fps.items():
        if fp["name"] != key:
            errors.append(f"{_ptr('/footprints', key)}/name: must equal its key '{key}'")
    ids: dict[str, int] = {}
    for i, layer in enumerate(b.get("layers", [])):
        if layer["id"] in ids:
            errors.append(f"/layers/{i}/id: duplicate layer id '{layer['id']}' (also /layers/{ids[layer['id']]})")
        ids.setdefault(layer["id"], i)
    for i, d in enumerate(b.get("drills", [])):
        if (d.get("x2") is None) != (d.get("y2") is None):
            errors.append(f"/drills/{i}: x2 and y2 must both be set (slot) or both be null (round hole)")
        if d.get("layer") is not None and d["layer"] not in ids:
            errors.append(f"/drills/{i}/layer: '{d['layer']}' is not a layer id")
    st = b.get("stackup", {})
    copper = [x for x in st.get("layers", []) if x["kind"] == "copper"]
    if copper and st.get("copper_layers") is not None and st["copper_layers"] != len(copper):
        errors.append(
            f"/stackup/copper_layers: {st['copper_layers']} but /stackup/layers has {len(copper)} copper layers"
        )


def validate_board(data: Any) -> list[str]:
    """Return a list of 'json/pointer: message' errors; empty when ``data`` is a valid boarddd/board@1."""
    errors: list[str] = []
    root = schema()
    _check(root, data, "", root, errors)
    if not errors:
        _rules(data, errors)
    return errors


def main(argv: list[str] | None = None) -> int:
    import sys

    argv = sys.argv[1:] if argv is None else argv
    bad = 0
    for p in argv:
        errs = validate_board(json.loads(Path(p).read_text("utf-8")))
        for e in errs:
            print(f"{p}: {e}")
        bad += bool(errs)
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
