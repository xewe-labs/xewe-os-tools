# xewe-os-tools — specification

What the `xewe` command does, file by file and step by step. The README is the short user view;
this page is the reference the code is checked against. Sections are cited as §N in the code.

---

## 1. Goals / non-goals

**Goals**
- One Python package, one console script `xewe`, for setup, build, flash, serial, provision, test,
  boards, modules, manifest, format and release in a firmware project, and `brand-lint` anywhere. Same code on Linux and macOS, x86_64 and arm64.
- Zero sudo, zero system package managers: the toolchain lands once per machine in
  `~/.xewe-os/build-tools/` (`XEWE_HOME` overrides `~/.xewe-os`), everything else in `<project>/build/`
  (layout in §6).
- Reproducible: `xewe.toml` pins core, modules, tools; setup is idempotent and resumable.
- Relocatable: no absolute path into the project is stored; a project directory can be moved or renamed.
- No-board is first class: every hardware command compiles, then reports "compiled, not run" and
  exits 0, unless `--require-board`.
- Stable outputs: fixed FQBN options (the partition scheme is the only per-project one), merged-image name, `manifest.json` schema and
  `static/firmware/releases/<version>/` layout.

**Non-goals**
- OTA, flash sizes other than 4 MB, custom partition tables (a sketch's own `partitions.csv`),
  chips other than C3/C6/S3. The partition scheme is one of the esp32 core's 4 MB schemes
  (`[build] partition_scheme`, default `no_ota`; §4, §7).
- PlatformIO, on-device Unity tests.
- Windows (not yet; nothing may assume POSIX-only paths or `/dev/*` in core logic: `pathlib`
  everywhere, `.exe` suffixes in the cli and esptool lookups).
- Pushing, tagging or creating GitHub releases: `xewe release` prints those commands.

---

## 2. Package layout

`src/xewe/` is the `xewe` package (standard `src` layout); its modules are grouped in five areas.
Every area `__init__.py` is a docstring only (the areas import each other both ways, which is safe
only without eager re-exports); `xewe.testing` also holds `module_dir`.

```
xewe-os-tools/
├── pyproject.toml
├── README.md  LICENSE.txt  .gitignore
├── .agents/               the agent workspace (AGENTS.md, RULES.md, PREFERENCES.md, handoffs/, skills/)
├── .github/workflows/     ci.yml (this repo) and the reusable xewe-*.yml (doc/ci.md)
├── doc/                   spec.md (this page), ci.md, tests.md, webui.md
├── scripts/release.sh     releases the tools (README "Releasing"): --prepare, then tag and GitHub Release
├── releases/<version>/    notes.txt (GitHub Release text) and testcard.md (what was tested) per release
├── src/xewe/              the `xewe` package
│   ├── __init__.py        __version__ = "3.0.1"
│   ├── __main__.py        python -m xewe
│   ├── cli.py             argparse: subcommands, global flags, exit codes
│   ├── report.py          output helpers (status lines, --verbose, no-board summary), exit codes
│   ├── env/               xewe.env: the machine and the project
│   │   ├── setup.py       xewe setup steps + build_config.toml
│   │   ├── runscript.py   the generated run.sh (xewe run)
│   │   ├── doctor.py      environment checks
│   │   ├── clean.py       xewe clean
│   │   ├── fetch.py       HTTP download (urllib) with .part + resume + sha256; git clone/ls-remote wrappers
│   │   ├── arduino.py     arduino-cli install, env, core install (retry + staging fallback), compile argv
│   │   ├── esptool.py     locate core-bundled esptool, run write-flash / chip-id
│   │   ├── pins.py        versions shipped with this release (arduino-cli, esp32 core, URLs)
│   │   ├── project.py     find the root (xewe.toml, or library.properties for format), Paths (§6)
│   │   ├── config.py      build/config/build_config.toml: what setup installed and where
│   │   └── dotenv.py      settings and credentials from the dotenv file (§13)
│   ├── build/             xewe.build: firmware out of the project
│   │   ├── compile.py     compile, XeWeBuildInfo generation, builds/<chip>/out/ artifacts
│   │   ├── flash.py       xewe flash, xewe run
│   │   ├── release.py     release matrix, static/firmware/releases/<version>/
│   │   ├── releasecard.py xewe release-card: run record, redaction, tree fingerprint, the card (§14)
│   │   ├── format.py      xewe format: clang-format (C++) + ruff format (Python)
│   │   ├── clang-format   the XeWe C++ style (package data; format passes it as --style=file:)
│   │   ├── brandlint.py   xewe brand-lint
│   │   ├── webui.py       xewe webui: Flask UI prototype -> PROGMEM headers + sketch (doc/webui.md)
│   │   ├── webui-prompt.txt  the porting prompt (package data) that xewe webui prompt prints
│   │   ├── partitions.py  partition schemes of the installed esp32 core, app partition size (§7)
│   │   └── chips.py       c3/c6/s3 table: FQBN board, chip family, esptool id, board options
│   ├── board/             xewe.board: the attached board
│   │   ├── serialio.py    Console: open, reset, listen with timestamps, send/expect, boot-banner wait
│   │   ├── boards.py      port scan, VID:PID filter, esptool probe, boards.toml
│   │   └── provision.py   xewe provision: answer the first-boot prompts
│   ├── modules/           xewe.modules: modules and the manifest
│   │   ├── registry.py    registry of a modules checkout, resolve deps, generate build/modules/ + src/Modules.h + Config.h blocks, validate
│   │   ├── manifest.py    xewe manifest show / update
│   │   ├── lockfile.py    read/write xewe.toml (the manifest), defaults, validation
│   │   └── tomlw.py       minimal TOML writer (tomllib reads; stdlib has no writer)
│   └── testing/           xewe.testing
│       ├── __init__.py    module_dir(): the module folder of a module test
│       ├── plugin.py      pytest plugin: fixtures board/firmware/serial, markers, summary
│       └── runner.py      xewe test
└── tests/                 self-tests (§12, doc/tests.md), mirroring src/xewe/: tests/<area>/test_<module>.py
    ├── README.md          pointer to doc/tests.md
    ├── conftest.py        the only conftest: shared fixtures and helpers
    ├── fakes/             arduino-cli, esptool, clang-format (Python scripts) used by the self-tests
    ├── fixtures/          golden manifest.json, the six real module.properties, a copy of the template's setup.sh,
    │                      webui/ (the fan-dashboard Flask example and its golden export),
    │                      release_matrix_43.csv (a product's 43-row release matrix)
    ├── test_cli.py
    ├── env/  build/  board/  modules/  testing/
```

`pyproject.toml` (setuptools; the version is static and matches `xewe.__version__`):

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "xewe-os-tools"
version = "3.0.1"
requires-python = ">=3.11"
license = "GPL-3.0-only"
license-files = ["LICENSE.txt"]
dependencies = ["pyserial>=3.5", "pytest>=8.0"]

[project.optional-dependencies]
dev = ["ruff"]          # xewe format runs ruff on Python when it is importable

[project.scripts]
xewe = "xewe.cli:main"

[project.entry-points.pytest11]
xewe = "xewe.testing.plugin"

[tool.setuptools]
package-dir = {"" = "src"}

[tool.setuptools.packages.find]
where = ["src"]

[tool.setuptools.package-data]
"xewe.build" = ["clang-format", "webui-prompt.txt"]

[tool.mypy]
mypy_path = "src"
packages = ["xewe"]
```

Subpackages are found under `src/`; the style file and the webui prompt ship as package data. Editable installs
(`pip install -e .`) work. A non-editable install builds inside the source tree; `setup.sh`
deletes `<source>/build/` and `<source>/*.egg-info` first so a stale build tree cannot ship
deleted modules. `python -m mypy` and `python -m pyflakes src tests` run from the repo root.

**Dependencies.** stdlib (`tomllib`, `urllib`, `tarfile`, `zipfile`, `hashlib`, `subprocess`,
`argparse`, `json`, `shutil`) + `pyserial` + `pytest`.
- `pytest` is the firmware test runner, and the `pytest11` entry point is how module tests get
  fixtures without a conftest. All of pytest's dependencies are pure Python.
- `esptool` is not a package dependency and is not pip-installed. The pinned esp32 core ships an
  esptool binary for every host (`esptool_py` 5.3.1 for core 3.3.12, `aarch64-linux-gnu` build
  included). `esptool.py` locates it by glob:
  `<arduino data>/packages/esp32/tools/esptool_py/*/esptool` (the shared
  `~/.xewe-os/build-tools/arduino15` by default). `XEWE_ESPTOOL="<command>"` (e.g.
  `python -m esptool`) overrides it.
- Python ≥ 3.11 (for `tomllib`). CI runs 3.11, 3.12 and 3.13.

**`setup.sh`** belongs to the template repo (`xewe-os`); projects copy it. A thin bootstrap, no
logic beyond this:
1. Find `python3` ≥ 3.11, or exit with a message.
2. Read `[tools] ref` and `repo` from `xewe.toml` with `python3 -c 'import tomllib…'`.
3. Get the tools source: `$XEWE_TOOLS_SOURCE` (a local directory) or a checkout of `<repo>` in
   `build/tools`. The ref may be a tag (`git clone --depth 1 --branch <ref>`; skipped when
   `git -C build/tools describe --tags --exact-match` is that tag), a branch (cloned the same way,
   then `git fetch` + `git reset --hard origin/<ref>` on every run, so a moving `main` is picked up
   without tags), `latest` (the default branch from `git ls-remote --symref <repo> HEAD`, else
   `main`, then handled like that branch; prints `tools: latest -> main@<sha7>`) or a commit SHA
   (fetched and checked out once).
4. `python3 -m venv build/tools/.venv`; if `build/tools/.venv/bin/python -m pip --version` fails, run
   `build/tools/.venv/bin/python -m ensurepip --upgrade`.
5. `build/tools/.venv/bin/python -m pip install --quiet --upgrade <tools source dir>` (non-editable;
   skipped when the source is not local and `build/tools/.venv/xewe-tools-commit` holds the checkout's commit).
6. `exec build/tools/.venv/bin/python -m xewe --project <root> setup "$@"`.

**`run.sh`** is generated by `xewe setup` on every run (§6 step 11) and is never committed (the
template's `.gitignore` has `/run.sh`). In a project it is `RUN_SH` of `src/xewe/env/runscript.py`:
line 2 is `# generated by xewe setup; do not edit (xewe-os-tools <version>; ./setup.sh rewrites
it).`, then comments naming the manifest's default chip (`--chip C` overrides) and the
erase-by-default behaviour (every run erases the whole flash, NVS included, so the board boots as
new; `--keep-nvs` skips it), then `exec "${ROOT}/build/tools/.venv/bin/python" -m xewe --project
"${ROOT}" run "$@"` (with a "run ./setup.sh first" message, exit 3, if the venv is missing). Whatever
`run.sh` is there is replaced.

---

## 3. Command surface

Global flags (any position before the subcommand): `--project DIR` (default: nearest ancestor of
CWD containing `xewe.toml`; for `format` also `library.properties`, else the current directory), `--verbose` / `-v` (print every subprocess command line and its full
output; also accepted after the subcommand, so `xewe -v test` and `xewe test -v` are the same),
`--version`. Arguments for pytest itself go after `--` (`xewe test -- -vv -k wifi`).

Common flags: `--chip c3|c6|s3`, `--all-chips`, `--port PATH`, `--require-board`
(also `XEWE_REQUIRE_BOARD=1`), `--no-board` (also `XEWE_NO_BOARD=1`; flash, serial, provision,
test, run, boards), `--latest` (setup only).

**Exit codes (all commands)**

| Code | Meaning |
|---|---|
| 0 | success, including "compiled, not run" (no board, no `--require-board`) |
| 1 | failure of the operation: compile error, flash error, test failure, expect timeout, validation error |
| 2 | usage error (argparse, conflicting flags, unknown chip/module, a bad `--define`/`XEWE_TEST_DEFINES` item, `xewe release` without `--notes` and without a terminal) |
| 3 | not set up / tool missing (`build/config/build_config.toml` absent or stale, `src/Modules.h` or `build/modules/` missing, arduino-cli/esptool missing) |
| 4 | board access disabled (`--no-board` / `XEWE_NO_BOARD=1`) for a command that needs a board, board required but none found (`--require-board`; for `xewe test` too), explicit `--port` not present, several boards and none chosen, or the port did not come back after esptool reset the board |

`xewe release-card record` maps the exit of the command it records onto the same codes: 0 when it
exited 0, 1 when it exited non-zero, 3 when it could not be started; the command's own code (127
when it could not start) is in `run.json` and the `exit N` line (§14).

**Chip selection rule (build/flash/test/run):** `--chip` → chip of the single attached board
(§5) → `[project] chip` in `xewe.toml` → `c3`. `--all-chips` = c3, c6, s3 in that order.

**No-board status line** (exact text, grep-able): `compiled, not run: no board attached (<chip>, build/builds/<chip>/out/<bin>)`.

**Hard no-board switch:** `--no-board` or `XEWE_NO_BOARD=1` (also `true`/`yes`) disables board
access for the process: port discovery returns nothing (`boards.scan` lists nothing and writes
nothing, `boards.select` returns None even for `--port`/`XEWE_PORT`/`[override]`, `port_exists` is
false) and `Console.open` refuses to open a port (exit 4). `flash` and `run` still build, print
`compiled, not run: board access disabled (--no-board / XEWE_NO_BOARD) (<chip>, <bin>)` and exit 4;
`serial`, `provision` and `boards` (listing; `--set-port`/`--clear` still work) exit 4 with
`error: board access disabled (--no-board / XEWE_NO_BOARD)`. `xewe test` treats it as "no board
attached": board tests compile and report compiled-not-run, exit 0 (4 with `--require-board`).
Agents in compile-only mode must set `XEWE_NO_BOARD=1`.

| Command | Synopsis | What it does | No board |
|---|---|---|---|
| `xewe setup` | `[--latest] [--modules LIST\|all\|none] [--force] [--core-source DIR] [--modules-source DIR] [--arduino-data DIR]` | §6. Installs the shared toolchain (once per machine) and everything else into `build/`, runs `modules generate`, writes `run.sh`; prints the dotenv hint (§13) when no file is found | n/a (never needs a board) |
| `xewe build` | `[--chip C \| --all-chips] [--define KEY=VALUE]... [--clean]` | §7. Compiles; writes `build/builds/<chip>/out/` | n/a; exit 0 on success |
| `xewe flash` | `[--chip C] [--port P] [--baud 921600] [--erase] [--no-build] [--define KEY=VALUE]... [--require-board] [--no-board]` | Builds if `builds/<chip>/out` is missing, older than any source, or was built with other `--define` values/version/chip/FQBN options (`build.stamp`, §7), then §8 write-flash. The defines are `--define`, else `XEWE_TEST_DEFINES` (as `xewe test`, §9), so it flashes the image a later `xewe test` with the same defines reuses | prints status line, exit 0 (4 with `--require-board`) |
| `xewe serial` | `[--port P] [--baud 115200] [--reset] [--send CMD [--expect RE] [--timeout 10] [--boot-timeout 90]] [--duration S] [--no-input] [--timestamps] [--log FILE] [--require-board] [--no-board]` | Listen with timestamps until Ctrl-C/`--duration` (on a terminal without `--no-input`: interactive, typed lines are sent, board lines printed raw unless `--timestamps`; Ctrl-D also exits) (opening the port resets the board on native USB; the boot log is shown), or wait for the boot to finish, send one command and wait for a regex (§8) | `no board attached; nothing to listen to`, exit 0 (4 with `--require-board`) |
| `xewe provision` | `[--port P] [--name NAME] [--modules all\|none\|LIST] [--timezone GMT+HH:MM] [--env\|--from FILE] [--timeout 60] [--no-reset] [--log FILE] [--no-board]` | §8, §13. Resets the board and answers its first-boot prompts (name, modules, Wi-Fi, timezone); exit 0 also when already provisioned, 2 bad/missing settings (checked before the port is opened), 1 prompt timeout or unexpected sequence | `no board attached; nothing to provision`, exit 4 |
| `xewe test` | `[--chip C \| --all-chips] [--port P] [--module SLUG]... [--unit-only] [--no-flash] [--define KEY=VALUE]... [--require-board] [--no-board] [-v] [-- PYTEST_ARGS]` | §9. Runs pytest over project and selected-module tests; builds (when stale for the defines) and flashes with `--define`, else `XEWE_TEST_DEFINES` | board tests compile and report compiled-not-run, exit 0 |
| `xewe run` | `[--chip C] [--port P] [--define K=V]... [--keep-nvs] [--no-serial] [--no-input] [--timestamps]` | build → erase-flash (default; NVS too, so every run is a true first boot; `--keep-nvs` skips it) → flash → serial, interactive on a terminal (what `run.sh` calls). The `flashed` line ends with `(flash erased: first boot)` or `(nvs kept)` | builds, prints status line, exit 0 |
| `xewe boards` | `[--no-probe] [--json] [--set-port P [--set-chip C]] [--clear] [--no-board]` | §5. Lists candidate ports and chips; writes `build/config/boards.toml` | `no board attached`, exit 0 (4 with `--require-board`) |
| `xewe modules list` | `[--json]` | Modules in the modules checkout: slug, id, version, deps, `*` if selected | — |
| `xewe modules select` | `LIST\|all\|none [--no-generate]` | Writes `[modules] selected` in `xewe.toml`, then generates | — |
| `xewe modules validate` | `[PATH]` | §10 rules over a modules repo checkout (default: the one setup uses, `[paths] modules`) | — |
| `xewe modules generate` | | §10. Rebuilds `build/modules/` and `src/Modules.h` from the manifest selection | — |
| `xewe manifest show` | `[--json]` | Manifest refs vs installed refs (from `build_config.toml`), drift marked `!` | — |
| `xewe manifest update` | `[core\|modules\|tools]... [--to REF]` | Resolves newest tag (§4) and rewrites `xewe.toml` (so it freezes a `latest` ref; `--to latest` tracks the default branch again); prints diff; does not run setup | — |
| `xewe clean` | `[--all] [--modules]` | Default: delete `build/builds/` and `build/tmp/`. `--all`: delete all of `build/` except `tools/` (the tools checkout and its venv; setup must re-run). `--modules`: also delete `build/modules/` and `src/Modules.h`. Never touches `~/.xewe-os/build-tools` (toolchain and shared modules checkouts) | — |
| `xewe format` | `[--check] [PATH...]` | clang-format the project's (or library's, or the current directory's) C++ and ruff-format its Python (see below). `--check` changes nothing and exits 1 listing `would reformat <file>` | n/a |
| `xewe brand-lint` | `[DIR...]` | Banned spellings of the brand (see below). Needs no project | n/a |
| `xewe webui export` | `FLASK_DIR OUT_DIR` | [webui.md](webui.md). Writes each `.html`/`.css`/`.js` of `templates/` and `static/` as `<stem>_<ext>.h` (PROGMEM raw literal, `url_for('static', ...)` rewritten), copies `app.py` and generates `<name>_app.ino` (one GET route per file) into `OUT_DIR/<name>_<YYYYmmdd_HHMMSS>/`; last line `webui: N headers and <name>_app.ino [+ app.py] in <dir>`. Exit 2 when `FLASK_DIR` is not a directory, 1 when two files map to one array name. Needs no project | n/a |
| `xewe webui prompt` | | Prints the LLM prompt that ports `app.py` into the generated sketch (`src/xewe/build/webui-prompt.txt`) | n/a |
| `xewe doctor` | | Checks python, git, venv, the shared toolchain (`XEWE_HOME` resolved; cli and core present), arduino-cli version, core version, esptool, manifest (`xewe.toml`) vs installed, free disk ≥ 6 GB, serial permissions (Linux: user in `dialout`/`uucp`), Rosetta 2 on Apple silicon (the esp32 core's `ctags` is x86_64: "bad CPU type" without it; probe `pgrep -q oahd` or `arch -x86_64 /usr/bin/true`; warns with `softwareupdate --install-rosetta --agree-to-license`), board scan | exit 0 if only warnings |
| `xewe release` | `--version X.Y.Z [--matrix FILE] [--notes FILE] [--chip C \| --assemble]` | §11; without `--notes` and without a terminal (or with `XEWE_NONINTERACTIVE=1`) exit 2 | n/a (no board needed) |
| `xewe release-card init` | `--dir D --repo OWNER/NAME --version X.Y.Z [--tag T] [--exclude DIR]... [--resume] [--root DIR]` | §14. Starts a run: empties `D` (only a previous run folder of the same repo and version inside the work tree; anything else is exit 2 with nothing deleted) and writes `D/run.json` (run id, base commit, tested-tree fingerprint); `--resume` keeps the run when commit and tree are unchanged | n/a |
| `xewe release-card record` | `--dir D --kind setup\|validate\|unit\|board\|flash\|build\|check [--chip C] [--name LABEL] [--junit PATH] [--format core-host] [--firmware BIN] [--meta FILE] [--resume] [--root DIR] -- CMD...` | §14. Runs `CMD`, writes its redacted output to `D/<n>-<kind>[-<chip>][-<LABEL>].log`, adds the step to `run.json` (with `CMD`'s own exit code); exits 0 when `CMD` exited 0, 1 when it exited non-zero, 3 when it cannot start. `unit` and `board` need `--junit` or `--format` (exit 2) | n/a |
| `xewe release-card render` | `--dir D --notes FILE --out FILE [--chips LIST] [--hardware LIST] [--require KINDS] [--title NAME] [--tests PREFIX]... [--root DIR]` | §14. Writes the card, or refuses: exit 1, reasons on stderr, `D/release_card.draft.md` | n/a |
| `xewe release-card verify` | `--card FILE [--dir D] [--root DIR] [--ci --junit-dir D [--meta-dir D] [--size-tolerance P] [--unit-only]]` | §14. Tag run: the card as `render` wrote it (`card_sha256`), clean tree with the card's fingerprint, local logs with their sha256, no `testcard.md`; `--ci`: the CI comparison (`--unit-only`: core and modules CI). A card without a valid data block on its last line is exit 2 | n/a |
| `xewe release-card fingerprint` | `[--card FILE [--check]] [--exclude DIR]... [--root DIR]` | §14. Prints the tested-tree fingerprint; `--card --check` exits 1 only when the tree is another one (the CI card jobs skip that card), 2 when the card has no valid data block on its last line | n/a |

**`xewe format`** (`src/xewe/build/format.py`). AI-first style: nothing is column-aligned, because runs
of alignment whitespace cost tokens, break exact-match edits and turn a one-line change into a
realigned block. Files: `*.h *.hpp *.c *.cpp *.ino` directly in the project root (the sketch,
`Config.h`) and under `src/`, `examples/`, `tests/unit/` (recursive; never `build/` at any depth,
hidden or `__pycache__` directories, nor the generated `src/Modules.h`); `PATH` arguments (files or
directories) replace that set. The root is the project or library; in a directory that is neither
(the tools or modules repo) it is the current directory. Style: `src/xewe/build/clang-format`, the
XeWe style shipped with the tools as package data, passed as `--style=file:<path>` (`format.STYLE_FILE`); a
`.clang-format` in the project is not read, and no repo carries one. The rules: LLVM base,
`IndentWidth: 4`, `ContinuationIndentWidth: 4`, `AccessModifierOffset: -4`, `ColumnLimit: 100`,
`UseTab: Never`, every `AlignConsecutive*: None`, `AlignArrayOfStructures: None`,
`AlignEscapedNewlines: DontAlign`, `AlignTrailingComments: false`, `AlignOperands: DontAlign`,
`AlignAfterOpenBracket: DontAlign`, `BreakBeforeBraces: Attach`, `PointerAlignment: Left`,
`DerivePointerAlignment: false`, `AllowShortFunctionsOnASingleLine: Empty`, `SortIncludes: false`
(include order matters for Arduino). Keys are limited to those clang-format 14 understands
(`--style=file:<path>` needs 14 or newer). clang-format binary: `XEWE_CLANG_FORMAT`, else `clang-format` on `PATH`, else in
the tools venv (`pip install clang-format`), else one inside the shared toolchain's esp32 core
(`build-tools/arduino15/packages/esp32/tools/*/…/bin/clang-format`; core 3.3.12 ships none), else
exit 3 with the install hint. Each file is formatted to stdout and rewritten (mode kept) only when
it differs. Python (`*.py` in the root, `src/`, `examples/`, `tests/`; a nested `build` folder is
kept here, since it can be a package such as `xewe.build`, only the root `build/` is skipped):
`ruff format [--check]` when ruff is importable in the tools venv (`pip install
'xewe-os-tools[dev]'`), else skipped with an info line. The files to change are read from both
`--check` outputs (`Would reformat: <f>` of older ruff, `--> <f>:L:C` of ruff 0.17); a
failing check that names no file is an error. Last line: `format: N C++ and M Python files, K
formatted|would reformat`.

**`xewe brand-lint [DIR...]`** (`src/xewe/build/brandlint.py`). Scans every text file under each
`DIR` (default `.`; a file argument is scanned as is) for the banned spellings of
`xewe-os/doc/naming.md` (`brandlint.PATTERN`, case sensitive): the brand with only its first
letter capitalised and a letter right after it, the lowercase brand followed by the word `os`, and
the brand with only its first letter capitalised followed by ` OS`. Inside a git work tree the
files are those git does not ignore (`git ls-files --cached --others --exclude-standard`), so
caches never count; elsewhere the directory is walked. Skipped either way: the directories `.git`,
`build`, `.venv`, `static`, `__pycache__`, `history`, `test_logs`, `.pytest_cache`, `.mypy_cache`,
`.ruff_cache` and `*.egg-info`; symlinks; binary files (a NUL byte or not UTF-8); any
file named `naming.md` in any case (the naming page lists the banned spellings as examples); the
dotenv file and `<dotenv file>.*` (their lines are never printed). Clean: `brand-lint: clean` on
stdout, exit 0. Hits: `brand-lint: banned spellings found (see xewe-os/doc/naming.md):` and one
`<path>:<line>:<text>` per hit on stderr, exit 1. A `DIR` that does not exist: exit 2. Every
repo's CI runs `python -m xewe brand-lint .` (doc/ci.md).

Example outputs (text is normative in spirit, not byte-exact, except the status lines):

```
$ xewe build --chip c3
build  c3  esp32:esp32:esp32c3:CDCOnBoot=cdc,CPUFreq=160,…,UploadSpeed=921600
       sketch xewe-os (2.0.15)  modules wifi, web-interface  core 1.0.0
ok     c3  51 s  Sketch uses 1123456 bytes (85%)  0 warnings
       build/builds/c3/out/2.0.15-c3-xewe-os.bin

$ xewe flash
build  c3  up to date (build/builds/c3/out/2.0.15-c3-xewe-os.bin)
compiled, not run: no board attached (c3, build/builds/c3/out/2.0.15-c3-xewe-os.bin)
$ echo $?
0

$ xewe flash --require-board
compiled, not run: no board attached (c3, build/builds/c3/out/2.0.15-c3-xewe-os.bin)
error: --require-board: no board attached
$ echo $?
4

$ xewe serial --send '$system status' --expect 'Uptime' --timeout 5
23:41:07.412  > $system status
23:41:07.530  Uptime: 00:03:12
match  'Uptime' after 0.12 s
```

---

## 4. `xewe.toml` format (the project manifest)

TOML, committed, the only dependency file the template commits. It is authored, not
generated: the project's name, version and chip, the core/modules/tools refs, the module selection
and extra libraries; `setup.sh` reads it to know what to fetch, so a clone cannot set up without it.
The resolved commits live in `build/config/` (generated). Read with `tomllib`, written by `tomlw.py`
(keys in the order below, comments preserved only for the header comment block).

```toml
# xewe.toml: this firmware's manifest (pinned inputs). Edit by hand or with `xewe manifest update`.
# ./setup.sh installs exactly these refs into build/. A tag freezes; ref = "latest" tracks the newest
# commit of the default branch (development); `xewe manifest update` freezes it at the newest tag.
schema = 1

[project]
name = "xewe-os"        # optional; default: stem of the single .ino in the project root
version = "2.0.15"      # firmware version (§7)
chip = "c3"             # chip used when --chip is not given and no board is attached

# [build]               # optional
# partition_scheme = "huge_app"   # FQBN PartitionScheme (§7); default "no_ota"

[core]
repo = "https://github.com/xewe-labs/xewe-os-core"
ref = "3.0.0"           # tags are X.Y.Z, no v (git-and-releases.md)

[modules]
repo = "https://github.com/xewe-labs/xewe-os-modules"
ref = "3.0.0"
selected = ["wifi", "web-interface"]   # dependencies are added at generate time, not written here

[tools]
repo = "https://github.com/xewe-labs/xewe-os-tools"
ref = "3.0.0"

[libraries]             # extra Arduino libraries, cloned into build/libraries/<name>
ArduinoJson = { repo = "https://github.com/bblanchon/ArduinoJson", ref = "v7.4.3" }

# [toolchain]           # optional overrides; defaults come from the tools release (src/xewe/env/pins.py)
# arduino_cli = "1.5.1"
# esp32 = "3.3.12"
```

Rules:
- `ref` is a tag (preferred) or a branch or a full commit SHA or `latest`. Setup records the resolved commit in
  `build/config/build_config.toml`. For `[tools]`, `setup.sh` follows a branch's remote head on every run (§2).
- **Tags freeze, `latest` tracks.** `ref = "latest"` (accepted in `[core]`, `[modules]`, `[tools]` and
  every `[libraries]` entry) means the newest commit on the repository's default branch, resolved
  with `git ls-remote --symref <repo> HEAD` (no symref reported: `main`). It is for development:
  every setup resolves it again and brings the checkout to that head (fetch + reset, like a branch;
  nothing is fetched when the checkout already is at that commit; an unreachable remote keeps the
  installed checkout with a warning, and fails only when nothing is installed yet). Setup prints
  `core: latest -> main@<sha7>` and records `{ ref = "latest", branch = "main", commit = "<sha>", source = ... }`;
  the modules checkout is `sources/xewe-os-modules/latest/`. `xewe manifest show` prints
  `latest (main@<sha7>)` in the installed column and marks drift (`!`, with `remote main@<sha7>`) when
  the remote head moved since setup (one `ls-remote` per `latest` entry; unreachable = no drift, a
  warning). To freeze tested code, `xewe manifest update [NAME]` replaces `latest` with the newest tag
  (or `--to X.Y.Z`); `--to latest` sets it back. A local source (`XEWE_*_SOURCE`, `--*-source`) still wins.
- `--latest` (on `xewe setup`): for each of core/modules/tools, run `git ls-remote --tags --refs <repo>`,
  keep tags matching `^v?\d+\.\d+\.\d+$`, pick the highest by numeric tuple; if none, use the remote
  default branch HEAD with a warning. Installs those, **never edits `xewe.toml`**;
  `xewe manifest show` and `xewe doctor` then show the drift (`!`). `[libraries]` and `[toolchain]` are not
  affected by `--latest`.
- `xewe manifest update [core|modules|tools] [--to REF]` applies the same resolution (or `--to`) and
  rewrites the manifest; that is the only command that changes refs.
- `[modules] ref` is checked out once per machine and ref in `~/.xewe-os/build-tools/sources/xewe-os-modules/<ref>/`
  (§6 step 8); a branch ref (and `latest`, in `.../latest/`) is fetched and reset to the remote head on every setup, like `[tools]`.
- Local sources (`--core-source DIR`, `--modules-source DIR`, `XEWE_TOOLS_SOURCE`, and env
  `XEWE_CORE_SOURCE`, `XEWE_MODULES_SOURCE`) override `repo`+`ref` for one run, are recorded as
  `source = "local:<abs path>"` in `build_config.toml` (that file is not committed), and never touch the manifest.
- `[build] partition_scheme` (optional, default `no_ota`) is the FQBN `PartitionScheme` option of every
  build, flash, run, test and release compile (§7). The manifest only checks its shape (a non-empty id of
  letters, digits and `_`, exit 2 otherwise); `xewe build` checks it against the installed esp32 core.
  Without the key the FQBN, `build.stamp` and the size limit are those of `no_ota`, as before.
  `xewe release` and `xewe manifest update` keep the table when they rewrite the manifest.
- Unknown keys are an error (exit 2) so typos do not silently fall back to defaults.

---

## 5. `build/config/boards.toml`

Generated by any command that scans for boards; not committed; the `[override]` table is preserved
across rewrites.

```toml
# generated by xewe; [override] is yours and is kept
schema = 1

[override]              # optional; set with `xewe boards --set-port /dev/ttyACM0 --set-chip c3`
port = "/dev/ttyACM0"
chip = "c3"

[[board]]
port = "/dev/ttyACM0"
chip = "c3"
vid = "303a"
pid = "1001"
serial_number = "F0:F5:BD:01:23:45"
description = "USB JTAG/serial debug unit"
detected_by = "esptool"          # esptool | override | cache
last_seen = "2026-10-08T12:00:00Z"
```

**Detection** (`boards.scan()`):
1. `serial.tools.list_ports.comports()`. Keep ports whose VID:PID is a known ESP USB interface:
   `303a:*` (Espressif native USB-Serial/JTAG and USB-OTG CDC; C3, C6, S3 all report `303a:1001`),
   `10c4:ea60` (CP210x), `1a86:7523` (CH340), `1a86:55d3` (CH343), `0403:6001/6010/6015` (FTDI).
   Ports with no VID (e.g. `/dev/ttyS*`) are dropped. `--port P` bypasses the filter.
2. Chip: VID:PID cannot tell C3/C6/S3 apart, so look up `serial_number` in the previous
   `boards.toml` (`detected_by = "cache"`); otherwise probe with the core esptool:
   `esptool --port P --before default-reset --after hard-reset chip-id` (timeout 20 s), parse
   `ESP32-(C3|C6|S3)` from its output. Probing resets the board; `--no-probe` skips it (chip stays
   unknown unless overridden). A chip outside C3/C6/S3 is reported and treated as unsupported.
3. `arduino-cli board list --format json` is not used for decisions (it only knows VID:PID); `xewe
   boards --verbose` and `xewe doctor` print it as a cross-check.

**"No board"** = after the filter there are zero candidate ports, no `--port`, and no
`[override] port` present on the system. Exactly one candidate → that board. Several → pick the one
matching `--chip` if unique, else exit 4 "several boards attached; pass --port" (one board at
a time). An explicit `--port` that does not exist is exit 4, never "no board" (catches typos).

**User override**, highest first: `--port`/`--chip` flags → `XEWE_PORT`/`XEWE_CHIP` env →
`[override]` in `boards.toml` → scan.

---

## 6. Setup procedure (`xewe setup`)

Target layout (the single description of it; README and AGENTS point here). The toolchain is
shared by every project on the machine; everything under `build/` is generated and disposable
(`build/.gitignore` is written as `*`), and `src/Modules.h` is generated too (git-ignored by the
template):

```
~/.xewe-os/build-tools/              shared toolchain, once per machine; XEWE_HOME overrides ~/.xewe-os
├── arduino15/                       ARDUINO_DIRECTORIES_DATA: indexes, esp32 core(s), toolchains, esptool
├── bin/arduino-cli-<version>        one binary per pinned arduino-cli version
├── arduino-user/                    ARDUINO_DIRECTORIES_USER: empty sketchbook (isolates ~/Arduino/libraries)
├── downloads/                       ARDUINO_DIRECTORIES_DOWNLOADS + arduino-cli archives (XEWE_CACHE overrides)
├── sources/xewe-os-modules/<ref>/   modules repo checkout per [modules] ref (ref sanitised: / -> _; a SHA is its own folder)
└── .lock                            fcntl.flock while setup installs the cli, the core or a source checkout

<project>/
├── <name>.ino  xewe.toml  setup.sh ...
├── run.sh                           generated by setup on every run, not committed (§2)
├── .env                             optional dotenv file; yours, never written by the tools (§13)
├── Config.h                         yours; generate appends one block per module with knobs (§10)
├── src/Modules.h                    generated: #include <XeWeModules.h> + the declare lines (§10)
└── build/
    ├── builds/<chip>/gen/XeWeBuildInfo/   generated per build (§7)
    ├── builds/<chip>/cache/         arduino-cli --build-path
    ├── builds/<chip>/out/           artifacts: <bin>, manifest.json, meta.json, compile.log, build.stamp
    ├── config/build_config.toml     what setup installed (below)
    ├── config/boards.toml           §5
    ├── libraries/XeWeCore/          core at [core] ref
    ├── libraries/ArduinoJson/       [libraries]
    ├── modules/                     generated from the selected modules (§10):
    │   ├── library.properties       the Arduino library XeWeModules (arduino-cli reads only this and src/)
    │   ├── src/XeWeModules.h  src/<Folder>/
    │   ├── tests/<slug>/{board,unit}/   each selected module's tests (what `xewe test` collects)
    │   └── modules.lock
    ├── tools/                       tools source at [tools] ref (setup.sh); tools/.venv: python venv
    └── tmp/                         only on demand: sketch mirror (tmp/sketch/<stem>), modules staging, pytest cache
```

`build/tmp/` is not created by setup; the code paths that use it create it, `xewe clean` (default
and `--all`) removes it, and `xewe modules generate` removes it again when its staging leaves it
empty.

The arduino-cli environment is computed on every invocation (nothing stored):

```
ARDUINO_DIRECTORIES_DATA=~/.xewe-os/build-tools/arduino15        # or --arduino-data / XEWE_ARDUINO_DATA
ARDUINO_DIRECTORIES_USER=~/.xewe-os/build-tools/arduino-user
ARDUINO_DIRECTORIES_DOWNLOADS=${XEWE_CACHE:-~/.xewe-os/build-tools/downloads}
ARDUINO_BOARD_MANAGER_ADDITIONAL_URLS=https://espressif.github.io/arduino-esp32/package_esp32_index.json
ARDUINO_NETWORK_CONNECTION_TIMEOUT=300s
ARDUINO_UPDATER_ENABLE_NOTIFICATION=false
```

No build cache is shared between projects: with `--build-path` arduino-cli ≥ 1.0.4 ignores its
global build cache (`build_cache.path`, `ARDUINO_BUILD_CACHE_PATH`) and builds everything, core
included, in the given path (arduino-cli `docs/UPGRADING.md`, 1.0.4; `--build-cache-path` is
deprecated). Recompiling only the core costs about 4 s on c3; the libraries are the cost of a
fresh build path.

No `arduino-cli.yaml` is written; `~/.arduino15` and `~/Arduino` are never read or written.

Steps (each is skipped when its record in `build_config.toml [installed]` matches the wanted
version/ref and its artefact exists; `--force` redoes all). Step 0 removes `build_config.toml`
and writes `build/.gitignore` = `*`.

| # | Step | Detail |
|---|---|---|
| 1 | Preflight | Need `git`; Python ≥ 3.11 (already true, we run in it); free disk ≥ 6 GB where the core goes. No package-manager installs: missing `git` is exit 3 with a hint. Creates `build/config/`. |
| 2 | Read manifest | Validate `xewe.toml`; resolve refs (`--latest` per §4). |
| 3 | arduino-cli | Map host: Linux x86_64 → `Linux_64bit`, Linux aarch64 → `Linux_ARM64`, Darwin arm64 → `macOS_ARM64`, Darwin x86_64 → `macOS_64bit`. Download `https://github.com/arduino/arduino-cli/releases/download/v<V>/arduino-cli_<V>_<plat>.tar.gz`, verify sha256 against `…/v<V>/<V>-checksums.txt`, extract `arduino-cli` to the shared `bin/arduino-cli-<V>` (skipped when it is there and reports `<V>`, so a second project downloads nothing), check its `version` contains `<V>`. Creates `build-tools/{arduino15,bin,arduino-user,downloads}`; steps 3-4 hold an exclusive `fcntl.flock` on `build-tools/.lock`, so two projects setting up at once do not install over each other. `XEWE_ARDUINO_CLI` replaces the download. |
| 4 | esp32 core | Into the shared `arduino15` (or `--arduino-data` / `XEWE_ARDUINO_DATA`), skipped when `core list` already shows the pinned version; several core versions coexist there. `arduino-cli core update-index`, then `arduino-cli core install esp32:esp32@<esp32 version>`. Retry up to 5 times with 10/20/40/80 s backoff. If output contains `Head "<url>"` (a connection reset on a large archive), download that URL with urllib into `$ARDUINO_DIRECTORIES_DOWNLOADS/packages/` and retry (max 10 such rescues). Verify with `arduino-cli core list --format json` (esp32:esp32 at the pinned version). |
| 5 | esptool | Locate core-bundled esptool (§2); run `esptool version` to confirm it executes on this host. |
| 6 | Core library | `git clone --quiet --depth 1 --branch <ref> <repo> build/libraries/XeWeCore` (a SHA ref: clone + `git fetch --depth 1 origin <sha>` + checkout). Clone into `build/libraries/.tmp-XeWeCore` then rename (atomic). Keep `.git` (needed for commit record and `--latest`; arduino-cli ignores it). Local source: copy tree without `.git`. Wrong ref present → delete and re-clone. |
| 7 | Libraries | Runs after step 10 (it needs the module selection). Same as 6 for every `[libraries]` entry of the manifest, plus every `depends_libraries` name of the resolved selected modules, looked up in the modules checkout's `libraries.toml` catalogue (`[Name] repo = "...", ref = "..."`), into `build/libraries/<name>`. **The manifest wins:** a name pinned in `[libraries]` is installed from the manifest and the catalogue entry is ignored. Each module library is logged as `library FastLED: from modules catalogue (3.10.3)` or `library FastLED: from xewe.toml (<ref>)`; a name in neither is a warning and not installed; `XeWeCore`/`XeWeOS` (the core) and a `(>=x.y.z)` suffix are ignored. The catalogue never edits `xewe.toml`. Recorded in `[installed] libraries.<name>` with `origin = "xewe.toml"` or `"modules catalogue"`; `xewe manifest show` lists these rows with their origin. |
| 8 | Modules repo | Outside the project, once per machine and ref: `~/.xewe-os/build-tools/sources/xewe-os-modules/<ref>/` (`Paths.modules_checkout(ref)`), under the `build-tools/.lock` flock. Cloned as in 6 when missing (or with `--force`, or when its `origin` is another repo); a branch checkout is fetched and reset to the remote head on every setup (a failed fetch keeps it, with a warning); a tag or SHA is never fetched again. `XEWE_MODULES_SOURCE` / `--modules-source` is used where it is, nothing copied. `[paths] modules` records the absolute path; `modules list/select/validate`, the `libraries.toml` catalogue and `manifest show` read it. `clean --all` never touches it. |
| 9 | Module selection | `--modules LIST\|all\|none` writes `[modules] selected` (this is a manifest edit the user asked for; `--modules ""` = `none`). Zero modules is a valid project and `selected = []` is the default. If `selected` is empty and stdin is a TTY: a numbered menu on stderr, where an empty answer means none; no TTY and empty → no prompt. Either way an empty selection prints one `warning:` line ("no modules selected (selected = [] in xewe.toml) …") naming `./setup.sh --modules LIST` and `xewe modules select LIST`, and continues; `xewe build` and `xewe run` print the same warning while the selection is empty. If the modules checkout holds no module at all (neither `modules/<slug>/module.properties` nor `xewe-os-module-<slug>/module.properties`), setup warns naming the source path and both layouts; explicitly requested modules that are not found are still exit 2. |
| 10 | Generate | `xewe modules generate` (§10). |
| 11 | Project files | Check exactly one `*.ino` in root and `Config.h` exist (do not create them: the template owns them). Do not touch the root `.gitignore`. A legacy `build/version_state` prints the `version = "M.m.P"` line for `[project]` (the file is left alone). Write `run.sh` (mode 755) from `RUN_SH` (§2: installed tools version, `[project] chip`) on every run, replacing whatever is there (the template git-ignores `/run.sh`). Never write a dotenv file; after step 12, when neither `XEWE_ENV` nor a project or tools-checkout file is found, log `dotenv.hint()` (§13). |
| 12 | build_config.toml | Written last, atomically (`.tmp` + rename). |

`build/config/build_config.toml` (paths inside `build/` are relative to `build/`; the shared toolchain
and the `--arduino-data` / `XEWE_ARDUINO_CLI` overrides are absolute):

```toml
# generated by xewe setup; do not edit
schema = 1
tools_version = "3.0.1"
setup_completed = "2026-10-08T12:00:00Z"

[paths]                  # relative to build/, or absolute outside it
arduino_cli = "/home/me/.xewe-os/build-tools/bin/arduino-cli-1.5.1"
arduino_data = "/home/me/.xewe-os/build-tools/arduino15"
arduino_user = "/home/me/.xewe-os/build-tools/arduino-user"
libraries = "libraries"
modules = "/home/me/.xewe-os/build-tools/sources/xewe-os-modules/3.0.0"   # or XEWE_MODULES_SOURCE
esptool = "/home/me/.xewe-os/build-tools/arduino15/packages/esp32/tools/esptool_py/5.3.1/esptool"

[installed]
arduino_cli = "1.5.1"
esp32 = "3.3.12"
core = { ref = "3.0.0", commit = "0123abc…", source = "https://github.com/xewe-labs/xewe-os-core" }
modules = { ref = "3.0.0", commit = "…", source = "…" }
tools = { ref = "3.0.1", commit = "…", source = "local:../xewe-os-tools" }
# a `latest` ref: core = { ref = "latest", branch = "main", commit = "…", source = "…" }
libraries = { ArduinoJson = { ref = "v7.4.2", commit = "…", source = "…", origin = "xewe.toml" } }
```

Relocation: `project.py` derives the root from the CWD or `--project` on every run, and every
stored path into the project is relative to `build/` (only the shared toolchain, which does not
move with the project, is absolute). A moved venv has stale shebangs, so `run.sh` and `setup.sh`
call `build/tools/.venv/bin/python -m xewe`, not the `xewe` script.

**Duration** (estimate): arduino-cli ~30 MB; esp32 core 3.3.12 for
aarch64 Linux is ~1.7 GB of downloads (computed from the package index: `esp-rv32` 556 MB,
`esp-x32` 314 MB, nine `*-libs` packages ~630 MB, esptool 72 MB, gdbs ~80 MB, core zip 51 MB) and
several GB on disk (the core also installs the c5/h2/p4/s2 libraries, about 370 MB of archives). Expect 5–15 min on broadband for the first setup on a machine, < 10 s for a no-op
re-run, and no toolchain download for a second project (the toolchain is shared).

**Idempotent and resumable**: per-step records (above); downloads go to `<file>.part` with HTTP
`Range` resume and are renamed only after the checksum matches; clones go to a temp name then
rename; `arduino-cli core install` is itself resumable from the downloads dir; `build_config.toml`
is written last, so an interrupted setup is detected (exit 3 from other commands: "setup incomplete,
re-run ./setup.sh").

---

## 7. Build procedure (`xewe build`)

Chip table (`chips.py`):

| chip | FQBN board | chipFamily (manifest) | esptool `--chip` |
|---|---|---|---|
| c3 | `esp32c3` | `ESP32-C3` | `esp32c3` |
| c6 | `esp32c6` | `ESP32-C6` | `esp32c6` |
| s3 | `esp32s3` | `ESP32-S3` | `esp32s3` |

Board options:
`CDCOnBoot=cdc,CPUFreq=160,DebugLevel=none,EraseFlash=all,FlashMode=qio,FlashSize=4M,PartitionScheme=<scheme>,UploadSpeed=921600`

`<scheme>` is `xewe.toml [build] partition_scheme`, `no_ota` without the key (`chips.board_options`,
`Chip.fqbn_for`). Full FQBNs with the default:
```
esp32:esp32:esp32c3:CDCOnBoot=cdc,CPUFreq=160,DebugLevel=none,EraseFlash=all,FlashMode=qio,FlashSize=4M,PartitionScheme=no_ota,UploadSpeed=921600
esp32:esp32:esp32c6:CDCOnBoot=cdc,CPUFreq=160,DebugLevel=none,EraseFlash=all,FlashMode=qio,FlashSize=4M,PartitionScheme=no_ota,UploadSpeed=921600
esp32:esp32:esp32s3:CDCOnBoot=cdc,CPUFreq=160,DebugLevel=none,EraseFlash=all,FlashMode=qio,FlashSize=4M,PartitionScheme=no_ota,UploadSpeed=921600
```

Partition scheme (`partitions.py`): the installed esp32 core
(`<arduino data>/packages/esp32/hardware/esp32/<esp32 version>/`) decides which schemes exist.
`boards.txt` lists, per FQBN board, `menu.PartitionScheme.<id>.build.partitions=<csv stem>`; a scheme
is usable for a chip when its board lists it, `tools/partitions/<stem>.csv` exists and has an app
partition, and every partition ends inside 4 MB (`FlashSize=4M`). In esp32 3.3.12 that is, for all
three chips, `default`, `defaultffat`, `minimal`, `no_fs`, `no_ota`, `noota_3g`, `noota_ffat`,
`noota_3gffat`, `huge_app`, `min_spiffs`, `rainmaker`, `rainmaker_4MB`, `zigbee_zczr`, and
on c6 also `zigbee` and the `zigbee*_2MB` ones; `custom` (the sketch's own `partitions.csv`) and the
8/16/32 MB schemes are refused. The app limit is the size of the first `app` partition in the CSV:

| scheme | app partition | rest of the 4 MB |
|---|---|---|
| `no_ota` (default) | 0x200000 (2,097,152 B) | 2 MB SPIFFS, coredump |
| `huge_app` | 0x300000 (3,145,728 B) | 896 KB SPIFFS, coredump |
| `min_spiffs` | 0x1E0000 (1,966,080 B), twice (OTA) | 128 KB SPIFFS |
| `default` | 0x140000 (1,310,720 B), twice (OTA) | 1.4 MB SPIFFS |

nvs (0x9000) and coredump (0x3F0000) sit at the same offsets in all of them, so changing the
scheme keeps the stored settings. A scheme the core does not offer, or one larger than 4 MB, is exit
2 naming the usable ones; a non-default scheme without an installed core is exit 3. `--dry-run`
prints the command without checking.

Steps per chip:
1. Require `build_config.toml` (else exit 3), `src/Modules.h` and `build/modules/library.properties`
   (else exit 3 "run `xewe modules generate`"). With zero modules selected, both exist and declare nothing.
   Resolve the partition scheme and its app partition size (above).
2. Sketch dir: the project root if the folder name equals the `.ino` stem (arduino-cli requires
   `<dir>/<dir>.ino`); otherwise mirror `*.ino`, `*.h`, `*.cpp`, `src/` into
   `build/tmp/sketch/<stem>/` with `shutil.copy2` (mtimes kept so the cache stays warm). This is the
   case after "Use this template" renames the repo.
3. Generate `build/builds/<chip>/gen/XeWeBuildInfo/library.properties` + `src/XeWeBuildInfo.h`:
   ```c
   // generated by xewe build; do not edit
   #pragma once
   #define PROJECT_NAME "xewe-os"
   #define BUILD_VERSION "2.0.15"
   #define BUILD_TIMESTAMP "2026-10-08T12:00:00Z"
   #define BUILD_CHIP "c3"
   #define XEWE_CHIP_C3 1       // the chip as a macro: C3, C6 or S3
   #define LED_PIN 8            // one line per --define / release-matrix column
   ```
   `XEWE_CHIP_<C3|C6|S3>` is for chip-conditional values in `Config.h`: the core's
   `CONFIG_IDF_TARGET_*` macros live in `sdkconfig.h` (not on the command line in esp32 3.3.12),
   and `Config.h` is read before any header (step 4).
   The template's `Config.h` does `#include <XeWeBuildInfo.h>` and wraps its own defaults in
   `#ifndef`. `Config.h` itself is never rewritten by a build. `--define KEY=VALUE` values are
   emitted verbatim (quote strings yourself: `--define 'WIFI_SSID="net"'`).

   > **Warning:** `Config.h` must `#include <XeWeBuildInfo.h>` unconditionally. Guarding it with
   > `#if __has_include(<XeWeBuildInfo.h>)` makes arduino-cli's library discovery skip the
   > generated library, so the `--library build/builds/<chip>/gen/XeWeBuildInfo` defines silently never
   > reach the sketch. The cost: a plain Arduino IDE build (without `xewe`) fails on
   > that include, and the user must delete that one line from `Config.h` (the `#ifndef` defaults
   > then apply).
4. Compile (env from §6):
   ```
   ~/.xewe-os/build-tools/bin/arduino-cli-<version> compile \
     --fqbn <FQBN for chip> \
     --build-path build/builds/<chip>/cache \
     --libraries build/libraries \
     --library build/modules \
     --library build/builds/<chip>/gen/XeWeBuildInfo \
     --build-property 'compiler.cpp.extra_flags=-I "<abs>/build/builds/<chip>/gen/XeWeBuildInfo/src" -include "<abs sketch dir>/Config.h"' \
     --warnings default \
     --jobs 0 \
     <sketch dir>
   ```
   (`XeWeBuildInfo` stays its own `--library`: module sources include it and must keep seeing it.)

   `compiler.cpp.extra_flags` (empty in esp32 platform.txt 3.3.12 and meant for this; not
   `build.extra_flags`, which the platform uses for `ARDUINO_USB_CDC_ON_BOOT`) prefixes every C++
   translation unit, sketch, modules, libraries and the esp32 core alike, with the project's
   `Config.h`, so a value set there (a module's `XEWE_MODULE_<SLUG>_<VAR>`, §10) reaches the module
   sources. The esp32 core is compiled with the core and variant include paths only, so the `-I`
   on the generated library makes `Config.h`'s `#include <XeWeBuildInfo.h>` resolve there too.
   Both paths are absolute and double-quoted (arduino-cli splits the recipe on unquoted spaces);
   the `-include` names the sketch directory's copy (the mirror of step 2 when the folder was
   renamed), the same file the sketch includes, so `#pragma once` makes that include a no-op. The
   up-to-date check counts `Config.h` as a source (step 5), and gcc's dependency files list
   `-include` headers, so arduino-cli's own cache is invalidated by an edit as well.
   stdout+stderr go to `build/builds/<chip>/out/compile.log`; on failure print the last 15 lines matching
   `error` and exit 1. `--verbose` streams the log live. arduino-cli itself refuses an image larger than
   the scheme's `upload.maximum_size`; the tools check the size again against the app partition of the
   scheme's CSV and print `FAIL   <chip>  <s> s  too big for <scheme>` (exit 1) when it does not fit.
5. Artifacts into `build/builds/<chip>/out/` (directory emptied first):
   - `<version>-<chip>-<project>.bin` ← `build/builds/<chip>/cache/<stem>.ino.merged.bin` (produced by the
     esp32 core 3.x itself; missing → exit 1 "core did not produce merged.bin").
   - `manifest.json` (ESP Web Tools):
     ```json
     {
       "name": "xewe-os",
       "version": "2.0.15",
       "new_install_improv_wait_time": 0,
       "builds": [
         {
           "chipFamily": "ESP32-C3",
           "parts": [
             { "path": "2.0.15-c3-xewe-os.bin", "offset": 0 }
           ]
         }
       ]
     }
     ```
   - `meta.json` — keys `type, chip_family, project_name, version, timestamp_param, config
     (the --define dict, sorted), fqbn, fqbn_extra (""), partition_scheme, compile_time_sec, artifacts{binary_filename,
     path_rel_binary, path_rel_manifest_json, path_rel_meta_json}, tools_version, arduino_cli,
     esp32_core, core_ref, modules_ref, modules_version` (the `version=` of
     `build/modules/library.properties`), `modules` (selected+resolved slugs),
     `sketch_size` (bytes, from "Sketch uses N bytes"), `sketch_size_percent` (the "(NN%)" of
     program storage on the same line, `null` if absent), `app_partition_size` (bytes of the scheme's
     app partition, the limit; `null` when no esp32 core is installed), `static_ram` and `static_ram_max`
     (bytes, from "Global variables use N bytes … Maximum is M bytes"; without "Maximum is", the maximum is
     N plus the "leaving" bytes; `null` if absent), `warnings` (count). No absolute path is
     stored; `path_rel_*` are relative to the project parent (`xewe-os/build/builds/c3/out/...`).
   - `build.stamp` — sha256 of chip, full FQBN (board options with the partition scheme), project version and the sorted
     `--define` list. `flash`/`run` and the `compiled` fixture of `xewe test` (§9) treat the binary as stale when the stamp differs from the
     inputs of the current call (a build with `--define X=1` followed by a plain `xewe flash`
     rebuilds), in addition to the mtime check against every source (sketch files, `src/`
     including `Modules.h`, `build/libraries/`, `build/modules/library.properties` and `build/modules/src/`).
6. Print a one-line summary (time, size with flash percentage, warnings):
   `ok     c3  51 s  Sketch uses 1123456 bytes (85%)  0 warnings`.

**Versioning.** The version is `[project] version` in `xewe.toml`; `build`, `flash` and `run` never
change it; `BUILD_TIMESTAMP` tells dev builds apart; `xewe release --version X.Y.Z` is the only
writer (and the version must be ≥ the current one). If a legacy `build/version_state` exists,
`xewe setup` prints the `version = "M.m.P"` line to put in the manifest and does not delete the
file.

**Parallelism**: one chip at a time, sequentially (each compile already uses all cores via
`--jobs 0`; ~50–65 s per chip from release 1.0.0/2.0.0 meta). `--all-chips` loops c3, c6, s3, continues
after a failure, prints a per-chip summary, exits 1 if any failed. Each chip has
its own `builds/<chip>/cache` and `builds/<chip>/gen`, so incremental rebuilds stay warm across chips.

---

## 8. Flash and serial

**Flash** (`flash.py`): resolve chip and board (§5); no board → status line, exit 0/4. Otherwise:

```
<esptool> --chip esp32c3 --port /dev/ttyACM0 --baud 921600 \
          --before default-reset --after hard-reset \
          write-flash 0x0 <piece at 0x0> 0xe000 <piece at 0xe000>
```
- The merged image spans the whole flash. Written as one piece at `0x0` it would fill every data
  partition with the image's 0xFF, so a flash without `--erase` would still wipe NVS (the
  provisioning). `flash_segments` reads the partition table at `0x8000` of the image and leaves
  out every data partition whose bytes in the image are all 0xFF (nvs, spiffs, coredump); the
  rest (bootloader, partition table, otadata, app) is written in one `write-flash` call. An image
  without a readable partition table is written whole at `0x0`. Provisioning survives the flash
  (verified on an ESP32-S3).
- `--erase` runs `<esptool> --chip … --port … erase-flash` first, then waits for the port (below) (wipes NVS; what
  `EraseFlash=all` would do via `arduino-cli upload`, which we do not use). `xewe flash` keeps NVS by default (see
  `flash_segments` above); `xewe run` erases by default (`--keep-nvs` skips the erase). The `flashed` line ends with
  `(flash erased: first boot)` or `(nvs kept)`.
- If the board's detected chip ≠ the selected chip → exit 2 before flashing.
- Defines (`xewe flash`): `--define KEY=VALUE` (repeatable, checked like `xewe build --define`),
  else `XEWE_TEST_DEFINES` (environment, else the dotenv file), the precedence of `xewe test` (§9);
  the image built or reused (`build.stamp`) with them is the one written. `xewe run` takes
  `--define` only.
- If esptool fails at 921600, retry once at 460800 (CH340 boards), then exit 1.
- After each esptool command (erase-flash, write-flash) the hard reset re-enumerates a native
  USB port (it can linger, vanish, then come back): `wait_for_port` waits up to 10 s until the
  port has stayed present for 0.5 s in a row (`--settle S` on `flash`, `run` and `boards`, or
  `XEWE_SETTLE`; raise it for a slow hub or VM passthrough), so the next esptool call / `run` /
  tests open a stable port. If the port does not return but a port with the same USB serial
  number appears under another name (macOS can give a re-enumerated board another
  `/dev/cu.usbmodem<N>`; Linux takes `ttyACM1` while `ttyACM0` is still held), that port is used
  after the same settle, with a warning `<old> came back as <new>`, and the `flashed` line names it.
  Not back in time → exit 4; the message names the new port (`pass --port <new>`) when one was seen.
  `xewe boards` waits the same way after each esptool probe (the probe hard-resets the board).

**Serial** (`serialio.Console`, pyserial):
- Open without resetting the board: `dsrdtr=False, rtscts=False`; set `dtr=True, rts=False`
  before `open()` and `dtr=False` after it. The chip resets on DTR=0 & RTS=1 (native USB
  USB-Serial/JTAG `303a:1001`: `rst:0x15 (USB_UART_CHIP_RESET)`; bridges: EN low). cdc_acm raises
  both lines on open and pyserial applies DTR then RTS, so this goes 1/1 -> 1/0 -> 0/0 and never
  through 0/1. `--reset`: pulse RTS (EN) low for 100 ms (deliberately 0/1). Flashing also resets.
- Listen: decode UTF-8 with `errors="replace"`, split on `\n`, strip `\r`, prefix local time
  `HH:MM:SS.mmm  `; tee to `--log FILE`. Ends on Ctrl-C (exit 0) or `--duration`.
  If the port disappears (reset), reopen for up to 5 s, printing `-- port reconnected --`.
- Interactive (`serial` without `--send`, and `run`, when stdin is a terminal and `--no-input` is
  absent): first discard keystrokes typed during flash/boot (`termios.tcflush(stdin, TCIFLUSH)`;
  skipped where termios is missing), so they are not glued to the first command (`n$system`);
  then print `interactive: type a command and press Enter; Ctrl-C to exit` and listen while a
  daemon thread reads stdin line by line (plain cooked line input: no raw mode or readline).
  Board lines are printed raw, without the `HH:MM:SS.mmm` prefix (`--timestamps` on `serial`
  and `run` restores it); the `--log` file keeps timestamps. Each line is sent like `--send`
  (`CMD + "\n"`; an empty line sends `\n`, which the firmware ignores) but no `> CMD` line is
  printed: the terminal shows the typed text and the firmware echoes the command (the log still
  records `> CMD`). Ctrl-C or EOF (Ctrl-D) ends it, exit 0. Non-terminal stdin (pipe, CI) or
  `--no-input`: listen only, timestamped as above. `--duration` applies to both.
- Before sending (`serialio.wait_until_ready`): wait for a boot in progress (after `--reset`
  or a flash). `System Setup Complete` → send. `Name your device` → `board is unprovisioned; run
  xewe provision`, exit 1, nothing sent. No ROM/boot line (`ESP-ROM:`, `rst:0x`, either banner)
  within 2 s of opening → no boot in progress (the normal case), send right away. A boot
  that starts but shows neither line within `--boot-timeout` (default 90 s; Wi-Fi + NTP take up to
  ~30 s) → exit 1, last 20 lines, nothing sent. One status line says what happened:
  `boot: System Setup Complete after 12.3 s`.
- Send: write `CMD + "\n"` (the XeWe serial reader ignores `\r` and ends a line on `\n`,
  `SerialPort.cpp`). A write longer than 900 bytes (`SEND_CHUNK_BYTES`) is split into 900-byte
  pieces 50 ms apart: the board's USB console (HWCDC, USB-Serial/JTAG) queues at most 1024 bytes
  (`SerialPortConfig::rx_buffer_size`) and has no flow control, so a longer burst loses its tail
  (an S3 lost `\n$system uid\n` at the end of a 1037-byte write). `Console.write(data,
  chunk=0)` still sends one burst (overflow tests; core's flood tests write `_ser` directly); echo it as `> CMD`; then read lines until `--expect` regex matches (exit 0)
  or `--timeout` (default 10 s, exit 1, last 20 lines printed). Without `--expect`, collect output
  until 500 ms of silence and exit 0.
- Default: 115200 baud.
- Boot wait (`serialio.wait_for_banner`, shared by `xewe provision` and the test plugin §9):
  optionally reset, then wait for a regex until a deadline, retrying while a native-USB port is
  gone (first boot ends with `Initial Setup Complete`, `Rebooting`, a port drop, then
  `System Setup Complete`). Lines read before the reset (the boot the open itself started on
  native USB) are skipped, so only a match from the boot after the reset counts; ROM lines and a
  prompt printed twice are harmless. `xewe provision` and the `firmware` fixture open the port and
  reset deliberately right away, so the open-reset boot is cut short and the wait covers the
  second boot.

**Provision** (`provision.py`, `xewe provision`): answers the first-boot prompts of a board with
erased NVS (or one never set up) so nobody has to type them. A flash keeps NVS unless erased
(`xewe flash --erase`; `xewe run` erases by default), and a board with stored settings skips what it
stored. First boot on an erased board: banner, `System Setup` → `Name your device` → `Confirm`;
then per module in declaration order: `<Name> Setup`, `Would you like to enable <Name> module?` for
a module that can be disabled, then the enabled module's own setup (Wi-Fi list/password, timezone,
or a module's own value prompts such as the template's `Starting level (0-100)?`); then
`Initial Setup Complete` → restart. Only selected modules are in the image.
- Settings, in precedence order: flags (`--name`, `--modules`, `--timezone`), environment
  (`XEWE_DEVICE_NAME`, `XEWE_WIFI_SSID`, `XEWE_WIFI_PASSWORD`, `XEWE_TIMEZONE`,
  `XEWE_PROVISION_MODULES`), the dotenv file (§13: `--env`/`--from FILE`, `XEWE_ENV`, `.env` in the
  project, `.env` in the tools checkout; applied to the environment without overriding it), defaults (name = `[project] name` or the folder name,
  modules = all, timezone unset = accept the detected one). Empty values count as unset (except
  `XEWE_PROVISION_MODULES=""` = none). SSID and password have no flag (argv is visible to other
  users). Wi-Fi enabled without SSID and password, a bad name or a timezone that is not
  `GMT[+-]HH:MM` (≤ 14:00) → exit 2 before the port is opened.
- Board: selected like `xewe serial`; none → exit 4. Unless `--no-reset`, RTS is pulsed. Then wait
  up to `--timeout` (60 s) for the first prompt of any kind (table below; `Name your device` on a
  fresh board) or `System Setup Complete` (→ `already provisioned`, exit 0, nothing sent). The
  firmware stores each answer as it goes (device name, each module's choice), so a board whose
  earlier provisioning stopped part-way skips those after the reset and starts at a later prompt,
  e.g. `Stored WiFi credentials not found` + the network list. Only in this wait, and only before
  any prompt was seen, one empty line ("nudge") is sent after 10 s: a board already sitting at the
  name prompt answers `Confirm ""?` (declined, so the name prompt prints again); one sitting at the
  Wi-Fi selection answers `! Invalid number` (answered `-2`, so the list prints again). After the
  first prompt silence never triggers a nudge (scan, join and NTP are slow).
- Prompts (firmware: core `XeWeOs.cpp`, `Module.cpp`, `Serial.cpp`; modules `Wifi.cpp`, `Time.cpp`).
  Each prompt is a CRLF line followed by an input line `> ` or `(y/n) > `; input typed before the
  prompt is discarded (`clear_input`), so each answer is sent only after its input line. The tool
  reacts to what it sees, in any order, with 30 s between prompts (progress lines such as
  `Joined`, `Detecting Timezone`, `<Module> Setup` restart the timer):

  | Board prints | Answer |
  |---|---|
  | `Name your device (ex: Kitchen Lights):` | the name |
  | `Confirm "<name>"?` | `y` if it is our name, else `n` (re-asks) |
  | `Would you like to enable <Name> module?` (modules that can be disabled: buttons, pins, wifi, time) | `y` if the slug of `<Name>` is selected, else `n` |
  | `Scanning WiFi networks...`, `N. <SSID>`…, blank, menu, `Selection: ` (then `> ` on its own line) | `N` whose SSID equals `XEWE_WIFI_SSID` (every `N. ` line between the scan and `Selection:` is an entry, whatever it contains: spaces, punctuation, a leading `!`); not listed → `-2` (rescan) once, then `-3` |
  | `! Invalid number. Please enter a base-10 integer.` + `> ` (get_int re-prompt) | the last selection again (`-2` if nothing was answered yet) |
  | `Enter custom SSID: ` | the SSID |
  | `Selected: '<ssid>'` / `Password: ` | the password (the firmware echoes it) |
  | `Is your time: <date> (<GMT±HH:MM>)?` | `y` without a timezone setting, else `n` |
  | `Enter your timezone offset (e.g. GMT-08:00)` | the configured offset; none configured → exit 1 |
  | `Initial Setup Complete`, `Rebooting`, port drop, `System Setup Complete` | — (boot wait, 90 s) |

  An input line nothing was queued for (an unknown prompt, e.g. a module's own value prompt) gets the
  prompt's default: `n` at `(y/n) > `, an empty line (Enter) at `> `; a bounded firmware prompt
  takes Enter as invalid and returns its default once its attempts are used (`! Invalid number` /
  `! …` lines after such an auto-answer are tolerated, up to three). The "enable?" question of a
  module not in `XEWE_PROVISION_MODULES`/`--modules` is answered `n`. Each auto-answer is logged
  (`auto-answer n|Enter (the default) to '<prompt>'`), and the summary counts them.
  `Unable to join` / `Invalid choice`, a fifth `Selection:`, a fourth `! Invalid number`, any other
  `! …` error line, more than 16 auto-answers, or `Name your device` after the reboot → exit 1
  with the last 20 lines.
- Output: board lines echoed with timestamps as `xewe serial` (and `--log FILE`); any line that
  contains the password, typed or echoed, is replaced by `********` there and in the tail. Last
  line: `provisioned in N s: name "<name>"; modules <enabled>; wifi "H***" (network N of M |
  custom SSID, not in the scan); timezone <y, board detected GMT+02:00 | n, set GMT-08:00 …>`. The
  tools' own lines mask the SSID as its first character and `***`: the summary, the rescan log line
  (`network 'H***' is not in the scan`) and the typed custom SSID (`> H***`, also in `--log`); the
  board's lines (`Selected: '…'`, `Joined …`, its echo) are left as the board printed them.

---

## 9. Test runner (`xewe test`)

`xewe test` builds the pytest argument list and calls `pytest.main()` in-process:
- Test roots: `<project>/tests/` (if present) and, for each module in `build/modules/modules.lock`
  (the resolved selection), `build/modules/tests/<slug>/` (generate copies the module's
  `tests/board/` and `tests/unit/` there). `--module SLUG`
  restricts to those modules (must be selected; else exit 2).
  Collection is recursive: every firmware repo keeps exactly `tests/unit/` (run on the developer
  machine: pure-logic Python, or pytest drivers that compile C++ with g++) and `tests/board/`
  (pytest files run on the ESP32); pytest runs in `--import-mode=importlib`, so the same basename
  may appear in both.
- A module test that reads its module's files (`module.properties`, `src/`, `README.md`, a C++
  unit test beside it) finds the folder with `xewe.testing.module_dir(__file__)`: `modules/<slug>/`
  when it runs in the modules repo, and `modules/<slug>/` of the checkout setup recorded
  (`XEWE_MODULES_SOURCE` or the shared `build-tools/sources/xewe-os-modules/<ref>/`) when it runs
  from the copy in `build/modules/tests/<slug>/`.
- Options passed to the plugin: `--xewe-chip`, `--xewe-port`, `--xewe-require-board`, `--xewe-define`,
  `--xewe-no-flash` (`xewe test --no-flash`: the `firmware` fixture skips the image write and tests
  what the board runs; still built, still reset; the board reports only `Version <x.y.z>`, no build
  id, so there is no automatic skip), `--xewe-project` (and `--xewe-no-board` for plain pytest; `xewe test --no-board` sets
  `XEWE_NO_BOARD=1`, which the plugin honours). Extra args after `--` go to pytest verbatim.
- `--unit-only` → `-m unit`. `--all-chips` runs the session once per chip (three `pytest.main`
  calls), aggregating exit codes.
- Defines: `--define KEY=VALUE` (repeatable; same syntax and check as `xewe build --define`, a bad
  item is exit 2) becomes `--xewe-define KEY=VALUE` for the plugin. Without any `--define`, the
  plugin reads `XEWE_TEST_DEFINES` (environment, else the dotenv file, §13): `KEY=VALUE` items
  separated by whitespace (a value cannot hold a space; a bad item is exit 2 naming the variable).
  Precedence: `--define` > `XEWE_TEST_DEFINES` in the environment > in the dotenv file; any
  `--define` replaces the whole variable (no per-key merge). The `compiled` fixture uses these
  defines, so `build.stamp` (§7) carries them: an image already built with the same defines
  (`xewe build --define …`) and newer than every source is reused (`build  <chip>  up to date
  (…)`), anything else is rebuilt with them, and `firmware` flashes that image. A board wired off
  the default pins (`XEWE_MODULE_LED_PIN_DATA=48`) therefore keeps its pin whether or not the
  session flashes; a plain `xewe test` with neither set builds and flashes the default-pin image.
  `xewe flash` takes the same `--define` items and the same `XEWE_TEST_DEFINES` default, so a
  `xewe flash --chip C --define …` followed by `xewe test --chip C --define …` (or both with only the
  variable set) flashes once and tests that image. Release scripts keep per-chip values in the
  git-ignored dotenv file (or export the variable per chip).

JUnit properties: every collected test carries `user_properties` for the release card
(`xewe_kind`, `xewe_needs`, `xewe_nodeid`, `xewe_chip`; a test that had the board also
`xewe_board`, `xewe_fw_sha256`, `xewe_fw_match`), written by `--junitxml` with
`-o junit_family=xunit1`. Their meaning and the result mapping are in §14. With defines,
`xewe_fw_sha256` is the sha256 of the image `compiled` returned (reused or built), which is the image
`firmware` flashes, so it equals the card's flash step sha.

Markers (registered by the plugin): `unit` (pure logic; runs on the developer machine, never needs
a board or a build), `board` (needs firmware on a board). A test that uses `compiled`, `board`,
`firmware` or `serial` is auto-marked `board`.

Fixtures (`xewe.testing.plugin`, loaded through the `pytest11` entry point, so module test
folders need no `conftest.py`):

| Fixture | Scope | Behaviour |
|---|---|---|
| `compiled` | session | The selected chip's image for the session's defines (`--xewe-define`, else `XEWE_TEST_DEFINES`): reused when `build.stamp` and the sources say it is up to date (as `xewe flash` decides), else built once (same code path as `xewe build`); a build failure fails every board test. |
| `board` | session | Depends on `compiled`; detects the board (§5). None, or board access disabled (`XEWE_NO_BOARD`/`--xewe-no-board`; no port is looked at) → `pytest.skip("compiled, not run: no board attached")`; with `--require-board` → `pytest.fail(...)`. Returns `Board(port, chip, serial_number)`. |
| `firmware` | session | Depends on `board`; flashes `build/builds/<chip>/out/` once per session (not with `--xewe-no-flash`), opens the session's one `Console`, pulses reset and waits up to 90 s for `System Setup Complete` (through a first-boot `Initial Setup Complete`/`Rebooting` and port drops), then 0.5 s of silence. `Name your device` (unprovisioned board) fails with a pointer to `xewe provision` and the runbook's first-boot provisioning. The wait is `serialio.wait_for_banner` (§8), shared with `xewe provision`. The console is closed at session end. Returns `Firmware(bin_path, version, chip, console)`. |
| `serial` | function | Depends on `firmware`; the session `Console` (never opened/closed per test), drained and with `lines` emptied at test start: `send(cmd)`, `expect(regex, timeout=10) -> re.Match`, `command(cmd, expect, timeout) -> re.Match`, `lines` (captured since the test started), `drain()`, `reset()`, `write(bytes)`, `reopen()`, `probe()`. Expect failures raise `AssertionError` with the last 20 lines. After a board test fails on a timeout or a vanished port, the next `serial` reopens the port once (a stale native-USB endpoint after a restart) and sends `$system uid` (3 tries in 15 s); no `uid64` reply → `pytest.exit("board on <port> is silent after a restart …; power-cycle it")`, exit 1 (the port cannot be reopened → `"board on <port> disappeared from USB …; power-cycle it (unplug and replug)…"`), so one silent board fails one test instead of every later one. A board at `Name your device` is not probed. The `firmware` boot wait does the same when the banner never comes. |

No-board summary and exit code: the plugin's `pytest_terminal_summary` lists every test skipped
with a reason starting `compiled, not run`:

```
==== compiled, not run (no board attached, chip c3) ====
modules/wifi/tests/board/test_wifi.py::test_status_reports_wifi
modules/wifi/tests/board/test_wifi.py::test_scan_lists_networks
xewe test: 4 unit passed, 2 compiled, not run, 0 failed
```

Exit 0 when nothing failed (pytest already returns 0 when tests are only skipped; the plugin turns
pytest's 5 "no tests collected" into 0 with a warning). With `--require-board` those tests fail and
`xewe test` exits 4 (like `flash`/`serial`; plain `pytest` reports 1). Compile failure → 1.

Example:

```python
# modules/wifi/tests/board/test_wifi.py
import pytest

pytestmark = pytest.mark.board


def test_status_reports_wifi(serial):
    m = serial.command("$system status", expect=r"(?i)wifi.*(connected|not connected)", timeout=5)
    assert m


def test_scan_lists_networks(serial):
    serial.send("$wifi scan")
    serial.expect(r"(?i)scan", timeout=15)
```

```python
# modules/wifi/tests/unit/test_wifi.py
import re
import pytest

from xewe.testing import module_dir

MODULE_DIR = module_dir(__file__)  # modules/wifi/, also from build/modules/tests/wifi/unit/


@pytest.mark.unit
def test_module_id_fits_nvs_namespace():
    props = dict(
        line.split("=", 1)
        for line in (MODULE_DIR / "module.properties").read_text().splitlines()
        if "=" in line
    )
    assert re.fullmatch(r"[a-z][a-z0-9_]{0,14}", props["id"])
```

With a board the summary line adds `, N board passed`. Core's own C++ unit tests
(`xewe-os-core/tests/unit/run.sh`) are not run by `xewe test`; core owns them. A module's C++ unit
test lives in `modules/<slug>/tests/unit/` next to the `unit`-marked pytest driver that compiles and
runs it with g++ (skipped when g++ is missing).

---

## 10. Modules generation and validation

Input: the modules repo checkout (`~/.xewe-os/build-tools/sources/xewe-os-modules/<ref>/` or
`XEWE_MODULES_SOURCE`, §6 step 8), laid out as the modules contract (`doc/contract.md` of
xewe-os-modules) says: `modules/<slug>/{module.properties, src/<Folder>/, tests/, README.md}`.
`registry.py` also reads the older layout, one `xewe-os-module-<slug>/` per module with
`module.properties` at its root.

`module.properties` keys used: `slug, id, name, version, description, folder, include, declare,
depends_modules, requires_core` (e.g. `requires_core=>=1.0.0`; `depends_libraries=XeWeOS (>=0.1.0)`
is accepted and the core name in it ignored). Parsing: the first `key=` line wins, the value is
everything after the first `=`.

`xewe modules generate`:
1. Resolve: depth-first over `selected` in manifest order, dependencies first, error on cycle
   ("dependency cycle through module 'x'"), so `Modules.h` comes out the same for the same
   selection. Print `x requires y; adding it` for additions.
2. Stage `build/modules/` in `build/tmp/modules/`. The Arduino library `XeWeModules`: copy
   `modules/<slug>/src/<Folder>` → `src/<Folder>` (skip `.git`), so a module's
   `#include "../Wifi/Wifi.h"` keeps working (one library for all selected modules; arduino-cli only
   discovers a library from a header directly under its `src/`). Write `library.properties`
   (`name=XeWeModules`, `version=` the tag without `v` when the modules ref is a release tag
   (`v1.2.3` → `1.2.3`), else `0.0.0+<sha7>` of the resolved commit (`latest`, a branch, a SHA, a
   local source), `0.0.0` when the commit is unknown; `author`/`maintainer=xewe-labs`,
   `category=Other`, `architectures=esp32`, `depends=XeWeCore`, `includes=XeWeModules.h`) and
   `src/XeWeModules.h` (`#pragma once` + the includes in dependency order:
   `include=src/Wifi/Wifi.h` → `#include "Wifi/Wifi.h"`). Beside it: each selected module's
   `tests/board/` and `tests/unit/` → `tests/<slug>/board/`, `tests/<slug>/unit/` (skip `.git`,
   `__pycache__`; arduino-cli reads only `library.properties` and `src/`, so they are never compiled),
   and `modules.lock` (step 4).
3. Replace `build/modules/` with the staged dir (rename; old dir removed only after staging succeeded),
   then remove `build/tmp/` if that left it empty. `build/modules-lib/` and `build/config/modules.lock`
   of the previous layout are removed (`removed legacy …`).
4. Write the project's `src/Modules.h` (git-ignored by the template):
   ```cpp
   // Generated by xewe modules generate; do not edit. Re-run ./setup.sh or `xewe modules select` to change modules.
   // Included by the sketch after `os`; modules are declared in dependency order.
   #pragma once

   #include <XeWeModules.h>

   Wifi wifi(os);
   WebInterface web_interface(os, wifi);
   ```
   and `build/modules/modules.lock`:
   `# generated by xewe: slug|folder|source|ref|commit` then one line per module
   (`source` = modules repo URL or `local:<path>`, `ref` = manifest ref, `commit` = checkout HEAD).
   With an empty selection the library is still written (`XeWeModules.h` includes nothing),
   `Modules.h` holds the header comment, `#pragma once`, `#include <XeWeModules.h>` and a
   `// no modules selected` comment, and `modules.lock` only its header line.
5. A `src/modules/` left by the previous layout is removed (`removed legacy src/modules/`) only when
   its `modules.lock` starts with the generated header line; otherwise it is left alone.
6. The project's `Config.h` (`fill_config_h`). For every module of the resolved order that has a
   `src/<Folder>/Config.h` (`Module.config_h`; the modules contract: pure preprocessor,
   `XEWE_MODULE_<SLUG>_<VAR>` defines in `#ifndef` guards), look for the marker line
   `// ---- <slug> (xewe modules generate) ----`. Present: leave the file alone. Absent: append
   ```c

   // ---- wifi (xewe modules generate) ----
   // Values of the wifi module. Edit the numbers; keep this marker and the end marker.
   <the module's Config.h after its #pragma once (else without its SPDX lines)>
   // ---- end wifi ----
   ```
   A block is never removed or rewritten: the user's edits in it survive every generate, and a
   block whose module is no longer selected stays with one `note:` line. `Config.h` is never
   created (the template owns it; setup fails without it). `xewe clean --modules` does not touch
   it. The CLI summary line reads `(build/modules, src/Modules.h, Config.h)` when a block was
   added, `(build/modules, src/Modules.h)` otherwise.

`xewe modules validate [PATH]` — runs over every module in the checkout, prints one line per
problem, exit 1 if any:

| Rule | Check |
|---|---|
| slug | `^[a-z0-9][a-z0-9-]*$`, unique, equals the `modules/<slug>` directory name |
| id | `^[a-z][a-z0-9_]*$`, ≤ 15 chars (NVS namespace limit), unique |
| folder | `^[A-Z][A-Za-z0-9]*$`, unique, `src/<folder>/` exists |
| include | starts with `src/<folder>/`, file exists |
| declare | matches `^(\w+)\s+(\w+)\s*\((.*)\)\s*;$`; type == folder (warning if not); variable unique across modules, ≠ `os`; every argument identifier is `os` or the variable of a transitive `depends_modules` entry |
| depends_modules | each slug exists; no cycles; no self-dependency |
| requires_core | parses as `>=X.Y.Z` (optionally `,<X.Y.Z`); satisfied by `[core] ref` of the harness manifest when run inside a project (skipped with a note for non-semver refs) |
| version | `X.Y.Z` |
| description | present, one line, ≤ 100 chars |
| required keys | all of the above present; unknown keys are warnings |
| tests | `tests/board/test_<slug>.py` exists; `tests/` holds only `board/` and (optionally) `unit/`; no `conftest.py` or `__init__.py` in either (`modules.check_tests_layout`, run by the CLI; the modules repo's `tools/validate.py` applies the same rule) |

The modules repo needs no copy of this code: its CI runs `./setup.sh --modules-source . --modules all`
in an `xewe-os` checkout, then `xewe modules validate` (the checkout setup used, here `.`),
`xewe build --all-chips`, `xewe test`.

---

## 11. Release (`xewe release --version X.Y.Z`)

1. Check the version is `X.Y.Z` and ≥ `[project] version` (else exit 2); check the working tree has
   no uncommitted changes except `xewe.toml` (warning only; we never run git writes).
2. Matrix: `release_matrix.csv` in the project root if present (`CHIP` column required,
   case-insensitive; `_BUILD_NOTES` notes only; every other column becomes a `--define`:
   integers/`true`/`false` bare, everything else quoted), else rows c3, c6, s3.
   Legacy `build/release_matrix.csv` is read with a warning to move it.
3. Release notes: `--notes FILE`, else `$EDITOR` (default `vi`) on a temp file with a 3-line
   header, which is stripped; result saved as `static/firmware/releases/<version>/release_notes.txt`.
   The editor is opened only when stdin is a terminal and `XEWE_NONINTERACTIVE` is not `1`;
   otherwise (CI, scripts, `< /dev/null`) a release without `--notes` stops with exit 2 and one
   line, `error: release notes: pass --notes FILE (…)`, before anything is built or written. The
   check sits in the editor fallback itself, so it covers a full run, `--chip` and `--assemble`
   (which needs notes only when the chip runs left no `release_notes.txt`).
4. For each row: `xewe build --chip <chip> --define …` with the release version, then move
   `build/builds/<chip>/out/{<bin>,manifest.json,meta.json}` into
   `static/firmware/releases/<version>/<col1>/<col2>/…` (folder per non-notes column, values with
   quotes/backslashes stripped, spaces → `_`, empty → `empty`; for the default matrix this is
   `<version>/c3/`). Write `build_notes.txt` when the row has notes. Rewrite `path_rel_*` in
   `meta.json` to the release location, and record what the release was built from, read from
   `build_config.toml [installed]` (what `./setup.sh` resolved, so a `latest` ref is pinned to the
   commit it meant at release time): `project_commit` (HEAD of the project, read-only
   `git rev-parse`), then each ref with its commit, `core_ref, core_commit, modules_ref,
   modules_commit, tools_ref, tools_commit`, and `libraries = {<name>: {ref, commit}}` at the end.
   An unknown commit (local source that is not a git checkout, nothing recorded) is `-`. CI reads
   these fields (doc/ci.md). `artifacts` gains `size` and `sha256` (of the bin) and the row's
   release-asset names `asset_name`, `meta_asset`, `manifest_asset` (step 5).
5. **Asset names** (`asset_names()`): the prefix is the row's folders joined by `_`. The bin keeps
   its name when the row has one folder and is `<prefix>_<bin>` otherwise; meta and manifest are
   always `<prefix>_meta.json` and `<prefix>_manifest.json`.

   | Matrix | Bin | Meta | Manifest |
   |---|---|---|---|
   | default (`CHIP` only) | `3.1.0-c3-xewe-os.bin` | `c3_meta.json` | `c3_manifest.json` |
   | `CHIP,XEWE_MODULE_LED_PIN_DATA,XEWE_MODULE_LED_PIN_CLOCK` | `c6_20_19_3.1.0-c6-xewe-led-os.bin` | `c6_20_19_meta.json` | `c6_20_19_manifest.json` |

6. **`firmware_map.csv`**, one row per built image in matrix order, written with the csv module
   (minimal quoting): the matrix columns without `_BUILD_NOTES` (cells verbatim), then the reserved
   columns `_PATH` (folders joined by `/`), `_ASSET`, `_SIZE` (bytes), `_SHA256`, `_META`,
   `_MANIFEST`, `_BUILD_NOTES`. Reserved columns start with `_`, so a reader that takes the leading
   matrix columns keeps working.
   ```
   CHIP,XEWE_MODULE_LED_PIN_DATA,XEWE_MODULE_LED_PIN_CLOCK,_PATH,_ASSET,_SIZE,_SHA256,_META,_MANIFEST,_BUILD_NOTES
   c6,20,19,c6/20/19,c6_20_19_3.1.0-c6-xewe-led-os.bin,4194304,<64 hex>,c6_20_19_meta.json,c6_20_19_manifest.json,Led Dock V2 pinout
   ```
7. Write `[project] version = "X.Y.Z"` into `xewe.toml`; create
   `static/firmware/releases/firmware-X.Y.Z.tar.gz` (tarfile, the whole version folder in the layout
   of steps 4 and 6). Then `stage_assets()` empties `build/release/X.Y.Z/assets/` and writes the
   GitHub Release assets into it, flat: every bin under its asset name, each row's `meta.json` as
   `<prefix>_meta.json`, an ESP Web Tools manifest per row as `<prefix>_manifest.json` whose part
   path is the flat bin name (so it resolves next to the manifest in the release),
   `firmware_map.csv` and the tarball. A bin whose size or sha256 differs from its map row fails
   (exit 1).
8. **Per chip** (one CI job per chip): `--chip C` builds only C's rows and writes
   `static/firmware/releases/X.Y.Z/firmware_map.C.csv` (no `firmware_map.csv`, no tarball, no
   assets); exit 2 when the matrix has no C row. `--assemble` builds nothing: it merges every
   `firmware_map.<chip>.csv` in that folder in matrix order, deletes them and goes on with step 7.
   It fails (exit 1, nothing written) when a matrix row has no build, a part has a row the matrix
   lacks, or a part was made from another matrix (different header). Without `--notes` it keeps the
   `release_notes.txt` of the chip runs. `--chip` runs followed by `--assemble` give the same
   `firmware_map.csv` and assets as one run. The two flags do not go together (exit 2).
9. Print, do not run (`release_card.md` is added when `releases/X.Y.Z/release_card.md` exists;
   firmware is never committed: it is release assets only, see doc/ci.md "Releases"):
   ```
   git add xewe.toml
   git commit -m "release X.Y.Z"
   git tag -a X.Y.Z -m "Release X.Y.Z"
   git push origin X.Y.Z
   gh release create X.Y.Z build/release/X.Y.Z/assets/* [releases/X.Y.Z/release_card.md] --verify-tag --title X.Y.Z --notes-file static/firmware/releases/X.Y.Z/release_notes.txt
   ```
   Time: ~3 min for three chips; the product's 43 rows take about 45 min locally.

---

## 12. Self-tests of the package

`pytest` from the repo root; no network, no arduino-cli, no board. Fakes: `tests/fakes/arduino-cli`
(Python script; selected with `XEWE_ARDUINO_CLI`, records argv+env to a JSON file, writes
`<stem>.ino.merged.bin` and a "Sketch uses N bytes" line, can be told to fail), a fake esptool
(`XEWE_ESPTOOL`), pyserial `serial_for_url("loop://")` plus a scripted fake port, and
`list_ports.comports` monkeypatched.
Layout: `tests/<area>/test_<module>.py` mirrors `src/xewe/` (§2); one `tests/conftest.py` holds the
shared fixtures and fakes (doc/tests.md); `pytest tests/<area>` runs one area.

| File | Covers |
|---|---|
| `env/test_project.py` | root discovery, `--project`, relative paths, project moved after setup still works |
| `modules/test_lockfile.py` | parse/validate/write round-trip, unknown keys rejected, defaults |
| `modules/test_tomlw.py` | writer output parses back with `tomllib` for all value types used |
| `env/test_fetch.py` | asset name per (os, arch), checksum mismatch rejected, `.part` resume via a local HTTP server |
| `env/test_setup.py` | shared toolchain reused by a second project (no second download or core install), `XEWE_HOME` override, `build-tools/.lock`, step order, skip-when-recorded idempotency, `--force`, interrupted run leaves no `build_config.toml`, local sources, `--latest` tag selection from fake `ls-remote` output, `Head "<url>"` rescue path, module libraries from the modules `libraries.toml` catalogue vs a `[libraries]` pin in the manifest (manifest wins), missing/bad catalogue, `manifest show` origin, shared modules checkout per ref (reused by a second project, branch followed, other-repo checkout re-cloned), no `build/tmp` after setup, ref `latest` (core/modules/tools/library resolved from fake `ls-remote --symref`, recorded with branch+commit, followed when the head moves, local source wins, offline keeps the checkout), the `latest` strings of the template's `setup.sh` (`tests/fixtures/setup.sh`), `run.sh` rewritten on every setup, no dotenv file written (provisioning then reads the tools checkout's file), no-modules `warning:` line from setup (Enter at the menu), build and run, none with modules selected |
| `env/test_runscript.py` | generated `run.sh` (version, chip, `--keep-nvs`), valid bash, any existing `run.sh` or symlink replaced; setup writes it (`env/test_setup.py`) |
| `build/test_format.py` | fake clang-format (`tests/fakes/clang-format`): default file set (no `build/`, hidden dirs, `src/Modules.h`, `tests/board/*.cpp`), `--check` exit 1 and lists without writing, apply then clean check, mode kept, always the shipped style file (a project `.clang-format` is ignored), the style's rules, the style is package data, format in a library and in a directory that is neither (the current directory), `PATH` arguments, nested `build` Python packages kept, ruff's old and new `--check` output, failure exit 1, binary search order (`XEWE_CLANG_FORMAT`, `PATH`, venv, toolchain, exit 3 hint), ruff skipped when missing and called with `--check`/`--quiet` |
| `env/test_arduino.py` | env vars (no `~/.arduino15`), exact compile argv per chip, FQBN strings (golden), no `JTAGAdapter` |
| `build/test_compile.py` | `builds/<chip>/out/` contents, bin name, `manifest.json` byte-equal to golden built from 2.0.0 release files, `meta.json` keys (`modules_version`), `XeWeBuildInfo.h` content, `--define`, sketch staging when folder ≠ stem, `--all-chips` continues after failure |
| `board/test_boards.py` | VID:PID filter, chip-id output parsing (sample esptool 5 output), cache by serial number, override precedence, no-board / one / several decision, `boards.toml` `[override]` preserved |
| `build/test_flash.py` | esptool argv, `--erase` (port wait after erase and after write), `flash` keeps NVS by default, `run` erases by default and `--keep-nvs` does not, port not back → exit 4, baud fallback, no-board exit 0 vs `--require-board` exit 4, chip mismatch exit 2, `--define` (repeatable) flashes the image built with those defines and reuses one already built with them, `XEWE_TEST_DEFINES` as the default with the precedence of `xewe test` |
| `board/test_serial.py` | timestamps, `\r` stripping, send/expect match and timeout (exit 1), reconnect, a closed handle after an unplug (`TypeError` from `in_waiting`) → `XeWeError` and `connected` False, reset across a port drop, `wait_for_port` settle and timeout (exit 4); interactive mode: typed line sent, `--no-input` and non-TTY stdin listen only, EOF exit 0, reconnect, raw lines and no `> CMD` echo (timestamped with `--timestamps`, in `--send` mode, listen-only and the log), stdin flushed only when interactive on a TTY (interactive mode in `board/test_serial_interactive.py`) |
| `board/test_provision.py` | scripted first-boot transcript: answers in order, SSID by number (the S3 list's shape, made-up names), rescan then `-3` custom, `! Invalid number` re-answered, board resuming at the network list, nudge only before the first prompt (fake clock), timezone `y` / `n` + offset, already provisioned sends nothing, unlisted module answered `n` and unknown value / y/n prompts answered with their default (logged), endless unknown prompt exit 1, nudge, port drop at reboot, prompt timeout exit 1 with masked tail, password never in stdout/log, the summary, the rescan log line and the typed custom SSID mask the SSID; settings precedence flags > env > dotenv file > defaults, settings from `<project>/.env` with no flags, exit 2 before the port, exit 4 without a board |
| `env/test_dotenv.py` | parsing (comments, quotes, `export`, no interpolation), errors without values, real environment wins, resolution order (`--env`, `XEWE_ENV`, project, tools checkout from `build_config.toml` or the package checkout), missing file is not an error, only the key count is logged, `hint()` (both paths, the provisioning keys) |
| `build/test_brandlint.py` | each banned spelling fails with the exact stderr lines, allowed spellings are clean, skipped directories (caches and `*.egg-info` included), in a git work tree only the files git does not ignore, outside one a directory walk, binary and non-UTF-8 files, `naming.md` in any case, dotenv files, several `DIR`s, a missing `DIR` exits 2, no project needed, the tools repo itself is clean |
| `build/test_webui.py` | export of `fixtures/webui/fan-dashboard/` byte-equal to the golden `fan-dashboard_export/`, the timestamped folder name, `url_for` rewrite (both quote styles), routes, array names and MIME types, only `.html`/`.css`/`.js` under `templates/` and `static/` (binary, other suffixes and folders skipped; non-UTF-8 warned), duplicate array name exit 1, missing folder exit 2, the prompt printed with placeholder Wi-Fi only |
| `testing/test_plugin.py` | `pytester`: no board → "compiled, not run" summary and exit 0; `--require-board` → pytest 1, `xewe test` 4; faked board: one port open per session, boot banner wait (first-boot reboot, unprovisioned, timeout, board gone from USB at the boot reset → `disappeared from USB`); `xewe test --no-flash` writes no image; unit tests run; `--unit-only` runs only `unit` tests and builds nothing; board tests found under `modules/<slug>/tests/board/`; compile failure fails board tests; pins from the project `.env` reach `os.environ` (real environment wins); JUnit `user_properties` (§14) under xunit1 and xunit2, board id, image sha256 and banner match on a fake board, a module test's source node id; `xewe test --define` and `XEWE_TEST_DEFINES`: the flag reaches `build_chip` and `build.stamp`, a bad item is exit 2 with no build, a matching stamp is reused (no rebuild, image bytes unchanged) and other defines rebuild, precedence flag > environment > dotenv file, the define image is the one flashed, and with defines the JUnit `xewe_fw_sha256` equals the sha256 of the flashed image whether `compiled` reused or built it (§14); the prepare flow `record --kind flash -- xewe flash --define …` then `xewe test --define …` agrees on the image (no build, `xewe_fw_sha256` equals the step's `fw_sha256`) |
| `modules/test_registry.py` | dependency order and cycle error against fixtures copied from the six real `module.properties`; generated `build/modules/` (`library.properties`, `XeWeModules.h`, `tests/<slug>/{board,unit}/`, `modules.lock`) and `src/Modules.h` golden, library version (tag / `0.0.0+<sha7>` / `0.0.0`), empty selection, legacy `src/modules/`, `build/modules-lib/` and `build/config/modules.lock` removal; every validator rule (tests layout included) has a failing fixture |
| `build/test_release.py` | matrix parsing and typing, folder layout, `firmware_map.csv` rows and asset names (the product's 43-row matrix as a fixture, the default matrix), `--chip` runs + `--assemble` equal to one run and its refusals, `stage_assets` listing and checks (two rows with one asset name refused), version ≥ check, printed commands (tags without `v`, no `git add` of firmware), never calls git (but `status`/`rev-parse`), `meta.json` provenance (`*_commit` next to each ref, libraries, `-` when unknown); `xewe-release.yml`: the tag-to-version, matrix-chips (CSV quoting, chip lower-cased) and latest-flag (drafts excluded) steps run with bash (fake `gh`), job order and assets, the branch commit through Git LFS with `[skip ci]`; the `card` jobs find the card of the tree with `xewe release-card fingerprint --card --check` (a read-only fake `git`), with no second fingerprint implementation (only exit 1 with `is not the card's` skips a card; a card with no data block, a block that is not JSON or not on the last line, and any other failure fail the job; ci.yml's card job too), and run `verify --ci --unit-only`; the assemble job's card check runs before the rows are downloaded and assembled; the xewe-build.yml no-committed-firmware lint; `xewe release` without `--notes`: `xewe release` without `--notes`: stdin not a terminal (subprocess, `stdin=DEVNULL`) or `XEWE_NONINTERACTIVE=1` → exit 2 naming `--notes`, no editor, nothing written; a terminal opens `$EDITOR`; `--chip` refused the same way; `--assemble` keeps the chip runs' notes and is refused only when there are none |
| `build/test_releasecard.py` | golden card (`fixtures/releasecard/golden_card.md`) byte for byte; JUnit fixtures (pass, hardware pass, compile-only pass, compiled-not-run skip, gated skip, xfail, fail, error, strict xpass) and core `ok  `/`FAIL` lines; redaction (dotenv file, environment, `$HOME` as a whole path part; XML-, JSON- and URL-escaped forms; JUnit and meta.json copies value by value, a short SSID never rewriting markup or ids); fingerprint stable and changed (content, mode, added, renamed, deleted, a symlink to a folder, a submodule commit; `*` matching one path part; `releases/` and ignored files left out; read-only git on this checkout); `init` refusing a folder that is not a previous run folder (the release folder, the root, a tracked folder, another version's run) and deleting nothing; `record` mapping the command's exit to 0/1/3 and resolving the root from a subfolder, `unit`/`board` without results exit 2; every refusal rule (stale run id, step before `run.json`, changed log, tree changed, missing kind, failed step and tests, JUnit not written, a test step with no rows, missing hardware chip, fw sha mismatch, banner mismatch and unknown, a board record of another chip, notes with a data block, a hardware pass without a board record) with the draft; `--tests` module card; `--resume` re-run selection and retries; `verify` (clean tree, renames, logs, `testcard.md`, a card edited after render in its body or data block, the data block read from the last line only, a bad last line exit 2) and the CI comparison (block, allow, warn, case-sensitive env gates, size tolerance, `--unit-only` with CI board rows, a module card's `only` scope, `--meta-dir`/`--size-tolerance` checks, the card's chips/hardware/require); the `fingerprint` action (`--card --check`, the card's exclude list) |
| `test_cli.py` | every subcommand's `--help` (`brand-lint` and `release-card` included), exit-code table, `--verbose` |
| `env/test_clean.py` | `clean` levels (`--modules`; `--all` keeps `build/tools`, never touches `build-tools`) |
| `modules/test_manifest.py` | `manifest show` drift marks, `manifest update` (latest tag, `--to`, usage errors); ref `latest`: `latest (main@<sha7>)`, drift when the remote head moved, unreachable remote, freeze with `update` and `--to latest` |
| `testing/test_no_board.py` | `--no-board` / `XEWE_NO_BOARD=1`: discovery returns nothing (ports never listed), console never opens, flash/run build then exit 4, serial/provision/boards exit 4, `boards --set-port` still works, `xewe test --no-board` compiled-not-run exit 0 (4 with `--require-board`), plugin `--xewe-no-board`; `-v` after the subcommand |
| `env/test_doctor.py` | checks report missing pieces without raising; Rosetta check on Apple silicon (installed / missing), skipped elsewhere |

---

## 13. Configuration: the dotenv file (`dotenv.py`)

Credentials and bench settings come from a dotenv file, `.env` (`dotenv.FILENAME`), that is never
committed (git-ignored in the tools repo and the template).

- File, first found wins: (1) `--env FILE` (on flash, serial, provision, test, run, boards;
  `provision --from` is an alias), else `XEWE_ENV`; a named file must exist (exit 2); (2) `.env` in
  the project root (nearest ancestor with `xewe.toml`); (3) `.env` in the tools source checkout:
  `installed.tools.source = "local:<path>"` in `build/config/build_config.toml` (written by setup
  when `XEWE_TOOLS_SOURCE` is used), else `Path(dotenv.__file__).resolve().parents[3]` when that
  directory holds `pyproject.toml` (package installed from a path). No file is not an error.
- Nothing writes the file. `xewe setup` in a project logs one line, `dotenv.hint()`, when
  `XEWE_ENV` is unset and neither the project nor the tools checkout has a file: the project path,
  the tools-checkout path, and the keys the first provision reads. A project file wins as a whole,
  so an empty or partial one hides the tools checkout's keys (setup used to write a commented
  skeleton there, which did exactly that). The first provision needs `XEWE_WIFI_SSID` and
  `XEWE_WIFI_PASSWORD` when the wifi module is enabled; every other key is optional or used only by
  board tests.
- Format: `KEY=VALUE`; `#` comment lines, blank lines, optional `export ` prefix, optional matching
  single or double quotes around the value; no interpolation; a line without `=` is exit 2 naming
  file and line only.
- Keys (`dotenv.KEYS`): `XEWE_DEVICE_NAME`, `XEWE_WIFI_SSID`, `XEWE_WIFI_PASSWORD`, `XEWE_TIMEZONE`,
  `XEWE_PROVISION_MODULES`, `XEWE_TEST_BUTTONS_PIN`, `XEWE_TEST_PINS_ADC_PIN`,
  `XEWE_TEST_DEFINES` (§9; `xewe test` and `xewe flash`), `XEWE_PORT`, `XEWE_CHIP` (other `XEWE_*` variables the tools read are accepted; an unknown `XEWE_*` key is a
  warning naming the key). Only `XEWE_*` keys with a non-empty value are applied, through
  `apply()`, which sets `os.environ[key]` only when the variable is not set: the real environment
  wins, flags win over both.
- Applied by the CLI before flash, serial, provision, test, run and boards (so `XEWE_PORT`/`XEWE_CHIP`
  reach board selection and the chip rule), and by the pytest plugin at `pytest_configure` inside a
  project (so module tests read `XEWE_TEST_*` from `os.environ`).
- Logging: `loaded N keys from <path>` only; values are never logged. The Wi-Fi password is masked
  (`********`) in console output, `--log` files and failure tails (§8).

---

## 14. Release card (`xewe release-card`)

`src/xewe/build/releasecard.py`. Every repo of the ecosystem (tools, core, modules and the module
set, the harness, products) publishes one generated file per release, `release_card.md`: what was
tested, on which tree, with which result. It replaces `testcard.md`. The only hand-written input is
`notes.txt`, embedded verbatim. `scripts/release.sh --prepare <v>` drives the four actions; the
card is committed with the prepare commit and is the only release file published (the logs are not).
`release-card` needs no project: it runs in the repo's git work tree (`--root`, default
`git rev-parse --show-toplevel` of the current directory, for every action) and runs no git write.
The card guards against mistakes, not against edits to the run folder: `run.json` is the record of
every exit code and file sha, and the CI comparison cannot see hardware rows.

**Run folder.** `releases/<v>/logs/` (the modules repo: the root's `releases/<v>/logs/` holds the
shared run), gitignored in every repo:

| File | Written by | Content |
|---|---|---|
| `run.json` | `init`, each `record` | `schema` (1), `run_id` (UTC `YYYYmmddTHHMMSSZ` + 4 hex digits), `started`, `repo`, `version`, `tag`, `base_commit` (`git rev-parse HEAD`), `tree`, `exclude`, `tools_version`, `tools_commit` (`<venv>/xewe-tools-commit`, else HEAD of the source checkout, else `-`), `host` (OS, architecture, Python), `steps` (every attempt, in order) |
| `<n>-<kind>[-<chip>][-<label>].log` | `record` | the step's stdout and stderr, redacted; `n` counts every attempt of the run |
| `<stem>.xml` | `record --junit PATH` | the JUnit file the command wrote, redacted value by value (below) |
| `<stem>.build_config.toml` | `record --kind setup` | `build/config/build_config.toml` (pins, toolchain) |
| `<stem>.meta.json`, `<stem>.compile.log` | `record --kind build\|flash --chip C` | `build/builds/<C>/out/meta.json` (or `--meta`; its string values redacted), and for build the compile log |
| `release_card.draft.md` | `render` when it refuses | the card with the refusal reasons on top, for diagnosis; never committed |

A step in `run.json`: `n`, `name` (`<n>-<kind>[-<chip>][-<label>]`), `key` (kind, chip, label and the
redacted argv: what makes two attempts the same step), `kind`, `chip`, `label`, `argv`, `run_id`,
`start`, `end`, `seconds`, `exit` (the command's own code; 127 when it could not start), `attempt`, `log`, `format`, `junit` (or `junit_missing`),
`build_config`, `meta`, `compile_log`, for flash `firmware` (relative to the root) and `fw_sha256`
(of `--firmware`, default the one `*.bin` in `build/builds/<C>/out/`), and `files` (sha256 of every
file the step wrote into the folder).

**Actions.**
- `init --dir D --repo OWNER/NAME --version V [--tag T] [--exclude DIR]... [--resume]`: empties `D`
  and writes `run.json`. `D` must lie inside the work tree (not be its root or above it), hold no
  tracked file, and be missing, empty or a previous run folder of the same repo and version
  (`run.json` naming them, and nothing but `run.json`, `run.tmp`, `release_card.draft.md` and step
  files `<n>-…`, no subfolder); anything else (`--dir releases/<v>` holding `notes.txt`, `--dir .`)
  is exit 2 and nothing is deleted. Only those files are deleted. `--resume` keeps an existing
  `run.json` (prints `resuming run <id> (N steps recorded)`) when the base commit and the tree
  fingerprint are unchanged; otherwise it warns `cannot resume …` and starts a new run.
- `record --dir D --kind K [--chip C] [--name LABEL] [--junit PATH] [--format core-host] [--firmware
  BIN] [--meta FILE] [--resume] -- CMD...`: runs `CMD` in the root (stdin inherited; output also
  echoed, redacted), records the step with `CMD`'s own exit code (127 when it cannot start) and
  exits 0 when `CMD` exited 0, 1 when it exited non-zero, 3 when it could not start (X-07). The
  `--junit` file is deleted before `CMD` runs, so a file left by an earlier run never counts. `--chip`
  is required for `board`, `flash` and `build`; `unit` and `board` need `--junit` or `--format`
  (exit 2), since a test step without results proves nothing. `--resume`: a step whose latest attempt with the same
  key exited 0 is not run again (`<name> is already green in run <id>; not run again`, exit 0); any
  other step runs as a new attempt. pytest runs pass `--junitxml=PATH` themselves (`-o
  junit_family=xunit1` recommended; the properties below are written in both families);
  `--format core-host` reads the test rows from the log instead. A chip with a board is recorded
  as `record --kind flash --chip C -- xewe flash --chip C --define …` followed by `record --kind board
  --chip C --junit PATH -- xewe test --chip C --define … -- --junitxml=PATH -o junit_family=xunit1`
  with the same defines (or only `XEWE_TEST_DEFINES`): the session reuses the flashed image, so its
  `xewe_fw_sha256` is the flash step's `fw_sha256`.
- `render --dir D --notes FILE --out FILE [--chips LIST] [--hardware LIST] [--require KINDS] [--title
  NAME] [--tests PREFIX]...`: writes the card, or refuses (below). `--tests` shows only the tests whose
  id starts with a prefix (one module's card out of the modules repo's shared run, `modules/<m>/`);
  the hardware rules then look at those rows, while a FAIL or ERROR anywhere in the run still refuses. `--chips`: the chips built in this run (none: a repo
  without firmware); `--hardware` (a subset of `--chips`): the chips whose board was attached;
  `--require` defaults to `setup,validate,unit,build,board` with `--chips` and `setup,unit` without.
  The title is `--title`, else the repo name. Exit 2 for a chip outside c3/c6/s3, a `--hardware` chip
  not in `--chips`, an unknown kind or a missing notes file. `--chips`, `--hardware` and `--require`
  are kept in the data block.
- `verify --card FILE [--dir D]` (the tag run): `D` defaults to `<card folder>/logs`. Exit 1 when the
  card was edited after render (its `card_sha256`, below, does not match: `… was edited after render`),
  when a `testcard.md` is in the card's folder, when `git status --porcelain` shows a change outside
  the excluded folders (a rename counts with both paths), when the fingerprint of HEAD's tree is not
  the card's, or when a file listed under "Local logs" is missing from `D` or has another sha256 (`…
  re-run \`--prepare <v>\` here`). Exit 2 when the card cannot be read or its last line is not a data
  block of valid JSON (every action that reads a card).
- `verify --ci --card FILE --junit-dir D [--meta-dir D] [--size-tolerance 0.5] [--unit-only]`: the CI
  comparison (below); `D` holds the CI run's JUnit `*.xml` (and core host output as `*.log`/`*.txt`), the meta
  dir any number of `meta.json` files. Every file is read whatever its name (subfolders too); the
  workflows use `tests-<chip>.xml` (xewe-release.yml, one per chip job), `tests-unit.xml`
  (xewe-modules-tests.yml and the tools' ci.yml), `tests-unit.log` (xewe-core-tests.yml: the `run.sh`
  output) and `build-<chip>.meta.json` (a meta counts when it has `type` and `sketch_size`). A
  `--meta-dir` that is not a directory and a negative `--size-tolerance` are exit 2.
- `fingerprint [--card FILE [--check]] [--exclude DIR]...`: prints the tested-tree fingerprint
  (below). With `--card` it uses the card's `exclude` list, exactly as `verify` does; `--check` then
  prints `<card>: tree … matches` and exits 0, or `… is not the card's …` and exits 1, the only
  exit 1 (`--check` without `--card`, or a card with no valid data block on its last line, is exit
  2; the card jobs fail on it instead of skipping the card). The `card` jobs of xewe-core-tests.yml and xewe-modules-tests.yml find
  the card of the commit under test with `fingerprint --card <card> --check`, so CI and the tools
  share one implementation of the fingerprint.

**Redaction** (inside `record`, so no raw log is stored; also the argv in `run.json` and every copied
file): each value of the dotenv file (§13, found from the root) and of the environment whose key
matches `(?i)(PSW|PASS|PASSWORD|SECRET|TOKEN|KEY|SSID)` becomes `«redacted:<KEY>»` (values shorter
than 4 characters, `true`, `false`, `none`, `null` and `yes` are left alone; the longest value is
replaced first), in each form it can take in a log: raw, XML-escaped (`&amp;`, `&lt;`, `&gt;`,
`&quot;`, `&apos;`: a JUnit message), JSON-escaped and URL-encoded (`%XX`, and `+` for a space);
then `$HOME` becomes `~` where it is a whole path part (`/home/user/x`, not `/home/username`). Logs,
the argv and `build_config.toml` are redacted as text. The JUnit copy is parsed and redacted value
by value: element text and attribute values, never tag or attribute names, `<testcase>` `name`,
`classname`, `file`, `line`, `time`, or the `xewe_kind`, `xewe_needs`, `xewe_nodeid`, `xewe_chip`,
`xewe_fw_sha256` and `xewe_fw_match` property values; the meta.json copy has its string values
redacted. In these structured copies a secret shorter than 8 characters is replaced only as a whole
word, so an SSID such as `test` never rewrites `test_x`. The tools read the dotenv file; nothing
prints it.

**Tested-tree fingerprint.** `sha256:` + sha256 over the lines `"<mode> <blob> <path>\n"`, sorted by
path, for every path of `git ls-files -s` outside the excluded folders: `releases/`, the run folder
and each `--exclude` (`*` matches exactly one path part, e.g. `modules/*/releases`, not
`modules/a/b/releases`). `mode` is the index mode (what a CI checkout of the commit has, whatever the
file system says about exec bits). `blob` is `git hash-object --stdin-paths` of the working file for
a regular file (no `-w`; clean filters apply, so a Git LFS file counts as its pointer), the blob id
of the link text for a symlink (hashed by the tools, never passed to git), and the index commit for
a submodule (`160000`); a tracked path missing from the work tree is `000000 deleted <path>`. With `--prepare` refusing a dirty tree,
the fingerprint of the prepare commit equals the one recorded at `init`. CI computes it only through
`xewe release-card fingerprint`/`verify` on the pristine checkout (in xewe-release.yml before the
release rows are downloaded and `xewe release --assemble` rewrites `xewe.toml`).

**JUnit properties** (the test plugin, §9). At collection every test gets `user_properties`:
`xewe_kind` (`board` with the `board` marker, explicit or automatic, else `unit`), `xewe_needs`
(`hardware` when it uses `board`, `firmware` or `serial`; `compile` when it uses only `compiled`;
else `none`), `xewe_nodeid` (the source path: `build/modules/tests/<slug>/` becomes
`modules/<slug>/tests/`), `xewe_chip` (the session chip; empty outside a project). A test that uses
the board gets, when its call starts, `xewe_board` = `<chip> sn=<USB serial number or -> port=<port>`
(for native USB-Serial/JTAG the serial number is the MAC), `xewe_fw_sha256` (sha256 of the image
`compiled` built for the session, the one flashed) and `xewe_fw_match` (`true` when the last `Build
Timestamp <t>` line of the boot banner equals `timestamp_param` of the image's meta.json, `false` when
it differs, `unknown` when either is missing).

**Result vocabulary and mapping** (render, one row per test and chip):

| JUnit testcase | Properties | Result | Detail |
|---|---|---|---|
| `<error>` | any | `ERROR` | first line of the message |
| `<failure>`, or `<skipped message="xfail-marked test passes unexpectedly">` (strict XPASS) | any | `FAIL` | first line of the message |
| `<skipped type="pytest.xfail">` | any | `XFAIL` | the reason |
| `<skipped>` whose message contains `compiled, not run` | any | `SKIP (compiled, not run)` | the text after `compiled, not run: ` |
| other `<skipped>` (an env gate, a missing broker …) | any | `SKIP` | the message |
| no child | kind `board` with `xewe_board` | `PASS (hardware)` | `board <chip> sn=<id> fw <sha12> <defines>` (defines from that chip's flash meta) |
| no child | kind `board`, needs `compile` | `PASS (compile only)` | `built for <chip>, no board needed` |
| no child | kind `board`, needs `hardware`, no `xewe_board` | `ERROR` | `hardware test passed without a board record` |
| no child | anything else (kind `unit`) | `PASS` | time |

A unit row's chip is `-`. Without `xewe_nodeid` the id is `<file>::<classes>::<name>`. Core host
output (`--format core-host`): a `== <std>: tests` line names the run, each `ok   <name>` line is
`PASS` and each `FAIL <name>` line is `FAIL` of `tests/unit/run.sh::<name>[<std>]` (indented `  FAIL
file:line` check lines are not rows).

**Format** (`releasecard.CARD_TEMPLATE`; golden file `tests/fixtures/releasecard/golden_card.md`):

```
# <title> <version> release card

