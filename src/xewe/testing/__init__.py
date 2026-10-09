"""Firmware testing: the pytest plugin (fixtures ``compiled``/``board``/``firmware``/``serial``)
and the ``xewe test`` runner."""

from __future__ import annotations

import os
from pathlib import Path


def module_dir(test_file: str | os.PathLike[str]) -> Path:
    """The module folder (``module.properties``, ``src/``, ``tests/``, ``README.md``) of a module test.

    In the modules repo the test sits at ``modules/<slug>/tests/<sub>/``, so that is its folder. In a
    project, setup copies the test to ``build/modules/tests/<slug>/<sub>/`` (SPEC §10), so the folder
    is ``modules/<slug>/`` of the modules checkout setup recorded (``XEWE_MODULES_SOURCE`` or the
    shared ``build-tools/sources/xewe-os-modules/<ref>/``)."""
    here = Path(test_file).resolve()
    in_repo = here.parents[2]
    if (in_repo / "module.properties").is_file():
        return in_repo
    from xewe import config, lockfile, modules
    from xewe.project import Paths, find_root

    slug = here.parents[1].name
    p = Paths(find_root(start=here.parent))
    cfg = config.load(p)
    checkout = modules.checkout(p, lockfile.load(p.lock).modules.ref, cfg.path(p, "modules") if cfg else None)
    return modules.Registry.load(checkout).get(slug).dir
