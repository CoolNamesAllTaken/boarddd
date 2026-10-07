#!/bin/bash
# Rebuild base.svg / head.svg: the root sheet of KiCad's demos/pic_programmer exported with kicad-cli,
# head with R7's value 10K -> 4.7K, both cropped to sheet mm 120,5..200,60 and minified.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
cli=${KICAD_CLI:-/workspace/projects/kipr-tools/bin/kicad-cli}
demo=${KICAD_DEMO:-/workspace/projects/kipr-tools/kicad10-rootfs/usr/share/kicad/demos/pic_programmer}
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
for side in base head; do
  cp -r "$demo" "$tmp/$side" && chmod -R u+w "$tmp/$side"
done
python3 - "$tmp/head/pic_programmer.kicad_sch" <<'PY'
import re, sys
t = open(sys.argv[1]).read()
i = t.index('(property "Reference" "R7"')
j = t.index('(property "Value" "10K"', i)
assert j - i < 2000, 'R7 value not found'
open(sys.argv[1], 'w').write(t[:j] + '(property "Value" "4.7K"' + t[j + len('(property "Value" "10K"'):])
PY
for side in base head; do
  "$cli" sch export svg -o "$tmp/out-$side" "$tmp/$side/pic_programmer.kicad_sch" >/dev/null
  python3 "$here/crop_svg.py" "$tmp/out-$side/pic_programmer.svg" "$here/$side.svg" 120,5,200,60
done
ls -la "$here"/*.svg
