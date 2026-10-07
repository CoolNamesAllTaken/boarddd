#!/bin/bash
# Regenerate the STEP fixtures for boarddd's STEP reader tests (python/tests/step/):
#   royalblue54L_feather-excerpt.step  a few components of KiCad's royalblue54L_feather demo, exported
#                                      with KiCad's stock 3D models, cut down by make_excerpt.py
#   royalblue54L_feather.step.gz       the whole export (components and board body, no tracks), gzipped
# The demo board names its models ${KICAD8_3DMODEL_DIR}/....wrl; a temporary copy points them at the
# STEP models of KiCad's stock 3D library so the export carries bodies. KICAD_3DMODEL_DIR (else
# KICAD10_3DMODEL_DIR, else /usr/share/kicad/3dmodels) is that library; KICAD_CLI the kicad-cli to run.
#     bash fixtures/generated/step/make.sh
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$here/../../.." && pwd)
cli=${KICAD_CLI:-kicad-cli}
models=${KICAD_3DMODEL_DIR:-${KICAD10_3DMODEL_DIR:-/usr/share/kicad/3dmodels}}
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
# The whole board, gzipped, for the [step] extra's tests (python/tests/step/test_step_royalblue.py)
# (FILE_NAME's name and timestamp fixed; kicad-cli's entity order still varies between runs, so a
# regenerated file differs in bytes, not in what the tests check)
sed -E "s/^FILE_NAME\('[^']*','[^']*'/FILE_NAME('royalblue54L_feather.step','2000-01-01T00:00:00'/" "$tmp/full.step" \
  | gzip -9 -n >"$here/royalblue54L_feather.step.gz"
ls -l "$here"/*.step "$here"/*.step.gz
