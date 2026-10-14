#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Maxim Dokukin (maxdokukin.com)
# SPDX-License-Identifier: GPL-3.0-only
# xewe-os-tools/scripts/release.sh
#
# release.sh: test, card, tag and publish xewe-os-tools <version>.
# Usage: scripts/release.sh --prepare <version> [--resume] [--dry-run] | <version> [--dry-run] [--ci-timeout <min>] [--skip-ci]
# A release is two runs with a commit in between.
# `scripts/release.sh --prepare <version>` (7 stages) makes no git change:
#   1 preflight: refuses a detached HEAD, a merge/rebase/cherry-pick/revert in progress, unmerged
#     paths, and any change outside the version lines of VERSION_LOCATIONS and releases/<version>/;
#     finds every Python of ci.yml's test matrix that is installed here (PATH, else `uv python find`);
#   2 versions: sets every version location to <version>;
#   3 release notes: releases/<version>/notes.txt; when it is missing a skeleton is written, and
#     while the skeleton marker is in it the run stops with exit 2 (fill it, run again);
#   4 card run: a venv with the tree's tools (build/release-venv/card), then `xewe release-card init`
#     on releases/<version>/logs/ (gitignored);
#   5 setup (recorded): one venv per Python with the tree installed (`pip install .`) and the
#     checkers, as ci.yml installs them;
#   6 checks (recorded): per Python pytest (JUnit) as a unit run, pyflakes and mypy; then
#     `xewe format --check`, `xewe brand-lint .` and actionlint; a failing step does not stop the
#     others;
#   7 card: `xewe release-card render` writes releases/<version>/release_card.md (or refuses: the
#     draft is in the logs folder); prints the commit command.
# --resume (after a failed step): keeps the run when commit and tree are unchanged and re-runs only
# the steps that are not green. Commit the listed files as "prepare <version>" and push; ci.yml then
# checks the card against its own pytest run (the `card` job).
# `scripts/release.sh <version>` (5 stages), the tag run:
#   1 preflight: version X.Y.Z without `v`; on main, clean, level with origin/main after a fetch;
#     tag free locally and on origin; notes.txt (no skeleton marker) and release_card.md committed
#     in releases/<version>/; every version location says <version>; gh installed and logged in;
#   2 release card: `xewe release-card verify` (the card is as rendered, its tested tree is HEAD's
#     clean tree, the local logs it lists are present with their sha256, no testcard.md);
#   3 CI gate on HEAD: the newest ci.yml run for HEAD on main must succeed, with the card job's
#     verify step run and green (waits; --ci-timeout minutes, default 30);
#   4 annotated tag <version> on HEAD, push it, `gh release create` (--verify-tag, notes from
#     notes.txt, release_card.md the only asset);
#   5 print how consumers pick it up.
# Re-running the tag run resumes: a tag without a GitHub Release only creates the release (pushing
# the tag first if origin lacks it) from the tagged commit's notes.txt and release_card.md, provided
# its files say <version>; branch, clean tree, origin level, card verify and CI are not checked then.
# Tag and release both present: already released. --skip-ci skips the CI gate (emergencies only).
# --dry-run prints every command and changes nothing: only read-only git, gh, Python lookups and
# `release-card verify` run; it reports every failed check, not only the first, never waits, and
# exits 1 if a check failed.
# Exit codes: 0 done, 1 a refusal or a failed check, 2 releases/<version>/notes.txt to fill.
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
NOTES_MARKER="XEWE-NOTES-SKELETON"

# Every place that states the tools' own version: "file|ERE|replacement", {V} = the version. Each
# pattern must match at least one line; --prepare rewrites the matches and the preflight fails when
# that rewrite would change anything. ERE is portable (GNU and BSD sed -E); no `|` inside.
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
STAGES=5
stage() { echo "== $1/${STAGES} $2"; }

# A failed preflight check: fatal in a real run; a dry run notes it and goes on.
FAILED=0
fail() {
  [[ "${DRY_RUN}" == 1 ]] || die "$*"
  echo "error: $*" >&2
  FAILED=$((FAILED + 1))
}

