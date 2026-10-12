# CI/CD for XeWe OS (GitHub Actions)

The CI logic lives here, in `xewe-os-tools/.github/workflows/`, as reusable workflows. Every other
repository carries a caller of about 20 lines that names one of them. The steps are the ones you
run locally (`./setup.sh`, `xewe build`, `xewe test`, `xewe release`), so CI and a laptop stay the
same flow. Runners are `ubuntu-latest`; nothing needs a secret beyond the automatic `GITHUB_TOKEN`.

| Workflow (this repo) | Called by | What it runs |
|---|---|---|
| `xewe-build.yml` | `xewe-os`, every project (`ci.yml`) | `./setup.sh` → `xewe build` per chip (default c3,c6,s3) → `xewe test --no-board` → upload `.bin` + `manifest.json` + `meta.json` + `build_config.toml`. A separate **lint** job (`xewe format --check`, `xewe brand-lint`) is non-blocking unless the caller passes `strict_lint: true` |
| `xewe-release.yml` | the same callers, on an `X.Y.Z` tag | `./setup.sh` → `xewe release` → GitHub Release (assets) → commit of the release folder to `releases_branch` (`releases`, or `main`) |
| `xewe-core-tests.yml` | `xewe-os-core` (`tests.yml`) | `tests/unit/run.sh` (ArduinoJson at the `arduinojson_ref` input, fetched by `git clone`), `arduino-lint`, the examples compiled per chip (see below), `xewe format --check` and `xewe brand-lint`; every job blocks. Releases are core's own `scripts/release.sh` |
| `xewe-modules-tests.yml` | `xewe-os-modules` (`tests.yml`) | template clone as a harness, `./setup.sh --modules all` with the modules source = the commit under test, `tools/validate.py`, `xewe test --unit-only`, c3 compile; `xewe format --check modules tools` (blocking only with `strict_lint: true`, default false until the modules format commit) and `xewe brand-lint` (blocking), both from the tools at the `tools_ref` input |
| `xewe-board.yml` | nobody yet | board tests on a self-hosted runner with the board attached: setup, flash, first boot, provision, two test passes with a liveness probe and an optional power-cycle command (see below) |
| `xewe-pages.yml` | nobody yet | optional web flasher on GitHub Pages, rebuilt from the GitHub Releases (ESP Web Tools manifests, version picker); an alternative to the `releases` branch |
| `ci.yml` | this repo | pytest (3.11–3.13), pyflakes, mypy (settings in `pyproject.toml`), actionlint on these workflows, `xewe format --check` and `xewe brand-lint` from this checkout; every job blocks |

Callers pin a tools tag, the same one their `xewe.toml [tools] ref` names, for example
`uses: xewe-labs/xewe-os-tools/.github/workflows/xewe-build.yml@3.0.1`. Tags are `X.Y.Z` with no
`v` across the ecosystem from 3.0.0 on. `@main` follows the newest tools (development only).

Action versions: every action is on its newest major that runs on Node 24 (checkout v7,
setup-python v7, cache v6, upload-artifact v7, configure-pages v6, upload-pages-artifact v5,
deploy-pages v5, arduino-lint-action v3). `arduino/setup-arduino-cli` stays at v2: no release
runs on Node 24 yet, so the core `examples` job still shows the Node 20 deprecation notice.

## Core example builds

`xewe-core-tests.yml` job `examples` runs once per chip (c3, c6, s3). It reads `ARDUINO_CLI_VERSION`,
`ESP32_CORE_VERSION` and `ESP32_INDEX_URL` from `src/xewe/env/pins.py`, and the chip's FQBN from
`src/xewe/build/chips.py`, of the tools at the `tools_ref` input. It installs arduino-cli
(`arduino/setup-arduino-cli`), the esp32 core (cached in `~/.arduino15` per core version) and
ArduinoJson from the Library Manager at the `arduinojson_ref` version, then runs
`arduino-cli compile --fqbn <fqbn> --warnings default --library . <ino>` for every
`examples/*/*.ino`. The `format` job installs the tools at `tools_ref` with ruff and clang-format at
pinned versions (`XEWE_CLANG_FORMAT` points at that clang-format, so the runner's own one is not
used), so a new upstream release does not fail a tree that was formatted with the pinned versions.
The modules `format` job and the tools' own `ci.yml` `format` job pin the same versions (ruff
0.17.0, clang-format 23.1.3). The modules repo is neither a project nor a library, so its job
names the folders: `xewe format --check modules tools`.

## The shared toolchain cache

`./setup.sh` installs arduino-cli and the esp32 core into `~/.xewe-os/build-tools/` (1.7 GB
download, about 6 GB installed). The workflows read `ARDUINO_CLI_VERSION` and `ESP32_CORE_VERSION`
from `src/xewe/env/pins.py` of the tools at the ref the project's `xewe.toml` names, and cache
`build-tools/{bin,arduino15,arduino-user}` under
`xewe-toolchain-<os>-<arch>-cli<version>-esp32-<version>`. A new pin is a new key; nothing else
invalidates it. `downloads/` is not cached because the installed tree is enough. Caches belong to
the calling repository (10 GB limit each; one entry is about 2 GB compressed).

