# xewe.testing

Firmware testing for projects and modules: the `xewe test` runner and the pytest plugin it loads.
The contract is [doc/spec.md](../../../doc/spec.md) §9.

- `runner.py`: `xewe test`, collects the test roots and runs pytest in-process.
- `plugin.py`: the pytest plugin, loaded through the `pytest11` entry point. Its fixtures
  (`compiled`, `board`, `firmware`, `serial`) build the firmware once per session, find the board,
  flash it once and hand each test the session's serial console, so module test folders need no
  `conftest.py`. Without a board, board tests are skipped as "compiled, not run".

The release card is not here: it is `xewe release-card` in [build/](../build/README.md).
