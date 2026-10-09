# Using xewe as an agent

Run everything from inside a project (a directory with `xewe.toml`, its committed manifest) or
pass `--project DIR`.
Call it as `build/tools/.venv/bin/python -m xewe ...`; this keeps working after the project is moved.
Layout (SPEC.md §6): the toolchain and the modules repo checkout
(`sources/xewe-os-modules/<ref>/`) are shared per machine in `~/.xewe-os/build-tools` (`XEWE_HOME`
overrides `~/.xewe-os`); the project's generated files are `build/` and `src/Modules.h`, the
selected modules (library, tests, `modules.lock`) in `build/modules/`.
Results go to stdout, progress and errors to stderr. Add `-v`/`--verbose` (before or after the
command: `xewe -v test` = `xewe test -v`) to see every subprocess command line and its output;
pytest's own flags go after `--` (`xewe test -- -vv`).

## Exit codes

| Code | Meaning |
|---|---|
| 0 | success, including "compiled, not run" (no board) |
| 1 | operation failed: compile, flash, test failure, expect timeout, validation |
| 2 | usage error: bad flag, unknown chip/module, bad xewe.toml |
| 3 | not set up / tool missing: run `./setup.sh` |
| 4 | board required but none found, `--port` missing, several boards, or board access disabled (`XEWE_NO_BOARD=1`) |

## No board is normal

Machines running agents usually have no board. `flash`, `run` and `test` compile first and then
report, with exit 0:

```
compiled, not run: no board attached (<chip>, build/builds/<chip>/out/<bin>)
```

Grep for `compiled, not run` instead of treating it as an error. Pass `--require-board` only when
a board must be present (CI with hardware); then no board means exit 4 (`xewe test` too).
`xewe serial` without a board prints `no board attached; nothing to listen to` and exits 0.

**Agents in compile-only mode must set `XEWE_NO_BOARD=1`** (or pass `--no-board`): then no port is
ever listed, probed or opened, even if a board is plugged in. `xewe test` still compiles and
reports board tests as `compiled, not run` (exit 0); `flash`/`run` build and exit 4; `serial`,
`provision` and `boards` exit 4; all print `board access disabled (--no-board / XEWE_NO_BOARD)`.

## Inspect before running

- `xewe build --chip c3 --dry-run` prints the exact arduino-cli command and runs nothing
  (works before the toolchain is installed once `build/tools/.venv` exists; in a fresh project run
  `./setup.sh` first). `--all-chips --dry-run` prints one line per chip.
- `xewe doctor` lists what is installed and what is missing; it never changes anything. On
  Apple silicon it also warns when Rosetta 2 is missing (the esp32 core's x86_64 `ctags` fails
  with "bad CPU type" without it).
- The interactive console (`serial`/`run` on a terminal) prints board lines raw; scripted
  output (`--send`, `--no-input`, piped stdin) and `--log` stay timestamped, so parse those.
- `xewe manifest show` marks with `!` every entry where the installed ref differs from `xewe.toml`;
  library rows end with their origin: `(xewe.toml)` or `(modules catalogue)` (a module's
  `depends_libraries` from the modules repo's `libraries.toml`; a `[libraries]` pin in the manifest wins).

## Typical loop

```sh
XEWE_TOOLS_SOURCE=/path/to/xewe-os-tools ./setup.sh --modules wifi,web-interface
build/tools/.venv/bin/python -m xewe build --all-chips
build/tools/.venv/bin/python -m xewe test --unit-only
build/tools/.venv/bin/python -m xewe modules validate
```

Setup without a TTY never prompts: with no `--modules` and an empty `[modules] selected` it
continues with zero modules (a valid project; `--modules none` or `--modules ""` selects none
explicitly). A "no modules found in <path>" warning means the modules source has neither layout.
A successful build prints `ok     <chip>  <s> s  Sketch uses N bytes (NN%)  <n> warnings`;
`meta.json` has `sketch_size` and `sketch_size_percent`.

The esp32 core (~1.7 GB) is downloaded once per machine into `~/.xewe-os/build-tools/arduino15`
and shared by every project. To use another installed core instead:
`./setup.sh --arduino-data /path/to/arduino15` (or `XEWE_ARDUINO_DATA`); the directory must
hold `packages/esp32/...`. Setup checks the pinned core version and installs it there if missing,
and remembers the directory in `build/config/build_config.toml`.

## Provisioning a board

A flashed board waits at `Name your device` until it is provisioned (`xewe test` fails with a
pointer to this). `xewe provision` answers the first-boot prompts; with the `.env` file in place no
flags are needed: `build/tools/.venv/bin/python -m xewe provision` (or `--port P`). Exit 2 naming
`XEWE_WIFI_*` means the user has not filled in `.env` yet: ask them to, do not invent values. Exit 2 means
settings are missing (nothing was sent), 1 means the board did something unexpected (last 20
lines printed), 0 includes `already provisioned`.

## Credentials: .env

The user keeps credentials and bench settings (`XEWE_WIFI_SSID`, `XEWE_WIFI_PASSWORD`, test pins,
`XEWE_PORT`, `XEWE_CHIP`) in a git-ignored `.env` in the project directory or the xewe-os-tools
checkout (template: `.env.example`; resolution order in the README). Agents never read, print,
copy, create or edit `.env`; the tools read it. Never put Wi-Fi credentials on the command line, in
a repo or in your output. The tools log only `loaded N keys from <path>`, and the password is masked
as `********` in all output, including `--log` files and failure tails.

## Rules

- `xewe` never runs git writes; `xewe release` prints the git/gh commands for a human.
- Only `xewe manifest update`, `xewe modules select`, `xewe setup --modules` and `xewe release`
  edit `xewe.toml`. `xewe setup --latest` installs newer tags without editing the manifest.
- Never edit files under `build/` or `src/Modules.h` by hand; they are regenerated. `xewe clean`
  never touches `~/.xewe-os/build-tools`.
