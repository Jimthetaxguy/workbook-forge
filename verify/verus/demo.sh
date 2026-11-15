#!/usr/bin/env bash
# Shows the naive versions failing. Exit code 0 means "they failed, as expected".
set -uo pipefail
cd "$(dirname "$0")"
VERUS="${VERUS:-$(command -v verus || echo "$HOME/tools/verus/verus-x86-linux/verus")}"
if "$VERUS" --crate-type=lib demo/naive.rs; then
  echo "demo: UNEXPECTED, the naive code verified"
  exit 1
fi
echo "demo: naive versions rejected by Verus, as expected"
