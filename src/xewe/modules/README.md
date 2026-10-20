# xewe.modules

The modules repo checkout and the project manifest: the module registry, dependency resolution,
generation of `build/modules/` and `src/Modules.h`, validation, and `xewe.toml` itself. The
contract is [doc/spec.md](../../../doc/spec.md) §10 (modules), with the manifest format in §4.

- `registry.py`: `xewe modules`, the registry of a modules checkout, dependency resolution,
  generation of `build/modules/` (the `XeWeModules` library, the selected modules' tests,
  `modules.lock`) and `src/Modules.h`, the core and module blocks of the project's `Config.h`
  (a catalogue of commented-out overrides: each entry keeps its comment line, its definition is
  prefixed with `// ` and its `#ifndef` guard and `NAME_PROMPT` line are dropped), and validation.
- `lockfile.py`: reads, validates and writes `xewe.toml`.
- `manifest.py`: `xewe manifest show` and `xewe manifest update`, the refs of `xewe.toml`.
- `tomlw.py`: a minimal TOML writer for the files xewe generates (`tomllib` only reads).
