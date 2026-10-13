"""pytest plugin loaded through the ``pytest11`` entry point (doc/spec.md §9).

Module test folders need no conftest.py: the fixtures below build the firmware once per
session, find the board, flash it once, wait for the firmware to finish booting, and hand each
test the session's one serial console. Without a board, board tests are skipped with a reason
starting ``compiled, not run`` and the run exits 0; ``--xewe-require-board`` turns those skips
into failures (``xewe test`` then exits 4, see ``board_missing``). ``--xewe-no-board`` (or
``XEWE_NO_BOARD=1``, which ``xewe test --no-board`` sets) is the hard switch: no port is looked at
and board tests are "compiled, not run" as if no board were attached.

Every collected test carries JUnit ``user_properties`` for the release card (doc/spec.md §14):
``xewe_kind``, ``xewe_needs``, ``xewe_nodeid``, ``xewe_chip``, and once it had the board
``xewe_board``, ``xewe_fw_sha256`` and ``xewe_fw_match``.

At configure time the project's dotenv file is applied (:mod:`xewe.env.dotenv`; the real environment
wins), so ``XEWE_TEST_*`` pins, ``XEWE_PORT`` and ``XEWE_CHIP`` reach the tests without exports.

``--xewe-define KEY=VALUE`` (``xewe test --define``) gives the defines the session builds with;
without it ``XEWE_TEST_DEFINES`` (environment or dotenv file) does. ``compiled`` reuses an image
whose ``build.stamp`` matches those defines and builds one otherwise, and ``firmware`` flashes it.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Generator, Iterator, Mapping
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
from xewe.report import (
    BOARD_DISABLED,
    EXIT_NO_BOARD,
    NO_BOARD,
    XeWeError,
    board_disabled,
    disable_board,
    log,
)

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
"""``pytest.exit`` reason when ``revive`` fails: the rest of the session would only time out."""
GONE_MESSAGE = (
    "board on {port} disappeared from USB (the port did not come back, even after reopening it); "
    "power-cycle it (unplug and replug), then re-run the tests"
)
"""``pytest.exit`` reason when ``revive`` fails because the port cannot be reopened."""
DEFINES_VAR = build.TEST_DEFINES_VAR
"""Default defines of the session's build: space-separated ``KEY=VALUE`` (``--xewe-define`` replaces it)."""
UNPROVISIONED_MESSAGE = (
    "board on {port} is unprovisioned (first-boot prompt 'Name your device'); "
    "provision it once with `xewe provision` (RUNBOOK section 3, first-boot provisioning), then re-run the tests"
)
HARDWARE_FIXTURES = frozenset({"board", "firmware", "serial"})
"""Fixtures that need a board; a test using only ``compiled`` needs the build alone (release card, §14)."""
BANNER_TIMESTAMP_RE = re.compile(r"Build Timestamp[:\s]+(\d{4}-\d\d-\d\dT[\d:]+Z?)")
"""The core's boot banner line ``Build Timestamp <BUILD_TIMESTAMP>``."""
MODULE_COPY_RE = re.compile(r"^build/modules/tests/([^/]+)/")
"""Node id prefix of a module test run from its copy; the source is ``modules/<slug>/tests/``."""


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
        self.require_board: bool = (
            bool(config.getoption("xewe_require_board")) or os.environ.get("XEWE_REQUIRE_BOARD") == "1"
        )
        if config.getoption("xewe_no_board"):
            disable_board()
        explicit = config.getoption("xewe_project")
        try:
            self.paths: Paths | None = Paths(find_root(explicit, start=config.rootpath))
        except XeWeError:
            self.paths = None
        self.explicit = explicit is not None
        self.no_flash: bool = bool(config.getoption("xewe_no_flash"))
        """Skip the session's image write (``xewe test --no-flash``): the board keeps its firmware."""
        self._chip: str | None = config.getoption("xewe_chip")
        self.defines: dict[str, str] = {}
        """Defines of the session's build (``session_defines``), set at configure time."""
        self._lock: Lock | None = None
        self.board_tests: set[str] = set()
        """Node ids marked ``board`` at collection (the summary counts their passes)."""
        self.no_board = False
        """Set when a board fixture failed because the board was required but missing (exit 4)."""
        self.console: Console | None = None
        """The session console once ``firmware`` opened it."""
        self.suspect = False
        """Set when a board test failed on a timeout or a vanished port; the next ``serial`` checks the board."""
        self.binary: Path | None = None
        """The image ``compiled`` built for this session (its sha256 goes into the JUnit properties)."""
        self.board: Board | None = None
        """The board the ``board`` fixture found."""
        self.fw_match = "unknown"
        """Banner ``Build Timestamp`` == meta.json ``timestamp_param``: true, false or unknown."""
        self._fw_sha256: str | None = None

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

    def session_chip(self) -> str:
        """The chip for the JUnit properties: "" outside a project or with an unreadable manifest."""
        if self._chip is None and self.paths is None:
            return ""
        try:
            return self.chip
        except (XeWeError, OSError):
            return ""

    @property
    def fw_sha256(self) -> str:
        """sha256 of the session's image ("" before ``compiled`` built it)."""
        if self._fw_sha256 is None and self.binary is not None and self.binary.is_file():
            self._fw_sha256 = hashlib.sha256(self.binary.read_bytes()).hexdigest()
        return self._fw_sha256 or ""


