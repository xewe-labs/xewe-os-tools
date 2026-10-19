# xewe.env

The environment of a project: `xewe setup`, `xewe doctor`, `xewe clean`, the toolchain installs,
the pinned versions, project paths, and the dotenv file of credentials and bench settings. This
file lists every dotenv key. The contract is [doc/spec.md](../../../doc/spec.md) §6 (setup) and
§13 (the dotenv file).

- `setup.py`: `xewe setup`, the shared toolchain and everything a project needs in `build/`.
- `doctor.py`: `xewe doctor`, reports the state of the environment without changing it.
- `clean.py`: `xewe clean`, removes generated build output (never the shared toolchain).
- `dotenv.py`: finds, parses and applies the dotenv file; `KEYS` and `OTHER_KEYS` name its keys.
- `pins.py`: versions and URLs shipped with this release of the tools (below).
- `arduino.py`: arduino-cli install, its isolated environment, the esp32 core, the compile command.
- `esptool.py`: the esptool bundled with the pinned esp32 core (`XEWE_ESPTOOL` overrides it).
- `fetch.py`: downloads (resumable, sha256-checked) and git wrappers.
- `config.py`: `build/config/build_config.toml`, what setup installed and where.
- `project.py`: project root discovery, the paths under `build/`, the shared toolchain folder.
- `runscript.py`: the `run.sh` that `xewe setup` writes.

## Setup

A project's `./setup.sh` installs the tools and runs setup. The file belongs to the template repo
(`xewe-os`); projects copy it from there. It creates `build/tools/.venv`, installs this package
into it from `$XEWE_TOOLS_SOURCE` (a local checkout) or from the `[tools]` ref in `xewe.toml`
cloned into `build/tools` (a tag, a branch that is followed on every run, `latest`, or a commit
SHA), then runs `xewe setup "$@"`.

`xewe setup` installs the toolchain (arduino-cli, esp32 core, esptool) and the modules repo
checkout (`sources/xewe-os-modules/<ref>/`) once per machine into `~/.xewe-os/build-tools/`
(`$XEWE_HOME` overrides `~/.xewe-os`), everything else into the project's `build/`. Each step is
skipped when what it would install is already there at the wanted version or ref (`--force` redoes
every step). It writes the project's `./run.sh` on every run (it is not committed) and, when no
dotenv file is found, prints where to put one and which keys; it never writes one. The flags are
in the command table of [the package README](../README.md).

## Doctor

