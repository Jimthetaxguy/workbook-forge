#!/usr/bin/env bash
# Fails if a verified file uses an escape hatch that makes Verus skip a proof.
set -euo pipefail
cd "$(dirname "$0")"
pattern='assume\(|admit\(|#\[verifier::external_body\]|#\[verifier::exec_allows_no_decreases_clause\]|#\[verifier::external\]|#\[verifier\(external_body\)\]|assume_specification'
if grep -nE "$pattern" src/*.rs; then
  echo "cheat-check: FAIL (escape hatch found above)"
  exit 1
fi
echo "cheat-check: OK (no assume, admit, external_body or no-decreases escape hatches in src/)"
