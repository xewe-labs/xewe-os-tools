# xewe-os-tools documentation

The reference pages of the `xewe` command and the reusable workflows. The directory READMEs next
to the code hold the user view (commands, settings, boards, releasing); the pages here hold the
contract that the code is checked against.

## Reading path

1. **[The root README](../README.md)**: what the tools are and where each topic lives.
2. **[The command table](../src/xewe/README.md)**: every `xewe` command, its flags and exit codes.
3. **[spec.md](spec.md)**: the normative contract, when you need the exact behaviour of a step.
   The code cites it as §N.
4. **[ci.md](ci.md)**: when a repository calls one of the reusable workflows.

## Reference

### [spec.md](spec.md): the normative contract

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

### Other pages

| Page | Covers |
|---|---|
| [ci.md](ci.md) | the reusable GitHub workflows, their callers, the toolchain cache, releases in CI, repository settings |
| [webui.md](webui.md) | `xewe webui`: a Flask prototype of a device web page, exported as C++ headers |
| [tests.md](tests.md) | a pointer to [tests/README.md](../tests/README.md), the self-tests of this package |
