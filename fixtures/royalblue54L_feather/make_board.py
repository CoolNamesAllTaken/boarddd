"""Build the golden board.json for KiCad's royalblue54L_feather demo from the readers.

    python fixtures/royalblue54L_feather/make_board.py          # rewrites board.json
    python fixtures/royalblue54L_feather/make_board.py --check  # fails if board.json differs

Two readers, one board: ``boarddd.io.kicad.read_kicad_pcb`` on kicad/ (the .kicad_pcb and .kicad_pro:
components, footprints, outline, stackup, origins, nets, net classes) and ``boarddd.io.package.read_package``
on fab/ (the Gerber and drill layers, the Excellon drills). The placements are cross-checked against
kicad-cli's fab/pos.csv.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "python" / "src"))

from boarddd import model as m  # noqa: E402
from boarddd.io.kicad import read_kicad_pcb  # noqa: E402
from boarddd.io.kicad.geom import norm_angle  # noqa: E402
from boarddd.io.package import read_package  # noqa: E402

PCB = HERE / "kicad" / "RoyalBlue54L-Feather.kicad_pcb"
PRO = PCB.with_suffix(".kicad_pro")
FAB = HERE / "fab"
OUT = HERE / "board.json"


def check_pos(comps: list[m.Component]) -> int:
    """kicad-cli's pos file (board frame) must agree with what we read from the .kicad_pcb."""
    by_ref = {c.ref: c for c in comps}
    rows = list(csv.DictReader(open(FAB / "pos.csv", newline="")))
    for row in rows:
        c = by_ref[row["Ref"]]
        assert c.in_pos and c.side == row["Side"], row
        assert abs(c.x - float(row["PosX"])) < 1e-6 and abs(c.y - float(row["PosY"])) < 1e-6, (row, c.x, c.y)
        assert abs(norm_angle(c.rotation - float(row["Rot"]))) < 1e-6, (row, c.rotation)
    assert len(rows) == sum(c.in_pos for c in comps), "pos file and in_pos disagree"
    return len(rows)


def build() -> m.Board:
    board = read_kicad_pcb(PCB, PRO, root=HERE)
    check_pos(board.components)
    fab = read_package(FAB)

    def under_fab(path: str) -> str:
        return f"fab/{path}"

    for layer in fab.layers:
        layer.files = [under_fab(f) for f in layer.files]
    for f in fab.source.files:
        f.path = under_fab(f.path)
    board.layers, board.drills = fab.layers, fab.drills
    board.source.files = sorted(board.source.files + fab.source.files, key=lambda f: f.path)
    board.source.generator = f"{fab.source.generator} (fab outputs); {board.source.generator} (board file)"
    board.source.reader = "boarddd io.kicad.read_kicad_pcb (kicad/) + io.package.read_package (fab/)"
    board.warnings += fab.warnings
    return board


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
