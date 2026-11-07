#!/usr/bin/env bash
# Playwright owns building, serving dist/, and stopping its server. Keeping
# one lifecycle prevents tests from silently using another process's build
# when the requested port is occupied.
# Set SPIKE_SKIP_BUILD=1 to reuse an existing dist/.
set -euo pipefail
cd "$(dirname "$0")/.."
exec npx playwright test "$@"
