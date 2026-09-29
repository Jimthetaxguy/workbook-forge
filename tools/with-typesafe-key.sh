#!/usr/bin/env bash
# tools/with-typesafe-key.sh: run a command with TYPESAFE_API_KEY set, without
# changing the directory the command runs in.
#
# The key is taken, in order, from:
#   1. TYPESAFE_API_KEY, if already set
#   2. a .env file in the current directory, then the file named by
#      TYPESAFE_ENV_FILE
#   3. Infisical, when TYPESAFE_INFISICAL_SECRET names the secret.
#      TYPESAFE_INFISICAL_DIR is the directory holding .infisical.json.
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

if [[ -z "${TYPESAFE_API_KEY:-}" && -n "${TYPESAFE_INFISICAL_SECRET:-}" ]]; then
  infisical_dir="${TYPESAFE_INFISICAL_DIR:-$CALLING_DIR}"
  if command -v infisical >/dev/null 2>&1 && [[ -d "$infisical_dir" ]]; then
    TYPESAFE_API_KEY="$(
      cd "$infisical_dir" && infisical secrets get "$TYPESAFE_INFISICAL_SECRET" --env=dev --plain --silent 2>/dev/null | tr -d '\r\n'
    )"
    export TYPESAFE_API_KEY
  fi
fi

if [[ -z "${TYPESAFE_API_KEY:-}" ]]; then
  echo "error: TYPESAFE_API_KEY unavailable from the environment, a .env file, or Infisical" >&2
  exit 1
fi

cd "$CALLING_DIR"

if [[ $# -eq 0 ]]; then
  echo "usage: $0 <command...>  e.g. $0 python3 tools/jev_critic.py --help" >&2
  exit 2
fi

exec "$@"