## Releases

Push an annotated tag `X.Y.Z` (no `v`; a leading `v` is still accepted) on a commit whose
`xewe.toml [project] version` is `X.Y.Z`; the tag message becomes the release notes.
`X.Y.Z-<suffix>` (for example `3.1.0-rc1`) is a pre-release:
the build uses version `X.Y.Z` (what `xewe release` accepts) and the folder is renamed to
`X.Y.Z-<suffix>`, so a pre-release never takes the final version's place.

At tag time `./setup.sh` resolves every `latest` ref, and `xewe release` writes the result into
every `meta.json` ([spec.md](spec.md) §11): `project_commit`, `core_ref`/`core_commit`,
`modules_ref`/`modules_commit`, `tools_ref`/`tools_commit` and `libraries` (`{name: {ref,
commit}}`). The workflow adds `release_tag` and appends a "Built from" list to the release notes.
A tools ref whose `xewe release` does not write these fields gets them filled in from
`build_config.toml [installed]`.

Each release is published twice:

| | GitHub Release (always) | `releases_branch` (`publish_branch`, default true) |
|---|---|---|
| Holds | per-chip `.bin`, `firmware-<v>.tar.gz` (the whole folder, `meta.json` included), notes with the resolved commits | `static/firmware/releases/<v>/` exactly as `xewe release` lays it out, plus `index.json` (all versions, newest first) |
| For | people and scripts; canonical and permanent | the web flasher: static files at stable URLs (`raw.githubusercontent.com/<org>/<repo>/releases/...`, or GitHub Pages from that branch), no CORS-blocked asset redirects |
| Cost | none in git | binaries in git history, but on an orphan branch, never in `main` clones (`git clone --single-branch`) |

The orphan `releases` branch (the default) keeps `main` small and is written only by CI.
`releases_branch: main` commits the folder into the project's own `main` instead, after the
release: one bot commit `release <v> (<tag>, <sha7>) [skip ci]` that adds
`static/firmware/releases/<v>/` (and `index.json`) and leaves the rest of the tree as it is, so the
tag itself does not contain its binaries. The commit goes through Git LFS: the job runs
`git lfs install --local`, the `.gitattributes` of `main` decides what is stored in LFS (for
example `static/**/*.bin filter=lfs diff=lfs merge=lfs -text`), and the job fails if a `.bin` it
routes to LFS would be committed as a plain blob. Older binaries are not downloaded
(`GIT_LFS_SKIP_SMUDGE=1`). A push with `GITHUB_TOKEN` does not start new workflow runs, and
`[skip ci]` covers other tokens. The template (`xewe-os`) publishes this way.

The release job needs `permissions: contents: write`, which the caller grants to that job only.

## What CI does not do

No board: flashing, provisioning, serial and the `tests/board` suites stay on the Mac
(`run_tests/01`–`10`). CI proves "builds on every chip and passes the host tests", not "runs".

`xewe-board.yml` is ready for a self-hosted runner: register one on the Mac with the labels
`self-hosted, macOS, ARM64, xewe-board`, keep the dotenv on that machine (never in a secret), and
call it from a project's `ci.yml` on `schedule` or `workflow_dispatch` only (the snippet is in the
file header; the job refuses pull-request events). A board that goes silent after a software
restart is power-cycled by the `power_cycle` command when one is given (for example `uhubctl` on a
hub with per-port power switching). Until a runner exists, do not call it: the job would wait in
the queue.

A Nix-defined toolchain (python, arduino-cli at the pinned version, esptool, clang-format, ruff)
is a proposal only, kept in the xewe-labs workspace at `docs/phase4/reports/ci-cd-proposals.md`
(P3); nothing here uses Nix.

## Repository settings (owner)

- Actions → General → Workflow permissions: "Read repository contents" is enough; the callers
  raise it to `contents: write` for the release job only.
- Branch protection on `main`: require the `build / build and host tests` check (projects), the
  `core / ...` checks (core) or `modules / ...` (modules). Do not require the lint or format jobs
  while they are non-blocking.
- Leave `releases_branch` (`releases`, or `main` when the release commits there) unprotected, or
  give a ruleset a bypass for GitHub Actions: the release job pushes to it with `GITHUB_TOKEN`.
  Block force-pushes and deletion there.

## Making lint blocking

The project lint job (`xewe format --check`, `xewe brand-lint`) is advisory until the
whitespace and format commits have landed in a repo. Then, in that repo's `ci.yml`, set
`strict_lint: true` under the `build` job's `with:`: the job is named `build / lint` (no
"(non-blocking)") and a failure fails the run; add it to the required checks if you want merges
gated on it. A step whose command the tools ref in `xewe.toml` does not have yet is skipped with
a notice.

The modules repo does the same with `strict_lint: true` under its `modules` job's `with:` once its
format commit has landed (today 12 of its 13 C/C++ files would change); its brand-lint already
blocks. Core and the tools have no switch: their format and brand-lint jobs always block.

