#!/usr/bin/env bash
# Build the spike, serve dist/ statically, run the Playwright suite, stop the
# server. A static server keeps the run light (no dev-server optimizer) and
# exercises the same bundle that is measured for size.
# Set SPIKE_SKIP_BUILD=1 to reuse an existing dist/.
set -euo pipefail
cd "$(dirname "$0")/.."
PORT="${SPIKE_PORT:-4179}"
if [ -z "${SPIKE_SKIP_BUILD:-}" ]; then
  npx vite build --logLevel error
fi
python3 -m http.server "$PORT" --bind 127.0.0.1 --directory dist >/tmp/univer-spike-http.log 2>&1 &
SERVER_PID=$!
trap 'kill $SERVER_PID 2>/dev/null || true' EXIT
for _ in $(seq 1 40); do
  if node -e "fetch('http://127.0.0.1:$PORT/').then(r=>process.exit(r.ok?0:1),()=>process.exit(1))"; then break; fi
  sleep 0.25
done
SPIKE_EXTERNAL_SERVER=1 npx playwright test "$@"
