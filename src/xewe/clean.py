"""``xewe clean``: remove generated build output."""

from __future__ import annotations

import shutil

from xewe.project import Paths
from xewe.report import EXIT_OK, result

KEEP_ON_ALL = {".venv", "xewe-os-tools"}


def clean(p: Paths, everything: bool = False, modules: bool = False) -> int:
    """Default: build/cache, build/out, build/gen. ``everything``: all of build/ except the venv
    and tools checkout. ``modules``: also src/modules/."""
    targets = [p.cache, p.out, p.gen]
    if everything and p.build.is_dir():
        targets = [c for c in p.build.iterdir() if c.name not in KEEP_ON_ALL]
    if modules:
        targets.append(p.src_modules)
    for target in targets:
        if target.is_dir() and not target.is_symlink():
            shutil.rmtree(target)
        elif target.exists() or target.is_symlink():
            target.unlink()
        else:
            continue
        result(f"removed {p.rel(target)}")
    return EXIT_OK
