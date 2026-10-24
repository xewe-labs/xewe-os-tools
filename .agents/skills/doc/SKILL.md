---
name: doc
description: >
  What an agent needs to know about xewe-os-tools, the `xewe` Python CLI and the reusable XeWe
  OS workflows, that the code does not show: the checks to run before calling a change done
  (pytest, pyflakes, mypy, brand-lint, format with the pinned ruff), never running
  scripts/release.sh, running xewe from a firmware project, working with no board
  (XEWE_NO_BOARD, "compiled, not run"), provisioning and the dotenv file that is never read,
  the code rules (dependencies, no git writes, board-free tests with fakes, docs and exit codes
  as part of a change, generated files, comments), the paths that scripts, CI and other
  repositories read and that must not be renamed, and the decisions log. Use it before
  changing a command, a workflow, a release step or a doc file name, before running anything
  that touches a board or the dotenv file, and when asking why something is the way it is.
---

# doc

This is the index of the project's agent knowledge. Open only the file whose row matches the
task. Each row names one file under `references/`, and every file there has a row.

| File | Read it when |
|---|---|
| `references/COMMANDS.md` | checking a change, asked about a release, running `xewe` in a project, without a board, or near the dotenv file |
| `references/CODE_RULES.md` | adding a dependency, a command, a flag, an exit code, a test or a comment |
| `references/FIXED_PATHS.md` | renaming or moving any file, editing a workflow, or touching the version lines in the docs |
| `references/DECISIONS.md` | changing anything a past decision covers |