| Field | Value |
|---|---|
| Repository | <OWNER/NAME> |
| Release | <version> (tag `<tag>`) |
| Card generated | <UTC>, run `<run id>`, xewe-os-tools <version> (`<commit>`) |
| Tested commit | `<base commit>` |
| Tested tree | `sha256:<64 hex>` |
| Result | <U> unit passed; <chip>: <H> passed on hardware (board sn=<id>), <C> compile only, <R> compiled, not run; …; <S> skipped[; <X> xfail]; <F> failed |

## 1. Release notes            notes.txt verbatim in a text block
## 2. Metadata
### Pins                        Component | Ref | Commit | Source (build_config.toml [installed] of the setup step)
### Toolchain and host          xewe-os-tools | arduino-cli | esp32 core | Python | Host
### Images                      Chip | FQBN | Partition scheme | App partition (B) | Flash used (B) | % | Static RAM (B) | RAM max (B) | Warnings | Log
## 3. Boards                    Chip | Board id | Port | Defines | Firmware sha256 | Meta timestamp | Banner match | Flash step | Flash time
## 4. Validator and checks      Step | Command | Exit | Time | Summary line; then the last 20 lines of each log
## 5. Tests
### Runs                        Run | Command | Chip | Board | Unit passed | Hardware passed | Compile-only passed | Compiled, not run | Skipped (other) | XFAIL | Failed | Exit
### Every test                  Test | Kind | Chip | Result | Detail (sorted by file, name, chip)
## 6. Local logs                File | Bytes | sha256 of every file in the run folder (the draft excepted)
## 7. Not covered               chips without a hardware run; skips grouped by reason (first 3 tests each); manual checks pointer
## 8. Retries                   Earlier attempt | Attempt | Exit | Time | Log, or "None."

