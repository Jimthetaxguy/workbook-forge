#!/usr/bin/env bash
# Plants four patches in a scratch copy and shows what each layer says.
# Expected: the three cheats fail the spec lock, the harmless hint edit passes.
set -uo pipefail
cd "$(dirname "$0")/.."
run_case() {
  local title="$1" file="$2" py="$3"
  local tmp; tmp=$(mktemp -d)
  cp -r src spec.lock spec_lock.py cheat-check.sh "$tmp/"
  python3 - "$tmp/src/$file" "$py" <<'PY'
import sys
p, code = sys.argv[1], sys.argv[2]
s = open(p).read()
old, new = code.split("|||")
assert old in s, "patch anchor not found"
open(p, "w").write(s.replace(old, new, 1))
PY
  echo "=== $title"
  echo "--- spec lock:"
  (cd "$tmp" && python3 spec_lock.py check); echo "(exit $?)"
  echo "--- grep layer:"
  (cd "$tmp" && bash cheat-check.sh); echo "(exit $?)"
  rm -rf "$tmp"
  echo
}
run_case "1. weakened ensures: parts only need to sum to AT MOST the total" allocate.rs \
  "sum(parts@) == total as int,|||sum(parts@) <= total as int,"
run_case "2. added assume inside a proof block" round_to_place.rs \
  "        vstd::arithmetic::div_mod::lemma_mod_decreases(magnitude as nat, place as nat);|||        vstd::arithmetic::div_mod::lemma_mod_decreases(magnitude as nat, place as nat);
        assume(false);"
run_case "3. added external_body on the allocator" allocate.rs \
  "pub fn allocate(|||#[verifier::external_body]
pub fn allocate("
run_case "4. allowed: an extra proof hint in an exec body (no contract change)" allocate.rs \
  "        parts.push(part);|||        assert(part == cumulative - given);
        parts.push(part);"
