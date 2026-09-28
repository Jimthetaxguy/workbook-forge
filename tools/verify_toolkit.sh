#!/usr/bin/env bash
# Run from a checkout with the package installed using pip install -e '.[test]'.
set -euo pipefail
cd "$(dirname "$0")/.."
WORKBOOK_PYTHON="${WORKBOOK_PYTHON:-python3.13}"
"$WORKBOOK_PYTHON" -c 'from workbook_forge import _native; assert _native.Session'
"$WORKBOOK_PYTHON" -m pytest -q
"$WORKBOOK_PYTHON" -m compileall -q python tools examples
cargo fmt --manifest-path rust/Cargo.toml --check
CARGO_TARGET_DIR="$PWD/rust/target" cargo check --manifest-path rust/Cargo.toml --locked
CARGO_TARGET_DIR="$PWD/rust/target" cargo test --manifest-path rust/Cargo.toml --locked
CARGO_TARGET_DIR="$PWD/rust/target" cargo clippy --manifest-path rust/Cargo.toml --all-targets --locked -- -D warnings
cargo fmt --manifest-path native/Cargo.toml --check
CARGO_TARGET_DIR="$PWD/native/target" cargo check --manifest-path native/Cargo.toml --locked
CARGO_TARGET_DIR="$PWD/native/target" cargo clippy --manifest-path native/Cargo.toml --all-targets --locked -- -D warnings
git diff --check
