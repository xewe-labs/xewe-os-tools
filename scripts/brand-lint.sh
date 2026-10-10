#!/usr/bin/env bash
# brand-lint.sh: fail on banned brand spellings (see xewe-os/NAMING.md). Usage: scripts/brand-lint.sh [DIR...]
# Scans tracked text files; skips build/, .venv/, static/, docs history. Exit 1 on any hit.
set -euo pipefail
dirs=("${@:-.}")
pattern='Xewe[A-Za-z]|\bxewe os\b|Xewe OS'
hits=$(grep -rInE --exclude-dir=.git --exclude-dir=build --exclude-dir=.venv --exclude-dir=static \
  --exclude-dir=__pycache__ --exclude-dir=history --exclude-dir=test_logs \
  --exclude=NAMING.md --exclude=brand-lint.sh "$pattern" "${dirs[@]}" || true)
if [[ -n "$hits" ]]; then
  echo "brand-lint: banned spellings found (see NAMING.md):" >&2
  echo "$hits" >&2
  exit 1
fi
echo "brand-lint: clean"
