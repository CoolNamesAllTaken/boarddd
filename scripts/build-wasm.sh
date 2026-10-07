#!/bin/bash
# Build the renderer's wasm from third_party/wasm-gerber-renderer/crate/ into core/wasm/ (glue .js + _bg.wasm)
# and write core/wasm/BUILD.json (source hash, fork commit, toolchain, output hashes).
#
#     bash scripts/build-wasm.sh           build (Rust from crate/rust-toolchain.toml via rustup, wasm-pack pinned)
#     bash scripts/build-wasm.sh --check   no build: BUILD.json matches the crate sources and the committed outputs
#
# The check compares source hashes, not wasm bytes: builds are not byte-reproducible across machines.
set -euo pipefail
here=$(cd "$(dirname "$0")/.." && pwd)
tp="$here/third_party/wasm-gerber-renderer"
crate="$tp/crate"
out="$tp/core/wasm"
WASM_PACK_VERSION=0.14.0
export CARGO_HOME="${CARGO_HOME:-$HOME/.cargo}" PATH="${CARGO_HOME:-$HOME/.cargo}/bin:$PATH"

source_hash() {
  ( cd "$crate"
    find . -type f -not -path './target/*' -print0 | LC_ALL=C sort -z | xargs -0 sha256sum
    echo "wasm-pack $WASM_PACK_VERSION" ) | sha256sum | cut -d' ' -f1
}
file_hash() { sha256sum "$1" | cut -d' ' -f1; }

if [ "${1:-}" = "--check" ]; then
  [ -f "$out/BUILD.json" ] || { echo "no $out/BUILD.json: run scripts/build-wasm.sh" >&2; exit 1; }
  SOURCE=$(source_hash) FORK=$(cat "$tp/FORK_COMMIT") GLUE=$(file_hash "$out/wasm_gerber_processor.js") \
  WASM=$(file_hash "$out/wasm_gerber_processor_bg.wasm") node -e '
    const b = JSON.parse(require("fs").readFileSync(process.argv[1], "utf8"));
    const bad = [["source_sha256", process.env.SOURCE], ["fork_commit", process.env.FORK],
      ["glue_sha256", process.env.GLUE], ["wasm_sha256", process.env.WASM]].filter(([k, v]) => b[k] !== v);
    for (const [k, v] of bad) console.error(`BUILD.json ${k} ${b[k]} != ${v}`);
    if (bad.length) { console.error("the committed wasm is stale: run scripts/build-wasm.sh and commit core/wasm/"); process.exit(1); }
    console.log(`wasm up to date: source ${b.source_sha256.slice(0, 12)}, fork ${b.fork_commit.slice(0, 7)}, ${b.rustc}`);
  ' "$out/BUILD.json"
  exit 0
fi

cd "$crate"
rustup show active-toolchain >/dev/null 2>&1 || rustup toolchain install   # reads rust-toolchain.toml
if [ "$(wasm-pack --version 2>/dev/null)" != "wasm-pack $WASM_PACK_VERSION" ]; then
  case "$(uname -m)" in x86_64|amd64) arch=x86_64 ;; aarch64|arm64) arch=aarch64 ;; *) echo "no prebuilt wasm-pack for $(uname -m)" >&2; exit 1 ;; esac
  name="wasm-pack-v$WASM_PACK_VERSION-$arch-unknown-linux-musl"
  dl=$(mktemp -d)
  curl --proto '=https' --tlsv1.2 -fsSL --retry 3 "https://github.com/wasm-bindgen/wasm-pack/releases/download/v$WASM_PACK_VERSION/$name.tar.gz" | tar -xz -C "$dl"
  mkdir -p "$CARGO_HOME/bin"; install -m 0755 "$dl/$name/wasm-pack" "$CARGO_HOME/bin/wasm-pack"; rm -rf "$dl"
fi
pkg=$(mktemp -d); trap 'rm -rf "$pkg"' EXIT
wasm-pack build --target web --out-dir "$pkg" --release --no-pack
mkdir -p "$out"
cp "$pkg/wasm_gerber_processor.js" "$pkg/wasm_gerber_processor_bg.wasm" "$out/"
SOURCE=$(source_hash) FORK=$(cat "$tp/FORK_COMMIT") GLUE=$(file_hash "$out/wasm_gerber_processor.js") \
WASM=$(file_hash "$out/wasm_gerber_processor_bg.wasm") RUSTC=$(rustc --version) WASM_PACK=$(wasm-pack --version) \
BINDGEN=$(awk '/^name = "wasm-bindgen"$/ { getline; gsub(/[^0-9.]/, ""); print; exit }' Cargo.lock) node -e '
  const e = process.env;
  const build = { source_sha256: e.SOURCE, fork_commit: e.FORK, rustc: e.RUSTC, wasm_pack: e.WASM_PACK,
    wasm_bindgen: e.BINDGEN, glue_sha256: e.GLUE, wasm_sha256: e.WASM };
  require("fs").writeFileSync(process.argv[1], JSON.stringify(build, null, 2) + "\n");
' "$out/BUILD.json"
cat "$out/BUILD.json"
