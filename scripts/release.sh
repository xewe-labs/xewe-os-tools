#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Maxim Dokukin (maxdokukin.com)
# SPDX-License-Identifier: GPL-3.0-only
# xewe-os-tools/scripts/release.sh
#
# release.sh: test xewe-os-tools once, tag the commit with the notes and the release card in the tag
# message, then create the GitHub Release.
# Usage: scripts/release.sh <version> [--notes "line" ...] [--notes-file FILE] [--resume] [--ci-timeout <min>]
#
# One command, 6 stages. <version> is X.Y.Z or X.Y.Z-suffix (a pre-release), no `v`; the version
# lines (VERSION_LOCATIONS) get X.Y.Z. The notes are 1 to 3 lines: --notes once per line, and/or the
# lines of --notes-file. Everything the run writes is under build/release/<version>/ (gitignored).
#   1 preflight: refuses, and lists every problem, unless: on main; the work tree has no change at
#     all (untracked files included); main is level with origin/main after a fetch; the tag is free
#     here and on origin (--resume excepted); the notes pass `xewe release-notes check`; build/ is
#     ignored; gh is logged in; finds every Python of ci.yml's test matrix that is installed here
#     (PATH, else `uv python find`);
#   2 tests: the version lines set, then a card run (`xewe release-card init` on the logs folder),
#     every step recorded by `xewe release-card record`: one venv per Python with the tree
#     installed (`pip install .`) and the checkers, as ci.yml installs them; per Python pytest
#     (JUnit) as a unit run, pyflakes and mypy; then `xewe format --check`, `xewe brand-lint .` and
#     actionlint. A failing step does not stop the others; the card refuses it;
#   3 release card: `xewe release-card render` writes build/release/<version>/release_card.md from
#     those results, or refuses (the draft is in the logs folder);
#   4 commit: only the VERSION_LOCATIONS files are added, `release <version>`; refuses when any
#     other file changed;
#   5 tag: the message is the notes, one blank line, the card; `git tag -a --cleanup=verbatim` (git
#     would drop the card's `#` lines otherwise). Then `git push origin main`, a check that
#     origin/main equals HEAD, and `git push origin <version>`: main first, so that the workflow is
#     registered before the tag triggers it;
#   6 release: waits for ci.yml on the tag (--ci-timeout minutes, default 60); its card job compares
#     the tag's card with the run (the `xewe release-card verify --ci` step must have run and passed).
#     Then `gh release create` (--verify-tag, the notes as the text, build/release/<version>/
#     release_card.md as its only asset); prints the release URL and how consumers pick it up.
# A run that stops before stage 4 puts the version lines back. --resume: when the tag exists (here
# or on origin), pushes what is missing (main, then the tag) and runs stage 6 (a release that
# exists is only reported); when HEAD is the `release <version>` commit and there is no tag, reuses
# build/release/<version>/ if its card is there (else runs stage 2 again), then goes on from stage 3.
# Exit codes: 0 done, 1 a refusal or a failed check.
set -euo pipefail

TOOLS_REPO="xewe-labs/xewe-os-tools"
TOOLS_TITLE="xewe-os-tools"
CI_WORKFLOW="ci.yml"
CI_CARD_JOB="release card check"           # ci.yml job name
CI_CARD_STEP="xewe release-card verify --ci" # its step that compares the card with the run
CI_POLL="${RELEASE_CI_POLL:-15}"           # seconds between CI polls
VENVS="build/release-venv"                  # gitignored (/build/); one venv per Python, plus card
LINT_PYTHON="3.12"                          # ci.yml's format, brand-lint and card jobs use 3.12
CHECKERS=(pyflakes mypy types-pyserial)     # what ci.yml's test job installs next to the tree
LINTERS=("ruff==0.17.0" "clang-format==23.1.3" actionlint-py shellcheck-py) # format job pins
REQUIRE="setup,unit,check"

