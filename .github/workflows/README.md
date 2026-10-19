# .github/workflows

The reusable GitHub workflows of XeWe OS, plus this repo's own CI. Other repos call them with a
caller of about 20 lines that pins a tools tag:

```yaml
uses: xewe-labs/xewe-os-tools/.github/workflows/<name>.yml@<version>
```

Inputs, jobs and the release flow: [doc/ci.md](../../doc/ci.md).

- `ci.yml`: this repo's checks: pytest, pyflakes, mypy, actionlint on these workflows,
  `xewe format --check`, `xewe brand-lint` and the release card check. Every job blocks.
- `xewe-build.yml`: build and host tests for a firmware project: `./setup.sh`, `xewe build` per
  chip, `xewe test --no-board`, the build artifacts, and a lint job.
- `xewe-release.yml`: release of a firmware project on an `X.Y.Z` tag: plan, one build job per
  chip, assemble with the release card check and the GitHub Release.
- `xewe-core-tests.yml`: checks for xewe-os-core (the XeWeCore library): host unit tests,
  arduino-lint, the examples per chip, format, brand-lint and the release card check.
- `xewe-modules-tests.yml`: checks for xewe-os-modules: a template clone as harness, the validator,
  module unit tests, one compile, format, brand-lint and the release card check.
- `xewe-board.yml`: board tests on a self-hosted runner with a board attached; not called yet.
- `xewe-pages.yml`: an optional GitHub Pages web flasher rebuilt from the GitHub Releases; not
  called yet.
