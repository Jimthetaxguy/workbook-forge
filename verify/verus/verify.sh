#!/usr/bin/env bash
# One command: cheat check, then Verus on every proof in src/.
set -euo pipefail
cd "$(dirname "$0")"
VERUS="${VERUS:-$(command -v verus || echo "$HOME/tools/verus/verus-x86-linux/verus")}"
if [ ! -x "$VERUS" ]; then
  echo "verus not found. See README.md, 'Install', or set VERUS=/path/to/verus"
  exit 2
fi
bash cheat-check.sh
"$VERUS" --crate-type=lib src/lib.rs
