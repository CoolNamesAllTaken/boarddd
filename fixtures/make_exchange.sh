#!/bin/bash
# Re-export the IPC-2581 and ODB++ fixtures of the KiCad demo boards with kicad-cli 10 (KICAD_CLI, else
# kicad-cli on PATH), for boarddd.io.ipc2581 / boarddd.io.odbpp and their cross-checks against the
# .kicad_pcb (python/tests/io/test_exchange.py):
#     bash fixtures/make_exchange.sh
# IPC-2581 rev C in mm, gzipped without a name or timestamp (gzip -n); ODB++ as kicad-cli's zip (it
# carries a creation date, so re-running changes its bytes but not its content).
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
cli=${KICAD_CLI:-kicad-cli}
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
export_board() { # <pcb> <out dir> <stem>
  "$cli" pcb export ipc2581 --units mm -o "$tmp/$3.xml" "$1" >/dev/null 2>&1
  gzip -n -9 -c "$tmp/$3.xml" >"$2/$3-ipc2581.xml.gz"
  "$cli" pcb export odb -o "$2/$3-odb.zip" "$1" >/dev/null 2>&1
}
export_board "$here/royalblue54L_feather/kicad/RoyalBlue54L-Feather.kicad_pcb" "$here/royalblue54L_feather/exchange" RoyalBlue54L-Feather
export_board "$here/pic_programmer/kicad/pic_programmer.kicad_pcb" "$here/pic_programmer/exchange" pic_programmer
ls -l "$here"/*/exchange/
