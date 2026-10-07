#!/bin/bash
# Export a KiCad board's fab outputs + GLB for the examples and browser tests (needs kicad-cli 10).
#
#     bash scripts/export-demo.sh BOARD.kicad_pcb OUTDIR
#
# Writes OUTDIR/{*.gbr, *.drl, board.glb, manifest.json}: the Gerbers boarddd paints faces from
# (copper, mask, silk, paste, Edge.Cuts), Excellon drills split PTH/NPTH in mm (oval holes as G85 slots,
# KiCad's default), and kicad-cli's GLB with the 3D models (STEP substituted for VRML).
set -euo pipefail
pcb=$1; out=$2
cli=${KICAD_CLI:-/workspace/projects/kipr-tools/bin/kicad-cli}
mkdir -p "$out"
"$cli" pcb export gerbers -o "$out/" -l F.Cu,B.Cu,F.Mask,B.Mask,F.Silkscreen,B.Silkscreen,F.Paste,B.Paste,Edge.Cuts --no-protel-ext "$pcb" >/dev/null
"$cli" pcb export drill -o "$out/" --format excellon --excellon-separate-th --excellon-units mm "$pcb" >/dev/null
"$cli" pcb export glb -o "$out/board.glb" --subst-models --force "$pcb" >/dev/null
# placements for boarddd/models (mesh -> refdes matching): KiCad mm, y DOWN (the pos file has y up)
pos=$(mktemp --suffix=.csv)
"$cli" pcb export pos --format csv --units mm --side both -o "$pos" "$pcb" >/dev/null
python3 - "$pos" "$out/components.json" <<'PY'
import csv, json, os, sys
rows = [{"ref": r["Ref"], "value": r["Val"], "footprint": r["Package"], "x": float(r["PosX"]), "y": -float(r["PosY"]),
         "rot": float(r["Rot"]), "side": r["Side"]} for r in csv.DictReader(open(sys.argv[1]))]
json.dump(rows, open(sys.argv[2], "w"), indent=0)
os.unlink(sys.argv[1])
PY
( cd "$out" && ls *.gbr *.drl | python3 -c 'import json,sys; print(json.dumps({"source": sys.argv[1], "files": [l.strip() for l in sys.stdin if l.strip()], "glb": "board.glb", "components": "components.json"}, indent=1))' "$(basename "$pcb")" > manifest.json )
du -sh "$out"