# Every place that states the tools' own version: "file|ERE|replacement", {V} = X.Y.Z. Each
# pattern must match at least one line; stage 2 rewrites the matches. ERE is portable (GNU and BSD sed -E); no `|` inside.
VERSION_LOCATIONS=(
  'pyproject.toml|^version = "[^"]*"$|version = "{V}"'
  'src/xewe/__init__.py|^__version__ = "[^"]*"$|__version__ = "{V}"'
  'src/xewe/env/pins.py|^DEFAULT_TOOLS_REF = "[^"]*"$|DEFAULT_TOOLS_REF = "{V}"'
  'doc/spec.md|__version__ = "[^"]*"|__version__ = "{V}"'
  'doc/spec.md|^version = "[^"]*"$|version = "{V}"'
  'doc/spec.md|^tools_version = "[^"]*"|tools_version = "{V}"'
  'doc/spec.md|^tools = [{] ref = "[^"]*"|tools = { ref = "{V}"'
  'doc/ci.md|(xewe-os-tools/[.]github/workflows/[a-z-]+[.]yml)@v?[0-9]+[.][0-9]+[.][0-9]+|\1@{V}'
  'README.md|^ref = "[^"]*"( *# the tools tag)|ref = "{V}"\1'
  'README.md|(xewe-os-tools/[.]github/workflows/[a-z-]+[.]yml)@v?[0-9]+[.][0-9]+[.][0-9]+|\1@{V}'
)

die() { echo "error: $*" >&2; exit 1; }
warn() { echo "warning: $*" >&2; }
STAGES=6
stage() { echo "== $1/${STAGES} $2"; }

# A failed preflight check: noted, and all of them are listed before the run stops.
PROBLEMS=()
fail() { PROBLEMS+=("$*"); }
check_problems() {
  [[ "${#PROBLEMS[@]}" -eq 0 ]] && return 0
  local p
  echo "error: the release cannot start:" >&2
  for p in "${PROBLEMS[@]}"; do echo "  - ${p}" >&2; done
  exit 1
}

# Prints a command and runs it.
run() {
  printf '+'; printf ' %q' "$@"; printf '\n'
  "$@"
}

# file_at REF FILE: FILE's content at commit REF, or in the working tree when REF is empty.
file_at() {
  if [[ -z "$1" ]]; then cat -- "$2"; else git show "$1:$2"; fi
}

# set_location LOCATION VERSION REF: print the file of LOCATION (at REF, see file_at) with the
# location set to VERSION; exit 1 when the pattern matches no line.
set_location() {
  local file pattern repl rest
  file="${1%%|*}"; rest="${1#*|}"
  pattern="${rest%%|*}"; repl="${rest#*|}"
  repl="${repl//\{V\}/$2}"
  # grep reads all input (no -q): an early exit would SIGPIPE git show and fail the pipeline.
  file_at "$3" "${file}" 2>/dev/null | grep -E -- "${pattern}" >/dev/null || return 1
  file_at "$3" "${file}" | sed -E "s|${pattern}|${repl}|g"
}

# stale_locations VERSION REF: print "file: line" for every location (at REF) that does not say
# VERSION yet, or whose pattern matches nothing.
stale_locations() {
  local loc file
  for loc in "${VERSION_LOCATIONS[@]}"; do
    file="${loc%%|*}"
    if ! set_location "${loc}" "$1" "$2" >/dev/null; then
      echo "${file}: no line matches $(cut -d'|' -f2 <<< "${loc}")"
    elif ! cmp -s <(file_at "$2" "${file}") <(set_location "${loc}" "$1" "$2"); then
      echo "${file}: $(file_at "$2" "${file}" | grep -Em1 -- "$(cut -d'|' -f2 <<< "${loc}")")"
    fi
  done
}

# version_files: every file named in VERSION_LOCATIONS, once.
version_files() {
  local loc
  for loc in "${VERSION_LOCATIONS[@]}"; do echo "${loc%%|*}"; done | awk '!seen[$0]++'
}

# tree_problems: one line per reason the work tree cannot be released from; none when it can.
tree_problems() {
  local p path unmerged dirty
  git symbolic-ref -q HEAD >/dev/null || echo "HEAD is detached: check out main"
  for p in MERGE_HEAD CHERRY_PICK_HEAD REVERT_HEAD REBASE_HEAD rebase-merge rebase-apply; do
    path="$(git rev-parse --git-path "${p}")"
    [[ -e "${path}" ]] && echo "a merge, rebase, cherry-pick or revert is in progress (${path} exists): finish or abort it first"
  done
  unmerged="$(git diff --name-only --diff-filter=U)"
  [[ -z "${unmerged}" ]] || echo "unmerged (conflicted) paths: $(tr '\n' ' ' <<< "${unmerged}")"
  dirty="$(git status --porcelain=v1 --untracked-files=all | cut -c4- | head -n 10 | tr '\n' ' ')"
  [[ -z "${dirty}" ]] || echo "the work tree is not clean (commit, stash or remove): ${dirty% }"
  return 0
}