def session_defines(flag_items: list[str], environ: Mapping[str, str] = os.environ) -> dict[str, str]:
    """Defines of the session's build: the ``--xewe-define`` items when there are any, else
    ``XEWE_TEST_DEFINES`` split on whitespace (so a value cannot hold a space). Both are checked
    like ``xewe build --define``; a bad item is exit 2."""
    return build.defines_or_env(flag_items, environ)


_KEY = pytest.StashKey[XeWeContext]()


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("xewe")
    group.addoption("--xewe-chip", default=None, help="chip to build and test (c3, c6, s3)")
    group.addoption("--xewe-port", default=None, help="serial port of the board")
    group.addoption("--xewe-require-board", action="store_true", help="fail board tests when no board is attached")
    group.addoption(
        "--xewe-no-board",
        action="store_true",
        help="never look for or open a board (also XEWE_NO_BOARD=1); board tests are compiled, not run",
    )
    group.addoption("--xewe-project", default=None, help="project root (default: nearest xewe.toml)")
    group.addoption(
        "--xewe-no-flash", action="store_true", help="do not write the image; test the firmware already on the board"
    )
    group.addoption(
        "--xewe-define",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help=f"extra #define for the session's build (repeatable; replaces {DEFINES_VAR})",
    )


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", "unit: pure logic; runs on the developer machine, never needs a board or a build"
    )
    config.addinivalue_line("markers", "board: needs firmware on a board")
    ctx = XeWeContext(config)
    config.stash[_KEY] = ctx
    # XEWE_TEST_* pins, XEWE_PORT, XEWE_CHIP from the dotenv file (real environment wins), before
    # collection so module tests read them from os.environ. Only inside a xewe project.
    if ctx.paths is not None:
        try:
            dotenv.load_settings(ctx.paths.root)
            ctx.defines = session_defines(config.getoption("xewe_define"))
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
    if ctx is not None:
        chip = ctx.session_chip()
        for item in items:
            item.user_properties.extend(item_properties(item, chip))


def item_properties(item: pytest.Item, chip: str) -> list[tuple[str, str]]:
    """JUnit ``user_properties`` of a collected test (doc/spec.md §14): kind, needs, source node id, chip."""
    names = set(getattr(item, "fixturenames", ()))
    kind = "board" if item.get_closest_marker("board") is not None else "unit"
    needs = "hardware" if HARDWARE_FIXTURES & names else "compile" if "compiled" in names else "none"
    nodeid = MODULE_COPY_RE.sub(r"modules/\1/tests/", item.nodeid)
    return [("xewe_kind", kind), ("xewe_needs", needs), ("xewe_nodeid", nodeid), ("xewe_chip", chip)]


def board_properties(ctx: XeWeContext) -> list[tuple[str, str]]:
    """JUnit properties of a test that had the board: board id, image sha256, banner match."""
    assert ctx.board is not None
    b = ctx.board
    return [
        ("xewe_board", f"{b.chip or ctx.chip} sn={b.serial_number or '-'} port={b.port}"),
        ("xewe_fw_sha256", ctx.fw_sha256),
        ("xewe_fw_match", ctx.fw_match),
    ]


