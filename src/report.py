"""Output helpers, exit codes and the error type shared by every command.

Results go to stdout (``result``); progress, warnings and errors go to stderr through the
``xewe`` logger. ``--verbose`` lowers the logger level so every subprocess command line and its
output are shown.
"""

from __future__ import annotations

import logging
import os
import sys

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_USAGE = 2
EXIT_NOT_SETUP = 3
EXIT_NO_BOARD = 4

NO_BOARD = "no board attached"
BOARD_DISABLED = "board access disabled (--no-board / XEWE_NO_BOARD)"
NO_BOARD_ENV = "XEWE_NO_BOARD"

log = logging.getLogger("xewe")


class XeweError(Exception):
    """An error that ends the command with ``code`` (see the exit-code table in SPEC §3)."""

    def __init__(self, message: str, code: int = EXIT_FAIL) -> None:
        super().__init__(message)
        self.code = code


class _Formatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        msg = record.getMessage()
        if record.levelno >= logging.ERROR:
            return f"error: {msg}"
        if record.levelno >= logging.WARNING:
            return f"warning: {msg}"
        return msg


def setup_logging(verbose: bool = False) -> None:
    """Send the ``xewe`` logger to stderr; DEBUG with ``--verbose``, INFO otherwise."""
    for handler in list(log.handlers):
        log.removeHandler(handler)
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(_Formatter())
    log.addHandler(handler)
    log.setLevel(logging.DEBUG if verbose else logging.INFO)


def verbose() -> bool:
    """True when ``--verbose`` is active."""
    return log.isEnabledFor(logging.DEBUG)


def result(line: str = "") -> None:
    """Print one result line to stdout (flushed, so it interleaves correctly with stderr)."""
    print(line, flush=True)


def not_run_line(chip: str, binary: str) -> str:
    """The grep-able no-board status line of flash/run/test."""
    return f"compiled, not run: {NO_BOARD} ({chip}, {binary})"


def no_board(chip: str, binary: str, require_board: bool) -> int:
    """Print the no-board status line and return the exit code (0, or 4 with --require-board)."""
    result(not_run_line(chip, binary))
    if require_board:
        log.error("--require-board: %s", NO_BOARD)
        return EXIT_NO_BOARD
    return EXIT_OK


def board_disabled() -> bool:
    """True when board access is switched off (``--no-board`` or ``XEWE_NO_BOARD=1``): no port is
    discovered or opened, and commands that need a board exit 4."""
    return os.environ.get(NO_BOARD_ENV, "").strip().lower() in ("1", "true", "yes")


def disable_board() -> None:
    """``--no-board``: switch board access off for this process (and the in-process pytest run)."""
    os.environ[NO_BOARD_ENV] = "1"


def board_disabled_exit(chip: str | None = None, binary: str | None = None) -> int:
    """Report that board access is disabled; exit 4. With ``chip``/``binary`` (flash, run) the
    image was built first and the status line names it."""
    if chip is not None and binary is not None:
        result(f"compiled, not run: {BOARD_DISABLED} ({chip}, {binary})")
    log.error("%s", BOARD_DISABLED)
    return EXIT_NO_BOARD
