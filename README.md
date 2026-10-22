# xewe-os-tools

The Python package behind every XeWe OS firmware project: one command, `xewe`, sets up, builds,
flashes, tests and releases a project. It also holds the reusable GitHub workflows that the other
XeWe OS repositories call. Python >= 3.11, Linux and macOS.

## Use it in a project

A project pins the tools in its `xewe.toml`; its `./setup.sh` installs them into `build/tools/`:

```toml
[tools]
repo = "https://github.com/xewe-labs/xewe-os-tools"
ref = "3.1.5"   # the tools tag
```

Workflow callers pin the same tag:
`uses: xewe-labs/xewe-os-tools/.github/workflows/xewe-build.yml@3.1.5`.

## Documentation

| What | Where |
|---|---|
| Commands, flags, exit codes | [src/xewe/README.md](src/xewe/README.md) |
| [Settings](src/xewe/env/README.md): the dotenv file and the [dotenv keys](src/xewe/env/README.md#keys), setup, doctor, pins | [src/xewe/env/README.md](src/xewe/env/README.md) |
| Serial, board resets, provision | [src/xewe/board/README.md](src/xewe/board/README.md) |
| Build | [src/xewe/build/README.md](src/xewe/build/README.md) |
| Modules | [src/xewe/modules/README.md](src/xewe/modules/README.md) |
| The test runner | [src/xewe/testing/README.md](src/xewe/testing/README.md) |
| Releasing the tools | [scripts/README.md](scripts/README.md) |
| CI and the reusable workflows | [doc/ci.md](doc/ci.md), [.github/workflows/README.md](.github/workflows/README.md) |
| The full specification | [doc/spec.md](doc/spec.md) |
| Web UI export | [doc/webui.md](doc/webui.md) |
| Self-tests | [tests/README.md](tests/README.md) |
| All of `doc/` | [doc/README.md](doc/README.md) |

## License

GPL-3.0-only (see [LICENSE.txt](LICENSE.txt)).