# ci_pythons: the Python versions of ci.yml's test matrix, one per line.
ci_pythons() {
  sed -nE '/^ *python: *\[/{s/^ *python: *\[(.*)\] *$/\1/p;q;}' ".github/workflows/${CI_WORKFLOW}" \
    | tr -d '" ' | tr ',' '\n' | grep -E '^[0-9]+[.][0-9]+$' || true
}

# find_python X.Y: the path of an installed Python X.Y (PATH, else uv's), or nothing.
find_python() {
  local p=""
  p="$(command -v "python$1" 2>/dev/null)" || p=""
  if [[ -z "${p}" ]] && command -v uv >/dev/null 2>&1; then
    p="$(uv python find --system --no-project --no-config "$1" 2>/dev/null)" || p=""
  fi
  if [[ -n "${p}" && "$("${p}" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null)" == "$1" ]]; then
    echo "${p}"
  fi
}

tag_local() { git rev-parse -q --verify "refs/tags/$1" >/dev/null; }
tag_remote() { git ls-remote --exit-code --tags origin "refs/tags/$1" >/dev/null 2>&1; }

# tag_commit VERSION: the commit the tag points at (local tag first, else origin's peeled tag).
tag_commit() {
  if tag_local "$1"; then
    git rev-parse "refs/tags/$1^{commit}"
  else
    git ls-remote --tags origin \
      | awk -v t="refs/tags/$1" '$2 == t { c = $1 } $2 == t "^{}" { p = $1 } END { print (p != "" ? p : c) }'
  fi
}

# ci_run SHA REF: the newest run of CI_WORKFLOW for SHA on REF (the tag name for a tag push), as
# "id status url conclusion" (empty when there is none yet; conclusion is empty while the run is
# not completed).
ci_run() {
  gh run list --commit "$1" --workflow "${CI_WORKFLOW}" --branch "$2" --limit 20 \
    --json databaseId,status,url,conclusion \
    --jq '.[0] | select(. != null) | "\(.databaseId) \(.status) \(.url) \(.conclusion)"'
}

# card_step_result RUN_ID: the conclusion of the card job's verify step in that run (skipped when
# the job found no card).
card_step_result() {
  gh run view "$1" --json jobs \
    --jq ".jobs[] | select(.name == \"${CI_CARD_JOB}\") | .steps[] | select(.name == \"${CI_CARD_STEP}\") | .conclusion"
}

# ci_gate SHA REF: wait for CI_WORKFLOW on SHA and REF and fail unless it succeeds, within
# CI_TIMEOUT minutes. CI_RUN_ID is the run's id afterwards.
CI_RUN_ID=""
ci_gate() {
  local sha="$1" ref="$2" deadline=$((SECONDS + CI_TIMEOUT * 60)) line id status url conclusion pid
  CI_RUN_ID=""
  echo "   waiting for a ${CI_WORKFLOW} run on ${sha:0:12} (${ref}, timeout ${CI_TIMEOUT} min)"
  while :; do
    line="$(ci_run "${sha}" "${ref}")" || die "gh run list failed"
    [[ -n "${line}" ]] && break
    (( SECONDS < deadline )) || die "timed out after ${CI_TIMEOUT} min: no ${CI_WORKFLOW} run for ${sha:0:12} on ${ref}"
    sleep "${CI_POLL}"
  done
  read -r id status url conclusion <<< "${line}"
  echo "   run ${url} (${status})"
  if [[ "${status}" != completed ]]; then
    gh run watch "${id}" --exit-status --interval "${CI_POLL}" >/dev/null &
    pid=$!
    while kill -0 "${pid}" 2>/dev/null; do
      if (( SECONDS >= deadline )); then
        kill "${pid}" 2>/dev/null || true
        die "timed out after ${CI_TIMEOUT} min, CI still running: ${url} (re-run with --resume)"
      fi
      sleep 2
    done
    wait "${pid}" || die "CI failed: ${url}"
    conclusion=success
  fi
  [[ "${conclusion}" == success ]] || die "CI ${conclusion:-did not succeed}: ${url}"
  CI_RUN_ID="${id}"
  echo "   CI passed: ${url}"
}

