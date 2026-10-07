#!/bin/bash
# Vendor the upstream half of our wasm-gerber-viewer fork into third_party/wasm-gerber-renderer/:
#   core/   packages/wasm-gerber-renderer/{index.js, shared.js, index.d.ts} (upstream, lightly patched in the fork)
#   crate/  the Rust crate wasm/ (+ the fork's rust-toolchain.toml), built by scripts/build-wasm.sh
#   odb/    js/src/odb (the ODB++ job loader: archive trees, matrix, layer envelopes) + js/core/config.js,
#           wrapped for the browser by src/gerber/odb.js (loadOdbJob); its Node-only zip-node.js is left out
# Our own modules (board, diff, drills, ...) are boarddd source in src/gerber/ and are not touched.
#
#     bash scripts/sync-fork.sh [FORK_CHECKOUT] [REF]
#
# Then run scripts/build-wasm.sh (or the CI wasm job) and commit third_party/ together.
set -euo pipefail
here=$(cd "$(dirname "$0")/.." && pwd)
fork=${1:-${WGV:-$here/../wasm-gerber-viewer}}
PINNED_REF=92976b5   # fork main after PR #5 (ringsToGerber)
ref=${2:-$PINNED_REF}
target="$here/third_party/wasm-gerber-renderer"
pkg=packages/wasm-gerber-renderer
commit=$(git -C "$fork" rev-parse "$ref^{commit}")
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
git -C "$fork" archive "$commit" "$pkg" wasm rust-toolchain.toml js/src/odb js/core/config.js | tar -x -C "$tmp"

new="$target.new"
rm -rf "$new"; mkdir -p "$new/core" "$new/crate" "$new/odb"
cp "$tmp/$pkg/LICENSE" "$new/LICENSE"
cp "$tmp/$pkg/index.js" "$tmp/$pkg/shared.js" "$tmp/$pkg/index.d.ts" "$new/core/"
# shared.js imports the fork's drills.js, which lives in boarddd's src/gerber/ now.
grep -q 'from "./drills.js"' "$new/core/shared.js"
sed -i 's#from "./drills.js"#from "../../../src/gerber/drills.js"#' "$new/core/shared.js"
cp -r "$tmp/wasm/." "$new/crate/"
rm -rf "$new/crate/pkg" "$new/crate/target"
cp "$tmp/rust-toolchain.toml" "$new/crate/"
cp -r "$tmp/js/src/odb/." "$new/odb/"
rm "$new/odb/archive/zip-node.js"   # node:zlib; src/gerber/odb-zip.js reads zips in the browser
cp "$tmp/js/core/config.js" "$new/odb/core-config.js"
grep -q 'from "../../core/config.js"' "$new/odb/config.js"
sed -i 's#from "../../core/config.js"#from "./core-config.js"#' "$new/odb/config.js"
# The committed build (core/wasm/) belongs to build-wasm.sh; keep it.
[ -d "$target/core/wasm" ] && cp -r "$target/core/wasm" "$new/core/"
echo "$commit" > "$new/FORK_COMMIT"
cat > "$new/README.md" <<README
# wasm-gerber-renderer (vendored)

MIT, © dsafdsaf132 (see LICENSE). Upstream https://github.com/dsafdsaf132/wasm-gerber-viewer, via our fork
https://github.com/CoolNamesAllTaken/wasm-gerber-viewer at \`$commit\` (FORK_COMMIT).
**Generated; do not edit**: refresh with \`bash scripts/sync-fork.sh\`, then \`bash scripts/build-wasm.sh\`.

- \`core/\`: \`$pkg/{index.js, shared.js, index.d.ts}\`; shared.js's \`./drills.js\` import points at
  \`src/gerber/drills.js\`.
- \`core/wasm/\`: \`wasm_gerber_processor.js\` + \`_bg.wasm\` built from \`crate/\` by scripts/build-wasm.sh;
  BUILD.json records the source hash and toolchain (CI checks it).
- \`crate/\`: the fork's \`wasm/\` Rust crate (wasm_gerber_processor) and \`rust-toolchain.toml\`.
- \`odb/\`: \`js/src/odb\` (the ODB++ job loader) without the Node-only \`archive/zip-node.js\`, and \`js/core/config.js\` as
  \`odb/core-config.js\`; \`src/gerber/odb.js\` (\`loadOdbJob\`) wraps it.
README
rm -rf "$target"; mv "$new" "$target"
echo "synced fork $commit into ${target#$here/}"
( cd "$here" && bash scripts/build-wasm.sh --check ) || echo "the crate changed: run scripts/build-wasm.sh"
