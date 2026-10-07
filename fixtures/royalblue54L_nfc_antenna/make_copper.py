"""Build the golden copper.json (boarddd/copper@1) for KiCad's RoyalBlue54L NFC antenna demo from the KiCad reader.

    python fixtures/royalblue54L_nfc_antenna/make_copper.py          # rewrites copper.json
    python fixtures/royalblue54L_nfc_antenna/make_copper.py --check  # fails if copper.json differs

``boarddd.io.kicad.read_kicad_copper`` on kicad/: 160 tracks (48 of them arcs) on F.Cu/B.Cu, 2 vias, 2 pads and
4 copper polygons, all on net /ANT. python/tests/io/test_gerber_copper.py checks the Gerber reader (Python and JS)
against it on fab/ (kicad-cli's export of the same board, make_fab.sh).
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "python" / "src"))

from boarddd import copper as cu  # noqa: E402
from boarddd.io.kicad import read_kicad_copper  # noqa: E402

PCB = HERE / "kicad" / "RoyalBlue54L-NFC-Antenna.kicad_pcb"
OUT = HERE / "copper.json"


def build() -> cu.Copper:
    doc = read_kicad_copper(PCB, root=HERE, name="RoyalBlue54L-NFC-Antenna")
    doc.source.reader = "boarddd io.kicad.read_kicad_copper"  # no version: the golden survives releases
    return doc


def main() -> int:
    text = build().to_json()
    if "--check" in sys.argv:
        if OUT.read_text("utf-8") != text:
            print(f"{OUT} is out of date: run {Path(__file__).name}", file=sys.stderr)
            return 1
        return 0
    OUT.write_text(text, encoding="utf-8")
    print(f"wrote {OUT} ({len(text) // 1024} KiB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
