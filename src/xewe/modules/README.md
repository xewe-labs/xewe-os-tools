# xewe.modules

The modules repo checkout and the project manifest: the module registry, dependency resolution,
generation of `build/modules/` and `src/Modules.h`, validation, and `xewe.toml` itself. The
contract is [doc/spec.md](../../../doc/spec.md) §10 (modules), with the manifest format in §4.

- `registry.py`: `xewe modules`, the registry of a modules checkout, dependency resolution,
  generation of `build/modules/` (the `XeWeModules` library, the selected modules' tests,
  `modules.lock`) and `src/Modules.h`, the core and module blocks of the project's `Config.h`
  (a catalogue of commented-out overrides between `/* ---- start <slug> ---- */` and
  `/* ---- end <slug> ---- */`, each line framed by a bar; two sections, default runtime values and
  compile time defined constants, with their start and end lines; each entry's comment line is copied
  as `/* ... */`, the definition is prefixed with `// ` and its `#ifndef` guard and `NAME_PROMPT`
  line are dropped; the single-marker and `//` source forms and the two earlier block marker forms
  are still recognised), and validation.
- `lockfile.py`: reads, validates and writes `xewe.toml`.
- `manifest.py`: `xewe manifest show` and `xewe manifest update`, the refs of `xewe.toml`.
- `tomlw.py`: a minimal TOML writer for the files xewe generates (`tomllib` only reads).
