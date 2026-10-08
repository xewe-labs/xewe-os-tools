"""``xewe test``: collect test roots and run pytest in-process (SPEC §9)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from xewe import config, modules
from xewe.lockfile import Lock
from xewe.project import Paths
from xewe.report import EXIT_FAIL, EXIT_NO_BOARD, EXIT_OK, EXIT_USAGE, XeweError, log, result
from xewe.testing import plugin


def test_roots(p: Paths, lock: Lock, only: list[str]) -> list[Path]:
    """``tests/`` of the project plus ``tests/`` of every resolved module (or of ``only``)."""
    roots = [p.root / "tests"] if (p.root / "tests").is_dir() else []
    cfg = config.load(p)
    checkout = (cfg.path(p, "modules") if cfg else None) or p.modules_checkout
    if not checkout.is_dir() or not lock.selected:
        if only:
            raise XeweError("--module given but no modules are installed; run ./setup.sh", EXIT_USAGE)
        return roots
    resolved = modules.Registry.load(checkout).resolve(lock.selected)
    slugs = [m.slug for m in resolved]
    for slug in only:
        if slug not in slugs:
            raise XeweError(f"module '{slug}' is not selected (selected: {', '.join(slugs)})", EXIT_USAGE)
    for module in resolved:
        if (not only or module.slug in only) and module.tests_dir.is_dir():
            roots.append(module.tests_dir)
    return roots


class _Outcome:
    """In-process pytest plugin that records whether the session failed for want of a board."""

    def __init__(self) -> None:
        self.no_board = False

    def pytest_sessionfinish(self, session: pytest.Session) -> None:
        self.no_board = plugin.board_missing(session.config)


def _exit(code: int, no_board: bool = False) -> int:
    if code in (pytest.ExitCode.OK, pytest.ExitCode.NO_TESTS_COLLECTED):
        return EXIT_OK
    if code == pytest.ExitCode.USAGE_ERROR:
        return EXIT_USAGE
    if no_board:
        return EXIT_NO_BOARD
    return EXIT_FAIL


def run_tests(
    p: Paths,
    lock: Lock,
    chip_names: list[str],
    port: str | None = None,
    only: list[str] | None = None,
    host_only: bool = False,
    require_board: bool = False,
    extra: list[str] | None = None,
) -> int:
    """One pytest session per chip; the worst exit code wins."""
    roots = test_roots(p, lock, only or [])
    if not roots:
        result("no tests found (project tests/ and module tests/ are empty)")
        return EXIT_OK
    worst = EXIT_OK
    for chip in chip_names:
        args = [str(r) for r in roots]
        args += ["--rootdir", str(p.root), "--import-mode=importlib", "-o", f"cache_dir={p.cache / 'pytest'}"]
        # xewe is imported before pytest starts, so pytest cannot assert-rewrite the plugin package
        args += ["-W", "ignore:Module already imported so cannot be rewritten:pytest.PytestAssertRewriteWarning"]
        args += ["--xewe-project", str(p.root), "--xewe-chip", chip]
        if os.environ.get("PYTEST_DISABLE_PLUGIN_AUTOLOAD"):
            args += ["-p", "xewe.testing.plugin"]
        if port:
            args += ["--xewe-port", port]
        if require_board:
            args.append("--xewe-require-board")
        if host_only:
            args += ["-m", "host"]
        args += extra or []
        log.debug("pytest %s", " ".join(args))
        outcome = _Outcome()
        worst = max(worst, _exit(pytest.main(args, plugins=[outcome]), outcome.no_board))
    return worst
