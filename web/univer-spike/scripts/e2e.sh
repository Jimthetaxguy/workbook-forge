#!/usr/bin/env bash
# Start Vite, run the Playwright suite against it, stop Vite.
set -euo pipefail
cd "$(dirname "$0")/.."
PORT="${SPIKE_PORT:-4179}"
npx vite --port "$PORT" --strictPort --host 127.0.0.1 >/tmp/univer-spike-vite.log 2>&1 &
VITE_PID=$!
trap 'kill $VITE_PID 2>/dev/null || true' EXIT
for _ in $(seq 1 60); do
  if node -e "fetch('http://127.0.0.1:$PORT/').then(r=>process.exit(r.ok?0:1),()=>process.exit(1))"; then break; fi
  sleep 0.5
done
SPIKE_EXTERNAL_SERVER=1 npx playwright test "$@"
