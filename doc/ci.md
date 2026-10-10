# CI/CD for XeWe OS (GitHub Actions)

The CI logic lives here, in `xewe-os-tools/.github/workflows/`, as reusable workflows. Every other
repository carries a caller of about 20 lines that names one of them. The steps are the ones you
run locally (`./setup.sh`, `xewe build`, `xewe test`, `xewe release`), so CI and a laptop stay the
same flow. Runners are `ubuntu-latest`; nothing needs a secret beyond the automatic `GITHUB_TOKEN`.

| Workflow (this repo) | Called by | What it runs |
|---|---|---|
| `xewe-build.yml` | `xewe-os`, every project (`ci.yml`) | `./setup.sh` → `xewe build` per chip (default c3,c6,s3) → `xewe test --no-board` → upload `.bin` + `manifest.json` + `meta.json` + `build_config.toml`. A separate **lint** job (`xewe format --check`, `xewe brand-lint`) is non-blocking unless the caller passes `strict_lint: true` |
| `xewe-release.yml` | the same callers, on a `v*` tag | `./setup.sh` → `xewe release` → GitHub Release (assets) → commit to the `releases` branch |
| `xewe-core-tests.yml` | `xewe-os-core` (`tests.yml`) | `tests/unit/run.sh` (ArduinoJson at the `arduinojson_ref` input, fetched by `git clone`), `arduino-lint`, `xewe setup` in library mode then `xewe check --examples --chip <chip>` for c3, c6, s3 (shared toolchain cached under `xewe-home-<os>-<arch>-esp32-<version>`); `xewe brand-lint` non-blocking |
| `xewe-modules-tests.yml` | `xewe-os-modules` (`tests.yml`) | template clone as a harness, `./setup.sh --modules all` with the modules source = the commit under test, `tools/validate.py`, `xewe test --unit-only`, c3 compile; `xewe brand-lint` non-blocking |
| `xewe-board.yml` | nobody yet | board tests on a self-hosted runner with the board attached: setup, flash, first boot, provision, two test passes with a liveness probe and an optional power-cycle command (see below) |
| `xewe-pages.yml` | nobody yet | optional web flasher on GitHub Pages, rebuilt from the GitHub Releases (ESP Web Tools manifests, version picker); an alternative to the `releases` branch |
| `ci.yml` | this repo | pytest (3.11–3.13), pyflakes, mypy (settings in `pyproject.toml`), actionlint on these workflows; `xewe brand-lint` non-blocking |

Callers reference `@main`, matching the `latest` refs the manifests use until v3. Tag-pin them
(`@v0.2.0`) when the manifests freeze their refs.

## The shared toolchain cache

`./setup.sh` installs arduino-cli and the esp32 core into `~/.xewe-os/build-tools/` (1.7 GB
download, about 6 GB installed). The workflows read `ARDUINO_CLI_VERSION` and `ESP32_CORE_VERSION`
from `src/xewe/env/pins.py` of the tools at the ref the project's `xewe.toml` names, and cache
`build-tools/{bin,arduino15,arduino-user}` under
`xewe-toolchain-<os>-<arch>-cli<version>-esp32-<version>`. A new pin is a new key; nothing else
invalidates it. `downloads/` is not cached because the installed tree is enough. Caches belong to
the calling repository (10 GB limit each; one entry is about 2 GB compressed).

## Releases

Push an annotated tag `vX.Y.Z` on a commit whose `xewe.toml [project] version` is `X.Y.Z`; the tag
message becomes the release notes. `vX.Y.Z-<suffix>` (for example `v2.0.0-rc1`) is a pre-release:
the build uses version `X.Y.Z` (what `xewe release` accepts) and the folder is renamed to
`X.Y.Z-<suffix>`, so a pre-release never takes the final version's place.

At tag time `./setup.sh` resolves every `latest` ref, and `xewe release` writes the result into
every `meta.json` ([spec.md](spec.md) §11): `project_commit`, `core_ref`/`core_commit`,
`modules_ref`/`modules_commit`, `tools_ref`/`tools_commit` and `libraries` (`{name: {ref,
commit}}`). The workflow adds `release_tag` and appends a "Built from" list to the release notes.
A tools ref whose `xewe release` does not write these fields gets them filled in from
`build_config.toml [installed]`.

Each release is published twice:

| | GitHub Release (always) | `releases` branch (`publish_branch`, default true) |
|---|---|---|
| Holds | per-chip `.bin`, `firmware-<v>.tar.gz` (the whole folder, `meta.json` included), notes with the resolved commits | `static/firmware/releases/<v>/` exactly as `xewe release` lays it out, plus `index.json` (all versions, newest first) |
| For | people and scripts; canonical and permanent | the web flasher: static files at stable URLs (`raw.githubusercontent.com/<org>/<repo>/releases/...`, or GitHub Pages from that branch), no CORS-blocked asset redirects |
| Cost | none in git | binaries in git history, but on an orphan branch, never in `main` clones (`git clone --single-branch`) |

The branch keeps `main` small and is written only by CI. The template keeps
`publish_branch: false` until its `binaries` branch is retired or reused
(`releases_branch: binaries`).

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
  `core / ...` checks (core) or `modules / ...` (modules). Do not require the lint jobs while they
  are non-blocking.
- Leave `releases` unprotected, or give a ruleset a bypass for GitHub Actions: the release job
  pushes to it with `GITHUB_TOKEN`. Block force-pushes and deletion there.

## Making lint blocking

The project lint job (`xewe format --check`, `xewe brand-lint`) is advisory until the
whitespace and format commits have landed in a repo. Then, in that repo's `ci.yml`, set
`strict_lint: true` under the `build` job's `with:`: the job is named `build / lint` (no
"(non-blocking)") and a failure fails the run; add it to the required checks if you want merges
gated on it. A step whose command the tools ref in `xewe.toml` does not have yet is skipped with
a notice.