`xewe doctor` checks, without changing anything: Python, git, whether it runs in a venv, the setup
record (`build/config/build_config.toml`), the shared toolchain, Rosetta 2 on Apple silicon (the
esp32 core's `ctags` needs it), the arduino-cli and esp32 core versions against `xewe.toml`,
esptool, drift between installed and manifest refs, free disk space, serial port permissions and
the attached boards.

## Pins

`pins.py` holds the versions and URLs this release of the tools ships with: the arduino-cli and
esp32 core versions, the esp32 board index URL, the default repos of core, modules and tools, and
their default refs (`DEFAULT_CORE_REF`, `DEFAULT_MODULES_REF`, `DEFAULT_TOOLS_REF`). A project's
`xewe.toml [toolchain]` can override the toolchain versions ([doc/spec.md](../../../doc/spec.md)
§4). The CI workflows read the toolchain pins from this file to key their cache
([doc/ci.md](../../../doc/ci.md)). `DEFAULT_TOOLS_REF` is one of the version locations that
`scripts/release.sh` rewrites ([scripts/README.md](../../../scripts/README.md)).

## Credentials and settings: the dotenv file

Credentials and per-bench settings live in a dotenv file named `.env` that is never committed
(git-ignored here and in the template). You create it; nothing writes it. `./setup.sh` prints one
line naming where to put it and the provisioning keys when it finds none. Put it in the tools
checkout to share it between projects, or in the project to override that one: a project file
wins as a whole, so an empty or partial one hides the tools checkout's keys. The first provision
needs `XEWE_WIFI_SSID` and `XEWE_WIFI_PASSWORD` when the wifi module is enabled.

Which file is used, first found wins:

1. `--env FILE` (`xewe provision --from FILE` is an alias), else the `XEWE_ENV` variable (must
   exist; exit 2 otherwise);
2. `.env` in the project directory (where `xewe.toml` is);
3. `.env` in the xewe-os-tools source checkout: the `local:<path>` source that `./setup.sh`
   recorded for tools in `build/config/build_config.toml` (`XEWE_TOOLS_SOURCE`), else the
   checkout this package is installed from (an install from a path).

No file is fine. Format: `KEY=VALUE`, `#` comments, blank lines, optional `export ` prefix,
optional single or double quotes, no interpolation; a line without `=` is exit 2 naming the file
and line only. An empty value counts as unset. Variables already in the environment win over the
file, flags win over both.

### Keys

The keys the file usually holds (`dotenv.KEYS`):

| Key | What it is for |
|---|---|
| `XEWE_DEVICE_NAME` | device name the first boot asks for (default: `[project] name` in `xewe.toml`, else the folder name) |
| `XEWE_WIFI_SSID` | Wi-Fi network name; needed by the first provision when the wifi module is enabled |
| `XEWE_WIFI_PASSWORD` | Wi-Fi password, masked in all output; needed by the first provision when the wifi module is enabled |
| `XEWE_TIMEZONE` | `GMT+HH:MM`, set instead of the detected timezone (empty accepts the detected one) |
| `XEWE_PROVISION_MODULES` | modules to enable at their prompt: `all`, `none` or a comma list (default `all`) |
| `XEWE_TEST_BUTTONS_PIN` | free GPIO for the buttons module's board test (skipped when empty) |
| `XEWE_TEST_PINS_ADC_PIN` | ADC GPIO the pins module's board test reads (default 1) |
| `XEWE_TEST_DEFINES` | space-separated `KEY=VALUE` defines that `xewe test` and `xewe flash` build and flash with when no `--define` is given (any `--define` replaces it) |
| `XEWE_PORT` | serial port of the board (default: detected) |
| `XEWE_CHIP` | chip of the board, `c3`, `c6` or `s3` (default: detected) |

Other variables the tools read, allowed in the file too (`dotenv.OTHER_KEYS`):

| Key | What it is for |
|---|---|
| `XEWE_REQUIRE_BOARD` | `1`: a missing board is exit 4 (as `--require-board`) |
| `XEWE_ARDUINO_CLI` | use this arduino-cli instead of the shared one |
| `XEWE_ESPTOOL` | use this esptool instead of the one in the esp32 core |
| `XEWE_ARDUINO_DATA` | reuse this arduino-cli data dir (as `xewe setup --arduino-data`) |
| `XEWE_CACHE` | download folder instead of `build-tools/downloads` |
| `XEWE_CORE_SOURCE` | local xewe-os-core checkout (as `xewe setup --core-source`) |
| `XEWE_MODULES_SOURCE` | local modules checkout, used as is (as `xewe setup --modules-source`) |
| `XEWE_TOOLS_SOURCE` | local xewe-os-tools checkout that `./setup.sh` installs from |

Any other `XEWE_*` key in the file is a warning naming the key. The tools also read these from the
environment: `XEWE_ENV` (the dotenv file, above), `XEWE_HOME` (instead of `~/.xewe-os`),
`XEWE_NO_BOARD` (`1`: never touch a board, as `--no-board`), `XEWE_SETTLE` (as `--settle`),
`XEWE_CLANG_FORMAT` (the clang-format `xewe format` runs) and `XEWE_NONINTERACTIVE` (`1`: `xewe
release` never opens `$EDITOR`).

### Who reads it

`xewe provision`, `xewe test` (and the pytest plugin, so module tests see the pins without
exports), `xewe serial`, `xewe flash`, `xewe run` and `xewe boards` read it. The tools log only
`loaded N keys from <path>`, never a value; the Wi-Fi password is masked as `********` in all
output, and the tools' own lines show the SSID as its first character and `***` (the board's own
lines are left as it printed them).
