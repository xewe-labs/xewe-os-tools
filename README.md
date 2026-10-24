# xewe-os-tools

`xewe` is the one command that sets up, builds, flashes, tests and releases a XeWe OS firmware project for the ESP32-C3, -C6 and -S3. This repository also holds the reusable GitHub workflows that the other XeWe OS repositories call. It needs Python 3.11 or newer.

## Problem

- Every firmware repository needs the same install, build, flash and first-boot steps. Copied into each as scripts, they drift apart.
- Tests that need a board on the desk cannot run in CI or on a laptop.
- A hand-written test card goes out of date with the next commit.

## Solution

One Python package does every step. Each project pins it by tag in its `xewe.toml`, and the CI of the other repositories calls the workflows here at the same tag. Three ideas carry it:

- `xewe.toml` pins the tools, the core, the modules and the toolchain, and `./setup.sh` installs exactly those.
- `flash`, `run` and `test` work with no board: they compile, report "compiled, not run" and exit 0.
- A release card is generated from one recorded run, and CI checks it against the tag.

## See it

With no board attached, flashing still compiles the image and says what it skipped:

```sh
build/tools/.venv/bin/python -m xewe flash --chip c3
```

```text
compiled, not run: no board attached (c3, build/builds/c3/out/<image>.bin)
```

## Why not arduino-cli alone

| Need | arduino-cli | xewe |
|---|---|---|
| Pin the core, modules and libraries by git ref | no | `xewe.toml`, checked by `xewe manifest show` |
| Answer the first-boot prompts | no | `xewe provision` |
| Record what a release was tested on | no | `xewe release-card`, checked in CI |

## Install

You don't install the tools by hand. A project pins them in its `xewe.toml`, and `./setup.sh` installs them into `build/tools/`:

```toml
[tools]
repo = "https://github.com/xewe-labs/xewe-os-tools"
ref = "3.1.6"   # the tools tag
```

Workflow callers pin the same tag: `uses: xewe-labs/xewe-os-tools/.github/workflows/xewe-build.yml@3.1.6`.

## Usage

Run every command from the project folder that holds `xewe.toml`. Build, flash and open the console:

```sh
build/tools/.venv/bin/python -m xewe build --chip c3
build/tools/.venv/bin/python -m xewe flash --chip c3
build/tools/.venv/bin/python -m xewe serial
```

Answer the first-boot prompts, run the tests, and check the install against the manifest:

```sh
build/tools/.venv/bin/python -m xewe provision
build/tools/.venv/bin/python -m xewe test
build/tools/.venv/bin/python -m xewe doctor
```

Every command, flag and exit code is in [src/xewe/README.md](src/xewe/README.md). The specification is [doc/spec.md](doc/spec.md), and the reusable workflows are in [doc/ci.md](doc/ci.md).

## Status

The tools build, flash and test the c3, c6 and s3 chips on Linux and macOS, and the other XeWe OS repositories call the build, release, core and modules workflows. `xewe-board.yml` and `xewe-pages.yml` exist, but no repository calls them yet. `xewe provision` cannot answer a firmware's own `(y/n)` questions, as [the board README](src/xewe/board/README.md) explains.

---

## Development

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]' pyflakes mypy types-pyserial
.venv/bin/python -m pytest -q
.venv/bin/python -m pyflakes src tests
.venv/bin/python -m mypy
```

The tests need no network, no arduino-cli and no board. The release procedure is in [scripts/README.md](scripts/README.md).

## Layout

- [src/xewe/](src/xewe/README.md) is the package, with a README per area: env, build, board, modules and testing.
- [tests/](tests/README.md) holds the self-tests, the fakes and the fixtures.
- [.github/workflows/](.github/workflows/README.md) holds the CI and the reusable workflows.
- [doc/](doc/README.md) holds the specification, the CI guide and the web UI export guide.
- [scripts/](scripts/README.md) holds the release script.
- [releases/](releases/README.md) holds the notes and cards of past releases.

## License

GPL-3.0-only, see [LICENSE.txt](LICENSE.txt).
