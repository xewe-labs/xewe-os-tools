# scripts

Maintainer scripts. `release.sh` tests, cards, commits, tags and publishes a release of
xewe-os-tools, and this page is its full reference. The release card is
[doc/spec.md](../doc/spec.md) §14, and the firmware release flow is §11.

## Releasing

Maintainers only. Tags are `X.Y.Z` with no `v`; `X.Y.Z-suffix` is a pre-release, and its version
lines get `X.Y.Z`. A release is one command, run on a clean `main`. In the commands below
`<version>` is the new version, for example `X.Y.Z`:

```sh
scripts/release.sh <version> --notes "First line." --notes "Second line."   # the whole release
scripts/release.sh <version> --notes-file notes.txt                          # the notes from a file
scripts/release.sh <version> --resume                                        # after a stop: what is missing
```

Options: `--notes "line"` (once per line) and `--notes-file FILE` give the release notes, 1 to 3
lines, each at most 120 characters, none starting with `#` (`xewe release-notes check`);
`--ci-timeout <min>` (default 60) bounds the CI wait; `--resume` continues a run that stopped.

The script runs six stages:

1. preflight: it refuses, and lists every problem, unless you are on `main` with no change in the
   tree (untracked files included), `main` is level with `origin/main` after a fetch, the tag is
   free here and on origin (except with `--resume`), `build/` is ignored, `gh` is logged in and
   the notes are valid. It also finds every Python of `ci.yml`'s test matrix that is installed
   here, on `PATH` or else through `uv python find`;
2. tests: it sets every version location to the new version (`VERSION_LOCATIONS` in the script:
   `pyproject.toml`, `src/xewe/__init__.py`, `DEFAULT_TOOLS_REF` in `src/xewe/env/pins.py`, and
   the version examples in `doc/spec.md`, `doc/ci.md` and the root `README.md`; each pattern must
   match at least one line, so do not remove those example lines), then starts a card run on
   `build/release/<version>/logs/`; each step is recorded by `xewe release-card record`. For each
   Python of `ci.yml`'s matrix that is installed here: a venv with the tree installed
   (`pip install .`) and the checkers, installed as `ci.yml` installs them, then `pytest` (JUnit),
   `pyflakes` and `mypy`; then `xewe format --check`, `xewe brand-lint .` and `actionlint`. A
   failing step does not stop the others, and the card then refuses the release;
3. release card: `xewe release-card render` writes `build/release/<version>/release_card.md` from
   that run and the notes (or refuses, with the draft in the logs folder). There is no
   hand-written test card;
4. commit: only the version files are added, as `release <version>`; any other changed file stops
   the run;
5. tag: the tag message is the notes, a blank line and the card. The tag is annotated with
   `--cleanup=verbatim`, because git would otherwise drop the card's `#` lines. The script pushes
   `main`, checks that `origin/main` is HEAD, then pushes the tag, so the workflow is registered
   before the tag triggers it;
6. release: it waits for the `ci` workflow on the tag, `--ci-timeout` minutes at most. The card job
   compares the tag's card with the run, and its `xewe release-card verify --ci` step must have run
   and passed. The script then creates the GitHub Release with `gh release create --verify-tag`,
   the notes as its text and `release_card.md` as its only asset. It prints the release URL and
   how consumers pick it up: the `[tools]` ref for `xewe.toml` and the `uses:` line for workflow
   callers.

Everything the run writes is under `build/release/<version>/` (gitignored): `notes.txt`, the
JUnit files, `logs/`, `release_card.md` and `tag_message.txt`. A run that stops before the commit
puts the version lines back.

`--resume`: when the tag exists (here or on origin), the script pushes what is missing (`main`,
then the tag) and runs stage 6; a GitHub Release that exists is only reported. When HEAD is the
`release <version>` commit and there is no tag, it reuses the results under
`build/release/<version>/` if the card is there (otherwise it runs stage 2 again) and goes on from
stage 3.

Exit codes: 0 done, 1 a refusal or a failed check.

What is kept per version: [releases/](../releases/README.md).