# Runs a command, or only prints it with --dry-run.
run() {
  if [[ "${DRY_RUN}" == 1 ]]; then
    printf '+'; printf ' %q' "$@"; printf '\n'
  else
    "$@"
  fi
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

# versioned FILE VERSION REF: FILE at REF with every one of its locations set to VERSION.
versioned() {
  local loc rest pattern repl args=()
  for loc in "${VERSION_LOCATIONS[@]}"; do
    [[ "${loc%%|*}" == "$1" ]] || continue
    rest="${loc#*|}"; pattern="${rest%%|*}"; repl="${rest#*|}"
    args+=(-e "s|${pattern}|${repl//\{V\}/$2}|g")
  done
  file_at "$3" "$1" | sed -E "${args[@]}"
}

# version_files: every file named in VERSION_LOCATIONS, once.
version_files() {
  local loc
  for loc in "${VERSION_LOCATIONS[@]}"; do echo "${loc%%|*}"; done | awk '!seen[$0]++'
}

# stale_locations VERSION REF: print "file: pattern" for every location (at REF) that does not say
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

# allowed_change PATH VERSION: true for a path --prepare may find changed: anything under
# releases/VERSION/, or a version file whose only change from HEAD is its version lines.
allowed_change() {
  [[ "$1" == "releases/$2/"* ]] && return 0
  version_files | grep -xF -- "$1" >/dev/null || return 1 # no -q: an early exit would SIGPIPE awk
  [[ -f "$1" ]] && git cat-file -e "HEAD:$1" 2>/dev/null && cmp -s "$1" <(versioned "$1" "$2" HEAD)
}

# tree_problems VERSION: one line per reason the work tree cannot be prepared; none when it can.
tree_problems() {
  local v="$1" p path entry code unmerged dirty=()
  git symbolic-ref -q HEAD >/dev/null || echo "HEAD is detached: check out the branch to release from"
  for p in MERGE_HEAD CHERRY_PICK_HEAD REVERT_HEAD REBASE_HEAD rebase-merge rebase-apply; do
    path="$(git rev-parse --git-path "${p}")"
    [[ -e "${path}" ]] && echo "a merge, rebase, cherry-pick or revert is in progress (${path} exists): finish or abort it first"
  done
  unmerged="$(git diff --name-only --diff-filter=U)"
  [[ -z "${unmerged}" ]] || echo "unmerged (conflicted) paths: $(tr '\n' ' ' <<< "${unmerged}")"
  # porcelain v1 -z: "XY path", a rename or copy followed by its original path as its own field
  while IFS= read -r -d '' entry; do
    code="${entry:0:2}"; path="${entry:3}"
    allowed_change "${path}" "${v}" || dirty+=("${path}")
    if [[ "${code}" == *[RC]* ]]; then
      IFS= read -r -d '' path || true
      allowed_change "${path}" "${v}" || dirty+=("${path}")
    fi
  done < <(git status --porcelain=v1 -z --untracked-files=all)
  if [[ "${#dirty[@]}" -gt 0 ]]; then
    echo "changes outside the version lines and releases/${v}/ (commit or stash them first): ${dirty[*]}"
  fi
  git check-ignore -q "releases/${v}/logs/run.json" \
    || echo ".gitignore does not ignore releases/*/logs/ (the run folder must never be committed)"
  [[ ! -e "releases/${v}/testcard.md" ]] \
    || echo "releases/${v}/testcard.md exists: the generated release_card.md replaces it; delete it"
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

# ci_run SHA: the newest run of CI_WORKFLOW for SHA on main, as "id status url conclusion" (empty
# when there is none yet; conclusion is empty while the run is not completed).
ci_run() {
  gh run list --commit "$1" --workflow "${CI_WORKFLOW}" --branch main --limit 20 \
    --json databaseId,status,url,conclusion \
    --jq '.[0] | select(. != null) | "\(.databaseId) \(.status) \(.url) \(.conclusion)"'
}

# card_step_result RUN_ID: the conclusion of the card job's verify step in that run (skipped when
# the job found no card for the commit).
card_step_result() {
  gh run view "$1" --json jobs \
    --jq ".jobs[] | select(.name == \"${CI_CARD_JOB}\") | .steps[] | select(.name == \"${CI_CARD_STEP}\") | .conclusion"
}

# ci_gate SHA: wait for CI_WORKFLOW on SHA and fail unless it succeeds with the card compared,
# within CI_TIMEOUT minutes.
ci_gate() {
  local sha="$1" deadline=$((SECONDS + CI_TIMEOUT * 60)) line id status url conclusion pid card
  if [[ "${SKIP_CI}" == 1 ]]; then
    warn "!!!!!!!! --skip-ci: CI on ${sha:0:12} is NOT checked; this release may ship a red build !!!!!!!!"
    return 0
  fi
  if [[ "${DRY_RUN}" == 1 ]]; then
    echo "+ gh run list --commit ${sha} --workflow ${CI_WORKFLOW} --branch main ... (poll every ${CI_POLL}s until a run appears, up to ${CI_TIMEOUT} min)"
    echo "+ gh run watch <run id> --exit-status (if not completed; fail unless success)"
    echo "+ gh run view <run id> --json jobs (job '${CI_CARD_JOB}', step '${CI_CARD_STEP}' must be success, not skipped)"
    if [[ "${GH_OK}" == 1 && "${sha}" =~ ^[0-9a-f]{40}$ ]]; then
      line="$(ci_run "${sha}" 2>/dev/null)" || line="(gh run list failed)"
      echo "   current CI state of ${sha:0:12}: ${line:-no ${CI_WORKFLOW} run yet}"
      read -r id status url conclusion <<< "${line:-x x x x}"
      if [[ "${status}" == completed ]]; then
        card="$(card_step_result "${id}" 2>/dev/null)" || card="(gh run view failed)"
        echo "   card check of that run: ${card:-no \"${CI_CARD_STEP}\" step}"
      fi
    fi
    return 0
  fi
  echo "   waiting for a ${CI_WORKFLOW} run on ${sha:0:12} (timeout ${CI_TIMEOUT} min)"
  while :; do
    line="$(ci_run "${sha}")" || die "gh run list failed"
    [[ -n "${line}" ]] && break
    (( SECONDS < deadline )) || die "timed out after ${CI_TIMEOUT} min: no ${CI_WORKFLOW} run for ${sha:0:12} on main"
    sleep "${CI_POLL}"
  done
  read -r id status url conclusion <<< "${line}"
  echo "   run ${url} (${status})"
  if [[ "${status}" != completed ]]; then
    gh run watch "${id}" --exit-status --interval "${CI_POLL}" &
    pid=$!
    while kill -0 "${pid}" 2>/dev/null; do
      if (( SECONDS >= deadline )); then
        kill "${pid}" 2>/dev/null || true
        die "timed out after ${CI_TIMEOUT} min, CI still running: ${url}"
      fi
      sleep 2
    done
    wait "${pid}" || die "CI failed: ${url}"
    conclusion=success
  fi
  [[ "${conclusion}" == success ]] || die "CI ${conclusion:-did not succeed}: ${url}"
  card="$(card_step_result "${id}")" || die "gh run view ${id} failed"
  [[ "${card}" == success ]] \
    || die "CI ${url}: the '${CI_CARD_JOB}' job compared no card with this commit (step '${CI_CARD_STEP}': ${card:-not found}); is releases/${VERSION}/release_card.md the card of this tree?"
  echo "   CI passed, card compared: ${url}"
}

# card_tools: the venv with the tree's own tools that runs `xewe release-card` (not recorded: it
# makes the card; the recorded venvs are stage 5's). XEWE is the command prefix.
card_tools() {
  XEWE=("${VENVS}/card/bin/python" -m xewe)
  run "$1" -m venv --clear "${VENVS}/card"
  run "${VENVS}/card/bin/python" -m pip install -q --disable-pip-version-check --progress-bar off .
  # the tools commit the card header names (the tree's HEAD; the venv is not an editable install)
  if [[ "${DRY_RUN}" == 1 ]]; then
    echo "+ git rev-parse HEAD > ${VENVS}/card/xewe-tools-commit"
  else
    git rev-parse HEAD > "${VENVS}/card/xewe-tools-commit"
  fi
}

# step LABEL RECORD_ARGS...: one recorded step; a failed command is noted and the run goes on (the
# card refuses it), a usage error stops.
FAILED_STEPS=()
step() {
  local label="$1" rc=0
  shift
  if [[ "${DRY_RUN}" == 1 ]]; then
    run "${XEWE[@]}" release-card record --dir "${LOGS}" ${RESUME_ARGS[@]+"${RESUME_ARGS[@]}"} "$@"
    return 0
  fi
  "${XEWE[@]}" release-card record --dir "${LOGS}" ${RESUME_ARGS[@]+"${RESUME_ARGS[@]}"} "$@" || rc=$?
  case "${rc}" in
    0) ;;
    1|3) FAILED_STEPS+=("${label}"); warn "${label} failed (exit ${rc}; the log is in ${LOGS}); going on" ;;
    *) die "xewe release-card record exited ${rc} on ${label}" ;;
  esac
}

