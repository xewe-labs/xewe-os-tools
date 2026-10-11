# xewe-os-tools 3.0.0 test card

What was tested, where, on which commits, and what was not. The self-tests (`pytest`, no network,
no arduino-cli, no board: fakes for arduino-cli, esptool, clang-format and the serial port) ran on
the tree tagged 3.0.0 (the work, format and prepare changes). The hardware evidence is the v3 board
campaign, which the tools drove.

| Area (`src/xewe/`) | Test files (`tests/`) | Count | Pass | Not covered |
|---|---|---|---|---|
| env: setup, toolchain, fetch, paths, dotenv, doctor, clean, run.sh | `env/test_setup`, `test_fetch`, `test_arduino`, `test_project`, `test_dotenv`, `test_doctor`, `test_clean`, `test_runscript` | 101 | 101 | real toolchain download and esp32 core install (faked; last done for real by the campaign's `./setup.sh`); Windows |
| build: compile, flash, release, format, brand-lint | `build/test_compile`, `test_flash`, `test_release`, `test_format`, `test_brandlint` | 71 | 71 | a real arduino-cli compile and esptool write (board campaign only); real clang-format (fake; the real one ran on core and the template, see below) |
| board: ports, serial, provision | `board/test_boards`, `test_serial`, `test_serial_interactive`, `test_provision` | 114 | 114 | on hardware, only through the campaign; the masked SSID log line and the "disappeared from USB" path on a real board |
| modules: registry, generate, manifest, xewe.toml | `modules/test_registry`, `test_manifest`, `test_lockfile`, `test_tomlw` | 108 | 108 | the real modules repo at 3.0.0 (fixtures copy its six `module.properties`) |
| testing: pytest plugin and runner | `testing/test_plugin`, `test_no_board` | 46 | 46 | `--no-flash` on a board |
| cli | `test_cli` | 9 | 9 | none known |
| workflows (`.github/workflows/`, 7 files) | actionlint 1.7.12 with shellcheck 0.11.0 | 7 files | clean (with `-ignore xewe-board`, the self-hosted label) | no GitHub run yet of the new format jobs or the Node 24 action majors; `xewe-board.yml` and `xewe-pages.yml` have no caller |
| Totals | 24 files | **449** | **449 passed, 0 failed** | |

Also on the tagged tree: pyflakes and mypy clean (35 source files); `xewe format --check` 0 of 60
Python files to change (ruff 0.17.0); `xewe brand-lint .` clean. The format commit changed 44
Python files and every one parses to the same AST before and after. `xewe format --check` from
this tree on the other repos: core 62 C++ + 19 Python, nothing to change; template 7 to change and
modules 12 to change (their own format commits).

`scripts/release.sh`: `bash -n` and shellcheck clean; `--dry-run 3.0.0` against the repo; the
preflight failure paths; `--prepare` and the resume path (tag without a GitHub Release) on a scratch
clone with a stub `gh`. Not yet run for real.

## Environment

- Self-tests: Linux aarch64 VM, Python 3.14.7, pytest 9.1.1, the editable install
  (`pip install -e .`) in `.venv`, run without `PYTHONPATH`, `XEWE_NO_BOARD=1`. CI runs them on
  Python 3.11, 3.12 and 3.13.
- Formatters: ruff 0.17.0, clang-format 23.1.3 (pip), as pinned in the workflows.

## Commit

- Self-tests and checks: the 3.0.0 tree, that is v0.1.1..5224218 plus the uncommitted 3.0.0 work,
  the format commit and the prepare commit (version 3.0.0 and this folder).

## Board-side evidence

The tools drove the v3 board campaign (runs TV3, TV4, TV5, 2026-10-10; xewe-labs workspace
`docs/phase4/reports/testing-v3.md`): one ESP32-S3 dev board on USB (USB-Serial/JTAG) passed into
the Linux VM, a project set up from scratch with `./setup.sh --modules all` (arduino-cli 1.5.1,
esp32 core 3.3.12, esptool 5.3.1), then `xewe test` building, flashing and running every suite
through the tools' pytest plugin. Tools commit ff7a7c8.

- Core board tests: **241/241 passed** (14 of 16 files; core 3.0.0 test card).
- Modules: 27/27 unit; board pass 1 **54 passed**, 8 skipped (no fan tach, LED strip or MLX90614
  on the rig); pass 2 (`survive_restart`) **3/3**.
- Exercised on hardware: setup and the shared toolchain, build, flash (keep-NVS image write),
  serial open/reset/reconnect, boot-banner wait, provisioning, the plugin's one-console session,
  the silent-board reopen and probe.
- Board drops: three USB-Serial/JTAG drops during the campaign (a known board/USB issue, fixed by a
  replug), none a test-body failure. The `TypeError` two of them raised is what 3.0.0 turns into
  the "disappeared from USB" message.

## Not covered on a board

- `xewe test --no-flash` and the "board disappeared from USB" message were added after the
  campaign (b67ffd9); they have unit tests only.
- Everything changed since ff7a7c8 (library mode and `xewe check` removed, brand-lint git mode,
  format fixes, the SSID mask, workflows) has self-tests only. None of it touches flash, serial
  or the plugin's board path apart from the two items above and the one masked log line.