# card_tools PYTHON: the venv with the tree's own tools that runs `xewe release-card` (not recorded: it
# makes the card; the recorded venvs are stage 2's). XEWE is the command prefix.
card_tools() {
  XEWE=("${VENVS}/card/bin/python" -m xewe)
  run "$1" -m venv --clear "${VENVS}/card"
  run "${VENVS}/card/bin/python" -m pip install -q --disable-pip-version-check --progress-bar off .
  # the tools commit the card header names (the tree's HEAD; the venv is not an editable install)
  git rev-parse HEAD > "${VENVS}/card/xewe-tools-commit"
}

# step LABEL RECORD_ARGS...: one recorded step; a failed command is noted and the run goes on (the
# card refuses it), a usage error stops.
FAILED_STEPS=()
step() {
  local label="$1" rc=0
  shift
  "${XEWE[@]}" release-card record --dir "${LOGS}" "$@" </dev/null || rc=$?
  case "${rc}" in
    0) ;;
    1|3) FAILED_STEPS+=("${label}"); warn "${label} failed (exit ${rc}; the log is in ${LOGS}); going on" ;;
    *) die "xewe release-card record exited ${rc} on ${label}" ;;
  esac
}

# write_notes: the notes lines into NOTES_FILE.
write_notes() {
  mkdir -p "${OUT}"
  if [[ "${#NOTES[@]}" -gt 0 ]]; then printf '%s\n' "${NOTES[@]}" > "${NOTES_FILE}"; else : > "${NOTES_FILE}"; fi
}

# notes_problems: one line per problem of the notes (none when `xewe release-notes check` accepts them).
notes_problems() {
  local out line
  if [[ "${#NOTES[@]}" -eq 0 ]]; then
    echo "no release notes: give 1 to 3 lines with --notes \"line\" (repeated) or --notes-file FILE"
    return 0
  fi
  out="$("${XEWE[@]}" release-notes check "${NOTES_FILE}" 2>&1)" || {
    while IFS= read -r line; do [[ -z "${line}" ]] || echo "release notes: ${line}"; done <<< "${out}"
  }
}

# restore_versions: put the version files back to HEAD when a run stops before the commit.
VERSIONS_WRITTEN=0
VFILES=()
restore_versions() {
  if [[ "${VERSIONS_WRITTEN}" == 1 ]]; then
    VERSIONS_WRITTEN=0
    git checkout -q -- "${VFILES[@]}" 2>/dev/null || true
    echo "   version lines put back" >&2
  fi
}

# tests VERSION: stage 2 (see the header).
tests() {
  local v="$1" loc file i venv junit
  stage 2 tests
  for loc in "${VERSION_LOCATIONS[@]}"; do
    file="${loc%%|*}"
    set_location "${loc}" "${BASE}" "" >/dev/null || die "${file}: no line matches $(cut -d'|' -f2 <<< "${loc}")"
    if ! cmp -s "${file}" <(set_location "${loc}" "${BASE}" ""); then
      [[ "${MODE}" == fresh ]] && VERSIONS_WRITTEN=1
      set_location "${loc}" "${BASE}" "" > "${file}.tmp"
      cat "${file}.tmp" > "${file}" # cat keeps the file's mode
      rm -f "${file}.tmp"
    fi
  done
  echo "   version lines say ${BASE}"
  # the card tools carry the new version
  card_tools "${PATHS[LINT_I]}"
  run "${XEWE[@]}" release-card init --dir "${LOGS}" --repo "${TOOLS_REPO}" --version "${v}" --tag "${v}"

  for i in "${!PYS[@]}"; do
    venv="${VENVS}/py${PYS[i]}"
    step "venv-py${PYS[i]}" --kind setup --name "venv-py${PYS[i]}" -- "${PATHS[i]}" -m venv --clear "${venv}"
    if [[ "${i}" == "${LINT_I}" ]]; then
      step "install-py${PYS[i]}" --kind setup --name "install-py${PYS[i]}" -- "${venv}/bin/python" -m pip install --disable-pip-version-check \
        --progress-bar off . "${CHECKERS[@]}" "${LINTERS[@]}"
    else
      step "install-py${PYS[i]}" --kind setup --name "install-py${PYS[i]}" -- "${venv}/bin/python" -m pip install --disable-pip-version-check \
        --progress-bar off . "${CHECKERS[@]}"
    fi
  done

  for i in "${!PYS[@]}"; do
    venv="${VENVS}/py${PYS[i]}"
    junit="${OUT}/junit-py${PYS[i]}.xml"
    step "pytest-py${PYS[i]}" --kind unit --name "py${PYS[i]}" --junit "${junit}" -- env XEWE_NO_BOARD=1 \
      "${venv}/bin/python" -m pytest -q --junitxml="${junit}" -o junit_family=xunit1
    step "pyflakes-py${PYS[i]}" --kind check --name "pyflakes-py${PYS[i]}" -- "${venv}/bin/python" -m pyflakes src tests
    step "mypy-py${PYS[i]}" --kind check --name "mypy-py${PYS[i]}" -- "${venv}/bin/python" -m mypy
  done
  venv="${VENVS}/py${PYS[LINT_I]}"
  step format --kind check --name format -- env "XEWE_CLANG_FORMAT=${venv}/bin/clang-format" \
    "${venv}/bin/python" -m xewe format --check
  step brand-lint --kind check --name brand-lint -- "${venv}/bin/python" -m xewe brand-lint .
  step actionlint --kind check --name actionlint -- "${venv}/bin/actionlint" -ignore xewe-board \
    -shellcheck "${venv}/bin/shellcheck"
  if [[ "${#FAILED_STEPS[@]}" -gt 0 ]]; then
    warn "failed steps: ${FAILED_STEPS[*]} (the card will refuse them)"
  fi
}

