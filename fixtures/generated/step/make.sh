#!/bin/bash
# Regenerate the STEP fixtures for boarddd's STEP reader tests (python/tests/step/):
#   royalblue54L_feather-excerpt.step  a few components of KiCad's royalblue54L_feather demo, exported
#                                      with KiCad's stock 3D models, cut down by make_excerpt.py
# The demo board names its models ${KICAD8_3DMODEL_DIR}/....wrl; a temporary copy points them at the
# STEP models of the kicad-cli install (kipr-tools' KiCad 10 rootfs) so the export carries bodies.
#     bash fixtures/generated/step/make.sh
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$here/../../.." && pwd)
cli=${KICAD_CLI:-/workspace/projects/kipr-tools/bin/kicad-cli}
models=${KICAD_3DMODEL_DIR:-$(dirname "$(readlink -f "$cli")")/../kicad10-rootfs/usr/share/kicad/3dmodels}
models=$(cd "$models" && pwd)
python=${PYTHON:-python3}
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
sed "s#\${KICAD8_3DMODEL_DIR}#$models#g; s#\.wrl\"#.step\"#g" \
  "$repo/fixtures/royalblue54L_feather/kicad/RoyalBlue54L-Feather.kicad_pcb" >"$tmp/RoyalBlue54L-Feather.kicad_pcb"
"$cli" pcb export step -f -o "$tmp/full.step" "$tmp/RoyalBlue54L-Feather.kicad_pcb" >/dev/null 2>&1
# C1, C25 (+ C2 C3 C4 C10 C11 C24 R1 R4 R8 L1): passives, the 0402s sharing one PRODUCT; U2: a QFN; Y2: a crystal whose model lies on its side;
# J4: a connector the model places 4.5 mm from its footprint anchor; D3: a vendor LED model that
# is an assembly of its own (Body, Pins, Resin)
"$python" "$here/make_excerpt.py" "$tmp/full.step" "$here/royalblue54L_feather-excerpt.step" C1 C25 U2 Y2 J4 D3 C2 C3 C4 C10 C11 C24 R1 R4 R8 L1
ls -l "$here"/*.step
