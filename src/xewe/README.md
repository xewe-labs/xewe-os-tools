# xewe (the package)

The Python package behind the `xewe` command: every subcommand, its flags, the global flags and
the exit codes. The command line itself is `cli.py`; the normative contract is
[doc/spec.md](../../doc/spec.md) §3.

| Folder | What it holds |
|---|---|
| [env/](env/README.md) | setup, doctor, clean, the toolchain installs, pins, project paths, the dotenv file |
| [build/](build/README.md) | compile, flash, partitions, format, brand-lint, webui, release, release card |
| [board/](board/README.md) | serial console, board discovery and selection, provisioning |
| [modules/](modules/README.md) | modules registry and generation, `xewe.toml`, `xewe manifest` |
| [testing/](testing/README.md) | the pytest plugin and the `xewe test` runner |

`cli.py` parses the command line, `report.py` holds the output helpers and the exit codes,
`__main__.py` is the entry point for `python -m xewe`.

## Development install

For development of the tools themselves (Python >= 3.11), from the repo root:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]' pyflakes mypy types-pyserial
.venv/bin/python -m pytest -q
.venv/bin/python -m pyflakes src tests
.venv/bin/python -m mypy
```

The self-tests mirror this package; see [tests/README.md](../../tests/README.md).

Runtime dependencies: `pyserial`, `pytest`. esptool comes from the pinned esp32 core
(`XEWE_ESPTOOL` overrides it). The `dev` extra adds `ruff` (`xewe format` on Python).

## Commands

Run them as `xewe <command>` inside a project (a directory with `xewe.toml`), or as
`build/tools/.venv/bin/python -m xewe <command>`. Commands that say "needs no project" run in any
directory.

| Command | What it does |
|---|---|
| `xewe setup [--modules LIST\|all\|none] [--latest] [--force] [--core-source DIR] [--modules-source DIR] [--arduino-data DIR]` | arduino-cli, esp32 core and the modules repo (`sources/xewe-os-modules/<ref>/`, or `XEWE_MODULES_SOURCE` as is) into the shared `~/.xewe-os/build-tools` (once per machine and ref; a branch ref is followed), core library and libraries into `build/`; generates `build/modules/` (the `XeWeModules` library, the selected modules' tests, `modules.lock`) and `src/Modules.h` (zero modules is valid: `--modules none`, or no selection); installs the selected modules' `depends_libraries` from the modules repo's `libraries.toml` unless `xewe.toml` `[libraries]` pins that name (the manifest wins); writes `run.sh`; prints where to put the dotenv file when none is found. `--latest` installs the newest tags without changing `xewe.toml`; `--force` redoes every step |
| `xewe build [--chip C \| --all-chips] [--define K=V]... [--clean] [--dry-run] [--env FILE]` | compile into `build/builds/<chip>/out/`; prints `Sketch uses N bytes (NN%)` (`--dry-run` prints the arduino-cli command only; `--clean` drops the compile cache first). Each `#define NAME INJECTED_FROM_DOTENV` line of `Config.h` takes NAME from the environment, else the dotenv file (`--env FILE`, else as below); a missing value is exit 2 naming the `Config.h` line. The FQBN's `PartitionScheme` is `xewe.toml [build] partition_scheme` (default `no_ota`); an image larger than that scheme's app partition fails (exit 1), a scheme the installed esp32 core does not offer for the chip in 4 MB is exit 2 |
| `xewe flash [--chip C] [--port P] [--baud N] [--erase] [--no-build] [--define K=V]... [--settle S] [--require-board] [--env FILE] [--no-board]` | build if stale (sources, version or `--define` values changed), then write the merged image (`--baud`, default 921600); the defines are `--define`, else `XEWE_TEST_DEFINES` (as `xewe test`), so `xewe flash --define …` then `xewe test --define …` flash and test one image; NVS (name, Wi-Fi, module choices) is kept unless `--erase`. After esptool's reset the port must stay present `--settle` seconds (default 0.5, `XEWE_SETTLE`); a board that comes back under another name (macOS `cu.usbmodem<N>`, same USB serial number) is followed |
| `xewe serial [--port P] [--baud N] [--reset] [--send CMD [--expect RE [--timeout S]] [--boot-timeout S]] [--duration S] [--no-input] [--timestamps] [--log FILE] [--require-board] [--env FILE] [--no-board]` | timestamped console (`--baud`, default 115200); the tools open the port without resetting the board (`--reset` or flashing resets it); `--send` waits out a boot in progress (see [board/](board/README.md)); `--expect` waits up to `--timeout` seconds (default 10) for a matching line; without `--send` on a terminal it is interactive: keys typed during flash or boot are discarded, board lines are shown raw (`--timestamps` adds the time; `--log` always has it), each typed line is sent on Enter, Ctrl-C or Ctrl-D exits (`--no-input` or non-terminal stdin: listen only) |
| `xewe provision [--port P] [--name NAME] [--modules all\|none\|LIST] [--timezone GMT+HH:MM] [--env FILE \| --from FILE] [--timeout S] [--no-reset] [--log FILE] [--no-board]` | answer the first-boot prompts of a flashed board (settings from the dotenv file); `--timeout` is the seconds from reset to the first prompt (default 60). See [board/](board/README.md) |
| `xewe test [--chip C \| --all-chips] [--port P] [--module SLUG]... [--unit-only] [--no-flash] [--define K=V]... [--require-board] [--env FILE] [--no-board] [-- PYTEST_ARGS]` | pytest over `tests/` (`tests/unit/`, `tests/board/`) and the selected modules' tests in `build/modules/tests/<slug>/`; the image is written once per session unless `--no-flash` (test the firmware already on the board). It is built with `--define` (as `xewe build`), else with `XEWE_TEST_DEFINES` (space-separated `K=V`, environment or dotenv file; any `--define` replaces it), and reused when already built with those defines, so with the same defines (or `XEWE_TEST_DEFINES`) `xewe flash` and `xewe test` never write a default-pin image to a board on non-default pins |
| `xewe run [--chip C] [--port P] [--define K=V]... [--keep-nvs] [--no-serial] [--settle S] [--no-input] [--timestamps] [--env FILE] [--no-board]` | build, erase the whole flash (NVS too, so every run is a true first boot; `--keep-nvs` skips the erase), flash, then the console (interactive on a terminal, like `xewe serial`; `--no-serial` stops after flashing). The `flashed` line ends with `(flash erased: first boot)` or `(nvs kept)`. This is what `./run.sh` calls |
| `xewe boards [--no-probe] [--json] [--set-port P [--set-chip C]] [--clear] [--settle S] [--require-board] [--env FILE] [--no-board]` | list boards, set or clear an override in `build/config/boards.toml` (the esptool probe resets each board, `--no-probe` skips it; its port is waited for, `--settle`) |
| `xewe modules list [--json]` | the modules in the modules repo checkout (`*` = selected) |
| `xewe modules select LIST\|all\|none [--no-generate]` | write `[modules] selected` in `xewe.toml`, then generate (unless `--no-generate`) |
| `xewe modules validate [PATH]` | check every `module.properties` rule and the `tests/` layout of a modules checkout (default: the one setup uses, `XEWE_MODULES_SOURCE` or `~/.xewe-os/build-tools/sources/xewe-os-modules/<ref>`) |
| `xewe modules generate` | rebuild `build/modules/` and `src/Modules.h` from the manifest selection |
| `xewe manifest show [--json]` | `xewe.toml` refs vs installed refs (`!` = drift) |
| `xewe manifest update [core\|modules\|tools]... [--to REF]` | move refs to the newest tag, or to `REF`. `ref = "latest"` tracks the newest commit of the repo's default branch (re-fetched on every setup); a tag freezes it (`xewe manifest update` replaces `latest` with the newest tag, `--to latest` sets it back) |
| `xewe clean [--all] [--modules]` | delete generated output (`build/builds`, `build/tmp`; `--all`: all of `build/` but `tools/`; `--modules`: also `build/modules` and `src/Modules.h`; never `~/.xewe-os/build-tools`) |
| `xewe doctor` | check the environment without changing it (on Apple silicon also Rosetta 2, which the esp32 core's `ctags` needs); see [env/](env/README.md) |
| `xewe format [--check] [PATH...]` | clang-format `*.h *.hpp *.c *.cpp *.ino` (root, `src/`, `examples/`, `tests/unit/`; never `build/`) with the XeWe style shipped with the tools (no column alignment: it costs tokens and turns one-line changes into realigned blocks; a `.clang-format` in the project is not read), and `ruff format` the Python when ruff is installed; `--check` exits 1 listing files that would change. Runs in a project, a library, or any other directory (the current one is then the root; the modules repo passes `modules tools`). clang-format: `XEWE_CLANG_FORMAT`, `PATH` or the tools venv (`pip install clang-format`), version 14 or newer |
| `xewe brand-lint [DIR...]` | fail (exit 1) on banned spellings of the brand in the text files under each `DIR` (default `.`; rules: `xewe-os/doc/naming.md`); exit 2 for a missing `DIR`; in a git work tree only the files git does not ignore; skips `.git`, `build`, `.venv`, `static`, `__pycache__`, `history`, `test_logs`, `.pytest_cache`, `.mypy_cache`, `.ruff_cache`, `*.egg-info`, binary files, `naming.md`, the generated `release_card.md` (and its draft) and dotenv files. Needs no project |
| `xewe webui export FLASK_DIR OUT_DIR` \| `xewe webui prompt` | export the `.html`/`.css`/`.js` of a Flask prototype's `templates/` and `static/` as PROGMEM headers plus `app.py` and a sketch with one route per file into `OUT_DIR/<name>_<timestamp>/`; `prompt` prints the LLM prompt that ports `app.py` into that sketch ([doc/webui.md](../../doc/webui.md)). Needs no project, nor Flask |
| `xewe release --version X.Y.Z [--matrix FILE] [--notes FILE]` | release matrix into `static/firmware/releases/<version>/`, each `meta.json` recording the resolved core, modules, tools and library commits plus the bin's size, sha256 and release-asset names; `firmware_map.csv` with one row per image; the tarball and the flat GitHub Release assets in `build/release/<version>/assets/` (binaries are release assets only, never committed); refuses (exit 2) a `Config.h` with an active `INJECTED_FROM_DOTENV` line; writes `[project] version` and prints the git and gh commands (CI runs it on a version tag: [doc/ci.md](../../doc/ci.md)). Without `--notes` it opens `$EDITOR` only on a terminal; with stdin not a terminal or `XEWE_NONINTERACTIVE=1` it exits 2 asking for `--notes` |
| `xewe release --version X.Y.Z --chip C [--rows LIST]` \| `--assemble` | per chip: build only C's matrix rows into `firmware_map.C.csv` (no tarball, no assets); `--rows 0,1,2` (one CI job per chunk) only those 0-based indices into C's rows, into `firmware_map.C.<lowest index>.csv`; then `--assemble` merges the maps in matrix order and writes the map, tarball and assets, the same as one run. `--chip` and `--assemble` exclude each other |
| `xewe release-notes check FILE` | exit 0 when `FILE` holds valid notes for a release tag message (1 to 3 lines, none empty, none starting with `#`, each at most 120 characters), else 1 with one line per problem. Needs no project |
| `xewe release-card ACTION` | the generated release card ([doc/spec.md](../../doc/spec.md) §14); actions below. Needs no project; runs inside the repo's git work tree |

Chips (`--chip`, `--set-chip`) are `c3`, `c6` and `s3`; `--all-chips` builds them in turn.
`--no-board` exists on `flash`, `serial`, `provision`, `test`, `run` and `boards` only, `--env` on those and `build`;
`--require-board` on `flash`, `serial`, `test` and `boards`. The dotenv file and its keys:
[env/](env/README.md). Board access and the no-board switches: [board/](board/README.md).

### `xewe release-card` actions

| Action | What it does |
|---|---|
| `init --dir D --repo OWNER/NAME --version X.Y.Z [--tag TAG] [--exclude DIR]... [--resume]` | start a run in `releases/<v>/logs/` (`run.json`: run id, base commit, tested-tree fingerprint); it empties only a previous run folder of the same repo and version, anything else is exit 2; `--resume` keeps `run.json` when base commit and tree are unchanged |
| `record --dir D --kind setup\|validate\|unit\|board\|flash\|build\|check [--chip C] [--name LABEL] [--junit PATH] [--format junit\|core-host] [--firmware BIN] [--meta FILE] [--resume] -- CMD` | run one step and keep its redacted log (dotenv and environment secrets, also XML-, JSON- and URL-escaped, `$HOME` → `~`); `--resume` does not run a step that is already green in this run |
| `render --dir D --notes FILE --out FILE [--chips LIST] [--hardware LIST] [--require KINDS] [--title NAME] [--tests PREFIX]...` | write `releases/<v>/release_card.md` or refuse (exit 1; draft in the logs folder) |
| `verify --card FILE [--dir D] [--ci [--junit-dir D] [--meta-dir D] [--unit-only] [--size-tolerance PERCENT]]` | check that the card is as rendered (`card_sha256`) and matches the clean tree and the local logs, or with `--ci` against a CI run (`--unit-only` for core and modules CI) |
| `fingerprint [--root DIR] [--card FILE [--check]] [--exclude DIR]...` | print the tested-tree fingerprint (`--card --check`: exit 0 when this is the card's tree, else 1; used by the CI card jobs) |

Every action also takes `--root DIR` (repo root; default: the git work tree of the current
directory).

## Global flags and exit codes

Global flags (before the command): `--project DIR`, `--verbose`/`-v`, `--version`. `-v`/`--verbose`
is also accepted after the command (`xewe test --unit-only -v`); pytest's own flags go after `--`
(`xewe test -- -vv -k wifi`).

Exit codes: 0 ok, 1 failure, 2 usage, 3 not set up / tool missing, 4 board required but missing.
`xewe release-card record` maps the command it records onto them: 0 when it exited 0, 1 when it
exited non-zero, 3 when it could not start (the command's own code is kept in `run.json`).
