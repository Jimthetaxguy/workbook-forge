#!/usr/bin/env bash
# tools/with-typesafe-key.sh — Directory-safe Infisical credential bridge.
# Maps Infisical TypeSafe_API_Key_2026-09-16 -> TYPESAFE_API_KEY while preserving $PWD.
set -euo pipefail

CALLING_DIR="$PWD"
CODE_ROOT="${CODE_ROOT:-$HOME/code}"

if [[ -z "${TYPESAFE_API_KEY:-}" ]]; then
  # 1. Check local .env files without changing directory
  for env_file in "$CALLING_DIR/.env" "$CODE_ROOT/tax-doc-classifier/.env"; do
    if [[ -f "$env_file" ]]; then
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

if [[ -z "${TYPESAFE_API_KEY:-}" ]]; then
  # 2. Resolve via Infisical in $CODE_ROOT where .infisical.json lives
  if command -v infisical >/dev/null 2>&1 && [[ -d "$CODE_ROOT" ]]; then
    TYPESAFE_API_KEY="$(
      cd "$CODE_ROOT" && infisical secrets get TypeSafe_API_Key_2026-09-16 --env=dev --plain --silent 2>/dev/null | tr -d '\r\n'
    )"
    export TYPESAFE_API_KEY
  fi
fi

if [[ -z "${TYPESAFE_API_KEY:-}" ]]; then
  echo "error: TYPESAFE_API_KEY unavailable from env, .env, or Infisical" >&2
  exit 1
fi

# Ensure execution stays in caller directory
cd "$CALLING_DIR"

if [[ $# -eq 0 ]]; then
  echo "usage: $0 <command...>  e.g. $0 python3 tools/jev_critic.py --help" >&2
  exit 2
fi

exec "$@"
