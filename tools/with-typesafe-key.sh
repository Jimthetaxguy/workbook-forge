#!/usr/bin/env bash
# tools/with-typesafe-key.sh: run a command with TYPESAFE_API_KEY set, without
# changing the directory the command runs in.
#
# The key is taken, in order, from:
#   1. TYPESAFE_API_KEY, if already set
#   2. a .env file in the current directory, then the file named by
#      TYPESAFE_ENV_FILE
#   3. the output of TYPESAFE_KEY_COMMAND, a shell command that prints the
#      key, for whatever secret store holds it
set -euo pipefail

CALLING_DIR="$PWD"

if [[ -z "${TYPESAFE_API_KEY:-}" ]]; then
  for env_file in "$CALLING_DIR/.env" "${TYPESAFE_ENV_FILE:-}"; do
    if [[ -n "$env_file" && -f "$env_file" ]]; then
      set -a
      while IFS= read -r line || [[ -n "$line" ]]; do
        case "$line" in
          TYPESAFE_API_KEY=*) export "$line" ;;
        esac
      done < "$env_file"
      set +a
      break
    fi
  done
fi

if [[ -z "${TYPESAFE_API_KEY:-}" && -n "${TYPESAFE_KEY_COMMAND:-}" ]]; then
  TYPESAFE_API_KEY="$(bash -c "$TYPESAFE_KEY_COMMAND" 2>/dev/null | tr -d '\r\n')" || true
  export TYPESAFE_API_KEY
fi

if [[ -z "${TYPESAFE_API_KEY:-}" ]]; then
  echo "error: TYPESAFE_API_KEY unavailable from the environment, a .env file, or TYPESAFE_KEY_COMMAND" >&2
  exit 1
fi

cd "$CALLING_DIR"

if [[ $# -eq 0 ]]; then
  echo "usage: $0 <command...>  e.g. $0 python3 tools/jev_critic.py --help" >&2
  exit 2
fi

exec "$@"
