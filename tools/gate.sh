#!/usr/bin/env bash
# The single development gate. It runs tools/verify_toolkit.sh and adds the
# checks that script leaves out:
#   - the native bridge is rebuilt, so Rust changes are tested through a
#     current binary instead of one left over from an earlier branch
#   - the interpreter is the checkout's own virtual environment
#   - the Rust examples that the Python tests launch are built first, so a
#     cold build cannot hit a test's 180 second limit
#   - a skipped test fails the gate unless tools/gate-allowed-skips.txt names it
#   - whitespace is checked against HEAD, which covers staged changes too
#   - the whole run has a time limit
#   - the log is stored under the commit it tested, and under a different
#     name when the tree had uncommitted changes
#
# Usage: bash tools/gate.sh
# Environment:
#   WORKBOOK_PYTHON      interpreter to use (default: .venv/bin/python)
#   GATE_TIMEOUT_SECONDS overall limit (default: 3600)
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$PWD"
PY="${WORKBOOK_PYTHON:-$ROOT/.venv/bin/python}"
LIMIT="${GATE_TIMEOUT_SECONDS:-3600}"

if [ ! -x "$PY" ]; then
  echo "gate: interpreter not found at $PY; create the virtual environment first" >&2
  exit 2
fi

if [ -z "${GATE_INNER:-}" ]; then
  commit="$(git rev-parse HEAD)"
  mkdir -p .verification/gates
  if [ -n "$(git status --porcelain)" ]; then
    # What was tested is not what the commit holds, so the log must not be
    # filed as that commit's.
    log=".verification/gates/$commit-with-uncommitted-changes.log"
  else
    log=".verification/gates/$commit.log"
  fi
  # The limit is enforced from Python so the gate does not depend on a
  # `timeout` binary, which macOS does not ship.
  GATE_INNER=1 GATE_LOG="$log" "$PY" - "$LIMIT" "$0" <<'PYTHON'
import os
import signal
import subprocess
import sys

limit, script = int(sys.argv[1]), sys.argv[2]
with open(os.environ["GATE_LOG"], "w", encoding="utf-8") as log:
    process = subprocess.Popen(
        ["bash", script], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, start_new_session=True,
    )

    def expire(_signal, _frame):
        os.killpg(process.pid, signal.SIGKILL)
        message = f"GATE FAIL: exceeded {limit} seconds\n"
        sys.stdout.write(message)
        log.write(message)
        log.flush()
        os._exit(124)

    signal.signal(signal.SIGALRM, expire)
    signal.alarm(limit)
    for line in process.stdout:
        sys.stdout.write(line)
        log.write(line)
    sys.exit(process.wait())
PYTHON
  exit $?
fi

commit="$(git rev-parse HEAD)"
if [ -n "$(git status --porcelain)" ]; then
  tree_state="with uncommitted changes"
else
  tree_state="clean"
fi
echo "gate: commit $commit ($tree_state)"
echo "gate: interpreter $("$PY" --version 2>&1)"

echo "gate: rebuilding the native bridge"
export WORKBOOK_FORGE_BUILD_NATIVE=1
if command -v uv >/dev/null 2>&1; then
  CARGO_TARGET_DIR="$ROOT/native/target" \
    uv pip install --quiet --python "$PY" --no-build-isolation --reinstall-package workbook_forge -e .
else
  CARGO_TARGET_DIR="$ROOT/native/target" \
    "$PY" -m pip install --quiet --no-build-isolation --force-reinstall --no-deps -e .
fi
unset WORKBOOK_FORGE_BUILD_NATIVE
bridge="$(find python/workbook_forge -maxdepth 1 -name '_native*' | sort | head -1)"
if [ -z "$bridge" ]; then
  echo "GATE FAIL: the native bridge was not produced"
  exit 1
fi
echo "gate: bridge $(shasum -a 256 "$bridge")"

echo "gate: building the Rust examples the Python tests launch"
build_examples() {
  local target="$1"
  shift
  local examples=()
  local name
  for name in "$@"; do
    examples+=(--example "$name")
  done
  CARGO_TARGET_DIR="$ROOT/$target" \
    cargo build --quiet --manifest-path rust/Cargo.toml --locked --offline "${examples[@]}"
}
build_examples .verification/agent-contract-target agent_workbook
build_examples .verification/primitives-contract-target primitive_contract
build_examples .verification/xml-contract-target extract_xlsx agent_workbook
rust_examples=(xlsx_scenario)
if [ -f rust/examples/canonical_calc.rs ]; then
  rust_examples+=(canonical_calc)
fi
build_examples rust/target "${rust_examples[@]}"

echo "gate: checking for skipped tests"
skip_report="$(mktemp)"
trap 'rm -f "$skip_report"' EXIT
"$PY" -m pytest -q -rs -p no:cacheprovider >"$skip_report" 2>&1 || {
  cat "$skip_report"
  echo "GATE FAIL: pytest"
  exit 1
}
tail -1 "$skip_report"
allowed="tools/gate-allowed-skips.txt"
# grep exits 1 when nothing was skipped, which is the normal case.
unexpected="$({ grep -E '^SKIPPED ' "$skip_report" || true; } | while IFS= read -r line; do
  location="$(printf '%s\n' "$line" | sed -E 's/^SKIPPED \[[0-9]+\] ([^:]+):[0-9]+: .*/\1/')"
  if [ ! -f "$allowed" ] || ! grep -v '^#' "$allowed" | grep -qxF "$location"; then
    printf '%s\n' "$line"
  fi
done)"
if [ -n "$unexpected" ]; then
  printf '%s\n' "$unexpected"
  echo "GATE FAIL: tests were skipped and $allowed does not name their files"
  exit 1
fi

echo "gate: running tools/verify_toolkit.sh"
# The suite already ran above with the skip report; running it twice adds
# time and no evidence.
WORKBOOK_PYTHON="$PY" WORKBOOK_PYTEST_DONE=1 bash tools/verify_toolkit.sh

echo "gate: checking whitespace against HEAD"
git diff --check HEAD

echo "GATE PASS: commit $commit ($tree_state)"
