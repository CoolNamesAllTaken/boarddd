#!/bin/bash
# The royalblue54L_feather Edge.Cuts plotted with the drawing sheet (border and title block, --ibt),
# and its job file: the largest loop in this outline is the sheet, not the board.
#     bash fixtures/generated/outline/make.sh
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
cli=${KICAD_CLI:-/workspace/projects/kipr-tools/bin/kicad-cli}
pcb=$here/../../royalblue54L_feather/kicad/RoyalBlue54L-Feather.kicad_pcb
rm -rf "$here/sheet" && mkdir -p "$here/sheet"
"$cli" pcb export gerbers -o "$here/sheet/" --no-protel-ext -l Edge.Cuts --ibt "$pcb" >/dev/null
ls "$here/sheet"