def banner_match(lines: list[str], binary: Path) -> str:
    """Compare the last ``Build Timestamp`` in ``lines`` with ``timestamp_param`` of the meta.json
    next to ``binary``: "true" when equal, "false" when they differ, "unknown" when either is missing."""
    seen = [m[1] for line in lines if (m := BANNER_TIMESTAMP_RE.search(line))]
    try:
        meta = json.loads((binary.parent / "meta.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "unknown"
    stamp = meta.get("timestamp_param") if isinstance(meta, dict) else None
    if not seen or not stamp:
        return "unknown"
    return "true" if seen[-1] == stamp else "false"


@pytest.fixture(scope="session")
def xewe(pytestconfig: pytest.Config) -> XeWeContext:
    """The session's xewe context."""
    return pytestconfig.stash[_KEY]


@pytest.fixture(scope="session")
def compiled(xewe: XeWeContext) -> Path:
    """The selected chip's image for the session's defines: reused when ``build.stamp`` and the
    sources say it is up to date (as ``xewe flash`` decides), else built once; a build failure
    fails every board test."""
    p = xewe.project()
    try:
        binary, fresh = flash.built_image(p, xewe.lock, xewe.chip, xewe.defines)
        if fresh:
            xewe.binary = binary
            return binary
        res = build.build_chip(p, xewe.lock, xewe.chip, xewe.defines)
    except XeWeError as exc:
        pytest.fail(f"build failed for {xewe.chip}: {exc}", pytrace=False)
    if not res.ok or res.binary is None:
        pytest.fail(f"build failed for {xewe.chip}; see {p.rel(p.out_dir(xewe.chip) / 'compile.log')}", pytrace=False)
    xewe.binary = res.binary
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
    xewe.board = found
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
    """``revive`` or end the session with ``pytest.exit`` (exit 1), so one silent or vanished board
    does not turn every later test into a timeout."""
    if not revive(console):
        message = SILENT_MESSAGE if console.connected else GONE_MESSAGE
        pytest.exit(
            message.format(port=console.port, probe=serialio.PROBE_COMMAND), returncode=pytest.ExitCode.TESTS_FAILED
        )


def wait_for_boot(console: Console, timeout: float = BOOT_TIMEOUT_SECONDS) -> None:
    """Reset the board and wait for ``System Setup Complete``; fail on the provisioning prompt.

    The reset makes sure the banner is printed after the port was opened. A first boot prints
    ``Initial Setup Complete`` and ``Rebooting`` and boots again: that is waited through, as is
    the native-USB port dropping and coming back during either reset.
    No banner in time (or the port still gone): the port is reopened once and the board probed
    (``require_alive``); a board that answers is used, a silent or vanished one ends the session.
    """
    try:
        m = wait_for_banner(console, f"{BOOT_READY}|{BOOT_UNPROVISIONED}", timeout)
    # XeWeError: port still gone at the deadline; the others: a handle closed under the read
    except (ExpectTimeout, XeWeError, SerialException, OSError, TypeError) as exc:
        log.warning(
            "board on %s did not finish booting within %g s (no %r): %s",
            console.port,
            timeout,
            BOOT_READY,
            str(exc).partition("\n")[0],
        )
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
    native-USB board) and is closed when the session ends. ``--xewe-no-flash`` skips the write
    (the board's own firmware is tested; the board does not report a build id to compare).
    """
    p = xewe.project()
    if xewe.no_flash:
        log.warning("not flashing %s (--no-flash): testing the firmware already on the board", board.port)
    else:
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
        xewe.fw_match = banner_match(console.lines, compiled)
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
    vanished port (``XeWeError``); the next ``serial`` fixture then checks the board.

    When a test that uses the board reaches its call, its JUnit properties gain ``board_properties``."""
    if call.when == "call" and "board" in getattr(item, "fixturenames", ()):
        ctx = item.config.stash.get(_KEY, None)
        if ctx is not None and ctx.board is not None:
            item.user_properties.extend(board_properties(ctx))
    rep = yield
    if (
        call.excinfo is not None
        and rep.failed
        and "serial" in getattr(item, "fixturenames", ())
        and call.excinfo.errisinstance((ExpectTimeout, XeWeError, SerialException, OSError))
    ):
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
