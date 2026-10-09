"""``xewe clean``: remove generated build output (never the shared toolchain in ~/.xewe-os)."""

from __future__ import annotations

import shutil

from xewe.project import Paths
from xewe.report import EXIT_OK, result


def clean(p: Paths, everything: bool = False, modules: bool = False) -> int:
    """Default: build/builds and build/tmp. ``everything``: all of build/ except build/tools (the
    tools checkout and its venv). ``modules``: also build/modules and src/Modules.h.
    ``build-tools`` under ``~/.xewe-os`` (the toolchain and the shared modules checkouts) is never
    touched."""
    targets = [p.builds, p.tmp]
    if everything and p.build.is_dir():
        targets = [c for c in sorted(p.build.iterdir()) if c != p.tools_checkout]
    if modules:
        targets += [t for t in (p.modules, p.src_modules_h) if t not in targets]
    for target in targets:
        if target.is_dir() and not target.is_symlink():
            shutil.rmtree(target)
        elif target.exists() or target.is_symlink():
            target.unlink()
        else:
            continue
        result(f"removed {p.rel(target)}")
    return EXIT_OK
