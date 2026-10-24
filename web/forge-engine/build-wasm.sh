#!/usr/bin/env bash
# Build the Forge WASM facade (rust/src/wasm.rs) into web/forge-engine/pkg.
# Needs the wasm32-unknown-unknown target and wasm-bindgen-cli matching the
# wasm-bindgen version pinned in rust/Cargo.lock.
set -euo pipefail
cd "$(dirname "$0")"
HERE="$PWD"
ROOT="$(cd ../.. && pwd)"
TARGET="${CARGO_TARGET_DIR:-$ROOT/rust/target}"
CARGO_TARGET_DIR="$TARGET" cargo build --quiet --locked --release \
  --manifest-path "$ROOT/rust/Cargo.toml" --target wasm32-unknown-unknown --lib
pinned="$(grep -A1 '^name = "wasm-bindgen"$' "$ROOT/rust/Cargo.lock" | sed -n 's/^version = "\(.*\)"/\1/p')"
have="$(wasm-bindgen --version | awk '{print $2}')"
if [ "$pinned" != "$have" ]; then
  echo "wasm-bindgen-cli $have does not match Cargo.lock $pinned" >&2
  exit 1
fi
rm -rf "$HERE/pkg"
wasm-bindgen --target web --out-dir "$HERE/pkg" --out-name workbook_forge \
  "$TARGET/wasm32-unknown-unknown/release/workbook_forge.wasm"
ls -l "$HERE/pkg"
