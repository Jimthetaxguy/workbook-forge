#!/usr/bin/env bash
# tools/with-typesafe-key.sh: run a command with TYPESAFE_API_KEY set, without
# changing the directory the command runs in.
#
# The key is found by tools/jev_critic.py, so both tools follow one rule. In
# order: TYPESAFE_API_KEY; .env in the current directory; .env in the
# repository root; the file named by TYPESAFE_ENV_FILE; the one line printed
# by the shell command in TYPESAFE_KEY_COMMAND.
#
# The exit code is the command's own. When the command cannot be run it is
# 125 (no key, or no command given), 126 (the command cannot be run) or
# 127 (no such command).
set -euo pipefail

if [[ $# -eq 0 ]]; then
  echo "usage: $0 <command...>  e.g. $0 python3 tools/jev_critic.py --help" >&2
  exit 125
fi

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "${WORKBOOK_PYTHON:-python3}" "$here/jev_critic.py" run -- "$@"
