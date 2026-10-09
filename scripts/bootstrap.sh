#!/usr/bin/env bash
# bootstrap.sh: reference ./setup.sh for XeWe OS projects (copy it to the project root as setup.sh).
#
# Installs xewe-os-tools into build/tools/.venv, then runs `xewe setup "$@"`. Everything else
# (arduino-cli, esp32 core, esptool and the modules repo once per machine in ~/.xewe-os/build-tools;
# libraries and the generated modules library in build/) is done by `xewe setup`.
#
# Tools source: $XEWE_TOOLS_SOURCE (a local xewe-os-tools directory) or the [tools] repo/ref of
# xewe.toml, cloned into build/tools. The ref may be a tag (cloned once), a branch (fetched and
# reset to the remote head on every run) or a commit SHA (checked out once).
set -euo pipefail

ROOT="${XEWE_PROJECT:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
BUILD="${ROOT}/build"
VENV="${BUILD}/tools/.venv"
VPY="${VENV}/bin/python"

die() { echo "error: $*" >&2; exit 3; }

[[ -f "${ROOT}/xewe.toml" ]] || die "no xewe.toml in ${ROOT}"

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
' "${ROOT}/xewe.toml")

mkdir -p "${BUILD}"
if [[ -n "${XEWE_TOOLS_SOURCE:-}" ]]; then
  [[ -d "${XEWE_TOOLS_SOURCE}" ]] || die "XEWE_TOOLS_SOURCE is not a directory: ${XEWE_TOOLS_SOURCE}"
  SRC="$(cd "${XEWE_TOOLS_SOURCE}" && pwd)"
  LOCAL=1
else
  SRC="${BUILD}/tools"
  LOCAL=0
  command -v git >/dev/null 2>&1 || die "git is required"
  export GIT_TERMINAL_PROMPT=0
  if [[ "${TOOLS_REF}" =~ ^[0-9a-f]{7,40}$ ]]; then
    # commit SHA: check it out once
    if [[ "$(git -C "${SRC}" rev-parse HEAD 2>/dev/null || true)" != "${TOOLS_REF}"* ]]; then
      rm -rf "${SRC}"
      { git init --quiet "${SRC}" && git -C "${SRC}" remote add origin "${TOOLS_REPO}" \
        && git -C "${SRC}" fetch --quiet --depth 1 origin "${TOOLS_REF}" \
        && git -C "${SRC}" checkout --quiet FETCH_HEAD; } || die "cannot fetch ${TOOLS_REPO} at ${TOOLS_REF}"
    fi
  elif [[ -d "${SRC}" && "$(git -C "${SRC}" describe --tags --exact-match 2>/dev/null || true)" == "${TOOLS_REF}" ]]; then
    : # tag: already checked out
  elif [[ -d "${SRC}" && "$(git -C "${SRC}" symbolic-ref --quiet --short HEAD 2>/dev/null || true)" == "${TOOLS_REF}" ]]; then
    # branch: follow the remote head on every run
    if git -C "${SRC}" fetch --quiet --depth 1 origin "${TOOLS_REF}"; then
      git -C "${SRC}" reset --quiet --hard "origin/${TOOLS_REF}" || die "cannot reset ${SRC} to origin/${TOOLS_REF}"
    else
      echo "warning: cannot fetch ${TOOLS_REF} from ${TOOLS_REPO}; keeping the current checkout" >&2
    fi
  else
    # first run or another ref: fresh clone (a tag or a branch)
    rm -rf "${SRC}"
    git -c advice.detachedHead=false clone --quiet --depth 1 --branch "${TOOLS_REF}" "${TOOLS_REPO}" "${SRC}" \
      || die "cannot clone ${TOOLS_REPO} at ${TOOLS_REF}"
  fi
  # the venv lives inside the checkout: keep it out of that checkout's `git status`
  if [[ -d "${SRC}/.git/info" ]] && ! grep -qx '.venv/' "${SRC}/.git/info/exclude" 2>/dev/null; then
    echo '.venv/' >> "${SRC}/.git/info/exclude"
  fi
fi

[[ -x "${VPY}" ]] || "${PY}" -m venv "${VENV}" || die "cannot create ${VENV}"
if ! "${VPY}" -m pip --version >/dev/null 2>&1; then
  "${VPY}" -m ensurepip --upgrade >/dev/null || die "pip is not available in ${VENV}"
fi

# reinstall for a local source, or when the checkout's commit is not the installed one (a branch
# moves without a version bump)
MARK="${VENV}/xewe-tools-commit"
COMMIT="$(git -C "${SRC}" rev-parse HEAD 2>/dev/null || echo -)"
HAVE="$("${VPY}" -m xewe --version 2>/dev/null || true)"
if [[ ${LOCAL} -eq 1 || -z "${HAVE}" || ! -f "${MARK}" || "$(cat "${MARK}")" != "${COMMIT}" ]]; then
  "${VPY}" -m pip install --quiet --upgrade "${SRC}" || die "cannot install xewe-os-tools from ${SRC}"
  echo "${COMMIT}" > "${MARK}"
fi

exec "${VPY}" -m xewe --project "${ROOT}" setup "$@"
