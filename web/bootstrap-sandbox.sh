#!/usr/bin/env bash
# Toolchain for the browser spike on a fresh Debian/arm64 or amd64 box:
# Rust + wasm32 target, wasm-bindgen-cli pinned to rust/Cargo.lock, a C
# linker for build scripts, and a system Chromium for Playwright.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if ! command -v cargo >/dev/null 2>&1 && [ ! -x "$HOME/.cargo/bin/cargo" ]; then
  curl -sSf https://sh.rustup.rs | sh -s -- -y --no-modify-path --profile minimal \
    --default-toolchain stable -t wasm32-unknown-unknown
fi
export PATH="$HOME/.cargo/bin:$HOME/.local/bin:$PATH"
rustup target add wasm32-unknown-unknown >/dev/null
if ! command -v cc >/dev/null 2>&1 || ! command -v chromium >/dev/null 2>&1; then
  sudo apt-get update -q
  sudo apt-get install -y -q --no-install-recommends gcc libc6-dev chromium fonts-liberation
fi
pinned="$(grep -A1 '^name = "wasm-bindgen"$' "$ROOT/rust/Cargo.lock" | sed -n 's/^version = "\(.*\)"/\1/p')"
if [ "$(wasm-bindgen --version 2>/dev/null | awk '{print $2}')" != "$pinned" ]; then
  arch="$(uname -m)"
  tmp="$(mktemp -d)"
  curl -sSLf -o "$tmp/wb.tgz" \
    "https://github.com/wasm-bindgen/wasm-bindgen/releases/download/$pinned/wasm-bindgen-$pinned-$arch-unknown-linux-gnu.tar.gz"
  tar xzf "$tmp/wb.tgz" -C "$tmp"
  mkdir -p "$HOME/.local/bin"
  cp "$tmp"/wasm-bindgen-"$pinned"-*/wasm-bindgen* "$HOME/.local/bin/"
fi
echo "toolchain ready: $(cargo -V), $(wasm-bindgen --version), $(chromium --version)"
