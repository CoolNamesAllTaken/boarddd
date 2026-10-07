#!/bin/bash
# Re-export the "outline plotted on every layer" Gerbers used by python/tests/io/test_gerber.py and
# test_pads.py: royalblue54L_feather's F.Paste and B.Paste with Edge.Cuts as a common layer (KiCad
# writes those strokes with a %TA.AperFunction,Profile*% aperture).
#     bash fixtures/generated/profile/make.sh
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
cli=${KICAD_CLI:-kicad-cli}
pcb=$here/../../royalblue54L_feather/kicad/RoyalBlue54L-Feather.kicad_pcb
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
"$cli" pcb export gerbers -o "$tmp/" --no-protel-ext -l F.Paste,B.Paste --cl Edge.Cuts "$pcb" >/dev/null
for layer in F_Paste B_Paste; do
  cp "$tmp/RoyalBlue54L-Feather-$layer.gbr" "$here/RoyalBlue54L-Feather-$layer-profile.gbr"
done
ls -l "$here"
