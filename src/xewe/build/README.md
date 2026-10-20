# xewe.build

Compiling, flashing and releasing firmware, plus the repo-wide checks (`format`, `brand-lint`) and
the web UI export. The contract is [doc/spec.md](../../../doc/spec.md) §7 (build), §8 (flash),
§11 (release) and §14 (release card); the web UI export is [doc/webui.md](../../../doc/webui.md).

- `compile.py`: `xewe build`, the generated `XeWeBuildInfo.h` (version, timestamp, chip) and
  `XeWeOverrides.h` (`#undef`/`#define` of the values a `Config.h` line `#define NAME
  INJECTED_FROM_DOTENV` takes from the dotenv file, then of the `--define` items), arduino-cli
  compile into `build/builds/<chip>/out/`. Every C++ translation unit starts with
  `XeWeBuildInfo.h`, the project's `Config.h`, then `XeWeOverrides.h`.
- `chips.py`: the supported chips (c3, c6, s3): FQBN board, web-flasher chip family, esptool id.
- `partitions.py`: the `PartitionScheme` FQBN option and its app partition size, read from the
  installed esp32 core.
- `flash.py`: `xewe flash` and `xewe run`, build if stale and write the merged image with esptool.
- `release.py`: `xewe release`, the release matrix into `static/firmware/releases/<version>/`;
  it refuses a `Config.h` with an active `INJECTED_FROM_DOTENV` line.
- `releasecard.py`: `xewe release-card`, the generated `release_card.md` of a repo.
- `format.py` and `clang-format`: `xewe format` and the XeWe clang-format style it ships.
- `brandlint.py`: `xewe brand-lint`, banned spellings of the brand.
- `webui.py` and `webui-prompt.txt`: `xewe webui export` and the prompt `xewe webui prompt` prints.
