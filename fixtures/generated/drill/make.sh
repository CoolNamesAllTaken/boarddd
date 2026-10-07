#!/bin/bash
# Excellon variants of test/fixtures/slots-board/slots.kicad_pcb (oval PTH/NPTH holes) for the drill tests:
#   route/       oval holes as rout commands (G00 / M15 / G01 / M16), mm, decimal
#   alternate/   oval holes as G85 canned slots (KiCad's default), mm, decimal
#   inch-*/      inches with implied decimals: suppressleading, suppresstrailing, keep (zeros)
# Each export is one combined file (plated and non-plated tools together).
#     bash fixtures/generated/drill/make.sh
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
cli=${KICAD_CLI:-kicad-cli}
pcb=$here/../../../test/fixtures/slots-board/slots.kicad_pcb
export_drill() { # dir, extra args...
  local dir=$here/$1; shift
  rm -rf "$dir" && mkdir -p "$dir"
  "$cli" pcb export drill -o "$dir/" --format excellon "$@" "$pcb" >/dev/null
}
export_drill route --excellon-oval-format route --excellon-units mm
export_drill alternate --excellon-oval-format alternate --excellon-units mm
for zeros in suppressleading suppresstrailing keep; do
  export_drill "inch-$zeros" --excellon-units in --excellon-zeros-format "$zeros"
done
ls -R "$here"
