# doc

The reference pages of the `xewe` command and the reusable workflows. The READMEs next to the
code give the user's view of commands, settings, boards and releasing. The pages here hold the
contract the code is checked against.

Start with [the root README](../README.md) for what the tools are, then
[the command table](../src/xewe/README.md) for every command, its flags and exit codes. Open
[spec.md](spec.md) when you need the exact behaviour of a step; the code cites it as §N. Open
[ci.md](ci.md) when a repository calls one of the reusable workflows.

## spec.md, the normative contract

| § | Covers |
|---|---|
| 1 | goals and non-goals |
| 2 | package layout |
| 3 | command surface |
| 4 | `xewe.toml`, the project manifest |
| 5 | `build/config/boards.toml` |
| 6 | setup procedure (`xewe setup`) |
| 7 | build procedure (`xewe build`) |
| 8 | flash and serial |
| 9 | test runner (`xewe test`) |
| 10 | modules generation and validation |
| 11 | release (`xewe release`) |
| 12 | self-tests of the package |
| 13 | configuration: the dotenv file |
| 14 | release card (`xewe release-card`) |

## Other pages

| Page | Covers |
|---|---|
| [ci.md](ci.md) | the reusable GitHub workflows, their callers, the toolchain cache, releases in CI, repository settings |
| [webui.md](webui.md) | `xewe webui`: a Flask prototype of a device web page, exported as C++ headers |

The self-tests are described in [tests/README.md](../tests/README.md).
