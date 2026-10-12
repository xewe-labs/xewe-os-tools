# xewe-os-tools 3.0.1 test card

What was tested, where, on which commits, and what was not. The self-tests (`pytest`, no network,
no arduino-cli, no board) ran on the 3.0.1 tree. Everything not changed since 3.0.0 keeps the
[3.0.0 test card](https://github.com/xewe-labs/xewe-os-tools/blob/3.0.0/releases/3.0.0/testcard.md).

| Area | Test files | Count | Pass | Not covered |
|---|---|---|---|---|
| Whole suite | 24 files | 455 | 455 passed, 0 failed | as in 3.0.0 |
| release (`src/xewe/build/release.py`): printed commands without `v` | `build/test_release.py` | 1 changed | pass | |
| `xewe-release.yml` "Version from the tag" | `build/test_release.py` (the step's script run with bash) | 5 new | 5 | |
| `xewe-release.yml` branch commit | `build/test_release.py` (reads the step) | 1 new | 1 | the commit and push on GitHub (first run: the template's 3.0.0 tag) |
| workflows (7 files) | actionlint 1.7.12 with shellcheck 0.11.0 | 7 files | clean (`-ignore xewe-board`) | |
| `scripts/release.sh` | `bash -n`, shellcheck, `--prepare 3.0.1 --dry-run` | | clean; every location says 3.0.1 | a real run (the 3.0.0 one was the first) |

The tag step accepts `3.0.0`, `3.0.0-rc1` and `v3.0.0` and rejects `3.0` and a tag that differs
from `[project] version`. The branch-commit test checks that LFS is installed before the worktree,
smudge is skipped, a `.bin` routed to LFS must be staged as a pointer, the message ends in
`[skip ci]`, and the folder lands at `static/firmware/releases/<version>/`.

Also on the tree: pyflakes and mypy clean (35 source files); `xewe format --check` 0 of 60 Python
files to change (ruff 0.17.0); `xewe brand-lint .` clean.

From a template copy (core 3.0.0, modules 3.0.0, these tools as `XEWE_TOOLS_SOURCE`): `./setup.sh`,
`xewe build` c3/c6/s3 (0 warnings), `xewe test --no-board` (8 unit passed, 13 compiled, not run)
and `xewe release --version 3.0.0` (the pre-3.0 `static/firmware/releases/3.0.0/` layout, the
printed commands without `v`).

## Environment

- Linux aarch64 VM, Python 3.14.7, the editable install in `.venv`, `XEWE_NO_BOARD=1`.
- Formatters: ruff 0.17.0, clang-format 23.1.3 (pip).

## Commit

- The 3.0.1 tree: 3742334 (tag 3.0.0) plus the 3.0.1 changes and the prepare changes (version
  3.0.1 and this folder).
