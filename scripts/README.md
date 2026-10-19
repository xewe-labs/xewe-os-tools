# scripts

Maintainer scripts. `release.sh` tests, cards, tags and publishes a release of xewe-os-tools; its
header comment is the full reference. The release card is [doc/spec.md](../doc/spec.md) §14, the
firmware release flow §11.

## Releasing

Maintainers only. Tags are `X.Y.Z` with no `v`. A release is prepare, commit, tag: the prepare
run tests the tree and writes the release card, the owner commits it, the tag run publishes it. In
the commands below `<version>` is the new version, for example `X.Y.Z`:

```sh
scripts/release.sh --prepare <version> --dry-run  # stage plan and every command; changes nothing
scripts/release.sh --prepare <version>            # versions, checks, release card; no git change
scripts/release.sh --prepare <version> --resume   # after a failed step: only what is not green
scripts/release.sh <version> --dry-run            # preflight, card verify, CI state; changes nothing
scripts/release.sh <version>                      # verify, CI gate, tag, GitHub Release
```

### Prepare

`--prepare` makes no git change. It refuses a detached HEAD, a merge, rebase, cherry-pick or
revert in progress, conflicted paths and any change outside the version lines and
`releases/<version>/`. Then it sets every version location to the new version.

The release notes are `releases/<version>/notes.txt`. On the first run the file is missing, so
the script writes a skeleton and stops with exit 2: fill it in, delete the marker line, and run
again. While the marker is in the file, every run stops there.

The checks run in a card run started with `xewe release-card init` on the gitignored
`releases/<version>/logs/`; each step is recorded by `xewe release-card record`. For each Python
of `ci.yml`'s matrix that is installed here: a venv with the tree installed (`pip install .`) and
the checkers, then `pytest` (JUnit), `pyflakes` and `mypy`; then `xewe format --check`,
`xewe brand-lint .` and `actionlint`. A failing step does not stop the others. Finally
`xewe release-card render` writes `releases/<version>/release_card.md` from that run and
`notes.txt` (or refuses, with the draft in the logs folder). There is no hand-written test card.

When a step failed, fix it and run `--prepare <version> --resume`: the run is kept while commit and
tree are unchanged, and only the steps that are not green run again.

The script then lists the files to commit. Commit them as `prepare <version>` and push `main`;
`ci.yml` checks the card against its own pytest run (the `card` job).

The version locations are `pyproject.toml`, `src/xewe/__init__.py`, `DEFAULT_TOOLS_REF` in
`src/xewe/env/pins.py`, and the version examples in `doc/spec.md`, `doc/ci.md` and the root
`README.md` (`VERSION_LOCATIONS` in the script). Each pattern must match at least one line, so do
not remove those example lines.

### Tag

The tag run has five numbered stages:

1. preflight: version `X.Y.Z`; on `main`, clean and level with `origin/main` after a fetch; the tag
   free here and on origin; `notes.txt` and `release_card.md` committed; every version location
   says the version, or it lists the ones that do not; `gh` logged in;
2. `xewe release-card verify`: the card is as rendered, HEAD's clean tree is the tested tree, the
   local logs are present;
3. waits for the `ci` workflow on HEAD to pass, including its card check;
4. tags HEAD `<version>` (annotated), pushes the tag and creates the GitHub Release from
   `notes.txt` with `release_card.md` as its only asset;
5. prints how consumers pick it up: the `[tools]` ref for `xewe.toml` and the `uses:` line for
   workflow callers.

Options: `--ci-timeout <min>` (default 30) bounds the CI wait; `--skip-ci` skips it, for
emergencies only, with a loud warning. If the tag exists but the GitHub Release does not, the same
command only creates the release from the tagged commit's `notes.txt` and `release_card.md`
(pushing the tag first if origin lacks it), provided the tagged commit's files say the version
(otherwise it lists the stale locations); the branch, clean-tree, card and CI checks are skipped
then. With both tag and release present it reports the version as already released.

`--dry-run` prints every command and changes nothing: only read-only git, gh, Python lookups and
`release-card verify` run. It reports every failed check, not only the first, never waits, and
exits 1 if a check failed.

Exit codes: 0 done, 1 a refusal or a failed check, 2 `notes.txt` still to fill.

What is kept per version: [releases/](../releases/README.md).
