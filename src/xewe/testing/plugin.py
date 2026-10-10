"""pytest plugin loaded through the ``pytest11`` entry point (doc/spec.md §9).

Module test folders need no conftest.py: the fixtures below build the firmware once per
session, find the board, flash it once, wait for the firmware to finish booting, and hand each
test the session's one serial console. Without a board, board tests are skipped with a reason
starting ``compiled, not run`` and the run exits 0; ``--xewe-require-board`` turns those skips
into failures (``xewe test`` then exits 4, see ``board_missing``). ``--xewe-no-board`` (or
``XEWE_NO_BOARD=1``, which ``xewe test --no-board`` sets) is the hard switch: no port is looked at
and board tests are "compiled, not run" as if no board were attached.

At configure time the project's dotenv file is applied (:mod:`xewe.env.dotenv`; the real environment
wins), so ``XEWE_TEST_*`` pins, ``XEWE_PORT`` and ``XEWE_CHIP`` reach the tests without exports.
"""

from __future__ import annotations

import os
from collections.abc import Generator, Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from serial import SerialException

from xewe.board import boards, serialio
from xewe.board.boards import Board
from xewe.board.serialio import BOOT_READY, BOOT_UNPROVISIONED, Console, ExpectTimeout, wait_for_banner
from xewe.build import flash
from xewe.build import compile as build
from xewe.env import dotenv
from xewe.env.project import Paths, find_root
from xewe.modules import lockfile
from xewe.modules.lockfile import Lock
from xewe.report import BOARD_DISABLED, EXIT_NO_BOARD, NO_BOARD, XeWeError, board_disabled, disable_board, log

NOT_RUN = "compiled, not run"
BOARD_FIXTURES = frozenset({"compiled", "board", "firmware", "serial"})
BOOT_TIMEOUT_SECONDS = 90.0
"""Longest wait for the end-of-boot banner (Wi-Fi + NTP, plus the one reboot after first boot)."""
BOOT_WAIT_SECONDS = 0.5
"""Settle after the banner: read until this much silence before the first test."""
PROBE_TIMEOUT_SECONDS = 15.0
"""``revive``: how long the probe may take (3 sends of ``$system uid``)."""
SILENT_MESSAGE = (
    "board on {port} is silent after a restart (no reply to {probe!r}, even after reopening the port); "
    "power-cycle it (unplug and replug), then re-run the tests"
)
"""``pytest.exit`` reason when ``revive`` fails: the rest of the session would only time out (TV1 F7)."""
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


class XeWeContext:
    """Per-session state: project paths, lock, chosen chip and port."""

    def __init__(self, config: pytest.Config) -> None:
        self.config = config
        self.port: str | None = config.getoption("xewe_port")
        self.require_board: bool = bool(config.getoption("xewe_require_board")) or os.environ.get(
            "XEWE_REQUIRE_BOARD"
        ) == "1"
        if config.getoption("xewe_no_board"):
            disable_board()
        explicit = config.getoption("xewe_project")
        try:
            self.paths: Paths | None = Paths(find_root(explicit, start=config.rootpath))
        except XeWeError:
            self.paths = None
        self.explicit = explicit is not None
        self._chip: str | None = config.getoption("xewe_chip")
        self._lock: Lock | None = None
        self.board_tests: set[str] = set()
        """Node ids marked ``board`` at collection (the summary counts their passes)."""
        self.no_board = False
        """Set when a board fixture failed because the board was required but missing (exit 4)."""
        self.console: Console | None = None
        """The session console once ``firmware`` opened it."""
        self.suspect = False
        """Set when a board test failed on a timeout or a vanished port; the next ``serial`` checks the board."""

    @property
    def active(self) -> bool:
        """True inside a xewe project."""
        return self.paths is not None

    def fail(self, exc: XeWeError) -> None:
        """``pytest.fail`` with the error; remember board-missing errors for ``xewe test``'s exit 4."""
        if exc.code == EXIT_NO_BOARD:
            self.no_board = True
        pytest.fail(str(exc), pytrace=False)

    def project(self) -> Paths:
        if self.paths is None:
            pytest.fail("not inside a xewe project (no xewe.toml); pass --xewe-project DIR", pytrace=False)
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