notes_skeleton() {
  cat <<EOF
${NOTES_MARKER}: fill in this file, then delete this line (--prepare stops while it is here)
${TOOLS_TITLE} $1

What the tools are, in two or three sentences.

CHANGED SINCE <previous version>

- ...

BREAKING

- ...

INSTALL

- xewe.toml: [tools] ref = "$1", then ./setup.sh.
- Workflow callers: ${TOOLS_REPO}/.github/workflows/<workflow>.yml@$1

TESTING

What was tested, on which tree and with which result: release_card.md, generated by
scripts/release.sh --prepare $1 and attached to this release.
EOF
}

# prepare VERSION: the 7 stages of --prepare (see the header).
prepare() {
  local v="$1" dir="releases/$1" notes="releases/$1/notes.txt" card="releases/$1/release_card.md"
  local problems loc file changed=() x p i lint=0 pys=() paths=() py venv junit rc
  LOGS="${dir}/logs"
  STAGES=7

  stage 1 preflight
  problems="$(tree_problems "${v}")"
  if [[ -n "${problems}" ]]; then
    if [[ "${DRY_RUN}" == 1 ]]; then
      while IFS= read -r x; do fail "${x}"; done <<< "${problems}"
    else
      die "the work tree cannot be prepared:"$'\n'"${problems}"
    fi
  fi
  for x in $(ci_pythons); do
    p="$(find_python "${x}")"
    if [[ -n "${p}" ]]; then
      pys+=("${x}"); paths+=("${p}")
      echo "   python ${x}: ${p}"
    else
      warn "python ${x} of the ${CI_WORKFLOW} matrix is not installed here (uv python install ${x}); no run for it"
    fi
  done
  [[ "${#pys[@]}" -gt 0 ]] || die "none of the ${CI_WORKFLOW} matrix Pythons ($(ci_pythons | tr '\n' ' ')) is installed here"
  for i in "${!pys[@]}"; do [[ "${pys[i]}" == "${LINT_PYTHON}" ]] && lint="${i}"; done
  echo "   checkers and card tools on python ${pys[lint]}"
  [[ "${FAILED}" -eq 0 ]] && echo "   ok: $(git symbolic-ref --short HEAD 2>/dev/null || echo '(detached)') at $(git rev-parse --short=12 HEAD)"

  stage 2 versions
  for loc in "${VERSION_LOCATIONS[@]}"; do
    file="${loc%%|*}"
    set_location "${loc}" "${v}" "" >/dev/null || die "${file}: no line matches $(cut -d'|' -f2 <<< "${loc}")"
    if ! cmp -s "${file}" <(set_location "${loc}" "${v}" ""); then
      if [[ "${DRY_RUN}" != 1 ]]; then
        set_location "${loc}" "${v}" "" > "${file}.tmp"
        cat "${file}.tmp" > "${file}" # cat keeps the file's mode
        rm -f "${file}.tmp"
      fi
      [[ " ${changed[*]-} " == *" ${file} "* ]] || changed+=("${file}")
    fi
  done
  if [[ "${#changed[@]}" -gt 0 ]]; then
    echo "   version ${v}: ${changed[*]}$([[ "${DRY_RUN}" == 1 ]] && echo ' (would change)')"
  else
    echo "   version ${v}: every location already says it"
  fi

  stage 3 "release notes"
  if [[ ! -s "${notes}" ]]; then
    echo "   create ${notes} (skeleton)"
    if [[ "${DRY_RUN}" == 1 ]]; then
      echo "   a real run stops here (exit 2) until ${notes} is filled"
    else
      mkdir -p "${dir}"
      notes_skeleton "${v}" > "${notes}"
      echo "error: fill ${notes} (the GitHub Release text, embedded in the card), delete its ${NOTES_MARKER} line, then run scripts/release.sh --prepare ${v} again" >&2
      exit 2
    fi
  elif grep -q -- "${NOTES_MARKER}" "${notes}"; then
    if [[ "${DRY_RUN}" == 1 ]]; then
      echo "   ${notes} is still the skeleton: a real run stops here (exit 2)"
    else
      echo "error: ${notes} is still the skeleton (its ${NOTES_MARKER} line): fill it, delete that line, then run scripts/release.sh --prepare ${v} again" >&2
      exit 2
    fi
  else
    echo "   ${notes}: filled ($(wc -l < "${notes}" | tr -d ' ') lines)"
  fi

  stage 4 "card run"
  card_tools "${paths[lint]}"
  run "${XEWE[@]}" release-card init --dir "${LOGS}" --repo "${TOOLS_REPO}" --version "${v}" --tag "${v}" \
    ${RESUME_ARGS[@]+"${RESUME_ARGS[@]}"}

  stage 5 setup
  for i in "${!pys[@]}"; do
    venv="${VENVS}/py${pys[i]}"
    step "venv-py${pys[i]}" --kind setup --name "venv-py${pys[i]}" -- "${paths[i]}" -m venv --clear "${venv}"
    if [[ "${i}" == "${lint}" ]]; then
      step "install-py${pys[i]}" --kind setup --name "install-py${pys[i]}" -- "${venv}/bin/python" -m pip install --disable-pip-version-check \
        --progress-bar off . "${CHECKERS[@]}" "${LINTERS[@]}"
    else
      step "install-py${pys[i]}" --kind setup --name "install-py${pys[i]}" -- "${venv}/bin/python" -m pip install --disable-pip-version-check \
        --progress-bar off . "${CHECKERS[@]}"
    fi
  done

  stage 6 checks
  for i in "${!pys[@]}"; do
    venv="${VENVS}/py${pys[i]}"
    junit="${VENVS}/junit-py${pys[i]}.xml"
    step "pytest-py${pys[i]}" --kind unit --name "py${pys[i]}" --junit "${junit}" -- env XEWE_NO_BOARD=1 \
      "${venv}/bin/python" -m pytest -q --junitxml="${junit}" -o junit_family=xunit1
    step "pyflakes-py${pys[i]}" --kind check --name "pyflakes-py${pys[i]}" -- "${venv}/bin/python" -m pyflakes src tests
    step "mypy-py${pys[i]}" --kind check --name "mypy-py${pys[i]}" -- "${venv}/bin/python" -m mypy
  done
  venv="${VENVS}/py${pys[lint]}"
  step format --kind check --name format -- env "XEWE_CLANG_FORMAT=${venv}/bin/clang-format" \
    "${venv}/bin/python" -m xewe format --check
  step brand-lint --kind check --name brand-lint -- "${venv}/bin/python" -m xewe brand-lint .
  step actionlint --kind check --name actionlint -- "${venv}/bin/actionlint" -ignore xewe-board \
    -shellcheck "${venv}/bin/shellcheck"
  if [[ "${#FAILED_STEPS[@]}" -gt 0 ]]; then
    warn "failed steps: ${FAILED_STEPS[*]} (the card will refuse them)"
  fi

  stage 7 card
  rc=0
  run "${XEWE[@]}" release-card render --dir "${LOGS}" --notes "${notes}" --out "${card}" \
    --require "${REQUIRE}" --title "${TOOLS_TITLE}" || rc=$?
  case "${rc}" in
    0) ;;
    1) die "the card was refused (reasons above; draft: ${LOGS}/release_card.draft.md). Fix the cause, then: scripts/release.sh --prepare ${v} --resume" ;;
    *) die "xewe release-card render exited ${rc}" ;;
  esac
  if [[ "${DRY_RUN}" != 1 ]]; then
    changed=()
    while IFS= read -r file; do
      [[ -n "${file}" ]] && changed+=("${file}")
    done < <(version_files | while IFS= read -r file; do git diff --quiet HEAD -- "${file}" || echo "${file}"; done)
  fi
  if [[ "${DRY_RUN}" == 1 ]]; then
    echo "   dry run: nothing was prepared; a real run ends with:"
  else
    echo "   prepared in ${SECONDS} s; no git change was made. Review ${card}, then commit and push:"
  fi
  echo "  git add ${changed[*]+${changed[*]} }${notes} ${card} && git commit -m \"prepare ${v}\" && git push origin $(git symbolic-ref --short HEAD 2>/dev/null || echo '<branch>')"
  echo "then, once on main with ci.yml green: scripts/release.sh ${v} --dry-run; scripts/release.sh ${v}"
  if [[ "${DRY_RUN}" == 1 && "${FAILED}" -gt 0 ]]; then
    echo "== dry run: ${FAILED} check(s) failed; a real run stops at the first"
    exit 1
  fi
}

