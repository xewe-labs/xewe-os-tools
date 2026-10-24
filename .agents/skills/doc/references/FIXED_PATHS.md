# Fixed paths

Some file names here look like documentation but are read by scripts, code or other
repositories. Renaming one breaks a release or a caller without a failing test.

- The other XeWe OS repositories call `.github/workflows/xewe-*.yml` by path at a tools tag.
  Never rename these files.
- `scripts/release.sh` rewrites the version in `pyproject.toml`, `src/xewe/__init__.py`,
  `src/xewe/env/pins.py`, `doc/spec.md`, `doc/ci.md` and `README.md` (`VERSION_LOCATIONS`). Each
  pattern must match at least one line, or the release stops. Keep those file names, the
  `ref = "X.Y.Z"   # the tools tag` line and the `xewe-os-tools/.github/workflows/<name>.yml@X.Y.Z`
  line in the root README.
- `doc/webui.md` is named in the `xewe webui` help text in `cli.py` and in a log line of
  `build/webui.py`.
- `release_card.md` is the generated card. `ci.yml` and `xewe-core-tests.yml` find committed
  cards with `git ls-files 'releases/*/release_card.md'`, and brand-lint skips that name.
- `releasecard.py` refuses a release while a `testcard.md` sits next to the new card.
- `tests/build/test_releasecard.py` reads `tests/fixtures/releasecard/golden_card.md`. Set
  `RELEASECARD_WRITE_GOLDEN=1` to rewrite it.
- The webui tests compare all of `tests/fixtures/webui/fan-dashboard_export/` with a fresh
  export, so an extra file there fails them.
- `pyproject.toml` publishes `README.md` as the package's long description.
