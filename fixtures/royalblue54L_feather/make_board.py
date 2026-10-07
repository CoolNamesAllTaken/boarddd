"""Build the golden board.json for KiCad's royalblue54L_feather demo.

    python fixtures/royalblue54L_feather/make_board.py          # rewrites board.json
    python fixtures/royalblue54L_feather/make_board.py --check  # fails if board.json differs

The KiCad half (components, footprints, outline, stackup, origins, nets, net classes) is
``boarddd.io.kicad.read_kicad_pcb`` on kicad/RoyalBlue54L-Feather.kicad_pcb + .kicad_pro, checked against
kicad-cli's fab/pos.csv. The fab half is still hand-written here: the layer files from fab/*.gbrjob and the
drills from fab/*.drl (python/tests/kicad/test_royalblue.py checks that the board file's own drills match
them). It goes when phase F1's ``boarddd.io`` fab readers (gbrjob, excellon, read_package) land.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "python" / "src"))

from boarddd import model as m  # noqa: E402
from boarddd.io.kicad import read_kicad_pcb  # noqa: E402
from boarddd.io.kicad.geom import norm_angle, r  # noqa: E402

PCB = HERE / "kicad" / "RoyalBlue54L-Feather.kicad_pcb"
PRO = PCB.with_suffix(".kicad_pro")
FAB = HERE / "fab"
OUT = HERE / "board.json"


ROLE = {"Copper": "copper", "SolderMask": "mask", "SolderPaste": "paste", "Legend": "silk", "Profile": "outline"}
DRAW_ORDER = ["outline", "paste", "silk", "mask", "copper", "drill"]


def layer_id(path: str) -> str:
    stem = path.rsplit("-", 1)[1].rsplit(".", 1)[0]  # RoyalBlue54L-Feather-F_Cu.gbr -> F_Cu
    return stem.replace("_", ".", 1) if stem not in ("PTH", "NPTH") else stem


def read_layers(job: dict) -> list[m.Layer]:
    layers = []
    n_copper = job["GeneralSpecs"]["LayerNumber"]
    for f in job["FilesAttributes"]:
        func = f["FileFunction"].split(",")
        role = ROLE[func[0]]
        if role == "copper":
            side = {"Top": "top", "Bot": "bottom", "Inr": "inner"}[func[2]]
            order = int(func[1][1:])
        else:
            side = {"Top": "top", "Bot": "bottom"}.get(func[-1], "none")
            order = n_copper + 1 + DRAW_ORDER.index(role)
        layers.append(
            m.Layer(
                id=layer_id(f["Path"]),
                role=role,
                side=side,
                order=order,
                files=[f"fab/{f['Path']}"],
                format="gerber",
                polarity=f["FilePolarity"].lower(),
                function=f["FileFunction"],
            )
        )
    for name, plated in (("PTH", True), ("NPTH", False)):
        path = next(FAB.glob(f"*-{name}.drl"))
        func = re.search(r"TF\.FileFunction,(.*)", path.read_text()).group(1).strip()
        layers.append(
            m.Layer(
                id=name,
                role="drill",
                side="none",
                order=n_copper + 1 + len(DRAW_ORDER) + (not plated),
                files=[f"fab/{path.name}"],
                format="excellon",
                function=func,
                plated=plated,
            )
        )
    return sorted(layers, key=lambda la: (la.order, la.side != "top", la.id))


DRILL_FUNCTION = {"ViaDrill": "via", "ComponentDrill": "component", "MechanicalDrill": "mechanical"}


def read_drills(layer_id_: str, plated: bool) -> list[m.Drill]:
    text = next(FAB.glob(f"*-{layer_id_}.drl")).read_text()
    assert "METRIC" in text
    tools, funcs, func, out, tool = {}, {}, None, [], None
    for line in text.splitlines():
        if mt := re.match(r"; #@! TA\.AperFunction,.*,(\w+)$", line):
            func = DRILL_FUNCTION.get(mt.group(1))
        elif mt := re.fullmatch(r"(T\d+)C([\d.]+)", line):
            tools[mt.group(1)], funcs[mt.group(1)] = float(mt.group(2)), func
        elif re.fullmatch(r"T\d+", line):
            tool = line
        elif mt := re.fullmatch(r"X(-?[\d.]+)Y(-?[\d.]+)(?:G85X(-?[\d.]+)Y(-?[\d.]+))?", line):
            x, y, x2, y2 = (float(v) if v is not None else None for v in mt.groups())
            out.append(
                m.Drill(
                    x=r(x),
                    y=r(y),
                    diameter=r(tools[tool]),
                    plated=plated,
                    x2=r(x2) if x2 is not None else None,
                    y2=r(y2) if y2 is not None else None,
                    tool=tool,
                    function=funcs[tool],
                    layer=layer_id_,
                )
            )
    return out


def sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


ROLE_OF_FILE = {".kicad_pcb": "pcb", ".gbrjob": "job", ".drl": "drill", ".csv": "placement"}


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
    job = json.loads(next(FAB.glob("*.gbrjob")).read_text())
    gen = job["Header"]["GenerationSoftware"]
    drills = read_drills("PTH", True) + read_drills("NPTH", False)
    zero = sum(d.diameter == 0 for d in drills)
    layers = read_layers(job)
    side_of = {f: la.side for la in layers for f in la.files}
    role_of = {f: la.role for la in layers for f in la.files}
    src_files = []
    for p in sorted([PCB, PRO, *FAB.iterdir()], key=lambda p: (p.parent.name, p.name)):
        rel = p.relative_to(HERE).as_posix()
        role = role_of.get(rel) or ROLE_OF_FILE.get(p.suffix, "other")
        side = side_of.get(rel)
        src_files.append(m.SourceFile(path=rel, role=role, side=side if side != "none" else None, sha256=sha256(p)))
    board.source.files = src_files
    board.source.generator = f"{gen['Vendor']} {gen['Application']} {gen['Version']} (fab outputs); {board.source.generator} (board file)"
    board.source.reader = "boarddd io.kicad.read_kicad_pcb + fixtures/royalblue54L_feather/make_board.py (fab files)"
    board.layers = layers
    board.drills = drills
    if zero:
        board.warnings.append(
            f"{zero} vias have a 0 mm drill in the drill file (the demo board's vias use a 0.00001 mm placeholder drill)"
        )
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
