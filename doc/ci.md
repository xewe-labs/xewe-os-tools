# CI/CD for XeWe OS (GitHub Actions)

The CI logic lives here, in `xewe-os-tools/.github/workflows/`, as reusable workflows. Every other
repository carries a caller of about 20 lines that names one of them. The steps are the ones you
run locally (`./setup.sh`, `xewe build`, `xewe test`, `xewe release`), so CI and a laptop stay the
same flow. Runners are `ubuntu-latest`; nothing needs a secret beyond the automatic `GITHUB_TOKEN`.

| Workflow (this repo) | Called by | What it runs |
|---|---|---|
| `xewe-build.yml` | `xewe-os`, every project (`ci.yml`) | `./setup.sh` → `xewe build` per chip (default c3,c6,s3) → `xewe test --no-board` → upload `.bin` + `manifest.json` + `meta.json` + `build_config.toml`. A separate **lint** job (`xewe format --check`, `xewe brand-lint`, no committed firmware under `static/firmware/releases/`) is non-blocking unless the caller passes `strict_lint: true` |
| `xewe-release.yml` | the same callers, on an `X.Y.Z` tag | `plan` (version, matrix chips, notes) → `build` per chip (`./setup.sh`, `xewe release --chip C`, `xewe build`, `xewe test --no-board` with JUnit) → `assemble` (`xewe release-card verify --ci`, `xewe release --assemble`, GitHub Release with the assets). The commit to `releases_branch` is opt-in and deprecated (`publish_branch`) |
| `xewe-core-tests.yml` | `xewe-os-core` (`tests.yml`) | `tests/unit/run.sh` (ArduinoJson at the `arduinojson_ref` input, fetched by `git clone`), `arduino-lint` (`compliance: strict`, `library-manager: update`; the rules are core's `doc/library-rules.md`), the examples compiled per chip (see below), `xewe format --check` and `xewe brand-lint`, and the `card` job on a prepare commit (below); every job blocks. Releases are core's own `scripts/release.sh` |
| `xewe-modules-tests.yml` | `xewe-os-modules` (`tests.yml`) | template clone as a harness, `./setup.sh --modules all` with the modules source = the commit under test, `tools/validate.py`, `xewe test --unit-only`, c3 compile; `xewe format --check modules tools` (blocking only with `strict_lint: true`, default false until the modules format commit) and `xewe brand-lint` (blocking), both from the tools at the `tools_ref` input, and the `card` job on a prepare commit (below) |
| `xewe-board.yml` | nobody yet | board tests on a self-hosted runner with the board attached: setup, flash, first boot, provision, two test passes with a liveness probe and an optional power-cycle command (see below) |
| `xewe-pages.yml` | nobody yet | optional web flasher on GitHub Pages, rebuilt from the GitHub Releases' `firmware-<v>.tar.gz` (ESP Web Tools manifests, version picker) |
| `ci.yml` | this repo | pytest (3.11–3.13; the 3.12 run uploads its JUnit file as `card-inputs`), pyflakes, mypy (settings in `pyproject.toml`), actionlint on these workflows, `xewe format --check` and `xewe brand-lint` from this checkout, and the `card` job on a prepare commit (below, with the tools of this checkout); every job blocks |

Callers pin a tools tag, the same one their `xewe.toml [tools] ref` names, for example
`uses: xewe-labs/xewe-os-tools/.github/workflows/xewe-build.yml@3.1.0`. Tags are `X.Y.Z` with no
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
the build uses version `X.Y.Z` (what `xewe release` accepts) and the folder and tarball are renamed
to `X.Y.Z-<suffix>`, so a pre-release never takes the final version's place.

At tag time `./setup.sh` resolves every `latest` ref, and `xewe release` writes the result into
every `meta.json` ([spec.md](spec.md) §11): `project_commit`, `core_ref`/`core_commit`,
`modules_ref`/`modules_commit`, `tools_ref`/`tools_commit` and `libraries` (`{name: {ref,
commit}}`). The workflow adds `release_tag` and appends a "Built from" list to the release notes.
The workflow needs tools 3.1.0 or later in the project's `xewe.toml` (`--chip`, `--assemble`,
`release-card`).

