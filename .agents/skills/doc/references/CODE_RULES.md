# Code rules

The runtime dependencies are `pyserial` and `pytest` and nothing else. Adding one needs the
owner's agreement. Prefer the standard library.

The tools never run a git command that changes a repository, except their own checkouts under
`build/` and `~/.xewe-os/build-tools/`. A command that would need a git write prints the commands
for a human instead, as `xewe release` does.

The self-tests need no network, no arduino-cli and no board, and they use the fakes in
`tests/fakes/`. A new command gets a test that runs against the fakes. Tests that need the dotenv
file name take it from `dotenv.FILENAME` and assert on its content in Python, so nobody opens the
real file.

A new or changed command, flag, output line, exit code or file goes into `doc/spec.md` and the
command table in `src/xewe/README.md` in the same change.

The exit codes 0 to 4 in `doc/spec.md` §3 are an interface that every caller scripts against. A
change to what a command returns is announced in `src/xewe/README.md`.

The tools write `run.sh`, `src/Modules.h`, everything under `build/`, and the core and module
blocks of a project's `Config.h`. Their text lives in the code (`runscript.py`, `registry.py`) and
is never copied by hand into other repositories.

A comment says what the code does or why. It never names an agent, a session, a date, a decision
id, a phase or what the code used to do. The `help=` strings in `cli.py` and `releasecard.py` are
the `--help` output, so they change only together with the behaviour they describe.

The xewe-labs organization guidelines at `https://github.com/xewe-labs/.github/tree/main/guidelines` apply where this skill is silent.
