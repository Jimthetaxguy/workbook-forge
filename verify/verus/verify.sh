#!/usr/bin/env bash
# One command: toolchain pin, structural spec lock, text scan, then Verus.
set -euo pipefail
cd "$(dirname "$0")"
VERUS="${VERUS:-$(command -v verus || echo "$HOME/tools/verus/verus-x86-linux/verus")}"
if [ ! -x "$VERUS" ]; then
  echo "verus not found. See README.md, 'Install', or set VERUS=/path/to/verus"
  exit 2
fi
pin() { grep "^$1=" toolchain.lock | cut -d= -f2-; }
want_verus=$(pin VERUS_VERSION)
want_rustc=$(pin RUSTC_VERSION)
want_z3=$(pin Z3_VERSION)
have_verus=$("$VERUS" --version 2>&1 | sed -n 's/^ *Version: *//p')
have_rustc=$(rustc +"$(pin RUST_TOOLCHAIN)" --version 2>/dev/null || echo "missing")
z3_bin="$(dirname "$VERUS")/z3"
have_z3=$("$z3_bin" --version 2>/dev/null || echo "missing")
fail=0
[ "$have_verus" = "$want_verus" ] || { echo "toolchain: Verus is '$have_verus', toolchain.lock wants '$want_verus'"; fail=1; }
[ "$have_rustc" = "$want_rustc" ] || { echo "toolchain: rustc is '$have_rustc', toolchain.lock wants '$want_rustc'"; fail=1; }
[ "$have_z3" = "$want_z3" ] || { echo "toolchain: Z3 is '$have_z3', toolchain.lock wants '$want_z3'"; fail=1; }
if [ "$fail" -ne 0 ]; then
  echo "toolchain: MISMATCH, refusing to run. Install the pinned versions (README.md, Install)."
  exit 3
fi
echo "toolchain: OK (Verus $have_verus, $have_rustc, $have_z3)"
python3 spec_lock.py check
bash cheat-check.sh
"$VERUS" --crate-type=lib src/lib.rs
