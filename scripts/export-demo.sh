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
( cd "$out" && ls *.gbr *.drl | python3 -c 'import json,sys; print(json.dumps({"source": sys.argv[1], "files": [l.strip() for l in sys.stdin if l.strip()], "glb": "board.glb"}, indent=1))' "$(basename "$pcb")" > manifest.json )
du -sh "$out"
