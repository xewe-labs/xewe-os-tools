# Self-tests

`tests/` mirrors the package (`src/`, SPEC §2): `tests/<area>/test_<module>.py` tests
`xewe.<area>.<module>`; `test_cli.py` (help, version, exit codes) stays at the top.

```
tests/
├── conftest.py   the only conftest: shared fixtures and helpers (below)
├── fakes/        arduino-cli, esptool (Python scripts)
├── fixtures/     manifest-2.0.0-c3.json (golden), module_properties/ (the six real module.properties)
├── test_cli.py
├── env/          test_setup test_doctor test_clean test_dotenv test_fetch test_arduino test_project
├── build/        test_compile test_flash test_release
├── board/        test_serial test_serial_interactive test_boards test_provision
├── modules/      test_registry test_manifest test_lockfile test_tomlw
└── testing/      test_plugin test_no_board
```

No network, no arduino-cli, no board, no serial port. Run everything from the repo root with
`.venv/bin/python -m pytest`, one area with `.venv/bin/python -m pytest tests/board`, one file or
test as usual (`pytest tests/board/test_serial.py -k reconnect`). `pyproject.toml` sets
`norecursedirs` without `build` (pytest skips any folder named `build` by default) and
`-p pytester` (`testing/test_plugin.py` runs inner pytest sessions). There are no `__init__.py`
files, so test basenames must stay unique; helpers are imported with `from conftest import ...`.

## Fakes

- `fakes/arduino-cli`: records argv, cwd and `ARDUINO_*` env as one JSON line per call in
  `$FAKE_ARDUINO_LOG`; `compile` writes `<stem>.ino.merged.bin` and a "Sketch uses N bytes" line.
  Switches: `FAKE_ARDUINO_COMPILE_FAIL` (FQBN board ids or `all`), `FAKE_ARDUINO_NO_MERGED=1`,
  `FAKE_ARDUINO_INSTALL_FAILS=N`, `FAKE_ARDUINO_HEAD_URL` (the `Head "<url>"` install error).
- `fakes/esptool`: records argv in `$FAKE_ESPTOOL_LOG`; `chip-id` reports `FAKE_ESPTOOL_CHIP`
  (default ESP32-C3); `FAKE_ESPTOOL_FAIL_BAUD` makes that baud fail.
- `FakeSerial` (conftest): in-memory `serial.Serial`. `incoming` bytes are readable at once,
  `chunks` arrive one per read (`DROP` raises like a vanished native-USB port), `script` maps each
  write to the board's answer, `fail_reads` raises on the next N reads; records `written`,
  `opens`, `rts_history`, `line_history` and every instance in `FakeSerial.instances`.
- `FakeClock` / `clock` fixture: fake `time` for `serialio` (`sleep` advances `monotonic`; the
  fixture also ticks 1 ms per `monotonic` call so polling loops end).
- `ports` fixture (append `Port(...)` entries), `no_ports`, `port_node` (a port path that exists),
  `select_board(board)` (a `select` callable for `serial_main` / `provision_main`), `PORT`, `BIN`.

## Fixtures

- autouse `_isolated_env`: clears every `XEWE_*` variable (`XEWE_NO_BOARD` included, so the
  no-board tests set it themselves) and the dotenv keys, points `XEWE_HOME` at the test's
  `tmp_path` (never the real `~/.xewe-os`), never finds this checkout's dotenv file, and restores
  `os.environ` afterwards.
- `fake_cli` / `fake_esptool`: select the fakes via `XEWE_ARDUINO_CLI` / `XEWE_ESPTOOL`; each
  returns a reader of its call log.
- A scratch project: `project` is a set-up project in `tmp_path/xewe-os` (manifest, sketch,
  generated modules from `make_modules_checkout`, `build_config.toml`; cwd is its root, the fake
  arduino-cli and no serial ports are active); `project_with_tests` adds `write_tests` (one unit
  and one board test). For files only (no `build/`), call `write_project(root, selected=...)`;
  `LOCK` is its manifest text, `make_modules_checkout(dest, layout, board_tests)` a modules
  checkout built from `fixtures/module_properties/`.
- `REPO` is the checkout root (`scripts/`, `.env.example`), `FIXTURES` / `FAKES` / `PROPERTIES`
  the shared folders.
