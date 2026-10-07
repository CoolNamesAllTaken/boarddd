#!/bin/bash
# Re-export fab/ from kicad/RoyalBlue54L-Feather.kicad_pcb with kicad-cli 10 (all 8 copper layers, mask,
# paste, silk, Edge.Cuts, the gbrjob, PTH/NPTH Excellon in mm and the pos file), then rebuild board.json:
#     bash fixtures/royalblue54L_feather/make_fab.sh && python fixtures/royalblue54L_feather/make_board.py
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
cli=${KICAD_CLI:-kicad-cli}
pcb=$here/kicad/RoyalBlue54L-Feather.kicad_pcb
rm -rf "$here/fab" && mkdir -p "$here/fab"
"$cli" pcb export gerbers -o "$here/fab/" --no-protel-ext \
  -l F.Cu,In1.Cu,In2.Cu,In3.Cu,In4.Cu,In5.Cu,In6.Cu,B.Cu,F.Mask,B.Mask,F.Paste,B.Paste,F.Silkscreen,B.Silkscreen,Edge.Cuts \
  "$pcb" >/dev/null
"$cli" pcb export drill -o "$here/fab/" --format excellon --excellon-separate-th --excellon-units mm "$pcb" >/dev/null
"$cli" pcb export pos --format csv --units mm --side both -o "$here/fab/pos.csv" "$pcb" >/dev/null
ls "$here/fab"