**Jobs.** Each job gets `timeout-minutes` from the `timeout_minutes` input (default 90; a chip job
builds every matrix row of that chip, so the product, with 22 s3 rows, passes more).

| Job | What it does |
|---|---|
| `plan` | version from the tag, the chips that have rows in `release_matrix.csv` (c3, c6, s3 without a matrix), the release notes (artifact `notes`) |
| `build (<chip>)` | one job per chip, `fail-fast: false`: `./setup.sh`, `xewe release --chip C` (that chip's rows and `firmware_map.C.csv`), `xewe build --chip C` (the default image, whose `meta.json` the card check reads), `xewe test --no-board --chip C -- --junitxml=… -o junit_family=xunit1 -o junit_logging=no -ra`; uploads `release-<chip>` (the rows) and `card-<chip>` (JUnit and meta) |
| `assemble` | downloads the card inputs and runs the release-card check, then downloads the rows, `xewe release --assemble` (`firmware_map.csv` in matrix order, `firmware-<v>.tar.gz`, `build/release/<v>/assets/`), `release_tag` and the notes, then `gh release create` (or `gh release upload --clobber` on a rerun) |

**Assets.** The GitHub Release is the only home of the binaries; nothing under
`static/firmware/releases/` is committed (the `xewe-build.yml` lint checks it). For each matrix
row: the bin, `<prefix>_meta.json` and `<prefix>_manifest.json` (an ESP Web Tools manifest whose
part path is the bin's asset name); then `firmware_map.csv` (one row per image: the matrix columns,
`_PATH`, `_ASSET`, `_SIZE`, `_SHA256`, `_META`, `_MANIFEST`, `_BUILD_NOTES`),
`firmware-<v>.tar.gz` (the whole folder, as `xewe release` lays it out) and `release_card.md`. No
logs are published. Names ([spec.md](spec.md) §11):

| Project | Bin | Meta | Manifest |
|---|---|---|---|
| default matrix (template) | `3.1.0-c3-xewe-os.bin` | `c3_meta.json` | `c3_manifest.json` |
| product with pin columns | `c6_20_19_3.1.0-c6-xewe-led-os.bin` | `c6_20_19_meta.json` | `c6_20_19_manifest.json` |

A flasher reads `https://github.com/<org>/<repo>/releases/download/<tag>/firmware_map.csv`, picks
a row and fetches its `_ASSET` or `_MANIFEST` from the same release. The newest is
`…/releases/latest/download/firmware_map.csv`: the release is marked latest only when its version
is at least every published non-pre-release (a re-hosted legacy release or a patch of an older line
never takes "latest"); a pre-release never is.

**Release card check.** The tagged tree must hold `releases/<v>/release_card.md` (written by
`scripts/release.sh --prepare`; `require_card: false` turns a missing card into a warning). The
`assemble` job runs `xewe release-card verify --ci --card releases/<v>/release_card.md
--junit-dir … --meta-dir …` with the chip jobs' JUnit and meta files first, on the pristine checkout
(before the release rows are downloaded and `xewe release --assemble` rewrites `xewe.toml`), so
before anything is published; a blocking difference (tree fingerprint, collected test ids, a CI
failure, a card PASS that fails in CI) fails the job. Core and modules have no tag workflow: their
`xewe-core-tests.yml` and `xewe-modules-tests.yml` have a `card` job. It installs the tools at
`tools_ref` and keeps each `releases/*/release_card.md` (and `modules/*/releases/*/release_card.md`)
of HEAD for which `xewe release-card fingerprint --card <card> --check` says the card's tested tree
is HEAD's tree (the tools' own fingerprint with the card's exclude list, doc/spec.md §14), that is,
the prepare commit; for each one it runs `verify --ci --unit-only` with the test job's results (CI
runs unit tests only, so the card's board rows are not compared; a module card is compared with
its own module's tests). On any other commit no card matches and the later steps are skipped.
Only `fingerprint --check`'s "another tree" result (exit 1 with `… is not the card's …`) skips a
card; anything else fails the job: a card whose data block (its last line) is missing or not
valid JSON is exit 2, so a malformed card can never turn the comparison off. The tools' own
`ci.yml` has the same `card` job over `releases/*/release_card.md`, with the tools installed from
the checkout (`pip install .`) and the pytest JUnit file of its `test` job (Python 3.12) as the CI
results (no `--meta-dir`: the tools build no firmware).

**The tools' own release** has no tag workflow: `scripts/release.sh` ([spec.md](spec.md) §11).
`--prepare <v>` runs the checks of ci.yml locally (per installed matrix Python: `pip install .`,
pytest with JUnit, pyflakes, mypy; then `xewe format --check`, `xewe brand-lint .`, actionlint),
each recorded with `xewe release-card record`, and renders `releases/<v>/release_card.md`. The
owner commits it ("prepare <v>"); on that commit ci.yml's `card` job compares the card with its
own pytest run. The tag run (`scripts/release.sh <v>`) runs `xewe release-card verify` on the
clean HEAD, waits for that ci.yml run to pass with the card job's `verify --ci` step run (not
skipped), then tags `<v>` and creates the GitHub Release with the card as its only asset.

**GitHub Pages.** Release download URLs answer with a redirect that may lack CORS headers, so a
browser flasher on another origin may not fetch them directly (to be checked before relying on
it). `xewe-pages.yml` rebuilds a static site from every release's `firmware-<v>.tar.gz`; enable it
with a `pages:` job after the release job. A Pages site may be 1 GB: 43 rows of 4 MiB images are
172 MiB per release.

**`publish_branch` (deprecated, opt-in).** `publish_branch: true` also commits
`static/firmware/releases/<v>/` (plus `index.json`, all versions newest first) to
`releases_branch` (`releases`, an orphan branch, by default; or `main`): one bot commit
`release <v> (<tag>, <sha7>) [skip ci]`. The commit goes through Git LFS: the job runs
`git lfs install --local`, the branch's `.gitattributes` decides what is stored in LFS, and the job
fails if a `.bin` it routes to LFS would be committed as a plain blob. Older binaries are not
downloaded (`GIT_LFS_SKIP_SMUDGE=1`). It puts binaries in git history; new callers leave it off
and drop `releases_branch`.

The release workflow needs `permissions: contents: write`, which the caller grants to that job only.

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
- Only with the deprecated `publish_branch: true`: leave `releases_branch` (`releases`, or `main`
  when the release commits there) unprotected, or give a ruleset a bypass for GitHub Actions: the
  release job pushes to it with `GITHUB_TOKEN`. Block force-pushes and deletion there.
- Product and template `.gitignore`: `/static/firmware/releases/*` and
  `!/static/firmware/releases/.gitkeep`, so the lint's no-committed-firmware check stays green.

## Making lint blocking

The project lint job (`xewe format --check`, `xewe brand-lint`, no committed firmware) is advisory until the
whitespace and format commits have landed in a repo. Then, in that repo's `ci.yml`, set
`strict_lint: true` under the `build` job's `with:`: the job is named `build / lint` (no
"(non-blocking)") and a failure fails the run; add it to the required checks if you want merges
gated on it. A step whose command the tools ref in `xewe.toml` does not have yet is skipped with
a notice.

The modules repo does the same with `strict_lint: true` under its `modules` job's `with:` once its
format commit has landed (today 12 of its 13 C/C++ files would change); its brand-lint already
blocks. Core and the tools have no switch: their format and brand-lint jobs always block.

