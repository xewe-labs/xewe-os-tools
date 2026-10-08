#!/usr/bin/env bash
# bootstrap.sh: reference ./setup.sh for XeWe OS projects (copy it to the project root as setup.sh).
#
# Installs xewe-os-tools into build/.venv, then runs `xewe setup "$@"`. Everything else
# (arduino-cli, esp32 core, libraries, modules) is done by `xewe setup`.
#
# Tools source: $XEWE_TOOLS_SOURCE (a local xewe-os-tools directory) or the [tools] repo/ref of
# xewe.lock, cloned into build/xewe-os-tools.
set -euo pipefail

ROOT="${XEWE_PROJECT:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
BUILD="${ROOT}/build"
VENV="${BUILD}/.venv"
VPY="${VENV}/bin/python"

die() { echo "error: $*" >&2; exit 3; }

[[ -f "${ROOT}/xewe.lock" ]] || die "no xewe.lock in ${ROOT}"

PY=""
for cand in python3 python3.14 python3.13 python3.12 python3.11; do
  if command -v "${cand}" >/dev/null 2>&1 && "${cand}" -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
    PY="${cand}"
    break
  fi
done
[[ -n "${PY}" ]] || die "Python >= 3.11 is required (python3 --version)"

read -r TOOLS_REPO TOOLS_REF < <("${PY}" -c '
import sys, tomllib
t = tomllib.load(open(sys.argv[1], "rb")).get("tools", {})
print(t.get("repo", "https://github.com/xewe-labs/xewe-os-tools"), t.get("ref", "v0.1.1"))
' "${ROOT}/xewe.lock")

mkdir -p "${BUILD}"
if [[ -n "${XEWE_TOOLS_SOURCE:-}" ]]; then
  [[ -d "${XEWE_TOOLS_SOURCE}" ]] || die "XEWE_TOOLS_SOURCE is not a directory: ${XEWE_TOOLS_SOURCE}"
  SRC="$(cd "${XEWE_TOOLS_SOURCE}" && pwd)"
  LOCAL=1
else
  SRC="${BUILD}/xewe-os-tools"
  LOCAL=0
  if [[ ! -d "${SRC}" ]] || [[ "$(git -C "${SRC}" describe --tags --exact-match 2>/dev/null || true)" != "${TOOLS_REF}" ]]; then
    command -v git >/dev/null 2>&1 || die "git is required"
    rm -rf "${SRC}"
    GIT_TERMINAL_PROMPT=0 git clone --quiet --depth 1 --branch "${TOOLS_REF}" "${TOOLS_REPO}" "${SRC}" \
      || die "cannot clone ${TOOLS_REPO} at ${TOOLS_REF}"
  fi
fi

[[ -x "${VPY}" ]] || "${PY}" -m venv "${VENV}" || die "cannot create ${VENV}"
if ! "${VPY}" -m pip --version >/dev/null 2>&1; then
  "${VPY}" -m ensurepip --upgrade >/dev/null || die "pip is not available in ${VENV}"
fi

WANT="${TOOLS_REF#v}"
HAVE="$("${VPY}" -m xewe --version 2>/dev/null || true)"
if [[ ${LOCAL} -eq 1 || "${HAVE}" != *" ${WANT}" ]]; then
  "${VPY}" -m pip install --quiet --upgrade "${SRC}" || die "cannot install xewe-os-tools from ${SRC}"
fi

exec "${VPY}" -m xewe --project "${ROOT}" setup "$@"