<!-- xewe-card-data {"schema":1,"tree":…,"run":…,"tests":[[id,kind,chip,result]…],"images":{"<chip>":{"sketch_size":…,"static_ram":…,"config":{…}}},"exclude":[…],"logs":{"<file>":"<sha256>"…},"repo":…,"version":…,"commit":…,"chips":[…],"hardware":[…],"require":[…][,"only":["<prefix>"…]],"card_sha256":…} -->
```

Images come from each chip's latest build step: the copied meta.json, else the last `Sketch uses` and
`Global variables use` lines of the compile log (`%` is flash used over the app partition). Section 4
lists the setup, validate and check steps; build and flash steps show in Images and Boards, unit and
board steps in Runs. A section with nothing to show says so in one sentence (`No firmware images were
built in this run.`, `No board run (no --hardware chip).`). Table cells escape `|`; the data block is
one line of JSON with `<` and `>` escaped, always the card's last line, and every action reads it
from there only, never from the notes or log tails embedded above it. `card_sha256` is the sha256
of the card body (every byte before the data block's line) followed by the data block's JSON
without `card_sha256` (keys sorted, no spaces): any edit to either after render changes it.

**Refusal rules.** `render` exits 1, writes no card (`--out` is left as it is), prints `refused:
<reason>` per reason on stderr and writes `release_card.draft.md` when:
- a step has another run id, or started before `run.json`;
- the notes contain `<!-- xewe-card-data` (the data block must be the card's only one);
- a file a step wrote is missing or changed since (`… changed after it was recorded`);
- the tree fingerprint is not the one `init` recorded;
- a required kind is missing (`build` and `board` for every `--chips` chip; `flash` for every `--hardware` chip);
- a `unit` or `board` step produced no test rows;
- the latest attempt of any step exited non-zero, or a `--junit` step wrote no JUnit file;
- any test is `FAIL` or `ERROR`;
- a `--hardware` chip has no `PASS (hardware)` row;
- a hardware row's `xewe_fw_sha256` is not that chip's flash step `fw_sha256`, its `xewe_fw_match` is not `true` (`false`, or `unknown` when the boot banner was never seen), or its `xewe_board` names another chip.

A successful render removes an old draft and prints `release-card: <out> (<result line>)`.

**`--resume`.** After a failure (a board that dropped off USB, a flaky step), `release.sh --prepare <v>
--resume` calls `init --resume` and `record --resume` for every step in the same order: green steps
are skipped, the others run again as new attempts. The card uses the latest attempt of each step and
lists the earlier ones under "Retries" with their exit codes.

**CI comparison** (`verify --ci`; exit 1 on any block, before `gh release create`):
- **Block:** the fingerprint of the CI checkout is not the card's; the set of test ids differs
  (chips ignored: CI runs every chip); any CI `FAIL` or `ERROR` (marked `(PASS on the card)` when it
  was); an image size (`sketch_size`, `static_ram`) of a CI meta.json with the card's chip and defines
  differs by more than `--size-tolerance` percent (default 0.5); a `testcard.md` next to the card;
  a card edited after render (`card_sha256`).
- **Allowed** (printed as `allowed: …`): a card PASS that is a CI skip for `compiled, not run` or an
  env gate (the reason names an `UPPER_SNAKE` environment variable, case-sensitive, says `not set`
  or names a golden dir); a chip with no CI image built with the card's defines. The card's
  `chips`, `hardware` and `require` are printed as `allowed: card rendered with chips …, hardware
  …, require …`.
- **Warning:** a card PASS that is a CI skip for another reason; an image size difference within the
  tolerance (`__FILE__` paths differ between the local and the runner checkout).
- **Scope.** A module card (`render --tests PREFIX`) records the prefixes as `"only"` in its data
  block; the CI rows outside them are not compared, so each module's card is checked against the
  modules repo's one CI run.
- **`--unit-only`** (the `card` jobs of xewe-core-tests.yml and xewe-modules-tests.yml: CI runs
  `tests/unit/run.sh` or `xewe test --unit-only`, no board session): only the card's rows of kind
  `unit` are compared, with the CI rows of kind `unit`; the card's board rows are listed as
  `allowed: N board tests on the card not compared (unit-only CI)`. Within the unit rows every rule
  above holds: a unit id on the card but not in CI (or the reverse) blocks, so a unit test missing the
  `unit` marker (which `--unit-only` deselects) blocks too; a CI FAIL/ERROR blocks; image sizes are
  compared as above (the modules job uploads its c3 build meta). Core's unit rows are its `run.sh`
  `ok`/`FAIL` lines in both places (no JUnit, so the ids are the same). Harness and product run the
  full comparison in xewe-release.yml, where every test is collected per chip.
