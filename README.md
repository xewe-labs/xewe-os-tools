# xewe-os-tools

One Python package, one command (`xewe`), for XeWe OS firmware projects: setup, build, flash,
serial, test, boards, modules, lock and release. Linux and macOS, x86_64 and arm64. No sudo and
no system package manager: everything goes into the project's `build/` (plus a shared download
cache in `~/.cache/xewe-os`, or `$XEWE_CACHE`).

## Install

A project's `./setup.sh` (reference copy: `scripts/bootstrap.sh`) does this for you: it creates
`build/.venv`, installs this package into it from `$XEWE_TOOLS_SOURCE` (a local checkout) or from
the `[tools]` ref in `xewe.lock`, then runs `xewe setup "$@"`. `scripts/run.sh` is the reference
`./run.sh`.

For development of the tools themselves (Python >= 3.11):

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m pytest -q
```

Runtime dependencies: `pyserial`, `pytest`. esptool comes from the pinned esp32 core
(`XEWE_ESPTOOL` overrides it).

## Commands

| Command | What it does |
|---|---|
| `xewe setup [--modules LIST\|all\|none] [--latest] [--force] [--core-source DIR] [--modules-source DIR] [--arduino-data DIR]` | arduino-cli, esp32 core, core library, libraries and modules into `build/`; generates `src/modules/` (zero modules is valid: `--modules none`, or no selection) |
| `xewe build [--chip C \| --all-chips] [--define K=V]... [--clean] [--dry-run]` | compile into `build/out/<chip>/`; prints `Sketch uses N bytes (NN%)` (`--dry-run` prints the arduino-cli command only) |
| `xewe flash [--chip C] [--port P] [--erase] [--no-build] [--require-board]` | build if stale (sources, version or `--define` values changed), then write the merged image at 0x0 |
| `xewe serial [--port P] [--send CMD [--expect RE]] [--duration S] [--log FILE]` | timestamped console |
| `xewe test [--chip C \| --all-chips] [--module SLUG]... [--host-only] [-- PYTEST_ARGS]` | pytest over `tests/` and the selected modules' `tests/` |
| `xewe run [--chip C] [--define K=V]... [--no-serial]` | build, flash, listen |
| `xewe boards [--no-probe] [--json] [--set-port P [--set-chip C]] [--clear]` | list boards, set an override |
| `xewe modules list\|select\|validate\|generate` | the modules checkout and `src/modules/` |
| `xewe lock show\|update` | lock refs vs installed refs; move refs to new tags |
| `xewe clean [--all] [--modules]` | delete generated output |
| `xewe doctor` | check the environment |
| `xewe release --version X.Y.Z [--matrix FILE] [--notes FILE]` | release matrix into `static/firmware/releases/<version>/`; prints the git/gh commands |

Global flags (before the command): `--project DIR`, `--verbose`, `--version`.
Exit codes: 0 ok, 1 failure, 2 usage, 3 not set up / tool missing, 4 board required but missing.

## Without a board

Every hardware command still compiles, then prints

```
compiled, not run: no board attached (c3, build/out/c3/2.0.15-c3-xewe-os.bin)
```

and exits 0. `xewe test` runs host tests and reports hardware tests as "compiled, not run".
`--require-board` (or `XEWE_REQUIRE_BOARD=1`) turns this into exit 4 (`xewe test` too).

## License

GPL-3.0-only (see `LICENSE.txt`).