usage() { sed -n 's/^# Usage: //p' "${BASH_SOURCE[0]}"; }

main() {
  VERSION=""
  DRY_RUN=0
  SKIP_CI=0
  PREPARE=0
  RESUME_ARGS=()
  CI_TIMEOUT=30
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --dry-run) DRY_RUN=1 ;;
      --skip-ci) SKIP_CI=1 ;;
      --prepare) PREPARE=1 ;;
      --resume) RESUME_ARGS=(--resume) ;;
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
  [[ "${VERSION}" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] \
    || die "version must be X.Y.Z with no 'v' (the ecosystem's tags are un-prefixed): ${VERSION}"

  # Repo root: the top of the git work tree this is run in.
  command -v git >/dev/null 2>&1 || die "git is required"
  ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" || die "not inside a git work tree"
  cd "${ROOT}"
  grep -qx 'name = "xewe-os-tools"' pyproject.toml 2>/dev/null || die "${ROOT} is not the xewe-os-tools repo"
  [[ "${DRY_RUN}" == 1 ]] && echo "== dry run: nothing is changed; commands are printed with a leading +"
  export XEWE_NO_BOARD=1 # the self-tests and the tools never touch a board here

  if [[ "${PREPARE}" == 1 ]]; then
    [[ "${SKIP_CI}" == 0 ]] || die "--skip-ci does not apply to --prepare"
    prepare "${VERSION}"
    return
  fi
  [[ "${#RESUME_ARGS[@]}" -eq 0 ]] || die "--resume applies to --prepare only (the tag run resumes by itself)"
  [[ "${SKIP_CI}" == 1 ]] && warn "!!!!!!!! --skip-ci: the CI gate is skipped (emergencies only) !!!!!!!!"

  stage 1 preflight
  GH_OK=0
  if ! command -v gh >/dev/null 2>&1; then
    fail "gh is not installed (https://cli.github.com)"
  elif ! gh auth status >/dev/null 2>&1; then
    fail "gh is not logged in (gh auth login)"
  else
    GH_OK=1
  fi

  run git fetch --quiet origin

  local notes="releases/${VERSION}/notes.txt" card="releases/${VERSION}/release_card.md"
  # resume: the tag exists (here or on origin) but there is no GitHub Release yet.
  local resume=0 tagged="" stale
  if tag_local "${VERSION}" || tag_remote "${VERSION}"; then
    [[ "${GH_OK}" == 1 ]] || die "tag ${VERSION} exists; gh is needed to check for its GitHub Release"
    if gh release view "${VERSION}" >/dev/null 2>&1; then
      die "${VERSION} is already released (tag and GitHub Release exist)"
    fi
    resume=1
    tagged="$(tag_commit "${VERSION}")"
    git cat-file -e "${tagged}^{commit}" 2>/dev/null \
      || die "tag ${VERSION} points at ${tagged:0:12}, which is not in this clone (git fetch --tags origin)"
    stale="$(stale_locations "${VERSION}" "${tagged}")"
    [[ -z "${stale}" ]] \
      || die "tag ${VERSION} points at ${tagged:0:12}, whose files do not say ${VERSION}:"$'\n'"${stale}"
    if ! git cat-file -e "${tagged}:${notes}" 2>/dev/null || ! git cat-file -e "${tagged}:${card}" 2>/dev/null; then
      die "tag ${VERSION} points at ${tagged:0:12}, which lacks ${notes} or ${card}"
    fi
  fi

  local head
  head="$(git rev-parse HEAD)"
  # A fresh release needs a clean main at origin's head; a resume only needs the tagged commit.
  if [[ "${resume}" == 0 ]]; then
    [[ -s "${notes}" ]] || fail "${notes} is missing (the release notes; scripts/release.sh --prepare ${VERSION})"
    [[ -s "${card}" ]] || fail "${card} is missing (the release card; scripts/release.sh --prepare ${VERSION})"
    git ls-files --error-unmatch -- "${notes}" "${card}" >/dev/null 2>&1 \
      || fail "${notes} and ${card} are not both committed (the prepare commit)"
    if [[ -s "${notes}" ]] && grep -q -- "${NOTES_MARKER}" "${notes}"; then
      fail "${notes} is still the skeleton (its ${NOTES_MARKER} line)"
    fi
    [[ ! -e "releases/${VERSION}/testcard.md" ]] \
      || fail "releases/${VERSION}/testcard.md exists: the release card replaces it; delete it"
    [[ -z "$(git status --porcelain)" ]] || fail "the working tree is not clean (git status)"
    local branch remote
    branch="$(git symbolic-ref --quiet --short HEAD || echo "(detached)")"
    [[ "${branch}" == main ]] || fail "on branch ${branch}, not main"
    remote="$(git ls-remote origin refs/heads/main | cut -f1)"
    if [[ -z "${remote}" ]]; then
      fail "origin has no main branch"
    elif [[ "${head}" != "${remote}" ]]; then
      fail "main (${head:0:12}) is not level with origin/main (${remote:0:12}): pull or push first"
    fi
    stale="$(stale_locations "${VERSION}" "")"
    [[ -z "${stale}" ]] \
      || fail "these version locations do not say ${VERSION} (scripts/release.sh --prepare ${VERSION}):"$'\n'"${stale}"
  fi
  [[ "${FAILED}" -eq 0 ]] && echo "   ok: ${VERSION} on ${head:0:12}"

  local upload_notes="${notes}"
  if [[ "${resume}" == 1 ]]; then
    echo "== resuming at stage 4: tag ${VERSION} exists (${tagged:0:12}), no GitHub Release yet"
    # the release files are those of the tagged commit, whatever is checked out now
    UPLOAD_DIR="$(mktemp -d)"
    trap 'rm -rf "${UPLOAD_DIR}"' EXIT
    git show "${tagged}:${notes}" > "${UPLOAD_DIR}/notes.txt"
    git show "${tagged}:${card}" > "${UPLOAD_DIR}/release_card.md"
    upload_notes="${UPLOAD_DIR}/notes.txt"
    card="${UPLOAD_DIR}/release_card.md"
  else
    stage 2 "release card"
    # verify is read-only: it runs in a dry run too, with the card tools of --prepare
    if [[ -x "${VENVS}/card/bin/python" ]]; then
      XEWE=("${VENVS}/card/bin/python" -m xewe)
    elif [[ "${DRY_RUN}" == 1 ]]; then
      XEWE=()
    else
      local py
      py="$(find_python "${LINT_PYTHON}")"
      [[ -n "${py}" ]] || py="$(command -v python3)" || die "python3 is required"
      card_tools "${py}"
    fi
    if [[ "${#XEWE[@]}" -gt 0 ]]; then
      echo "+ ${XEWE[*]} release-card verify --card ${card}"
      "${XEWE[@]}" release-card verify --card "${card}" \
        || fail "release-card verify failed on ${card} (reasons above)"
    else
      echo "+ ${VENVS}/card/bin/python -m xewe release-card verify --card ${card}"
      echo "   (not run: no card tools in ${VENVS}/card; --prepare ${VERSION} creates them, a real run too)"
    fi

    stage 3 "CI on HEAD"
    ci_gate "${head}"
  fi

  stage 4 "tag and GitHub Release"
  if [[ "${resume}" == 0 ]]; then
    run git tag -a "${VERSION}" -m "${TOOLS_TITLE} ${VERSION}" "${head}"
  fi
  tag_remote "${VERSION}" || run git push origin "refs/tags/${VERSION}"
  run gh release create "${VERSION}" --verify-tag --title "${TOOLS_TITLE} ${VERSION}" \
    --notes-file "${upload_notes}" "${card}"

  stage 5 "consumers"
  echo "Projects: xewe.toml [tools] ref = \"${VERSION}\" (or xewe manifest update), then ./setup.sh."
  echo "Workflow callers: uses: ${TOOLS_REPO}/.github/workflows/<workflow>.yml@${VERSION}"
  if [[ "${DRY_RUN}" == 1 && "${FAILED}" -gt 0 ]]; then
    echo "== dry run: ${FAILED} check(s) failed; a real run stops at the first"
    exit 1
  fi
}

# Sourcing the file (tests) defines the functions without running a release.
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
