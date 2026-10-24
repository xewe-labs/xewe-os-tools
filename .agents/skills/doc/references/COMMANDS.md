# Commands

## Checking a change in this repository

Install the checkout once with `pip install -e .` in a venv. The tests import the installed
package, and an editable install follows every change. Then run, from the repository root:

```sh
python -m pytest -q             # no network, no arduino-cli, no board
python -m pyflakes src tests
python -m mypy                  # package and paths come from pyproject.toml
python -m xewe brand-lint .
python -m xewe format --check   # CI pins ruff 0.17.0 and blocks on this
```

CI installs `pyflakes mypy types-pyserial` next to the tree. The format job also pins
`clang-format==23.1.3`. A different ruff version can reformat files that CI accepts.

## Releasing the tools

Never run `scripts/release.sh`. It commits, tags, pushes and creates the GitHub Release, and the
owner runs it. The one exception is a scratch clone with its own origin. It writes everything
under `build/release/<version>/`, which is gitignored, and there is no hand-written test card.
`scripts/README.md` and `doc/spec.md` §11 and §14 describe it.

## Running xewe from a firmware project

Run from inside a project, which is a directory with `xewe.toml`, or pass `--project DIR`. Call
it as `build/tools/.venv/bin/python -m xewe ...`, because that form keeps working after the
project folder moves. Results go to stdout, and progress and errors go to stderr. `-v`, before or
after the command, prints every subprocess command and its output. Pytest's own flags go after
`--`. The exit codes 0 to 4 are in `src/xewe/README.md`.

Look before you act. `xewe build --chip c3 --dry-run` prints the arduino-cli command, `xewe doctor`
lists what is installed and changes nothing, and `xewe manifest show` marks drift with `!`.

Only `manifest update`, `modules select`, `setup --modules` and `release` edit `xewe.toml`. Never
edit `build/` or `src/Modules.h` by hand. `xewe` itself never runs a git write, and `xewe release`
prints the git and gh commands instead.

## No board

No board is a normal state. `flash`, `run` and `test` compile, print
`compiled, not run: no board attached (<chip>, <bin>)` and exit 0. In a compile-only session set
`XEWE_NO_BOARD=1`. Then no port is listed, probed or opened, and `flash` and `run` exit 4.

## Provisioning and the dotenv file

`xewe provision` answers the first-boot prompts from the dotenv file. Exit 2 naming
`XEWE_WIFI_*` means the user has not filled in the file. Ask them for the values and never invent
them.

The user creates the dotenv file, in the tools checkout or in the project. Nothing writes it, and
setup only says where to put it. Never read, print, copy or edit it. The tools log only
`loaded N keys from <path>` and mask the Wi-Fi password.
