"""Write royalblue54L_feather-bom.csv: a KiCad-style grouped BOM of the royalblue54L_feather demo board.

    python fixtures/generated/bom/make_bom.py           # rewrites the CSV
    python fixtures/generated/bom/make_bom.py --check   # fails if the CSV differs

Generated test data for boarddd.io.bom (python/tests/io/test_bom.py), from the golden
fixtures/royalblue54L_feather/board.json: every component in the BOM (in_bom), grouped the way
KiCad's BOM export groups them (same value, footprint and LCSC number), in the column layout of a
KiCad export configured with the usual fields. Components marked not populated are written with
Populate = DNP. The demo board carries no MPNs or manufacturers, so those columns are blank; the
LCSC numbers are the demo's own. Stdlib only.
"""

from __future__ import annotations

import csv
import io
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BOARD = HERE.parents[1] / "royalblue54L_feather" / "board.json"
OUT = HERE / "royalblue54L_feather-bom.csv"
HEADER = [
    "Reference",
    "Value",
    "Footprint",
    "Quantity",
    "Populate",
    "Standard Cost",
    "Manufacturer",
    "MPN",
    "LCSC PN",
    "Note",
]


def ref_key(ref: str):
    return (re.sub(r"\d+", "", ref), int(re.sub(r"\D", "", ref) or 0))


def build() -> str:
    board = json.loads(BOARD.read_text("utf-8"))
    groups: dict[tuple, list[dict]] = {}
    for comp in sorted((c for c in board["components"] if c["in_bom"]), key=lambda c: ref_key(c["ref"])):
        key = (comp["value"], comp["footprint"], comp["attributes"].get("LCSC", ""), comp["populate"])
        groups.setdefault(key, []).append(comp)
    out = io.StringIO()
    writer = csv.writer(out, quoting=csv.QUOTE_ALL, lineterminator="\n")
    writer.writerow(HEADER)
    for (value, footprint, lcsc, populate), comps in groups.items():
        refs = ",".join(c["ref"] for c in comps)
        writer.writerow([refs, value, footprint, len(comps), "" if populate else "DNP", "", "", "", lcsc, ""])
    return out.getvalue()


def main() -> int:
    text = build()
    if "--check" in sys.argv:
        return 0 if OUT.read_text("utf-8") == text else 1
    OUT.write_text(text, encoding="utf-8")
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
