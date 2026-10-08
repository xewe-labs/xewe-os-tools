"""pytest plugin loaded through the ``pytest11`` entry point (SPEC §9).

Module test folders need no conftest.py: the fixtures below build the firmware once per
session, find the board, flash it once, and hand each test a serial console. Without a board,
hardware tests are skipped with a reason starting ``compiled, not run`` and the run exits 0;
``--xewe-require-board`` turns those skips into failures.
"""

from __future__ import annotations

import os
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from xewe import boards, build, flash, lockfile
from xewe.boards import Board
from xewe.lockfile import Lock
from xewe.project import Paths, find_root
from xewe.report import NO_BOARD, XeweError
from xewe.serialio import Console

NOT_RUN = "compiled, not run"
HARDWARE_FIXTURES = frozenset({"compiled", "board", "firmware", "serial"})
BOOT_WAIT_SECONDS = 3.0


@dataclass
class Firmware:
    """The image flashed for this session."""

    bin_path: Path
    version: str
    chip: str


class XeweContext:
    """Per-session state: project paths, lock, chosen chip and port."""

    def __init__(self, config: pytest.Config) -> None:
        self.config = config
        self.port: str | None = config.getoption("xewe_port")
        self.require_board: bool = bool(config.getoption("xewe_require_board")) or os.environ.get(
            "XEWE_REQUIRE_BOARD"
        ) == "1"
        explicit = config.getoption("xewe_project")
        try:
            self.paths: Paths | None = Paths(find_root(explicit, start=config.rootpath))
        except XeweError:
            self.paths = None
        self.explicit = explicit is not None
        self._chip: str | None = config.getoption("xewe_chip")
        self._lock: Lock | None = None

    @property
    def active(self) -> bool:
        """True inside a xewe project."""
        return self.paths is not None

    def project(self) -> Paths:
        if self.paths is None:
            pytest.fail("not inside a xewe project (no xewe.lock); pass --xewe-project DIR", pytrace=False)
        return self.paths

    @property
    def lock(self) -> Lock:
        if self._lock is None:
            self._lock = lockfile.load(self.project().lock)
        return self._lock

    @property
    def chip(self) -> str:
        if self._chip is None:
            self._chip = boards.resolve_chip(None, None, self.lock.chip)
        return self._chip


_KEY = pytest.StashKey[XeweContext]()


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("xewe")
    group.addoption("--xewe-chip", default=None, help="chip to build and test (c3, c6, s3)")
    group.addoption("--xewe-port", default=None, help="serial port of the board")
    group.addoption("--xewe-require-board", action="store_true", help="fail hardware tests when no board is attached")
    group.addoption("--xewe-project", default=None, help="project root (default: nearest xewe.lock)")


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "host: pure logic; never needs a board or a build")
    config.addinivalue_line("markers", "hardware: needs firmware on a board")
    config.stash[_KEY] = XeweContext(config)


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Mark tests that use a board fixture as ``hardware`` (before ``-m`` deselection runs)."""
    for item in items:
        if HARDWARE_FIXTURES & set(getattr(item, "fixturenames", ())):
            item.add_marker(pytest.mark.hardware)


@pytest.fixture(scope="session")
def xewe(pytestconfig: pytest.Config) -> XeweContext:
    """The session's xewe context."""
    return pytestconfig.stash[_KEY]


@pytest.fixture(scope="session")
def compiled(xewe: XeweContext) -> Path:
    """Build the selected chip once; a failure fails every hardware test."""
    p = xewe.project()
    try:
        res = build.build_chip(p, xewe.lock, xewe.chip)
    except XeweError as exc:
        pytest.fail(f"build failed for {xewe.chip}: {exc}", pytrace=False)
    if not res.ok or res.binary is None:
        pytest.fail(f"build failed for {xewe.chip}; see {p.rel(p.out_dir(xewe.chip) / 'compile.log')}", pytrace=False)
    return res.binary


@pytest.fixture(scope="session")
def board(xewe: XeweContext, compiled: Path) -> Board:
    """The attached board; skipped ("compiled, not run") when there is none."""
    p = xewe.project()
    try:
        found = boards.select(p, chip=xewe.chip, port=xewe.port, esptool_cmd=lambda: flash.esptool_cmd(p))
    except XeweError as exc:
        pytest.fail(str(exc), pytrace=False)
    if found is None:
        if xewe.require_board:
            pytest.fail(f"{NOT_RUN}: {NO_BOARD} (--require-board)", pytrace=False)
        pytest.skip(f"{NOT_RUN}: {NO_BOARD}")
    if found.chip and found.chip != xewe.chip:
        pytest.fail(f"board on {found.port} is {found.chip}, tests were built for {xewe.chip}", pytrace=False)
    return found


@pytest.fixture(scope="session")
def firmware(xewe: XeweContext, board: Board, compiled: Path) -> Firmware:
    """Flash the session's image once and wait for the first boot line (or 3 s)."""
    p = xewe.project()
    try:
        flash.write_image(flash.esptool_cmd(p), board, xewe.chip, compiled)
    except XeweError as exc:
        pytest.fail(str(exc), pytrace=False)
    with Console(board.port) as console:
        end = time.monotonic() + BOOT_WAIT_SECONDS
        while time.monotonic() < end and not console.poll():
            pass
    return Firmware(compiled, xewe.lock.version, xewe.chip)


@pytest.fixture
def serial(firmware: Firmware, board: Board) -> Iterator[Console]:
    """A console on the board: ``send``, ``expect``, ``command``, ``lines``, ``drain``, ``reset``."""
    with Console(board.port) as console:
        yield console


def _not_run(reports: list[pytest.TestReport]) -> list[pytest.TestReport]:
    out = []
    for rep in reports:
        longrepr = rep.longrepr
        reason = longrepr[2] if isinstance(longrepr, tuple) else str(longrepr)
        if NOT_RUN in reason:
            out.append(rep)
    return out


def pytest_terminal_summary(terminalreporter: pytest.TerminalReporter, config: pytest.Config) -> None:
    """List the "compiled, not run" tests and print the xewe summary line."""
    ctx = config.stash[_KEY]
    stats = terminalreporter.stats
    not_run = _not_run(stats.get("skipped", []))
    if not (ctx.explicit or not_run):
        return
    if not_run:
        terminalreporter.write_sep("=", f"{NOT_RUN} ({NO_BOARD}, chip {ctx.chip})")
        for rep in not_run:
            terminalreporter.write_line(rep.nodeid)
    passed = [r for r in stats.get("passed", []) if r.when == "call"]
    hw_passed = sum(1 for r in passed if "hardware" in r.keywords)
    failed = len(stats.get("failed", [])) + len(stats.get("error", []))
    line = f"xewe test: {len(passed) - hw_passed} host passed, {len(not_run)} {NOT_RUN}, {failed} failed"
    if hw_passed:
        line += f", {hw_passed} hardware passed"
    terminalreporter.write_line(line)


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """In a xewe project, "no tests collected" (5) is success."""
    if exitstatus == pytest.ExitCode.NO_TESTS_COLLECTED and session.config.stash[_KEY].active:
        session.exitstatus = pytest.ExitCode.OK
        tr = session.config.pluginmanager.get_plugin("terminalreporter")
        if tr is not None:
            tr.write_line("warning: no tests collected")
