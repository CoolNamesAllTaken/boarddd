#!/bin/bash
# KiCad's own SVG plot of royalblue54L_feather's F.Mask (board area, no drawing sheet, black and white): the
# board render's pixel reference (test_render_board.py). KICAD_CLI: the kicad-cli to run (KiCad 10).
#     bash python/tests/render/fixtures/kicad/make.sh
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$here/../../../../.." && pwd)
cli=${KICAD_CLI:-kicad-cli}
"$cli" pcb export svg --mode-single --layers F.Mask --page-size-mode 2 --exclude-drawing-sheet --drill-shape-opt 0 \
  --black-and-white -o "$here/royalblue54L-F_Mask.svg" "$repo/fixtures/royalblue54L_feather/kicad/RoyalBlue54L-Feather.kicad_pcb" >/dev/null
# the plot's title carries a timestamp: drop it so the file depends only on the board
sed -i -E 's#<title>SVG Image created as [^<]*</title>#<title>royalblue54L F.Mask</title>#' "$here/royalblue54L-F_Mask.svg"