# card_stage: stage 3 (see the header).
card_stage() {
  local rc=0
  stage 3 "release card"
  run "${XEWE[@]}" release-card render --dir "${LOGS}" --notes "${NOTES_FILE}" --out "${CARD}" \
    --require "${REQUIRE}" --title "${TOOLS_TITLE}" || rc=$?
  case "${rc}" in
    0) ;;
    1) die "the card was refused (reasons above; draft: ${LOGS}/release_card.draft.md). Fix the cause, then run again" ;;
    *) die "xewe release-card render exited ${rc}" ;;
  esac
  [[ "$(head -n 1 "${CARD}")" == "# "* ]] || die "${CARD} does not start with a '# ' title line"
}

# commit_stage VERSION: stage 4 (see the header).
commit_stage() {
  local entry path stray=()
  stage 4 commit
  if [[ "${MODE}" == commit ]]; then
    echo "   HEAD is already the release ${1} commit"
    return 0
  fi
  while IFS= read -r -d '' entry; do
    path="${entry:3}"
    version_files | grep -xF -- "${path}" >/dev/null || stray+=("${path}")
  done < <(git status --porcelain=v1 -z --untracked-files=all)
  [[ "${#stray[@]}" -eq 0 ]] || die "files other than the version lines changed (${stray[*]}): nothing was committed"
  run git add -- "${VFILES[@]}"
  run git commit -q --allow-empty -m "release ${1}"
  VERSIONS_WRITTEN=0
}

# push_main: push main and check that origin/main is HEAD (main first: the workflow is registered
# before the tag triggers it).
push_main() {
  run git push origin main
  [[ "$(git rev-parse origin/main)" == "$(git rev-parse HEAD)" ]] \
    || die "origin/main ($(git rev-parse --short=12 origin/main)) is not HEAD ($(git rev-parse --short=12 HEAD)) after the push"
}

# tag_stage VERSION: stage 5 (see the header).
tag_stage() {
  stage 5 tag
  { cat "${NOTES_FILE}"; echo; cat "${CARD}"; } > "${MSG}"
  run git tag -a --cleanup=verbatim -F "${MSG}" "${1}"
  push_main
  run git push origin "refs/tags/${1}"
}

# release_stage VERSION TAGGED: stage 6 (see the header).
release_stage() {
  local v="$1" card
  stage 6 "release CI and GitHub Release"
  ci_gate "$2" "${v}"
  card="$(card_step_result "${CI_RUN_ID}")" || die "gh run view ${CI_RUN_ID} failed"
  [[ "${card}" == success ]] \
    || die "CI passed, but the '${CI_CARD_JOB}' job compared no card with this tag (step '${CI_CARD_STEP}': ${card:-not found})"
  echo "   card check: ${card}"
  if gh release view "${v}" >/dev/null 2>&1; then
    echo "   GitHub Release ${v} exists already"
  else
    run gh release create "${v}" --verify-tag --title "${TOOLS_TITLE} ${v}" ${PRE:+--prerelease} \
      --notes-file "${NOTES_FILE}" "${CARD}"
  fi
  echo "   GitHub Release: $(gh release view "${v}" --json url --jq .url)"
  echo "Projects: xewe.toml [tools] ref = \"${v}\" (or xewe manifest update), then ./setup.sh."
  echo "Workflow callers: uses: ${TOOLS_REPO}/.github/workflows/<workflow>.yml@${v}"
}