_KEY = pytest.StashKey[XeWeContext]()


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("xewe")
    group.addoption("--xewe-chip", default=None, help="chip to build and test (c3, c6, s3)")
    group.addoption("--xewe-port", default=None, help="serial port of the board")
    group.addoption("--xewe-require-board", action="store_true", help="fail board tests when no board is attached")
    group.addoption("--xewe-no-board", action="store_true",
                    help="never look for or open a board (also XEWE_NO_BOARD=1); board tests are compiled, not run")
    group.addoption("--xewe-project", default=None, help="project root (default: nearest xewe.toml)")


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "unit: pure logic; runs on the developer machine, never needs a board or a build")
    config.addinivalue_line("markers", "board: needs firmware on a board")
    ctx = XeWeContext(config)
    config.stash[_KEY] = ctx
    # XEWE_TEST_* pins, XEWE_PORT, XEWE_CHIP from the dotenv file (real environment wins), before
    # collection so module tests read them from os.environ. Only inside a xewe project.
    if ctx.paths is not None:
        try:
            dotenv.load_settings(ctx.paths.root)
        except XeWeError as exc:
            raise pytest.UsageError(str(exc)) from None


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Mark tests that use a board fixture as ``board`` (before ``-m`` deselection runs)."""
    ctx = config.stash.get(_KEY, None)
    for item in items:
        if BOARD_FIXTURES & set(getattr(item, "fixturenames", ())):
            item.add_marker(pytest.mark.board)
        if ctx is not None and item.get_closest_marker("board") is not None:
            ctx.board_tests.add(item.nodeid)


@pytest.fixture(scope="session")
def xewe(pytestconfig: pytest.Config) -> XeWeContext:
    """The session's xewe context."""
    return pytestconfig.stash[_KEY]


@pytest.fixture(scope="session")
def compiled(xewe: XeWeContext) -> Path:
    """Build the selected chip once; a failure fails every board test."""
    p = xewe.project()
    try:
        res = build.build_chip(p, xewe.lock, xewe.chip)
    except XeWeError as exc:
        pytest.fail(f"build failed for {xewe.chip}: {exc}", pytrace=False)
    if not res.ok or res.binary is None:
        pytest.fail(f"build failed for {xewe.chip}; see {p.rel(p.out_dir(xewe.chip) / 'compile.log')}", pytrace=False)
    return res.binary


@pytest.fixture(scope="session")
def board(xewe: XeWeContext, compiled: Path) -> Board:
    """The attached board; skipped ("compiled, not run") when there is none."""
    p = xewe.project()
    if board_disabled():
        if xewe.require_board:
            xewe.fail(XeWeError(f"{NOT_RUN}: {BOARD_DISABLED} (--require-board)", EXIT_NO_BOARD))
        pytest.skip(f"{NOT_RUN}: {NO_BOARD} ({BOARD_DISABLED})")
    try:
        found = boards.select(p, chip=xewe.chip, port=xewe.port, esptool_cmd=lambda: flash.esptool_cmd(p))
    except XeWeError as exc:
        xewe.fail(exc)
    if found is None:
        if xewe.require_board:
            xewe.fail(XeWeError(f"{NOT_RUN}: {NO_BOARD} (--require-board)", EXIT_NO_BOARD))
        pytest.skip(f"{NOT_RUN}: {NO_BOARD}")
    if found.chip and found.chip != xewe.chip:
        pytest.fail(f"board on {found.port} is {found.chip}, tests were built for {xewe.chip}", pytrace=False)
    return found


def revive(console: Console) -> bool:
    """After a timeout: is the board still there? Reopen the port once, then probe it.

    A native-USB board that restarted can leave the host with a stale endpoint: the open port reads
    nothing although the board runs. ``Console.reopen`` binds to the re-enumerated device (the
    open never resets the board). Then ``$system uid`` is sent (``Console.probe``, 3 tries over
    15 s); a board sitting at the first-boot prompt (``Name your device`` in the first 3 s) counts
    as alive and is not probed (the probe would be taken as its name). False when the port does
    not come back or nothing answers.
    """
    try:
        console.reopen()
    except XeWeError:
        return False
    seen = console.collect(silence=BOOT_WAIT_SECONDS, limit=3)
    if any(BOOT_UNPROVISIONED in line for line in seen):
        return True
    if console.probe(PROBE_TIMEOUT_SECONDS):
        log.warning("board on %s answered after the port was reopened", console.port)
        return True
    return False


def require_alive(console: Console) -> None:
    """``revive`` or end the session with ``pytest.exit`` (exit 1), so one silent board does not
    turn every later test into a timeout."""
    if not revive(console):
        pytest.exit(SILENT_MESSAGE.format(port=console.port, probe=serialio.PROBE_COMMAND),
                    returncode=pytest.ExitCode.TESTS_FAILED)


