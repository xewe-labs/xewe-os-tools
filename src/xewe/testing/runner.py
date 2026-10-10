"""``xewe test``: collect test roots and run pytest in-process (doc/spec.md §9)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from xewe.env.project import Paths
from xewe.modules.lockfile import Lock
from xewe.report import EXIT_FAIL, EXIT_NO_BOARD, EXIT_OK, EXIT_USAGE, XeWeError, log, result
from xewe.testing import plugin


def installed_modules(p: Paths) -> list[str]:
    """Slugs in ``build/modules/modules.lock`` (the resolved selection, dependencies first)."""
    if not p.modules_lock.is_file():
        return []
    lines = p.modules_lock.read_text(encoding="utf-8").splitlines()
    return [line.split("|", 1)[0] for line in lines if line and not line.startswith("#")]


def test_roots(p: Paths, lock: Lock, only: list[str]) -> list[Path]:
    """``tests/`` of the project plus ``build/modules/tests/<slug>/`` of every installed module (or of
    ``only``); setup copies them there from the modules checkout."""
    roots = [p.root / "tests"] if (p.root / "tests").is_dir() else []
    slugs = installed_modules(p)
    if not slugs:
        if only:
            raise XeWeError("--module given but no modules are installed; run ./setup.sh", EXIT_USAGE)
        return roots
    for slug in only:
        if slug not in slugs:
            raise XeWeError(f"module '{slug}' is not selected (selected: {', '.join(slugs)})", EXIT_USAGE)
    for slug in slugs:
        if (not only or slug in only) and p.module_tests(slug).is_dir():
            roots.append(p.module_tests(slug))
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
    unit_only: bool = False,
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
        args += ["--rootdir", str(p.root), "--import-mode=importlib", "-o", f"cache_dir={p.tmp / 'pytest'}"]
        # xewe is imported before pytest starts, so pytest cannot assert-rewrite the plugin package
        args += ["-W", "ignore:Module already imported so cannot be rewritten:pytest.PytestAssertRewriteWarning"]
        args += ["--xewe-project", str(p.root), "--xewe-chip", chip]
        if os.environ.get("PYTEST_DISABLE_PLUGIN_AUTOLOAD"):
            args += ["-p", "xewe.testing.plugin"]
        if port:
            args += ["--xewe-port", port]
        if require_board:
            args.append("--xewe-require-board")
        if unit_only:
            args += ["-m", "unit"]
        args += extra or []
        log.debug("pytest %s", " ".join(args))
        outcome = _Outcome()
        worst = max(worst, _exit(pytest.main(args, plugins=[outcome]), outcome.no_board))
    return worst
