# xewe-os-tools — specification

Status: spec for agent A5 (phase 1, step 2.1). Written 2026-10-07 by A4.
Replaces `xewe-os-build-toolchain` (scripts/{mac,linux,windows}, `install_arduino_esp32.sh`), the
module installer half of `xewe-os/setup.sh`, and the per-module `scripts/validate.sh`.
Locked decisions are published in `xewe-os/ARCHITECTURE.md` (the template repo); the working copy is the tracker `wip/xewe-labs/priorities.md`. This spec cites them by number.

Sources read: toolchain `README.md`, `CLAUDE.md`, `scripts/common/paths.sh`, all of `scripts/linux/*`
and `scripts/mac/*` (linux `build/compile/upload/listen_serial/release/format` are one-line forwarders
to `mac/`, so the mac files are the real implementation), `scripts/windows/` (skimmed: same flow,
winget, no release), template `xewe-os/setup.sh`, `build/{release_matrix.csv,required_libraries.txt,version_state,.gitignore}`,
`static/firmware/releases/2.0.0/*`, `xewe-os-module-wifi/scripts/validate.sh`, `module.properties`
of all six modules, `xewe-os-modules/repositories.txt`, `org-tooling/.github/guidelines/*.md`.
No read was blocked.

---

## 1. Goals / non-goals

**Goals**
- One Python package, one console script `xewe`, for setup, build, flash, serial, test, boards,
  modules, manifest, release (D10). Same code on Linux and macOS, x86_64 and arm64.
- Zero sudo, zero system package managers: the toolchain lands once per machine in
  `~/.xewe-os/build-tools/` (`XEWE_HOME` overrides `~/.xewe-os`), everything else in `<project>/build/`
  (layout in §6).
- Reproducible: `xewe.toml` pins core, modules, tools (D11, D12); setup is idempotent and resumable.
- Relocatable: no absolute path into the project is stored; a project directory can be moved or renamed.
- No-board is first class (D6, D22): every hardware command compiles, then reports
  "compiled, not run" and exits 0, unless `--require-board`.
- Bit-compatible outputs: same FQBN options (minus `JTAGAdapter`), same merged-image name,
  same `manifest.json` schema, same `static/firmware/releases/<version>/` layout.

**Non-goals**
- OTA, partitions other than `no_ota`, chips other than C3/C6/S3 (D1, D2).
- PlatformIO, on-device Unity tests (D5, D14).
- Windows in phase 1 (deferred; nothing below may assume POSIX-only paths or `/dev/*` in core logic).
- Pushing, tagging, or creating GitHub releases (agents never write git state; `release` prints commands).
- The C++ formatter (`tools/code_formatter/`) — out of scope; it stays where it is until a later step
  decides to move it (see §14).

---

## 2. Package layout

```
xewe-os-tools/
├── pyproject.toml
├── README.md  AGENTS.md  LICENSE.txt  .gitignore
├── src/xewe/
│   ├── __init__.py        __version__ = "0.1.1"
│   ├── __main__.py        python -m xewe
│   ├── cli.py             argparse: subcommands, global flags, exit codes
│   ├── project.py         find project root (walk up to xewe.toml), Paths dataclass (build/ layout + shared toolchain, §6)
│   ├── lockfile.py        read/write xewe.toml (the manifest), defaults, validation
│   ├── manifest.py        xewe manifest show / update
│   ├── tomlw.py           minimal TOML writer (tomllib reads; stdlib has no writer)
│   ├── pins.py            tool versions shipped with this release (arduino-cli, esp32 core, URLs)
│   ├── fetch.py           HTTP download (urllib) with .part + resume + sha256; git clone/ls-remote wrappers
│   ├── arduino.py         arduino-cli install, env, core install (retry + staging fallback), compile
│   ├── chips.py           c3/c6/s3 table: FQBN board, chip family, esptool id
│   ├── setup.py           xewe setup steps + build_config.toml
│   ├── build.py           compile, XeWeBuildInfo generation, builds/<chip>/out/ artifacts
│   ├── boards.py          port scan, VID:PID filter, esptool probe, boards.toml
│   ├── esptool.py         locate core-bundled esptool, run write-flash / chip-id
│   ├── flash.py           xewe flash
│   ├── serialio.py        Console: open, reset, listen with timestamps, send/expect, boot-banner wait
│   ├── provision.py       xewe provision: answer the first-boot prompts
│   ├── dotenv.py          settings and credentials from .env (§15)
│   ├── modules.py         registry of a modules checkout, resolve deps, generate build/modules/ + src/Modules.h, validate
│   ├── release.py         release matrix, static/firmware/releases/<version>/
│   ├── doctor.py          environment checks
│   ├── report.py          output helpers (status lines, --verbose, no-board summary)
│   └── testing/
│       ├── __init__.py
│       └── plugin.py      pytest plugin: fixtures board/firmware/serial, markers, summary
└── tests/                 self-tests (§12)
    └── fakes/arduino-cli  fake arduino-cli (Python script) used by the self-tests
```

`pyproject.toml`:

```toml
[build-system]
requires = ["flit_core>=3.9,<4"]
build-backend = "flit_core.buildapi"

[project]
name = "xewe-os-tools"
dynamic = ["version", "description"]
requires-python = ">=3.11"
license = { file = "LICENSE.txt" }
dependencies = ["pyserial>=3.5", "pytest>=8.0"]

[project.scripts]
xewe = "xewe.cli:main"

[project.entry-points.pytest11]
xewe = "xewe.testing.plugin"

[tool.flit.module]
name = "xewe"
```

**Dependencies.** stdlib (`tomllib`, `urllib`, `tarfile`, `zipfile`, `hashlib`, `subprocess`,
`argparse`, `json`, `shutil`) + `pyserial` + `pytest`.
- `pytest` is a deviation from "stdlib + pyserial only": D14 makes pytest the test runner and the
  `pytest11` entry point is how module tests get fixtures with no conftest. All of pytest's
  dependencies are pure Python, so there is no wheel/ARM64 risk. Making it an optional extra would
  just move the same install into setup.
- `esptool` is **not** a package dependency and is **not pip-installed** (change from the brief).
  The pinned esp32 core already ships an esptool binary for every host
  (`esptool_py` 5.3.1 for core 3.3.12, verified to have an `aarch64-linux-gnu` build). Using it
  ties esptool to the core pin, avoids pip-building `cryptography`/`PyYAML` on new Pythons, and saves
  one install step. `esptool.py` locates it by glob:
  `<arduino data>/packages/esp32/tools/esptool_py/*/esptool` (the shared
  `~/.xewe-os/build-tools/arduino15` by default; `esptool.exe` later on Windows).
  Escape hatch: `XEWE_ESPTOOL="<command>"` (e.g. `python -m esptool`) overrides it.
