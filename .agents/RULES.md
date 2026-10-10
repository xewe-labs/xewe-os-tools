# RULES.md — root rules for agents in this project

Every rule below is absolute. It applies without exception, in every project that carries this
folder, and is never changed at install time or by an agent. Rules are cited by ID (`R-01` …
`R-15`) and are never renumbered. User-level rules live in `PREFERENCES.md` (R-15). If two
instructions conflict, R-03 decides.

## 1. Entry and Read Order

- **R-01 Read order.** On opening the project, read completely and in this order: `AGENTS.md`,
  `RULES.md`, `PREFERENCES.md`, `handoffs/HANDOFF.md` (through R-04), then the frontmatter of
  every `skills/*/SKILL.md`. Do no project work before all of these have been read.
- **R-02 Pickup before work.** The first action after reading is the `pickup` procedure of the
  `wax_handoff` skill. Restate the previous exit point to the human before touching anything
  else.
- **R-03 Precedence.** An explicit human instruction given in the current session outranks
  `RULES.md`, which outranks `PREFERENCES.md`, which outranks `AGENTS.md`, which outranks any
  individual skill. A preference never overrides a rule.
  Every human-instructed deviation is recorded in that session's handoff under "Design
  decisions", quoting the instruction.

## 2. Handoffs

- **R-04 Access only through `wax_handoff`.** Nothing under `handoffs/` is read, created,
  edited, moved, or deleted except by executing the `pickup` or `handoff` procedure of the
  `wax_handoff` skill. The exceptions belong to `wax_init`: filling the project name into the
  shipped genesis entry of a brand-new copy, and, during an upgrade, adding head keys that the
  current format requires to an existing `HANDOFF.md`. It never creates, edits, or removes an
  entry.
- **R-05 HEAD moves with the directory.** Writing an entry under `handoffs/handoffs/` and
  updating `handoffs/HANDOFF.md` are one operation. Never do one without the other.
- **R-06 Every session ends with a handoff.** A session that produced changes and no handoff
  is incomplete; say so to the human.
- **R-07 The template is copied, never filled in place.** `handoffs/handoffs/yyyy-mm-dd-hh-mm-ss.md`
  keeps its literal name and its placeholder content permanently.
- **R-08 Naming.** Entry filenames are UTC timestamps in the form `yyyy-mm-dd-hh-mm-ss.md`.
  HEAD is always the lexically greatest entry filename. Never backdate an entry.
- **R-09 Stop on inconsistency.** If `handoffs/HANDOFF.md` disagrees with the directory
  (HEAD, count, index, or a missing file), do not repair, do not write, do not guess. Report
  the exact mismatch to the human and wait.

## 3. Skills

- **R-10 Discover skills from their frontmatter.** A skill is a folder `skills/<name>/` that
  holds a `SKILL.md`. Skills are found by reading the `name` and `description` frontmatter of
  every `skills/*/SKILL.md`, the same way the host tool finds them. Nothing else indexes them.
- **R-11 Valid or nonexistent.** A folder under `skills/` whose `SKILL.md` is missing or fails
  R-12 is not a skill and must not be used. Adding or removing a skill is one change: the
  whole folder, with a valid `SKILL.md`.
- **R-12 Skill shape.** A skill is a folder containing `SKILL.md` whose YAML frontmatter has
  exactly two keys, `name` and `description`, and whose `name` equals the folder name.
  Supporting files sit flat beside `SKILL.md` or under `references/`, `scripts/`, `assets/`,
  or `evals/`.

## 4. Structure of `.agents/`

- **R-13 Fixed top level.** `.agents/` contains exactly `AGENTS.md`, `RULES.md`,
  `PREFERENCES.md`, `handoffs/`, and `skills/`, plus only the items listed in P-11. Never add,
  rename, or remove a top-level item.
- **R-14 Uppercase names are fixed.** `AGENTS.md`, `RULES.md`, `PREFERENCES.md`, `HANDOFF.md`,
  and every `SKILL.md` keep their names and locations.
- **R-15 Rules are root, preferences are user-level.** `RULES.md` is never edited by an agent
  or at install time. `PREFERENCES.md` ships with defaults; they are changed only during the
  `setup` procedure of `wax_init`, or later on an explicit human instruction quoted in that
  session's handoff. Preferences are cited by ID (`P-01` …) and bind exactly like rules until
  changed.

## 5. Project rules (xewe-os-tools)

Added for this project on the owner's instruction; they bind like the rules above and are cited
as `X-NN`. They are not part of the WAX reference.

- **X-01 One package, stdlib first.** Runtime dependencies are `pyserial` and `pytest` only;
  anything else needs the owner's say. Python ≥ 3.11.
- **X-02 No git writes.** The tools never run a git command that changes a repository other than
  their own checkouts under `build/` and `~/.xewe-os/build-tools/`; `xewe release` prints the
  commands for a human.
- **X-03 Board-free tests.** The self-tests need no network, no arduino-cli and no board; they use
  the fakes in `tests/fakes/`. A new command gets a test with the fakes.
- **X-04 Credentials.** Never open, print or copy a dotenv or key file; the tools read them.
  Tests that need the dotenv file name take it from `dotenv.FILENAME` and assert on content in
  Python.
- **X-05 Docs are part of the change.** A new or changed command, flag, output line, exit code or
  file goes into `doc/spec.md` (and the README table) in the same change.
- **X-06 Generated files stay generated.** `run.sh`, `src/Modules.h`, `build/**` and the
  `Config.h` module blocks are written by the tools; their text lives in the code
  (`runscript.py`, `registry.py`), never hand-copied into other repos.
- **X-07 Exit codes are an interface.** 0 to 4 as in `doc/spec.md` §3; changing what a command
  returns is announced in the README.
- **X-08 Comments describe the code.** A comment says what the code does or why. No agent or
  session names, dates, decision ids, phase names or "used to" history.
