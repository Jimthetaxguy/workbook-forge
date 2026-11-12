#!/usr/bin/env bash
# Second layer: plain text scan for escape hatches. The structural check is spec_lock.py.
set -euo pipefail
cd "$(dirname "$0")"
pattern='assume\(|admit\(|axiom|#\[verifier::external_body\]|#\[verifier::external\]|#\[verifier\(external|external_fn_specification|assume_specification|exec_allows_no_decreases_clause'
if grep -nE "$pattern" src/*.rs; then
  echo "cheat-check: FAIL (escape hatch found above)"
  exit 1
fi
echo "cheat-check: OK (no assume, admit, axiom, external_body, external specs or no-decreases escape hatches in src/)"
