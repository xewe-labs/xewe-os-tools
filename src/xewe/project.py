"""Project root discovery and the paths under ``build/``.

Nothing absolute is stored: the root is found from the CWD (or ``--project``) on every run, so a
project directory can be moved or renamed after setup.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from xewe.report import EXIT_FAIL, EXIT_USAGE, XeweError

LOCK_NAME = "xewe.lock"


def find_root(explicit: str | os.PathLike[str] | None = None, start: Path | None = None) -> Path:
    """Return the project root: ``explicit`` if given, else the nearest ancestor with xewe.lock."""
    if explicit is not None:
        root = Path(explicit).expanduser().resolve()
        if not (root / LOCK_NAME).is_file():
            raise XeweError(f"{root} has no {LOCK_NAME}", EXIT_USAGE)
        return root
    here = (start or Path.cwd()).resolve()
    for candidate in (here, *here.parents):
        if (candidate / LOCK_NAME).is_file():
            return candidate
    raise XeweError(f"no {LOCK_NAME} in {here} or any parent; pass --project DIR", EXIT_USAGE)


@dataclass(frozen=True)
class Paths:
    """Every location xewe reads or writes inside a project."""

    root: Path

    @property
    def lock(self) -> Path:
        return self.root / LOCK_NAME

    @property
    def build(self) -> Path:
        return self.root / "build"

    @property
    def build_config(self) -> Path:
        return self.build / "build_config.toml"

    @property
    def boards_toml(self) -> Path:
        return self.build / "boards.toml"

    @property
    def venv(self) -> Path:
        return self.build / ".venv"

    @property
    def tools_checkout(self) -> Path:
        return self.build / "xewe-os-tools"

    @property
    def bin(self) -> Path:
        return self.build / "bin"

    @property
    def default_arduino_data(self) -> Path:
        return self.build / "arduino15"

    @property
    def arduino_user(self) -> Path:
        return self.build / "arduino-user"

    @property
    def libraries(self) -> Path:
        return self.build / "libraries"

    @property
    def modules_checkout(self) -> Path:
        return self.build / "xewe-os-modules"

    @property
    def gen(self) -> Path:
        return self.build / "gen"

    @property
    def cache(self) -> Path:
        return self.build / "cache"

    @property
    def out(self) -> Path:
        return self.build / "out"

    @property
    def src_modules(self) -> Path:
        return self.root / "src" / "modules"

    def cache_dir(self, chip: str) -> Path:
        return self.cache / chip

    def gen_dir(self, chip: str) -> Path:
        return self.gen / chip

    def out_dir(self, chip: str) -> Path:
        return self.out / chip

    def rel(self, path: Path) -> str:
        """``path`` relative to the project root when inside it, else absolute (for display/argv)."""
        try:
            return str(path.relative_to(self.root))
        except ValueError:
            return str(path)

    def from_build(self, stored: str) -> Path:
        """Resolve a path stored in build_config.toml (relative to build/, or absolute)."""
        p = Path(stored)
        return p if p.is_absolute() else self.build / p

    def to_build(self, path: Path) -> str:
        """Store ``path`` relative to build/ when inside it, else absolute."""
        try:
            return path.resolve().relative_to(self.build.resolve()).as_posix()
        except ValueError:
            return str(path.resolve())

    def sketch_ino(self) -> Path:
        """The single ``*.ino`` in the project root."""
        inos = sorted(self.root.glob("*.ino"))
        if len(inos) != 1:
            found = ", ".join(p.name for p in inos) or "none"
            raise XeweError(f"expected exactly one .ino in {self.root} (found: {found})", EXIT_FAIL)
        return inos[0]


def write_atomic(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` through ``<path>.tmp`` + rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
