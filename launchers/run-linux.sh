#!/usr/bin/env bash
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
python_bin="${PYTHON:-python3}"
if ! command -v "$python_bin" >/dev/null 2>&1; then
  echo "python3 not found; install python3 (>=3.10) first" >&2
  exit 1
fi
venv="$here/.venv"
if [ ! -x "$venv/bin/python" ]; then
  "$python_bin" -m venv "$venv"
fi
"$venv/bin/python" -m pip install --quiet --upgrade pip
"$venv/bin/python" -m pip install --quiet -e "$here"
exec "$venv/bin/python" -m gai_pro serve "$@"
