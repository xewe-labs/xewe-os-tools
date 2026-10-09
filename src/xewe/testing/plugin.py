"""pytest plugin loaded through the ``pytest11`` entry point (SPEC §9).

Module test folders need no conftest.py: the fixtures below build the firmware once per
session, find the board, flash it once, wait for the firmware to finish booting, and hand each
test the session's one serial console. Without a board, hardware tests are skipped with a reason
starting ``compiled, not run`` and the run exits 0; ``--xewe-require-board`` turns those skips
into failures (``xewe test`` then exits 4, see ``board_missing``).

At configure time the project's dotenv file is applied (:mod:`xewe.dotenv`; the real environment
wins), so ``XEWE_TEST_*`` pins, ``XEWE_PORT`` and ``XEWE_CHIP`` reach the tests without exports.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from serial import SerialException

from xewe import boards, build, dotenv, flash, lockfile
from xewe.boards import Board
from xewe.lockfile import Lock
from xewe.project import Paths, find_root
from xewe.report import EXIT_NO_BOARD, NO_BOARD, XeweError
from xewe.serialio import BOOT_READY, BOOT_UNPROVISIONED, Console, ExpectTimeout, wait_for_banner

NOT_RUN = "compiled, not run"
HARDWARE_FIXTURES = frozenset({"compiled", "board", "firmware", "serial"})
BOOT_TIMEOUT_SECONDS = 90.0
"""Longest wait for the end-of-boot banner (Wi-Fi + NTP, plus the one reboot after first boot)."""
BOOT_WAIT_SECONDS = 0.5
"""Settle after the banner: read until this much silence before the first test."""
UNPROVISIONED_MESSAGE = (
    "board on {port} is unprovisioned (first-boot prompt 'Name your device'); "
    "provision it once with `xewe provision` (RUNBOOK section 3, first-boot provisioning), then re-run the tests"
)


@dataclass
class Firmware:
    """The image flashed for this session."""

    bin_path: Path
    version: str
    chip: str
    console: Console
    """The session's serial console (open for the whole session; the ``serial`` fixture hands it out)."""


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
        self.no_board = False
        """Set when a hardware fixture failed because the board was required but missing (exit 4)."""

    @property
    def active(self) -> bool:
        """True inside a xewe project."""
        return self.paths is not None

    def fail(self, exc: XeweError) -> None:
        """``pytest.fail`` with the error; remember board-missing errors for ``xewe test``'s exit 4."""
        if exc.code == EXIT_NO_BOARD:
            self.no_board = True
        pytest.fail(str(exc), pytrace=False)

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
    ctx = XeweContext(config)
    config.stash[_KEY] = ctx
    # XEWE_TEST_* pins, XEWE_PORT, XEWE_CHIP from the dotenv file (real environment wins), before
    # collection so module tests read them from os.environ. Only inside a xewe project.
    if ctx.paths is not None:
        try:
            dotenv.load_settings(ctx.paths.root)
        except XeweError as exc:
            raise pytest.UsageError(str(exc)) from None


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
        xewe.fail(exc)
    if found is None:
        if xewe.require_board:
            xewe.fail(XeweError(f"{NOT_RUN}: {NO_BOARD} (--require-board)", EXIT_NO_BOARD))
        pytest.skip(f"{NOT_RUN}: {NO_BOARD}")
    if found.chip and found.chip != xewe.chip:
        pytest.fail(f"board on {found.port} is {found.chip}, tests were built for {xewe.chip}", pytrace=False)
    return found


def wait_for_boot(console: Console, timeout: float = BOOT_TIMEOUT_SECONDS) -> None:
    """Reset the board and wait for ``System Setup Complete``; fail on the provisioning prompt.

    The reset makes sure the banner is printed after the port was opened. A first boot prints
    ``Initial Setup Complete`` and ``Rebooting`` and boots again: that is waited through, as is
    the native-USB port dropping and coming back during either reset.
    """
    try:
        m = wait_for_banner(console, f"{BOOT_READY}|{BOOT_UNPROVISIONED}", timeout)
    except ExpectTimeout as exc:
        pytest.fail(f"board on {console.port} did not finish booting within {timeout:g} s "
                    f"(no {BOOT_READY!r}); {exc}", pytrace=False)
    except XeweError as exc:  # port still gone at the deadline
        pytest.fail(f"board on {console.port} did not finish booting within {timeout:g} s: {exc}", pytrace=False)
    if m.group(0) == BOOT_UNPROVISIONED:
        pytest.fail(UNPROVISIONED_MESSAGE.format(port=console.port), pytrace=False)
    console.collect(silence=BOOT_WAIT_SECONDS)


@pytest.fixture(scope="session")
def firmware(xewe: XeweContext, board: Board, compiled: Path) -> Iterator[Firmware]:
    """Flash the session's image once, open the session console, wait for the boot banner.

    The console stays open for the whole session (one port open: every open can pulse a
    native-USB board) and is closed when the session ends.
    """
    p = xewe.project()
    try:
        flash.write_image(flash.esptool_cmd(p), board, xewe.chip, compiled)
    except XeweError as exc:
        xewe.fail(exc)
    console = Console(board.port)
    try:
        console.open()
    except (SerialException, OSError) as exc:
        pytest.fail(f"cannot open {board.port}: {exc}", pytrace=False)
    try:
        wait_for_boot(console, BOOT_TIMEOUT_SECONDS)
        yield Firmware(compiled, xewe.lock.version, xewe.chip, console)
    finally:
        console.close()


@pytest.fixture
def serial(firmware: Firmware) -> Console:
    """The session console, drained (pending output dropped) and with ``lines`` emptied.

    ``send``, ``expect``, ``command``, ``lines`` (captured since this test started), ``drain``,
    ``reset``. The port is never opened or closed here.
    """
    console = firmware.console
    console.drain()
    console.clear()
    return console


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


def board_missing(config: pytest.Config) -> bool:
    """True when a hardware fixture failed because the required board was missing (or its port
    did not come back); ``xewe test`` maps such a run to exit 4 like ``flash``/``serial``."""
    ctx = config.stash.get(_KEY, None)
    return ctx is not None and ctx.no_board


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """In a xewe project, "no tests collected" (5) is success."""
    if exitstatus == pytest.ExitCode.NO_TESTS_COLLECTED and session.config.stash[_KEY].active:
        session.exitstatus = pytest.ExitCode.OK
        tr = session.config.pluginmanager.get_plugin("terminalreporter")
        if tr is not None:
            tr.write_line("warning: no tests collected")
