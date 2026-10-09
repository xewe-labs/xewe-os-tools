#!/usr/bin/env bash
# run.sh: reference ./run.sh for XeWe OS projects: build, flash, listen (`xewe run`).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="${ROOT}/build/tools/.venv/bin/python"
[[ -x "${PY}" ]] || { echo "error: run ./setup.sh first" >&2; exit 3; }
exec "${PY}" -m xewe --project "${ROOT}" run "$@"
