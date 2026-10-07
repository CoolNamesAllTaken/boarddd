#!/bin/bash
# Re-export fab/ from kicad/RoyalBlue54L-NFC-Antenna.kicad_pcb with kicad-cli 10 (copper, mask, Edge.Cuts, the
# gbrjob and PTH/NPTH Excellon in mm), for the copper readers' tests (docs/copper.md):
#     bash fixtures/royalblue54L_nfc_antenna/make_fab.sh
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
cli=${KICAD_CLI:-kicad-cli}
pcb=$here/kicad/RoyalBlue54L-NFC-Antenna.kicad_pcb
rm -rf "$here/fab" && mkdir -p "$here/fab"
"$cli" pcb export gerbers -o "$here/fab/" --no-protel-ext -l F.Cu,B.Cu,F.Mask,B.Mask,Edge.Cuts "$pcb" >/dev/null
"$cli" pcb export drill -o "$here/fab/" --format excellon --excellon-separate-th --excellon-units mm "$pcb" >/dev/null
ls "$here/fab"
