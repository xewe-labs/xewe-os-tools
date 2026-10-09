"""Project root discovery, the paths under ``build/`` and the shared toolchain folder.

Nothing absolute is stored about the project: the root is found from the CWD (or ``--project``)
on every run, so a project directory can be moved or renamed after setup. Only the shared
toolchain and source checkouts (``$XEWE_HOME`` or ``~/.xewe-os``, ``build-tools/``) are recorded
with absolute paths.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from xewe.report import EXIT_FAIL, EXIT_USAGE, XeweError

LOCK_NAME = "xewe.toml"
"""The project manifest (name, version, chip, core/modules/tools refs, module selection, libraries)."""


def find_root(explicit: str | os.PathLike[str] | None = None, start: Path | None = None) -> Path:
    """Return the project root: ``explicit`` if given, else the nearest ancestor with xewe.toml."""
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


HOME_ENV = "XEWE_HOME"
"""Overrides ``~/.xewe-os``, the per-machine folder that holds the shared toolchain."""


def xewe_home() -> Path:
    """``$XEWE_HOME`` or ``~/.xewe-os`` (resolved on every call, so tests can point it elsewhere)."""
    value = os.environ.get(HOME_ENV)
    return Path(value).expanduser().resolve() if value else Path.home() / ".xewe-os"


@dataclass(frozen=True)
class Paths:
    """Every location xewe reads or writes: the project (``build/``, ``src/Modules.h``) and the
    per-machine toolchain under ``~/.xewe-os/build-tools`` (SPEC §6 layout)."""

    root: Path

    # ------------------------------------------------------------- shared toolchain (per machine)

    @property
    def home(self) -> Path:
        return xewe_home()

    @property
    def build_tools(self) -> Path:
        return self.home / "build-tools"

    @property
    def default_arduino_data(self) -> Path:
        return self.build_tools / "arduino15"

    @property
    def arduino_user(self) -> Path:
        return self.build_tools / "arduino-user"

    @property
    def bin(self) -> Path:
        return self.build_tools / "bin"

    @property
    def downloads(self) -> Path:
        """``$XEWE_CACHE`` or ``build-tools/downloads`` (arduino-cli archives and core downloads)."""
        value = os.environ.get("XEWE_CACHE")
        return Path(value).expanduser() if value else self.build_tools / "downloads"

    @property
    def toolchain_lock(self) -> Path:
        return self.build_tools / ".lock"

    @property
    def sources(self) -> Path:
        """Shared source checkouts: ``build-tools/sources/<repo>/<ref>/``."""
        return self.build_tools / "sources"

    def modules_checkout(self, ref: str) -> Path:
        """The shared xewe-os-modules checkout at ``ref`` (one folder per ref, per machine)."""
        return self.sources / "xewe-os-modules" / ref_dir(ref)

    # ------------------------------------------------------------- project

    @property
    def lock(self) -> Path:
        return self.root / LOCK_NAME

    @property
    def build(self) -> Path:
        return self.root / "build"

    @property
    def config_dir(self) -> Path:
        return self.build / "config"

    @property
    def build_config(self) -> Path:
        return self.config_dir / "build_config.toml"

    @property
    def boards_toml(self) -> Path:
        return self.config_dir / "boards.toml"


    @property
    def tools_checkout(self) -> Path:
        return self.build / "tools"

    @property
    def venv(self) -> Path:
        return self.tools_checkout / ".venv"

    @property
    def libraries(self) -> Path:
        return self.build / "libraries"

    @property
    def modules(self) -> Path:
        """``build/modules/``: the generated Arduino library ``XeWeModules`` (``library.properties``,
        ``src/``), the selected modules' tests (``tests/<slug>/{board,unit}/``) and ``modules.lock``."""
        return self.build / "modules"

    @property
    def modules_lock(self) -> Path:
        return self.modules / "modules.lock"

    def module_tests(self, slug: str) -> Path:
        """``build/modules/tests/<slug>/`` (the module's ``board/`` and ``unit/`` tests)."""
        return self.modules / "tests" / slug

    @property
    def builds(self) -> Path:
        return self.build / "builds"

    @property
    def tmp(self) -> Path:
        """Sketch mirror, modules staging, pytest cache: created on demand by those code paths
        (never by setup); ``xewe clean`` removes it."""
        return self.build / "tmp"

    def drop_tmp_if_empty(self) -> None:
        """Remove ``build/tmp`` when nothing is left in it."""
        try:
            self.tmp.rmdir()
        except OSError:
            pass

    @property
    def src_modules_h(self) -> Path:
        return self.root / "src" / "Modules.h"

    @property
    def legacy_src_modules(self) -> Path:
        """``src/modules/`` of the previous layout (removed by ``modules generate`` when generated)."""
        return self.root / "src" / "modules"

    def gen_dir(self, chip: str) -> Path:
        return self.builds / chip / "gen"

    def cache_dir(self, chip: str) -> Path:
        return self.builds / chip / "cache"

    def out_dir(self, chip: str) -> Path:
        return self.builds / chip / "out"

    def sketch_mirror(self, stem: str) -> Path:
        return self.tmp / "sketch" / stem

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


_REF_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def ref_dir(ref: str) -> str:
    """``ref`` as a single folder name (``feature/x`` -> ``feature_x``; a commit SHA is its own folder)."""
    return _REF_UNSAFE.sub("_", ref).strip("._") or "_"


def write_atomic(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` through ``<path>.tmp`` + rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