def wait_for_boot(console: Console, timeout: float = BOOT_TIMEOUT_SECONDS) -> None:
    """Reset the board and wait for ``System Setup Complete``; fail on the provisioning prompt.

    The reset makes sure the banner is printed after the port was opened. A first boot prints
    ``Initial Setup Complete`` and ``Rebooting`` and boots again: that is waited through, as is
    the native-USB port dropping and coming back during either reset.
    No banner in time (or the port still gone): the port is reopened once and the board probed
    (``require_alive``); a board that answers is used, a silent one ends the session.
    """
    try:
        m = wait_for_banner(console, f"{BOOT_READY}|{BOOT_UNPROVISIONED}", timeout)
    except (ExpectTimeout, XeWeError) as exc:  # XeWeError: port still gone at the deadline
        log.warning("board on %s did not finish booting within %g s (no %r): %s", console.port, timeout,
                    BOOT_READY, str(exc).splitlines()[0])
        start = len(console.lines)
        require_alive(console)
        if any(BOOT_UNPROVISIONED in line for line in console.lines[start:]):
            pytest.fail(UNPROVISIONED_MESSAGE.format(port=console.port), pytrace=False)
        console.collect(silence=BOOT_WAIT_SECONDS)
        return
    if m.group(0) == BOOT_UNPROVISIONED:
        pytest.fail(UNPROVISIONED_MESSAGE.format(port=console.port), pytrace=False)
    console.collect(silence=BOOT_WAIT_SECONDS)


@pytest.fixture(scope="session")
def firmware(xewe: XeWeContext, board: Board, compiled: Path) -> Iterator[Firmware]:
    """Flash the session's image once, open the session console, wait for the boot banner.

    The console stays open for the whole session (one port open: every open can pulse a
    native-USB board) and is closed when the session ends.
    """
    p = xewe.project()
    try:
        flash.write_image(flash.esptool_cmd(p), board, xewe.chip, compiled)
    except XeWeError as exc:
        xewe.fail(exc)
    console = Console(board.port)
    try:
        console.open()
    except (SerialException, OSError) as exc:
        pytest.fail(f"cannot open {board.port}: {exc}", pytrace=False)
    xewe.console = console
    try:
        wait_for_boot(console, BOOT_TIMEOUT_SECONDS)
        yield Firmware(compiled, xewe.lock.version, xewe.chip, console)
    finally:
        xewe.console = None
        console.close()


@pytest.fixture
def serial(xewe: XeWeContext, firmware: Firmware) -> Console:
    """The session console, drained (pending output dropped) and with ``lines`` emptied.

    ``send``, ``expect``, ``command``, ``write``, ``lines`` (captured since this test started),
    ``drain``, ``reset``. The port is normally never opened or closed here; after a board test
    failed on a timeout or a vanished port (``pytest_runtest_makereport``) the port is reopened
    once and the board probed first, and a silent board ends the session (``require_alive``).
    """
    console = firmware.console
    if xewe.suspect:
        xewe.suspect = False
        require_alive(console)
    console.drain()
    console.clear()
    return console


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(
    item: pytest.Item, call: pytest.CallInfo[None]
) -> Generator[None, pytest.TestReport, pytest.TestReport]:
    """Mark the session suspect when a test using the board failed on ``ExpectTimeout`` or a
    vanished port (``XeWeError``); the next ``serial`` fixture then checks the board."""
    rep = yield
    if (call.excinfo is not None and rep.failed and "serial" in getattr(item, "fixturenames", ())
            and call.excinfo.errisinstance((ExpectTimeout, XeWeError, SerialException, OSError))):
        ctx = item.config.stash.get(_KEY, None)
        if ctx is not None and ctx.console is not None:
            ctx.suspect = True
    return rep


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
        why = BOARD_DISABLED if board_disabled() else NO_BOARD
        terminalreporter.write_sep("=", f"{NOT_RUN} ({why}, chip {ctx.chip})")
        for rep in not_run:
            terminalreporter.write_line(rep.nodeid)
    passed = [r for r in stats.get("passed", []) if r.when == "call"]
    # by marker, not keyword: a test file under tests/board/ carries "board" as a keyword too
    board_passed = sum(1 for r in passed if r.nodeid in ctx.board_tests)
    failed = len(stats.get("failed", [])) + len(stats.get("error", []))
    line = f"xewe test: {len(passed) - board_passed} unit passed, {len(not_run)} {NOT_RUN}, {failed} failed"
    if board_passed:
        line += f", {board_passed} board passed"
    terminalreporter.write_line(line)


def board_missing(config: pytest.Config) -> bool:
    """True when a board fixture failed because the required board was missing (or its port
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
