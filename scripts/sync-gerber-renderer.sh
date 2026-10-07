#!/bin/bash
# Refresh vendor/wasm-gerber-renderer/: a DEV/TEST copy of our fork of wasm-gerber-viewer's renderer
# (the fork's board.js renderFaceRaster, diff.js, drills.js, outline.js, ... are not on npm). boarddd
# itself never imports it: consumers inject their own copy. The examples and browser tests use this one.
#
#     bash scripts/sync-gerber-renderer.sh [FORK_CHECKOUT] [REF]
#
# Adapted from kipr's web/project/scripts/sync_vendored_renderer.bash. JS from the fork at REF (default:
# the pinned commit below, exported with `git archive`); the wasm from the npm release WASM_NPM_VERSION
# (the fork's main is upstream 0.7.0 plus JS-only modules) unless wasm-pack is installed.
set -euo pipefail
here=$(cd "$(dirname "$0")/.." && pwd)
fork=${1:-${WGV:-$here/../wasm-gerber-viewer}}
PINNED_REF=0acb469   # fork main: upstream 0.7.0 merged (PR #3)
ref=${2:-$PINNED_REF}
target="$here/vendor/wasm-gerber-renderer"
pkg=packages/wasm-gerber-renderer
commit=$(git -C "$fork" rev-parse --short "$ref^{commit}")
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
git -C "$fork" archive "$ref" "$pkg" wasm | tar -x -C "$tmp"
src="$tmp/$pkg"
wasm_npm_version=${WASM_NPM_VERSION:-0.7.0}
rm -rf "$target.new"; mkdir -p "$target.new/wasm"
cp "$src/LICENSE" "$target.new/"
for f in "$src"/*.js "$src"/*.d.ts; do
  case "$(basename "$f")" in node.js|node.d.ts) continue ;; esac
  cp "$f" "$target.new/"
done
if command -v wasm-pack >/dev/null 2>&1; then
  (cd "$tmp" && wasm-pack build wasm --target web --out-dir pkg --release)
  cp "$tmp/wasm/pkg/wasm_gerber_processor.js" "$tmp/wasm/pkg/wasm_gerber_processor_bg.wasm" "$target.new/wasm/"
  wasm_note="built with wasm-pack from fork commit $commit"
else
  (cd "$tmp" && npm pack --silent "wasm-gerber-renderer@$wasm_npm_version" >/dev/null && tar -xzf "wasm-gerber-renderer-$wasm_npm_version.tgz")
  cp "$tmp/package/wasm/wasm_gerber_processor.js" "$tmp/package/wasm/wasm_gerber_processor_bg.wasm" "$target.new/wasm/"
  wasm_note="the npm release wasm-gerber-renderer@$wasm_npm_version"
fi
cat > "$target.new/README.md" <<README
# wasm-gerber-renderer (our fork), dev/test copy

MIT (see LICENSE). Upstream https://github.com/dsafdsaf132/wasm-gerber-viewer, fork
https://github.com/CoolNamesAllTaken/wasm-gerber-viewer. **Generated; do not edit**: refresh with
\`bash scripts/sync-gerber-renderer.sh\`. Not part of the boarddd package (consumers inject their own).

- JavaScript: fork commit \`$commit\`, \`$pkg/*.js\` + \`*.d.ts\` minus the Node entry point.
- wasm: $wasm_note.
README
rm -rf "$target"; mv "$target.new" "$target"
echo "synced: JS $commit; wasm: $wasm_note"
