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
| `xewe setup [--modules LIST\|all\|none] [--latest] [--force] [--core-source DIR] [--modules-source DIR] [--arduino-data DIR]` | arduino-cli, esp32 core, core library, libraries and modules into `build/`; generates `src/modules/` (zero modules is valid: `--modules none`, or no selection); installs the selected modules' `depends_libraries` from the modules repo's `libraries.toml` unless `xewe.lock` `[libraries]` pins that name (the lock wins) |
| `xewe build [--chip C \| --all-chips] [--define K=V]... [--clean] [--dry-run]` | compile into `build/out/<chip>/`; prints `Sketch uses N bytes (NN%)` (`--dry-run` prints the arduino-cli command only) |
| `xewe flash [--chip C] [--port P] [--erase] [--no-build] [--require-board]` | build if stale (sources, version or `--define` values changed), then write the merged image at 0x0 |
| `xewe serial [--port P] [--send CMD [--expect RE] [--boot-timeout S]] [--duration S] [--log FILE]` | timestamped console; the tools open the port without resetting the board (`--reset` or flashing resets it); `--send` waits out a boot in progress (see below) |
| `xewe provision [--port P] [--name NAME] [--modules all\|none\|LIST] [--timezone GMT+HH:MM] [--env FILE] [--no-reset] [--log FILE]` | answer the first-boot prompts of a flashed board (settings from `.env`) |
| `xewe test [--chip C \| --all-chips] [--module SLUG]... [--host-only] [--no-board] [-- PYTEST_ARGS]` | pytest over `tests/` and the selected modules' `tests/` |
| `xewe run [--chip C] [--define K=V]... [--no-serial]` | build, flash, listen |
| `xewe boards [--no-probe] [--json] [--set-port P [--set-chip C]] [--clear]` | list boards, set an override |
| `xewe modules list\|select\|validate\|generate` | the modules checkout and `src/modules/` |
| `xewe lock show\|update` | lock refs vs installed refs; move refs to new tags |
| `xewe clean [--all] [--modules]` | delete generated output |
| `xewe doctor` | check the environment |
| `xewe release --version X.Y.Z [--matrix FILE] [--notes FILE]` | release matrix into `static/firmware/releases/<version>/`; prints the git/gh commands |

Global flags (before the command): `--project DIR`, `--verbose`/`-v`, `--version`. `-v`/`--verbose` is also
accepted after the command (`xewe test --host-only -v`); pytest's own flags go after `--`
(`xewe test -- -vv -k wifi`).
Exit codes: 0 ok, 1 failure, 2 usage, 3 not set up / tool missing, 4 board required but missing.

## Serial and resets

The tools open the port without resetting the board; `--reset` or flashing resets it. The chip
resets on the control-line state DTR=0 & RTS=1 (native USB, ESP32-S3/C3/C6 USB-Serial/JTAG
`303a:1001`: `rst:0x15 (USB_UART_CHIP_RESET)`; bridge boards: EN pulled low). The kernel raises
DTR and RTS on open and pyserial applies DTR before RTS, so `Console.open` configures DTR high and
RTS low for the open (1/1 -> 1/0) and drops DTR afterwards (-> 0/0), never passing through 0/1.
On this VM's USB passthrough a reset re-enumerates the port and can lose it, which is why opening
must not reset. After a reset, `xewe serial` shows the ROM lines (`ESP-ROM:...`, `rst:0x...`) and
the boot log. With `--send`, the command is sent right away when nothing boot-like arrives within
2 s of opening (the normal case), or, if a boot is in progress, after `System Setup Complete` (up to
`--boot-timeout`, default 90 s: Wi-Fi + NTP can take ~30 s). An unprovisioned board (`Name your device`) exits 1 without sending: run
`xewe provision`. A `boot: ...` line records which case happened.

## Provision

A freshly flashed (or erased) board stops at `Name your device`, then asks which modules to
enable, the Wi-Fi network and password, and whether the detected time is right. `xewe provision`
resets the board and answers those prompts:

```sh
build/.venv/bin/python -m xewe provision                      # settings from .env, board auto-selected
build/.venv/bin/python -m xewe provision --port /dev/ttyACM0
```

Values come from flags, then the environment (`XEWE_DEVICE_NAME`, `XEWE_WIFI_SSID`,
`XEWE_WIFI_PASSWORD`, `XEWE_TIMEZONE`, `XEWE_PROVISION_MODULES`), then the `.env` file (next
section), then defaults (name from `xewe.lock`, all modules, accept the detected timezone). The
password has no flag and is printed as `********`.

A board whose earlier provisioning stopped part-way (for example at Wi-Fi) keeps what it stored and
starts at a later prompt after the reset, such as the Wi-Fi network list; the tool answers whatever
prompt comes first. The SSID is picked by its number in the list; an SSID not in the scan gets one
rescan (`-2`), then is entered as a custom SSID (`-3`). `! Invalid number` is answered again. The
one empty "nudge" line (sent when no prompt appears within 10 s of the reset, for a board that
printed its prompt before the port was opened) is never sent after the first prompt. Exit 0 when provisioned or already provisioned, 2 for missing
or bad settings (before the port is touched), 1 when the board stops answering as expected, 4
without a board.

## Credentials and settings: .env

Credentials and per-bench settings live in a dotenv file that is never committed (`.env` and
`.env.*` are git-ignored here and in the xewe-os template; `.env.example` is the committed
template). Copy `.env.example` to `.env` and fill it in. Which file is used, first found wins:

1. `--env FILE` (`xewe provision --from FILE` is an alias), else the `XEWE_ENV` variable (must exist;
   exit 2 otherwise);
2. `.env` in the project directory (the harness, where `xewe.lock` is);
3. `.env` in the xewe-os-tools source checkout: the `local:<path>` source that `./setup.sh` recorded
   for tools in `build/build_config.toml` (`XEWE_TOOLS_SOURCE`), else the checkout this package is
   installed from (an install from a path).

No file is fine. Keys: `XEWE_DEVICE_NAME`, `XEWE_WIFI_SSID`, `XEWE_WIFI_PASSWORD`, `XEWE_TIMEZONE`,
`XEWE_PROVISION_MODULES` (provision), `XEWE_TEST_BUTTONS_PIN`, `XEWE_TEST_PINS_ADC_PIN` (module
tests), `XEWE_PORT`, `XEWE_CHIP` (board selection). Format: `KEY=VALUE`, `#` comments, blank lines,
optional `export ` prefix, optional single or double quotes, no interpolation; an empty value counts
as unset. Variables already in the environment win over the file, flags win over both.

`xewe provision`, `xewe test` (and the pytest plugin, so module tests see the pins without exports),
`xewe serial`, `xewe flash`, `xewe run` and `xewe boards` read it. The tools log only
`loaded N keys from <path>`, never a value; the Wi-Fi password is masked as `********` in all output.

## Without a board

Every hardware command still compiles, then prints

```
compiled, not run: no board attached (c3, build/out/c3/2.0.15-c3-xewe-os.bin)
```

and exits 0. `xewe test` runs host tests and reports hardware tests as "compiled, not run".
`--require-board` (or `XEWE_REQUIRE_BOARD=1`) turns this into exit 4 (`xewe test` too).

`--no-board` (or `XEWE_NO_BOARD=1`) is the hard switch for machines that must never touch a
board: no port is listed, probed or opened. `flash`/`run` build and then exit 4, `serial`,
`provision` and `boards` exit 4, all with `board access disabled (--no-board / XEWE_NO_BOARD)`;
`xewe test` reports hardware tests as "compiled, not run" and exits 0. Agents in compile-only mode
must set `XEWE_NO_BOARD=1`.

## License

GPL-3.0-only (see `LICENSE.txt`).
