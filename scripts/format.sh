#!/usr/bin/env bash
# format.sh: reference ./format.sh for XeWe OS projects: `xewe format [--check] [PATH...]`
# (clang-format the C++ with the project's .clang-format, ruff-format the Python when installed).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="${ROOT}/build/tools/.venv/bin/python"
[[ -x "${PY}" ]] || { echo "error: run ./setup.sh first" >&2; exit 3; }
exec "${PY}" -m xewe --project "${ROOT}" format "$@"
