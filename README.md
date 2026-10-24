# xewe-os-tools

`xewe` is the one command that sets up, builds, flashes, tests and releases a XeWe OS firmware
project for the ESP32-C3, -C6 and -S3. This repository also holds the reusable GitHub workflows
that the other XeWe OS repositories call. It runs on Linux and macOS with Python 3.11 or newer.

## Problem

- Every firmware repository needs the same steps: install arduino-cli and the esp32 core at known
  versions, compile per chip, flash, open the serial console and answer the first-boot prompts.
  Copied into each repository as scripts, those steps drift apart.
- Tests that need a board on the desk cannot run in CI or on a laptop without one.
- A release needs a record of what was tested on which tree, and a hand-written test card goes
  out of date with the next commit.

## Solution

One Python package does every step. Each project pins it by tag in its `xewe.toml`, and the CI
of the other repositories calls the workflows here at the same tag. Three ideas carry it:

- `xewe.toml` pins the tools, the core, the modules and the toolchain. `./setup.sh` installs
  exactly those, with the toolchain shared once per machine in `~/.xewe-os/build-tools/`.
- Working without a board is normal. `flash`, `run` and `test` compile, report
  "compiled, not run" and exit 0, and the exit codes 0 to 4 are a stable interface for scripts.
- A release card is generated from one recorded run. It says what ran on which tree with which
  result, and CI checks it against the tag.

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
| Build every chip | one FQBN per call | `xewe build --all-chips` |
| Answer the first-boot prompts | no | `xewe provision`, from the dotenv file |
| Run firmware tests without a board | no | board tests compile and report "compiled, not run" |
| Record what a release was tested on | no | `xewe release-card`, checked in CI |

`xewe` runs arduino-cli and esptool underneath.

## Install

You don't install the tools by hand. A project pins them in its `xewe.toml`, and its
`./setup.sh` installs them into `build/tools/`:

```toml
[tools]
repo = "https://github.com/xewe-labs/xewe-os-tools"
ref = "3.1.6"   # the tools tag
```

Workflow callers pin the same tag:
`uses: xewe-labs/xewe-os-tools/.github/workflows/xewe-build.yml@3.1.6`.

## Usage

Run every command from inside a project, the folder with `xewe.toml`.

Build, flash and open the console:

```sh
build/tools/.venv/bin/python -m xewe build --chip c3
build/tools/.venv/bin/python -m xewe flash --chip c3
build/tools/.venv/bin/python -m xewe serial
```

Answer the first-boot prompts, then run the tests:

```sh
build/tools/.venv/bin/python -m xewe provision
build/tools/.venv/bin/python -m xewe test
```

Check what is installed against the manifest, without changing anything:

```sh
build/tools/.venv/bin/python -m xewe doctor
build/tools/.venv/bin/python -m xewe manifest show
```

Every command, flag and exit code is in [src/xewe/README.md](src/xewe/README.md). The dotenv
file and its keys are in [src/xewe/env/README.md](src/xewe/env/README.md#keys).

## Status

Version 3.1.6 builds, flashes and tests the c3, c6 and s3 chips on Linux and macOS. The other
XeWe OS repositories call the build, release, core and modules workflows. `xewe-board.yml` and
`xewe-pages.yml` exist, but no repository calls them yet. `xewe provision` answers a `(y/n)`
prompt it does not know with `n`, so a firmware that asks its own first-boot questions needs one
pass by hand with `xewe serial`.

---

## Development

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]' pyflakes mypy types-pyserial
.venv/bin/python -m pytest -q
.venv/bin/python -m pyflakes src tests
.venv/bin/python -m mypy
```

The tests need no network, no arduino-cli and no board. Releasing the tools is one script,
described in [scripts/README.md](scripts/README.md).

## Layout

- [src/xewe/](src/xewe/README.md) is the package, with a README per area:
  [env/](src/xewe/env/README.md) for setup, doctor, pins and the dotenv file,
  [build/](src/xewe/build/README.md) for compile, flash and release,
  [board/](src/xewe/board/README.md) for serial, board resets and provisioning,
  [modules/](src/xewe/modules/README.md) for the modules registry and `xewe.toml`, and
  [testing/](src/xewe/testing/README.md) for the test runner.
- [tests/](tests/README.md) holds the self-tests, the fakes and the fixtures.
- [.github/workflows/](.github/workflows/README.md) holds this repository's CI and the reusable
  workflows, which [doc/ci.md](doc/ci.md) describes.
- [doc/](doc/README.md) holds the full specification, [doc/spec.md](doc/spec.md), and the web UI
  export guide, [doc/webui.md](doc/webui.md).
- [scripts/](scripts/README.md) holds the release script, and [releases/](releases/README.md)
  the notes and cards of past releases.

## License

GPL-3.0-only, see [LICENSE.txt](LICENSE.txt).