usage() { sed -n 's/^# Usage: //p' "${BASH_SOURCE[0]}"; }

main() {
  VERSION=""
  RESUME=0
  CI_TIMEOUT=60
  NOTES=()
  XEWE=()
  PYS=()
  PATHS=()
  LINT_I=0
  local line file x p i
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --resume) RESUME=1 ;;
      --notes)
        [[ $# -ge 2 ]] || die "--notes needs a line ($(usage))"
        NOTES+=("$2")
        shift
        ;;
      --notes-file)
        file="${2:-}"
        [[ -f "${file}" ]] || die "--notes-file needs a file: ${file}"
        while IFS= read -r line || [[ -n "${line}" ]]; do NOTES+=("${line}"); done < "${file}"
        shift
        ;;
      --ci-timeout)
        [[ "${2:-}" =~ ^[0-9]+$ ]] || die "--ci-timeout needs whole minutes ($(usage))"
        CI_TIMEOUT="$2"
        shift
        ;;
      -h|--help) usage; exit 0 ;;
      -*) die "unknown option $1 ($(usage))" ;;
      *)
        [[ -z "${VERSION}" ]] || die "one version only ($(usage))"
        VERSION="$1"
        ;;
    esac
    shift
  done
  [[ -n "${VERSION}" ]] || die "no version given ($(usage))"
  [[ "${VERSION}" =~ ^[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z][0-9A-Za-z.-]*)?$ ]] \
    || die "version must be X.Y.Z or X.Y.Z-suffix with no 'v' (the ecosystem's tags are un-prefixed): ${VERSION}"
  BASE="${VERSION%%-*}"
  while IFS= read -r line; do VFILES+=("${line}"); done < <(version_files)
  PRE=""
  [[ "${VERSION}" == "${BASE}" ]] || PRE=1

  # Repo root: the top of the git work tree this is run in.
  command -v git >/dev/null 2>&1 || die "git is required"
  ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" || die "not inside a git work tree"
  cd "${ROOT}"
  grep -qx 'name = "xewe-os-tools"' pyproject.toml 2>/dev/null || die "${ROOT} is not the xewe-os-tools repo"
  export XEWE_NO_BOARD=1 # the self-tests and the tools never touch a board here
  OUT="build/release/${VERSION}"
  LOGS="${OUT}/logs"
  NOTES_FILE="${OUT}/notes.txt"
  CARD="${OUT}/release_card.md"
  MSG="${OUT}/tag_message.txt"
  trap restore_versions EXIT
  trap 'exit 130' INT TERM

  stage 1 preflight
  local head tagged="" lines
  MODE=fresh
  head="$(git rev-parse HEAD)"
  if ! command -v gh >/dev/null 2>&1; then
    fail "gh is not installed (https://cli.github.com)"
  elif ! gh auth status >/dev/null 2>&1; then
    fail "gh is not logged in (gh auth login)"
  fi
  run git fetch --quiet origin || die "git fetch origin failed"
  if tag_local "${VERSION}" || tag_remote "${VERSION}"; then
    if [[ "${RESUME}" == 1 ]]; then MODE=tag; else fail "tag ${VERSION} exists (here or on origin); --resume continues it"; fi
  elif [[ "${RESUME}" == 1 ]]; then
    if [[ "$(git log -1 --format=%s)" == "release ${VERSION}" ]]; then
      MODE=commit
    else
      fail "--resume: there is no tag ${VERSION} and HEAD is not the 'release ${VERSION}' commit: run without --resume"
    fi
  fi

  if [[ "${MODE}" == tag ]]; then
    tagged="$(tag_commit "${VERSION}")"
    if ! git cat-file -e "${tagged}^{commit}" 2>/dev/null; then
      fail "tag ${VERSION} points at ${tagged:0:12}, which is not in this clone (git fetch --tags origin)"
    else
      lines="$(stale_locations "${BASE}" "${tagged}")"
      [[ -z "${lines}" ]] || fail "tag ${VERSION} points at ${tagged:0:12}, whose files do not say ${BASE}:"$'\n'"${lines}"
      # the notes and the card of the release are the tag's own
      mkdir -p "${OUT}"
      git for-each-ref --format='%(contents)' "refs/tags/${VERSION}" | sed '/-----BEGIN PGP SIGNATURE-----/,$d' > "${MSG}"
      awk '/^# /{exit} {print}' "${MSG}" | sed -e :a -e '/^\n*$/{$d;N;ba' -e '}' > "${NOTES_FILE}"
      sed -n '/^# /,$p' "${MSG}" > "${CARD}"
      [[ -s "${CARD}" ]] || fail "the message of tag ${VERSION} holds no release card (no line starting with '# ')"
    fi
    check_problems
    echo "   ok: resuming, tag ${VERSION} exists (${tagged:0:12})"
  else
    [[ "$(git symbolic-ref --quiet --short HEAD || echo '(detached)')" == main ]] \
      || fail "on branch $(git symbolic-ref --quiet --short HEAD || echo '(detached)'), not main"
    while IFS= read -r x; do [[ -z "${x}" ]] || fail "${x}"; done < <(tree_problems)
    if [[ "${MODE}" == commit ]]; then
      git merge-base --is-ancestor refs/remotes/origin/main HEAD 2>/dev/null \
        || fail "origin/main is not an ancestor of HEAD: pull first"
      lines="$(stale_locations "${BASE}" HEAD)"
      [[ -z "${lines}" ]] || fail "the release commit's files do not say ${BASE}:"$'\n'"${lines}"
    else
      x="$(git rev-parse -q --verify refs/remotes/origin/main || true)"
      if [[ -z "${x}" ]]; then
        fail "origin has no main branch"
      elif [[ "${head}" != "${x}" ]]; then
        fail "main (${head:0:12}) is not level with origin/main (${x:0:12}): pull or push first"
      fi
    fi
    git check-ignore -q "${LOGS}/run.json" || fail ".gitignore does not ignore build/ (the run folder must never be committed)"
    for x in $(ci_pythons); do
      p="$(find_python "${x}")"
      if [[ -n "${p}" ]]; then
        PYS+=("${x}"); PATHS+=("${p}")
        echo "   python ${x}: ${p}"
      else
        warn "python ${x} of the ${CI_WORKFLOW} matrix is not installed here (uv python install ${x}); no run for it"
      fi
    done
    if [[ "${#PYS[@]}" -eq 0 ]]; then
      fail "none of the ${CI_WORKFLOW} matrix Pythons ($(ci_pythons | tr '\n' ' ')) is installed here"
    else
      for i in "${!PYS[@]}"; do [[ "${PYS[i]}" == "${LINT_PYTHON}" ]] && LINT_I="${i}"; done
      echo "   checkers and card tools on python ${PYS[LINT_I]}"
      write_notes
      card_tools "${PATHS[LINT_I]}"
      while IFS= read -r x; do [[ -z "${x}" ]] || fail "${x}"; done < <(notes_problems)
    fi
    check_problems
    echo "   ok: ${VERSION} on ${head:0:12}"

    if [[ "${MODE}" == commit && -s "${CARD}" && -f "${LOGS}/run.json" ]]; then
      echo "== reusing the results under ${OUT}/ (stage 2 skipped)"
    else
      tests "${VERSION}"
    fi
    card_stage
    commit_stage "${VERSION}"
    tag_stage "${VERSION}"
    tagged="$(git rev-parse HEAD)"
  fi

  if [[ "${MODE}" == tag ]]; then
    echo "== resuming at stage 6: tag ${VERSION} exists (${tagged:0:12})"
    if ! tag_remote "${VERSION}"; then
      if ! git merge-base --is-ancestor "${tagged}" refs/remotes/origin/main 2>/dev/null; then
        if [[ "$(git symbolic-ref --quiet --short HEAD || true)" != main ]] || ! git merge-base --is-ancestor "${tagged}" HEAD; then
          die "origin/main lacks the tagged commit ${tagged:0:12} and this clone's main does not hold it: push main first"
        fi
        push_main
      fi
      run git push origin "refs/tags/${VERSION}"
    fi
  fi

  release_stage "${VERSION}" "${tagged}"
  echo "done in ${SECONDS} s"
}

# Sourcing the file (tests) defines the functions without running a release.
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
