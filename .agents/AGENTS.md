# AGENTS.md — xewe-os-tools

This is **WAX 1.3**, the WAX Agentic Workspace. The rules, preferences, and skills below belong
to this version. Human documentation lives at https://github.com/maxdokukin/wax_agents.

This folder is the agentic part of the project. It gives you boundaries, context, and tools,
in that order. Read it as described below before doing anything else in the repository.

## Read in this order

1. **`RULES.md` — root boundaries.** Absolute rules, the same in every project. Cite them by
   ID when you explain a decision or a refusal.
2. **`PREFERENCES.md` — user boundaries.** This project's chosen defaults (R-15). They bind
   like rules until a human changes them; cite them by ID too.
3. **`handoffs/HANDOFF.md` — context.** The head of the work record: where the last session
   stopped and what is open. Reach it only through the `pickup` procedure of the
   `wax_handoff` skill (R-04), because the directory has invariants that the skill checks
   before you rely on anything in it.
4. **`skills/` — tools.** One folder per skill, each with a `SKILL.md`. Discover them by
   reading the frontmatter of every `skills/*/SKILL.md` (R-10); a folder without a valid one
   is not a skill (R-11).

## Session shape

- **Pickup → work → handoff.** Start with `wax_handoff` pickup, do the work, end with
  `wax_handoff` handoff. A session that changed anything and did not end with a handoff is
  incomplete (R-06); say so rather than letting it pass.

## Never do these without being asked

- **Edit `RULES.md`, `PREFERENCES.md`, or this file** (R-15, P-05). Propose changes in the
  handoff instead (P-10).
- **Touch anything under `handoffs/` by hand** (R-04). The skill is the only door.
- **Add, rename, or remove a top-level item in `.agents/`** (R-13).
- **Commit, push, tag, release, publish, or delete what you did not create** (P-09).

## Precedence

- **Human instruction in this session > `RULES.md` > `PREFERENCES.md` > this file > a
  skill** (R-03). A project's own `.agents/` wins over any organization-level agent file. Record every
  human-instructed deviation in the handoff, quoting the instruction.

## Reporting back

- **Say what you actually ran** and label anything unverified as unverified (P-07).
- **Never report a skipped or failed step as done** (P-08). Failures come with their output.

## Project: xewe-os-tools (`xewe`)

The Python package `xewe`: setup, build, flash, serial, provision, test, format, release and
brand-lint for XeWe OS firmware projects, and `setup`/`check` for the core library. Human
documentation: `README.md` and `doc/` (`doc/spec.md` is the full specification, `doc/ci.md` the
reusable workflows, `doc/tests.md` the self-tests). Project rules are X-01 … X-08 at the end of
`RULES.md`. Organization rules: `https://github.com/xewe-labs/.github/blob/main/AGENTS.md`; they
apply where this file is silent.

### Layout

- `src/xewe/` is the package (standard `src` layout); five areas: `env/` (machine and project:
  setup, toolchain, paths, dotenv), `build/` (compile, flash, release, format, check, brand-lint,
  the `clang-format` style file), `board/` (ports, serial, provision), `modules/` (registry,
  manifest, `xewe.toml`), `testing/` (pytest plugin and runner). `cli.py` dispatches.
- `tests/` mirrors the package: `tests/<area>/test_<module>.py`, one `tests/conftest.py`, fakes in
  `tests/fakes/` (arduino-cli, esptool, clang-format), fixtures in `tests/fixtures/`.
- `.github/workflows/`: `ci.yml` (this repo) and the reusable `xewe-*.yml` that the other repos
  call (`doc/ci.md`).

### Check your work

```bash
python -m pytest -q          # self-tests: no network, no arduino-cli, no board
python -m pyflakes src tests
python -m mypy               # package and paths from pyproject.toml
python -m xewe brand-lint .
```

Tests import the installed package: after a change, `pip install .` into the venv first.

### Using xewe from a project

Run everything from inside a project (a directory with `xewe.toml`) or pass `--project DIR`; call
it as `build/tools/.venv/bin/python -m xewe ...` (this keeps working after the project is moved).
Results go to stdout, progress and errors to stderr; `-v` (before or after the command) prints
every subprocess command and its output; pytest's own flags go after `--`.

| Exit | Meaning |
|---|---|
| 0 | success, including "compiled, not run" (no board) |
| 1 | operation failed: compile, flash, test failure, expect timeout, validation |
| 2 | usage error: bad flag, unknown chip or module, bad `xewe.toml` |
| 3 | not set up or tool missing: run `./setup.sh` |
| 4 | board required but none found, `--port` missing, several boards, or board access disabled |

- **No board is normal.** `flash`, `run` and `test` compile and print
  `compiled, not run: no board attached (<chip>, <bin>)` with exit 0. Compile-only agents set
  `XEWE_NO_BOARD=1`: no port is listed, probed or opened; `flash`/`run` then exit 4.
- **Inspect before running.** `xewe build --chip c3 --dry-run` prints the arduino-cli command;
  `xewe doctor` lists what is installed and changes nothing; `xewe manifest show` marks drift
  with `!`.
- **Provisioning.** `xewe provision` answers the first-boot prompts from the dotenv file. Exit 2
  naming `XEWE_WIFI_*` means the user has not filled in the dotenv file: ask them, never invent
  values.
- **Credentials.** `./setup.sh` writes a commented dotenv skeleton into the project; the user fills
  it in. Agents never read, print, copy or edit the dotenv file (X-04 of the workspace); the tools
  log only `loaded N keys from <path>` and mask the Wi-Fi password.
- **The core library.** In `xewe-os-core` (`library.properties`, no `xewe.toml`) `./setup.sh`
  runs `xewe setup` in library mode and writes `run.sh`, which runs `xewe check` (host unit tests
  and every example compiled).
- `xewe` never runs git writes; `xewe release` prints the git and gh commands. Only
  `manifest update`, `modules select`, `setup --modules` and `release` edit `xewe.toml`. Never edit
  `build/` or `src/Modules.h` by hand.