- Python ≥ 3.11 (for `tomllib`). Tested on 3.14 (this machine).

**Installation into a template project** (A6 writes these; the package must support them):

`setup.sh` (thin bootstrap, ~40 lines bash, no logic beyond this):
1. Find `python3` ≥ 3.11, or exit with a message.
2. Read `[tools] ref` and `repo` from `xewe.toml` with `python3 -c 'import tomllib…'`.
3. Get the tools source: `$XEWE_TOOLS_SOURCE` (local directory, used in phase 1 because nothing is
   pushed) or a checkout of `<repo>` in `build/tools`. The ref may be a tag (`git clone --depth 1
   --branch <ref>`; skipped when `git -C build/tools describe --tags --exact-match` is that tag), a
   branch (cloned the same way, then `git fetch` + `git reset --hard origin/<ref>` on every run, so a
   moving `main` is picked up without tags) or a commit SHA (fetched and checked out once).
4. `python3 -m venv build/tools/.venv`; if `build/tools/.venv/bin/python -m pip --version` fails, run
   `build/tools/.venv/bin/python -m ensurepip --upgrade`.
5. `build/tools/.venv/bin/python -m pip install --quiet --upgrade <tools source dir>` (non-editable;
   skipped when the source is not local and `build/tools/.venv/xewe-tools-commit` holds the checkout's commit).
6. `exec build/tools/.venv/bin/python -m xewe setup "$@"`.

`run.sh`: `exec "$(dirname "$0")/build/tools/.venv/bin/python" -m xewe run "$@"` (with a "run ./setup.sh
first" message if missing).

---

## 3. Command surface

Global flags (any position before the subcommand): `--project DIR` (default: nearest ancestor of
CWD containing `xewe.toml`), `--verbose` / `-v` (print every subprocess command line and its full
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
| 2 | usage error (argparse, conflicting flags, unknown chip/module) |
| 3 | not set up / tool missing (`build/config/build_config.toml` absent or stale, `src/Modules.h` or `build/modules/` missing, arduino-cli/esptool missing) |
| 4 | board access disabled (`--no-board` / `XEWE_NO_BOARD=1`) for a command that needs a board, board required but none found (`--require-board`; for `xewe test` too), explicit `--port` not present, several boards and none chosen, or the port did not come back after esptool reset the board |

**Chip selection rule (build/flash/test/run):** `--chip` → chip of the single attached board
(§5) → `[project] chip` in `xewe.toml` → `c3`. `--all-chips` = c3, c6, s3 in that order (D15).

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
| `xewe setup` | `[--latest] [--modules LIST\|all\|none] [--force] [--core-source DIR] [--modules-source DIR]` | §6. Installs the shared toolchain (once per machine) and everything else into `build/`, then runs `modules generate` | n/a (never needs a board) |
| `xewe build` | `[--chip C \| --all-chips] [--define KEY=VALUE]... [--clean]` | §7. Compiles; writes `build/builds/<chip>/out/` | n/a; exit 0 on success |
| `xewe flash` | `[--chip C] [--port P] [--baud 921600] [--erase] [--no-build] [--require-board] [--no-board]` | Builds if `builds/<chip>/out` is missing, older than any source, or was built with other `--define` values/version/chip/FQBN options (`build.stamp`, §7), then §8 write-flash | prints status line, exit 0 (4 with `--require-board`) |
| `xewe serial` | `[--port P] [--baud 115200] [--reset] [--send CMD [--expect RE] [--timeout 10] [--boot-timeout 90]] [--duration S] [--no-input] [--timestamps] [--log FILE] [--require-board] [--no-board]` | Listen with timestamps until Ctrl-C/`--duration` (on a terminal without `--no-input`: interactive, typed lines are sent, board lines printed raw unless `--timestamps`; Ctrl-D also exits) (opening the port resets the board on native USB; the boot log is shown), or wait for the boot to finish, send one command and wait for a regex (§8) | `no board attached; nothing to listen to`, exit 0 (4 with `--require-board`) |
| `xewe provision` | `[--port P] [--name NAME] [--modules all\|none\|LIST] [--timezone GMT+HH:MM] [--env\|--from FILE] [--timeout 60] [--no-reset] [--log FILE] [--no-board]` | §8, §15. Resets the board and answers its first-boot prompts (name, modules, Wi-Fi, timezone); exit 0 also when already provisioned, 2 bad/missing settings (checked before the port is opened), 1 prompt timeout or unexpected sequence | `no board attached; nothing to provision`, exit 4 |
| `xewe test` | `[--chip C \| --all-chips] [--port P] [--module SLUG]... [--unit-only] [--require-board] [--no-board] [-v] [-- PYTEST_ARGS]` | §9. Runs pytest over project and selected-module tests | board tests compile and report compiled-not-run, exit 0 |
| `xewe run` | `[--chip C] [--port P] [--define K=V]... [--no-serial] [--no-input] [--timestamps]` | build → flash → serial, interactive on a terminal (what `run.sh` calls) | builds, prints status line, exit 0 |
| `xewe boards` | `[--no-probe] [--json] [--set-port P [--set-chip C]] [--clear] [--no-board]` | §5. Lists candidate ports and chips; writes `build/config/boards.toml` | `no board attached`, exit 0 (4 with `--require-board`) |
| `xewe modules list` | `[--json]` | Modules in the modules checkout: slug, id, version, deps, `*` if selected | — |
| `xewe modules select` | `LIST\|all\|none [--no-generate]` | Writes `[modules] selected` in `xewe.toml`, then generates | — |
| `xewe modules validate` | `[PATH]` | §10 rules over a modules repo checkout (default: the one setup uses, `[paths] modules`) | — |
| `xewe modules generate` | | §10. Rebuilds `build/modules/` and `src/Modules.h` from the manifest selection | — |
| `xewe manifest show` | `[--json]` | Manifest refs vs installed refs (from `build_config.toml`), drift marked `!` | — |
| `xewe manifest update` | `[core\|modules\|tools]... [--to REF]` | Resolves newest tag (§4) and rewrites `xewe.toml`; prints diff; does not run setup | — |
| `xewe clean` | `[--all] [--modules]` | Default: delete `build/builds/` and `build/tmp/`. `--all`: delete all of `build/` except `tools/` (the tools checkout and its venv; setup must re-run). `--modules`: also delete `build/modules/` and `src/Modules.h`. Never touches `~/.xewe-os/build-tools` (toolchain and shared modules checkouts) | — |
| `xewe doctor` | | Checks python, git, venv, the shared toolchain (`XEWE_HOME` resolved; cli and core present), arduino-cli version, core version, esptool, lock vs installed, free disk ≥ 6 GB, serial permissions (Linux: user in `dialout`/`uucp`), Rosetta 2 on Apple silicon (the esp32 core's `ctags` is x86_64: "bad CPU type" without it; probe `pgrep -q oahd` or `arch -x86_64 /usr/bin/true`; warns with `softwareupdate --install-rosetta --agree-to-license`), board scan | exit 0 if only warnings |
| `xewe release` | `--version X.Y.Z [--matrix FILE] [--notes FILE]` | §11 | n/a (no board needed) |

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

TOML, committed, the only dependency file the template commits (D11). It is authored, not
generated: the project's name, version and chip, the core/modules/tools refs, the module selection
and extra libraries; `setup.sh` reads it to know what to fetch, so a clone cannot set up without it.
The resolved commits live in `build/config/` (generated). Read with `tomllib`, written by `tomlw.py`
(keys in the order below, comments preserved only for the header comment block).

```toml
# xewe.toml: this firmware's manifest (pinned inputs). Edit by hand or with `xewe manifest update`.
# ./setup.sh installs exactly these refs into build/.
schema = 1

[project]
name = "xewe-os"        # optional; default: stem of the single .ino in the project root
version = "2.0.15"      # firmware version (replaces build/version_state, see §7)
chip = "c3"             # chip used when --chip is not given and no board is attached

[core]
repo = "https://github.com/xewe-labs/xewe-os-core"
ref = "1.0.0"           # library tags are X.Y.Z (git-and-releases.md)

[modules]
repo = "https://github.com/xewe-labs/xewe-os-modules"
ref = "v1.0.0"
selected = ["wifi", "web-interface"]   # dependencies are added at generate time, not written here

[tools]
repo = "https://github.com/xewe-labs/xewe-os-tools"
ref = "v0.1.1"

[libraries]             # extra Arduino libraries, cloned into build/libraries/<name> (replaces required_libraries.txt)
ArduinoJson = { repo = "https://github.com/bblanchon/ArduinoJson", ref = "v7.4.2" }

# [toolchain]           # optional overrides; defaults come from the tools release (src/xewe/pins.py)
# arduino_cli = "1.5.1"
# esp32 = "3.3.12"
```

Rules:
- `ref` is a tag (preferred) or a branch or a full commit SHA. Setup records the resolved commit in
  `build/config/build_config.toml`. For `[tools]`, `setup.sh` follows a branch's remote head on every run (§2).
- `--latest` (on `xewe setup`): for each of core/modules/tools, run `git ls-remote --tags --refs <repo>`,
  keep tags matching `^v?\d+\.\d+\.\d+$`, pick the highest by numeric tuple; if none, use the remote
  default branch HEAD with a warning. Installs those, **never edits `xewe.toml`**;
  `xewe manifest show` and `xewe doctor` then show the drift (`!`). `[libraries]` and `[toolchain]` are not
  affected by `--latest`.
- `xewe manifest update [core|modules|tools] [--to REF]` applies the same resolution (or `--to`) and
  rewrites the manifest; that is the only command that changes refs.
- `[modules] ref` is checked out once per machine and ref in `~/.xewe-os/build-tools/sources/xewe-os-modules/<ref>/`
  (§6 step 8); a branch ref is fetched and reset to the remote head on every setup, like `[tools]`.
- Local sources (`--core-source DIR`, `--modules-source DIR`, `XEWE_TOOLS_SOURCE`, and env
  `XEWE_CORE_SOURCE`, `XEWE_MODULES_SOURCE`) override `repo`+`ref` for one run, are recorded as
  `source = "local:<abs path>"` in `build_config.toml` (that file is not committed), and never touch the manifest.
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
matching `--chip` if unique, else exit 4 "several boards attached; pass --port" (D15: one board at
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
├── <name>.ino  Config.h  xewe.toml  setup.sh  run.sh ...
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

No `arduino-cli.yaml` is written; `~/.arduino15` and `~/Arduino` are never read or written.

Steps (each is skipped when its record in `build_config.toml [installed]` matches the wanted
version/ref and its artefact exists; `--force` redoes all):

| # | Step | Today's equivalent | Detail |
|---|---|---|---|
| 1 | Preflight | `ensure_base_tools_linux`, `ensure_python` | Need `git`; Python ≥ 3.11 (already true, we run in it); free disk ≥ 6 GB where the core goes. No package-manager installs: missing `git` is exit 3 with a hint. Creates `build/config/`. |
| 2 | Read manifest | — | Validate `xewe.toml`; resolve refs (`--latest` per §4). |
| 3 | arduino-cli | `ensure_arduino_cli` (apt/brew/install.sh into `/usr/local/bin`) | Map host: Linux x86_64 → `Linux_64bit`, Linux aarch64 → `Linux_ARM64`, Darwin arm64 → `macOS_ARM64`, Darwin x86_64 → `macOS_64bit` (Windows later: `Windows_64bit.zip`). Download `https://github.com/arduino/arduino-cli/releases/download/v<V>/arduino-cli_<V>_<plat>.tar.gz`, verify sha256 against `…/v<V>/<V>-checksums.txt`, extract `arduino-cli` to the shared `bin/arduino-cli-<V>` (skipped when it is there and reports `<V>`, so a second project downloads nothing), check its `version` contains `<V>`. Creates `build-tools/{arduino15,bin,arduino-user,downloads}`; steps 3-4 hold an exclusive `fcntl.flock` on `build-tools/.lock`, so two projects setting up at once do not install over each other. `XEWE_ARDUINO_CLI` replaces the download. |
| 4 | esp32 core | `ensure_esp32_core`, `install_arduino_esp32.sh` | Into the shared `arduino15` (or `--arduino-data` / `XEWE_ARDUINO_DATA`), skipped when `core list` already shows the pinned version; several core versions coexist there. `arduino-cli core update-index`, then `arduino-cli core install esp32:esp32@<esp32 version>`. Retry up to 5 times with 10/20/40/80 s backoff. If output contains `Head "<url>"` (connection-reset case from `install_arduino_esp32.sh`), download that URL with urllib into `$ARDUINO_DIRECTORIES_DOWNLOADS/packages/` and retry (max 10 such rescues). Verify with `arduino-cli core list --format json` (esp32:esp32 at the pinned version). |
| 5 | esptool | `ensure_venv`, `ensure_esptool` | Locate core-bundled esptool (§2); run `esptool version` to confirm it executes on this host. |
| 6 | Core library | `ensure_libraries` (clone, `rm -rf .git`) | `git clone --quiet --depth 1 --branch <ref> <repo> build/libraries/XeWeCore` (a SHA ref: clone + `git fetch --depth 1 origin <sha>` + checkout). Clone into `build/libraries/.tmp-XeWeCore` then rename (atomic). Keep `.git` (needed for commit record and `--latest`; arduino-cli ignores it). Local source: copy tree without `.git`. Wrong ref present → delete and re-clone. |
| 7 | Libraries | `required_libraries.txt` loop | Runs after step 10 (it needs the module selection). Same as 6 for every `[libraries]` entry of the manifest, plus every `depends_libraries` name of the resolved selected modules, looked up in the modules checkout's `libraries.toml` catalogue (`[Name] repo = "...", ref = "..."`), into `build/libraries/<name>`. **The manifest wins:** a name pinned in `[libraries]` is installed from the manifest and the catalogue entry is ignored. Each module library is logged as `library FastLED: from modules catalogue (3.10.3)` or `library FastLED: from xewe.toml (<ref>)`; a name in neither is a warning and not installed; `XeWeCore`/`XeWeOS` (the core) and a `(>=x.y.z)` suffix are ignored. The catalogue never edits `xewe.toml`. Recorded in `[installed] libraries.<name>` with `origin = "xewe.toml"` or `"modules catalogue"`; `xewe manifest show` lists these rows with their origin. |
| 8 | Modules repo | template `setup.sh` registry + `git clone` per module | Outside the project, once per machine and ref: `~/.xewe-os/build-tools/sources/xewe-os-modules/<ref>/` (`Paths.modules_checkout(ref)`), under the `build-tools/.lock` flock. Cloned as in 6 when missing (or with `--force`, or when its `origin` is another repo); a branch checkout is fetched and reset to the remote head on every setup (a failed fetch keeps it, with a warning); a tag or SHA is never fetched again. `XEWE_MODULES_SOURCE` / `--modules-source` is used where it is, nothing copied. `[paths] modules` records the absolute path; `modules list/select/validate`, the `libraries.toml` catalogue and `manifest show` read it. `clean --all` never touches it. |
| 9 | Module selection | whiptail checklist / `--modules` | `--modules LIST\|all\|none` writes `[modules] selected` (this is a manifest edit the user asked for; `--modules ""` = `none`). Zero modules is a valid project and `selected = []` is the default. If `selected` is empty and stdin is a TTY: numbered menu like today's `--text` menu (no whiptail dependency), where an empty answer means none; no TTY and empty → no prompt. Either way an empty selection prints one "no modules selected" line and continues. If the modules checkout holds no module at all (neither `modules/<slug>/module.properties` nor `xewe-os-module-<slug>/module.properties`), setup warns naming the source path and both layouts; explicitly requested modules that are not found are still exit 2. |
| 10 | Generate | template `setup.sh` staging + swap | `xewe modules generate` (§10). |
| 11 | Project files | `ensure_project_ino`, `ensure_project_config_h`, `ensure_gitignore` | Check exactly one `*.ino` in root and `Config.h` exist (do not create them: the template owns them). Write `build/.gitignore` = `*`. Do not touch the root `.gitignore`. |
| 12 | build_config.toml | `write_build_config` | Written last, atomically (`.tmp` + rename). |

`build/config/build_config.toml` (paths inside `build/` are relative to `build/`; the shared toolchain
and the `--arduino-data` / `XEWE_ARDUINO_CLI` overrides are absolute):

```toml
# generated by xewe setup; do not edit
schema = 1
tools_version = "0.1.1"
setup_completed = "2026-10-08T12:00:00Z"

[paths]                  # relative to build/, or absolute outside it
arduino_cli = "/home/me/.xewe-os/build-tools/bin/arduino-cli-1.5.1"
arduino_data = "/home/me/.xewe-os/build-tools/arduino15"
arduino_user = "/home/me/.xewe-os/build-tools/arduino-user"
libraries = "libraries"
modules = "/home/me/.xewe-os/build-tools/sources/xewe-os-modules/v1.0.0"   # or XEWE_MODULES_SOURCE
esptool = "/home/me/.xewe-os/build-tools/arduino15/packages/esp32/tools/esptool_py/5.3.1/esptool"

[installed]
arduino_cli = "1.5.1"
esp32 = "3.3.12"
core = { ref = "1.0.0", commit = "0123abc…", source = "https://github.com/xewe-labs/xewe-os-core" }
modules = { ref = "v1.0.0", commit = "…", source = "…" }
tools = { ref = "v0.1.1", commit = "…", source = "local:../xewe-os-tools" }
libraries = { ArduinoJson = { ref = "v7.4.2", commit = "…", source = "…", origin = "xewe.toml" } }
```

Fix for today's relocation bug: today `write_build_config` stores `project_root`, `venv_python_bin`,
`arduino_cli` etc. as absolute paths and `compile.sh`/`upload.sh` read them, so moving or renaming
the project breaks every script until setup re-runs. Here, `project.py` derives the root from the
CWD/`--project` and every stored path into the project is relative to `build/` (only the shared
toolchain, which does not move with the project, is absolute). One caveat: a moved venv has stale
shebangs, so `run.sh`/`setup.sh` must call `build/tools/.venv/bin/python -m xewe` (not the `xewe`
script) — this works after a move.

**Duration** (estimate, unverified on this machine): arduino-cli ~30 MB; esp32 core 3.3.12 for
aarch64 Linux is ~1.7 GB of downloads (computed from the package index: `esp-rv32` 556 MB,
`esp-x32` 314 MB, nine `*-libs` packages ~630 MB, esptool 72 MB, gdbs ~80 MB, core zip 51 MB) and
several GB on disk. Expect 5–15 min on broadband for the first setup on a machine, < 10 s for a no-op
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

Board options (from `compile.sh`, `compile.ps1`, `validate.sh`, minus `JTAGAdapter=default`):
`CDCOnBoot=cdc,CPUFreq=160,DebugLevel=none,EraseFlash=all,FlashMode=qio,FlashSize=4M,PartitionScheme=no_ota,UploadSpeed=921600`

Full FQBNs:
```
esp32:esp32:esp32c3:CDCOnBoot=cdc,CPUFreq=160,DebugLevel=none,EraseFlash=all,FlashMode=qio,FlashSize=4M,PartitionScheme=no_ota,UploadSpeed=921600
esp32:esp32:esp32c6:CDCOnBoot=cdc,CPUFreq=160,DebugLevel=none,EraseFlash=all,FlashMode=qio,FlashSize=4M,PartitionScheme=no_ota,UploadSpeed=921600
esp32:esp32:esp32s3:CDCOnBoot=cdc,CPUFreq=160,DebugLevel=none,EraseFlash=all,FlashMode=qio,FlashSize=4M,PartitionScheme=no_ota,UploadSpeed=921600
```

Steps per chip:
1. Require `build_config.toml` (else exit 3), `src/Modules.h` and `build/modules/library.properties`
   (else exit 3 "run `xewe modules generate`"). With zero modules selected, both exist and declare nothing.
2. Sketch dir: the project root if the folder name equals the `.ino` stem (arduino-cli requires
   `<dir>/<dir>.ino`); otherwise mirror `*.ino`, `*.h`, `*.cpp`, `src/` into
   `build/tmp/sketch/<stem>/` with `shutil.copy2` (mtimes kept so the cache stays warm). This is what
   happens after "Use this template" renames the repo; today's setup instead created a second `.ino`.
3. Generate `build/builds/<chip>/gen/XeWeBuildInfo/library.properties` + `src/XeWeBuildInfo.h`:
   ```c
   // generated by xewe build; do not edit
   #pragma once
   #define PROJECT_NAME "xewe-os"
   #define BUILD_VERSION "2.0.15"
   #define BUILD_TIMESTAMP "2026-10-08T12:00:00Z"
   #define BUILD_CHIP "c3"
   #define LED_PIN 8            // one line per --define / release-matrix column
   ```
   The template's `Config.h` does `#include <XeWeBuildInfo.h>` and wraps its own defaults in
   `#ifndef`. This replaces today's in-place rewriting of `Config.h` by `build.sh` (which dirtied
   a committed file on every build). `--define KEY=VALUE` replaces `--config_json`; values are
   emitted verbatim (quote strings yourself: `--define 'WIFI_SSID="net"'`).

   > **Warning:** `Config.h` must `#include <XeWeBuildInfo.h>` unconditionally. Guarding it with
   > `#if __has_include(<XeWeBuildInfo.h>)` makes arduino-cli's library discovery skip the
   > generated library, so the `--library build/builds/<chip>/gen/XeWeBuildInfo` defines silently never
   > reach the sketch (proven by A6). The cost: a plain Arduino IDE build (without `xewe`) fails on
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
     --warnings default \
     --jobs 0 \
     <sketch dir>
   ```
   (`XeWeBuildInfo` stays its own `--library`: module sources include it and must keep seeing it.)
   stdout+stderr go to `build/builds/<chip>/out/compile.log`; on failure print the last 15 lines matching
   `error` (as `validate.sh` does) and exit 1. `--verbose` streams the log live.
5. Artifacts into `build/builds/<chip>/out/` (directory emptied first; no timestamped history, no `latest`
   symlink, no copy of `src/` and libraries as today):
   - `<version>-<chip>-<project>.bin` ← `build/builds/<chip>/cache/<stem>.ino.merged.bin` (produced by the
     esp32 core 3.x itself; missing → exit 1 "core did not produce merged.bin").
   - `manifest.json` — byte-compatible with today:
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
   - `meta.json` — today's keys `type, chip_family, project_name, version, timestamp_param, config
     (the --define dict, sorted), fqbn, fqbn_extra (""), compile_time_sec, artifacts{binary_filename,
     path_rel_binary, path_rel_manifest_json, path_rel_meta_json}`; the `path_abs_*` keys are
     dropped (they leaked `/Users/user/...` into committed releases); new keys `tools_version`,
     `arduino_cli`, `esp32_core`, `core_ref`, `modules_ref`, `modules` (selected+resolved slugs),
     `sketch_size` (bytes, from "Sketch uses N bytes"), `sketch_size_percent` (the "(NN%)" of
     program storage on the same line, `null` if absent), `warnings` (count). `path_rel_*` are
     relative to the project parent (`xewe-os/build/builds/c3/out/...`).
   - `build.stamp` — sha256 of chip, full FQBN (board options), project version and the sorted
     `--define` list. `flash`/`run` treat the binary as stale when the stamp differs from the
     inputs of the current call (a build with `--define X=1` followed by a plain `xewe flash`
     rebuilds), in addition to the mtime check against every source (sketch files, `src/`
     including `Modules.h`, `build/libraries/`, `build/modules/library.properties` and `build/modules/src/`).
6. Print a one-line summary (time, size with flash percentage, warnings):
   `ok     c3  51 s  Sketch uses 1123456 bytes (85%)  0 warnings`.

**Versioning — recommendation: drop auto-bump.** Today a successful upload bumps PATCH and
BUILD_ID in the committed `build/version_state`, so every dev flash dirties the repo, the running
firmware reports a version lower than the file, and `release.sh` then overwrites the file and drops
`BUILD_ID`. New rule: the version is `[project] version` in `xewe.toml`; `build`, `flash` and `run`
never change it; `BUILD_TIMESTAMP` distinguishes dev builds; `xewe release --version X.Y.Z` is the
only writer (and must be ≥ the current version, as today). Migration: if a legacy
`build/version_state` exists, `xewe setup` prints the `version = "M.m.P"` line to put in the manifest
and does not delete the file. `.github/guidelines/git-and-releases.md` ("Firmware PATCH is not
hand-written — build.sh bumps it") must be updated in the same change (A13 sweep).

**Parallelism**: one chip at a time, sequentially (each compile already uses all cores via
`--jobs 0`; ~50–65 s per chip from release 1.0.0/2.0.0 meta). `--all-chips` loops c3, c6, s3, continues
after a failure, prints a per-chip summary like `validate.sh`, exits 1 if any failed. Each chip has
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
  without a readable partition table is written whole at `0x0`. Verified on an ESP32-S3
  (2026-10-08): provisioning survives the flash.
- `--erase` runs `<esptool> --chip … --port … erase-flash` first, then waits for the port (below) (wipes NVS; what
  `EraseFlash=all` would do via `arduino-cli upload`, which we do not use). Default keeps NVS (see `flash_segments` above).
- If the board's detected chip ≠ the selected chip → exit 2 before flashing.
- If esptool fails at 921600, retry once at 460800 (CH340 boards), then exit 1.
- After each esptool command (erase-flash, write-flash) the hard reset re-enumerates a native
  USB port (it can linger, vanish, then come back): `wait_for_port` waits up to 10 s until the
  port has stayed present for 0.5 s in a row, so the next esptool call / `run` / tests open a
  stable port. Not back in time → exit 4.

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
  `SerialPort.cpp`); echo it as `> CMD`; then read lines until `--expect` regex matches (exit 0)
  or `--timeout` (default 10 s, exit 1, last 20 lines printed). Without `--expect`, collect output
  until 500 ms of silence and exit 0.
- Defaults: 115200 baud (today's `SERIAL_BAUD`).
- The old fallbacks (`arduino-cli monitor`, miniterm, `screen`) are dropped.
- Boot wait (`serialio.wait_for_banner`, shared by `xewe provision` and the test plugin §9):
  optionally reset, then wait for a regex until a deadline, retrying while a native-USB port is
  gone (first boot ends with `Initial Setup Complete`, `Rebooting`, a port drop, then
  `System Setup Complete`). Lines read before the reset (the boot the open itself started on
  native USB) are skipped, so only a match from the boot after the reset counts; ROM lines and a
  prompt printed twice are harmless. `xewe provision` and the `firmware` fixture open the port and
  reset deliberately right away, so the open-reset boot is cut short and the wait covers the
  second boot.

**Provision** (`provision.py`, `xewe provision`): answers the first-boot prompts of a freshly
flashed (or erased) board so nobody has to type them.
- Settings, in precedence order: flags (`--name`, `--modules`, `--timezone`), environment
  (`XEWE_DEVICE_NAME`, `XEWE_WIFI_SSID`, `XEWE_WIFI_PASSWORD`, `XEWE_TIMEZONE`,
  `XEWE_PROVISION_MODULES`), the dotenv file (§15: `--env`/`--from FILE`, `XEWE_ENV`, `.env` in the
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

  `Unable to join` / `Invalid choice`, a fifth `Selection:`, a fourth `! Invalid number`, any other
  `! …` error line, an input line for
  an unknown prompt, or `Name your device` after the reboot → exit 1 with the last 20 lines.
- Output: board lines echoed with timestamps as `xewe serial` (and `--log FILE`); any line that
  contains the password, typed or echoed, is replaced by `********` there and in the tail. Last
  line: `provisioned in N s: name "<name>"; modules <enabled>; wifi "<ssid>" (network N of M |
  custom SSID, not in the scan); timezone <y, board detected GMT+02:00 | n, set GMT-08:00 …>`.

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
- Options passed to the plugin: `--xewe-chip`, `--xewe-port`, `--xewe-require-board`,
  `--xewe-project` (and `--xewe-no-board` for plain pytest; `xewe test --no-board` sets
  `XEWE_NO_BOARD=1`, which the plugin honours). Extra args after `--` go to pytest verbatim.
- `--unit-only` → `-m unit`. `--all-chips` runs the session once per chip (three `pytest.main`
  calls), aggregating exit codes.

Markers (registered by the plugin): `unit` (pure logic; runs on the developer machine, never needs
a board or a build), `board` (needs firmware on a board). A test that uses `compiled`, `board`,
`firmware` or `serial` is auto-marked `board`.

Fixtures (`xewe.testing.plugin`, loaded through the `pytest11` entry point, so module test
folders need no `conftest.py`):

| Fixture | Scope | Behaviour |
|---|---|---|
| `compiled` | session | Builds the selected chip once (same code path as `xewe build`); a build failure fails every board test. |
| `board` | session | Depends on `compiled`; detects the board (§5). None, or board access disabled (`XEWE_NO_BOARD`/`--xewe-no-board`; no port is looked at) → `pytest.skip("compiled, not run: no board attached")`; with `--require-board` → `pytest.fail(...)`. Returns `Board(port, chip, serial_number)`. |
| `firmware` | session | Depends on `board`; flashes `build/builds/<chip>/out/` once per session, opens the session's one `Console`, pulses reset and waits up to 90 s for `System Setup Complete` (through a first-boot `Initial Setup Complete`/`Rebooting` and port drops), then 0.5 s of silence. `Name your device` (unprovisioned board) fails with a pointer to `xewe provision` and the runbook's first-boot provisioning. The wait is `serialio.wait_for_banner` (§8), shared with `xewe provision`. The console is closed at session end. Returns `Firmware(bin_path, version, chip, console)`. |
| `serial` | function | Depends on `firmware`; the session `Console` (never opened/closed per test), drained and with `lines` emptied at test start: `send(cmd)`, `expect(regex, timeout=10) -> re.Match`, `command(cmd, expect, timeout) -> re.Match`, `lines` (captured since the test started), `drain()`, `reset()`. Expect failures raise `AssertionError` with the last 20 lines. |

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

Example (illustrative only; A8 owns the final shape, Q1):

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
`XEWE_MODULES_SOURCE`, §6 step 8; layout per D4/plan step 3:
`modules/<slug>/{module.properties, src/<Folder>/, tests/, README.md}`). Until A8 fixes the contract,
`modules.py` also accepts today's layout (one `xewe-os-module-<slug>/` per module with
`module.properties` at its root), so A5 can test against the reference clones (read-only).

`module.properties` keys used: `slug, id, name, version, description, folder, include, declare,
depends_modules, requires_core` (new, D12; e.g. `requires_core=>=1.0.0`; today's
`depends_libraries=XeWeOS (>=0.1.0)` is accepted as a fallback and ignored for checks).
Parsing = today's `prop()`: first `key=` line wins, value is everything after the first `=`.

`xewe modules generate`:
1. Resolve: depth-first over `selected` in manifest order, dependencies first, error on cycle
   ("dependency cycle through module 'x'") — identical order to today's `visit()`, so `Modules.h`
   comes out the same for the same selection. Print `x requires y; adding it` for additions.
2. Stage `build/modules/` in `build/tmp/modules/`. The Arduino library `XeWeModules`: copy
   `modules/<slug>/src/<Folder>` → `src/<Folder>` (skip `.git`), so a module's
   `#include "../Wifi/Wifi.h"` keeps working (one library for all selected modules; arduino-cli only
   discovers a library from a header directly under its `src/`). Write `library.properties`
   (`name=XeWeModules`, `version=<modules ref, or the commit>`, `author`/`maintainer=xewe-labs`,
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
   and `build/modules/modules.lock` in today's format:
   `# generated by xewe: slug|folder|source|ref|commit` then one line per module
   (`source` = modules repo URL or `local:<path>`, `ref` = manifest ref, `commit` = checkout HEAD).
   With an empty selection the library is still written (`XeWeModules.h` includes nothing),
   `Modules.h` holds the header comment, `#pragma once`, `#include <XeWeModules.h>` and a
   `// no modules selected` comment, and `modules.lock` only its header line.
5. A `src/modules/` left by the previous layout is removed (`removed legacy src/modules/`) only when
   its `modules.lock` starts with the generated header line; otherwise it is left alone.

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

The modules repo needs no copy of this code (D21): its CI runs `./setup.sh --modules-source . --modules all`
in an `xewe-os` checkout, then `xewe modules validate` (the checkout setup used, here `.`),
`xewe build --all-chips`, `xewe test`.

---

## 11. Release (`xewe release --version X.Y.Z`)

1. Check the version is `X.Y.Z` and ≥ `[project] version` (else exit 2); check the working tree has
   no uncommitted changes except `xewe.toml` (warning only; we never run git writes).
2. Matrix: `release_matrix.csv` in the project root if present (format unchanged: `CHIP` column
   required, case-insensitive; `_BUILD_NOTES` notes only; every other column becomes a `--define`
   with today's typing: integers/`true`/`false` bare, everything else quoted), else rows c3, c6, s3.
   Legacy `build/release_matrix.csv` is read with a warning to move it.
3. Release notes: `--notes FILE`, else `$EDITOR` (default `vi`) on a temp file with today's 3-line
   header, which is stripped; result saved as `static/firmware/releases/<version>/release_notes.txt`.
4. For each row: `xewe build --chip <chip> --define …` with the release version, then move
   `build/builds/<chip>/out/{<bin>,manifest.json,meta.json}` into
   `static/firmware/releases/<version>/<col1>/<col2>/…` (folder per non-notes column, values with
   quotes/backslashes stripped, spaces → `_`, empty → `empty`; for the default matrix this is
   `<version>/c3/`, exactly today's layout). Write `build_notes.txt` when the row has notes, and
   `firmware_map.csv` with the header row minus `_BUILD_NOTES`, as today. Rewrite `path_rel_*` in
   `meta.json` to the release location.
5. Write `[project] version = "X.Y.Z"` into `xewe.toml`; create
   `static/firmware/releases/firmware-X.Y.Z.tar.gz` (tarfile, as today).
6. Print, do not run:
   ```
   git add xewe.toml static/firmware/releases/X.Y.Z
   git commit -m "release X.Y.Z"
   git tag -a vX.Y.Z -m "Release X.Y.Z"
   git push origin vX.Y.Z
   gh release create vX.Y.Z static/firmware/releases/firmware-X.Y.Z.tar.gz --verify-tag --title vX.Y.Z --notes-file static/firmware/releases/X.Y.Z/release_notes.txt
   ```
   Time: ~3 min for three chips.

---

## 12. Self-tests of the package

`pytest` from the repo root; no network, no arduino-cli, no board. Fakes: `tests/fakes/arduino-cli`
(Python script; selected with `XEWE_ARDUINO_CLI`, records argv+env to a JSON file, writes
`<stem>.ino.merged.bin` and a "Sketch uses N bytes" line, can be told to fail), a fake esptool
(`XEWE_ESPTOOL`), pyserial `serial_for_url("loop://")` plus a scripted fake port, and
`list_ports.comports` monkeypatched.

| File | Covers |
|---|---|
| `test_project.py` | root discovery, `--project`, relative paths, project moved after setup still works |
| `test_lockfile.py` | parse/validate/write round-trip, unknown keys rejected, defaults |
| `test_tomlw.py` | writer output parses back with `tomllib` for all value types used |
| `test_fetch.py` | asset name per (os, arch), checksum mismatch rejected, `.part` resume via a local HTTP server |
| `test_setup.py` | shared toolchain reused by a second project (no second download or core install), `XEWE_HOME` override, `build-tools/.lock`, step order, skip-when-recorded idempotency, `--force`, interrupted run leaves no `build_config.toml`, local sources, `--latest` tag selection from fake `ls-remote` output, `Head "<url>"` rescue path, module libraries from the modules `libraries.toml` catalogue vs a `[libraries]` pin in the manifest (manifest wins), missing/bad catalogue, `manifest show` origin, shared modules checkout per ref (reused by a second project, branch followed, other-repo checkout re-cloned), no `build/tmp` after setup |
| `test_arduino.py` | env vars (no `~/.arduino15`), exact compile argv per chip, FQBN strings (golden), no `JTAGAdapter` |
| `test_build.py` | `builds/<chip>/out/` contents, bin name, `manifest.json` byte-equal to golden built from 2.0.0 release files, `meta.json` keys, `XeWeBuildInfo.h` content, `--define`, sketch staging when folder ≠ stem, `--all-chips` continues after failure |
| `test_boards.py` | VID:PID filter, chip-id output parsing (sample esptool 5 output), cache by serial number, override precedence, no-board / one / several decision, `boards.toml` `[override]` preserved |
| `test_flash.py` | esptool argv, `--erase` (port wait after erase and after write), port not back → exit 4, baud fallback, no-board exit 0 vs `--require-board` exit 4, chip mismatch exit 2 |
| `test_serial.py` | timestamps, `\r` stripping, send/expect match and timeout (exit 1), reconnect, reset across a port drop, `wait_for_port` settle and timeout (exit 4); interactive mode: typed line sent, `--no-input` and non-TTY stdin listen only, EOF exit 0, reconnect, raw lines and no `> CMD` echo (timestamped with `--timestamps`, in `--send` mode, listen-only and the log), stdin flushed only when interactive on a TTY |
| `test_provision.py` | scripted first-boot transcript: answers in order, SSID by number (the real S3 list), rescan then `-3` custom, `! Invalid number` re-answered, board resuming at the network list, nudge only before the first prompt (fake clock), timezone `y` / `n` + offset, already provisioned sends nothing, nudge, port drop at reboot, prompt timeout exit 1 with masked tail, password never in stdout/log; settings precedence flags > env > dotenv file > defaults, settings from `<project>/.env` with no flags, exit 2 before the port, exit 4 without a board |
| `test_dotenv.py` | parsing (comments, quotes, `export`, no interpolation), errors without values, real environment wins, resolution order (`--env`, `XEWE_ENV`, project, tools checkout from `build_config.toml` or the package checkout), missing file is not an error, only the key count is logged, `.env.example` lists every key |
| `test_plugin.py` | `pytester`: no board → "compiled, not run" summary and exit 0; `--require-board` → pytest 1, `xewe test` 4; faked board: one port open per session, boot banner wait (first-boot reboot, unprovisioned, timeout); unit tests run; `--unit-only` runs only `unit` tests and builds nothing; board tests found under `modules/<slug>/tests/board/`; compile failure fails board tests; pins from the project `.env` reach `os.environ` (real environment wins) |
| `test_modules.py` | dependency order and cycle error against fixtures copied from the six real `module.properties`; generated `build/modules/` (`library.properties`, `XeWeModules.h`, `tests/<slug>/{board,unit}/`, `modules.lock`) and `src/Modules.h` golden, empty selection, legacy `src/modules/`, `build/modules-lib/` and `build/config/modules.lock` removal; every validator rule (tests layout included) has a failing fixture |
| `test_release.py` | matrix parsing and typing, folder layout, `firmware_map.csv`, version ≥ check, printed commands, never calls git |
| `test_cli.py` | every subcommand's `--help`, exit-code table, `--verbose`, `clean` (`--all` keeps `build/tools`, never touches `build-tools`) |
| `test_no_board.py` | `--no-board` / `XEWE_NO_BOARD=1`: discovery returns nothing (ports never listed), console never opens, flash/run build then exit 4, serial/provision/boards exit 4, `boards --set-port` still works, `xewe test --no-board` compiled-not-run exit 0 (4 with `--require-board`), plugin `--xewe-no-board`; `-v` after the subcommand |
| `test_doctor.py` | checks report missing pieces without raising; Rosetta check on Apple silicon (installed / missing), skipped elsewhere |

---

## 13. Migration notes

**Dropped on purpose**
- Per-OS script copies (`scripts/mac`, `scripts/linux` forwarders, `scripts/windows/*.ps1`),
  `paths.sh`, `_paths.ps1`, sourced bash `build_config` / `build_config.ps1` and `get_cfg`.
- System installs: Homebrew, apt/dnf/yum/pacman/zypper/apk, winget, `sudo`, `/usr/local/bin`,
  `~/.local/bin`, `~/.arduino15`, `arduino-cli config init`.
- `JTAGAdapter=default` from the FQBN.
- Absolute paths in `build_config` and `meta.json` (`path_abs_*`).
- Auto PATCH/BUILD_ID bump on upload; `build/version_state` (moved to `[project] version`).
- In-place rewriting of `Config.h` (`--config_json`, PROJECT_NAME/BUILD_VERSION/BUILD_TIMESTAMP)
  → generated `XeWeBuildInfo.h`.
- Timestamped `builds/<ts>-<ver>-<chip>-<project>/` history with `src/` and `libs/` snapshots and
  the `builds/latest` symlink → `build/builds/<chip>/out/`.
- `required_libraries.txt` → `[libraries]` in `xewe.toml`.
- whiptail checklist, registry `repositories.txt` + per-module repos → modules repo at a ref.
- Serial monitor fallbacks (arduino-cli monitor, miniterm, screen); pip-installed esptool.
- Creating missing `.ino`/`Config.h`/`release_matrix.csv` (the template owns them).
- `git tag`, `git push`, `gh release create` being executed (now printed).

**Kept bit-for-bit**
- Board options `CDCOnBoot=cdc,CPUFreq=160,DebugLevel=none,EraseFlash=all,FlashMode=qio,FlashSize=4M,PartitionScheme=no_ota,UploadSpeed=921600`.
- Merged image (written in pieces that skip blank data partitions, §8), name `<version>-<chip>-<project>.bin`, flash baud 921600, serial baud 115200.
- `manifest.json` schema and formatting (ESP Web Tools), `meta.json` keys except `path_abs_*`.
- `static/firmware/releases/<version>/<chip>/`, `release_notes.txt`, `firmware_map.csv`,
  `build_notes.txt`, `firmware-<version>.tar.gz`, tag name `v<version>`.
- `release_matrix.csv` format and column typing.
- Module resolution order, the declare form (now in `src/Modules.h`, the includes in `XeWeModules.h`), `modules.lock` line format.

---

## 14. Open points (with recommendation)

| # | Point | Recommendation |
|---|---|---|
| O1 | esp32 core version to pin | `3.3.12` (latest stable in the index on 2026-10-07; every tool has an aarch64-linux build). Fall back to `3.3.7` (referenced by `install_arduino_esp32.sh`, so likely what built 2.0.x) if Gate 1/2 compile fails on 3.3.12. |
| O2 | arduino-cli version | `1.5.1` (latest; `Linux_ARM64` asset and `1.5.1-checksums.txt` exist). |
| O3 | pytest as a runtime dependency | Accept (see §2). |
| O4 | esptool from the core vs pip | Core-bundled binary; `XEWE_ESPTOOL` override. |
| O5 | Where the firmware version lives | `[project] version` in `xewe.toml`; no auto-bump. |
| O6 | `XeWeBuildInfo` needs a one-line change in the template `Config.h` | A6 adds `#include <XeWeBuildInfo.h>` and `#ifndef` defaults. |
| O7 | Shared toolchain in `~/.xewe-os/build-tools` | Done: cli, core, esptool and downloads once per machine (saves ~1.7 GB of downloads and several GB of disk per extra project); `XEWE_HOME` moves it (CI, read-only homes), `XEWE_CACHE` the downloads, `--arduino-data` the core. |
| O8 | Disk: core installs libs for c5/h2/p4/s2 too (~370 MB of archives) | Accept for now; pruning risks breaking arduino-cli's install records. |
| O9 | `modules/<slug>` vs legacy layout | Support both until A9 ports the modules; drop legacy after Gate 3. |
| O10 | Code formatter (`tools/code_formatter/`) | Not part of this package in phase 1; decide later between `xewe format` and leaving it in core. |
| O11 | Windows | Keep `pathlib` everywhere, `.exe` suffix in `chips/arduino` lookups, `COMx` accepted by `--port`; no PowerShell. Implement after phase 1. |
| O12 | `serial` fixture scope | Function scope over a session-flashed board; reboot between tests only via an explicit `serial.reset()`. |

---

## 15. Configuration: `.env` (`dotenv.py`)

Credentials and bench settings come from a dotenv file that is never committed (`.env`, `.env.*`
git-ignored in the tools repo, `.env` in the xewe-os template; `.env.example` at the tools repo root
is the committed template).

- File, first found wins: (1) `--env FILE` (on flash, serial, provision, test, run, boards;
  `provision --from` is an alias), else `XEWE_ENV`; a named file must exist (exit 2); (2) `.env` in
  the project root (nearest ancestor with `xewe.toml`, i.e. the harness); (3) `.env` in the tools
  source checkout: `installed.tools.source = "local:<path>"` in `build/config/build_config.toml` (written by
  setup when `XEWE_TOOLS_SOURCE` is used), else `Path(dotenv.__file__).resolve().parents[2]` when
  that directory holds `pyproject.toml` (package installed from a path). No file is not an error.
- Format: `KEY=VALUE`; `#` comment lines, blank lines, optional `export ` prefix, optional matching
  single or double quotes around the value; no interpolation; a line without `=` is exit 2 naming
  file and line only.
- Keys: `XEWE_DEVICE_NAME`, `XEWE_WIFI_SSID`, `XEWE_WIFI_PASSWORD`, `XEWE_TIMEZONE`,
  `XEWE_PROVISION_MODULES`, `XEWE_TEST_BUTTONS_PIN`, `XEWE_TEST_PINS_ADC_PIN`, `XEWE_PORT`,
  `XEWE_CHIP` (other `XEWE_*` variables the tools read are accepted; an unknown `XEWE_*` key is a
  warning naming the key). Only `XEWE_*` keys with a non-empty value are applied, through
  `apply()`, which sets `os.environ[key]` only when the variable is not set: the real environment
  wins, flags win over both.
- Applied by the CLI before flash, serial, provision, test, run and boards (so `XEWE_PORT`/`XEWE_CHIP`
  reach board selection and the chip rule), and by the pytest plugin at `pytest_configure` inside a
  project (so module tests read `XEWE_TEST_*` from `os.environ`).
- Logging: `loaded N keys from <path>` only; values are never logged. The Wi-Fi password is masked
  (`********`) in console output, `--log` files and failure tails (§8).
