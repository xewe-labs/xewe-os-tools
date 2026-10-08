"""Serial console on top of pyserial: listen with timestamps, send, expect (SPEC §8)."""

from __future__ import annotations

import datetime as dt
import re
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, TextIO

import serial

from xewe.report import EXIT_FAIL, EXIT_NO_BOARD, EXIT_OK, NO_BOARD, XeweError, log, result

RECONNECT_SECONDS = 5.0
SILENCE_SECONDS = 0.5
TAIL_LINES = 20


class ExpectTimeout(AssertionError):
    """``expect`` saw no matching line in time; the message carries the last lines."""


def stamp() -> str:
    """Local time as ``HH:MM:SS.mmm``."""
    now = dt.datetime.now()
    return now.strftime("%H:%M:%S.") + f"{now.microsecond // 1000:03d}"


class Console:
    """Line-oriented serial console.

    ``factory`` builds an unopened ``serial.Serial``-like object (tests pass a fake). With
    ``echo`` every line is printed to ``out`` prefixed by a timestamp and appended to ``log_path``.
    """

    def __init__(
        self,
        port: str,
        baud: int = 115200,
        factory: Callable[[], Any] = serial.Serial,
        echo: bool = False,
        out: TextIO | None = None,
        log_path: Path | None = None,
    ) -> None:
        self.port = port
        self.baud = baud
        self.factory = factory
        self.echo = echo
        self.out = out or sys.stdout
        self.log_file = log_path.open("a", encoding="utf-8") if log_path else None
        self.lines: list[str] = []
        self._cursor = 0
        self._buffer = b""
        self._ser: Any = None

    # ------------------------------------------------------------------ connection

    def open(self) -> Console:
        """Open the port without asserting DTR/RTS (keeps bridge boards out of reset/bootloader)."""
        ser = self.factory()
        ser.port = self.port
        ser.baudrate = self.baud
        ser.timeout = 0.05
        ser.dsrdtr = False
        ser.rtscts = False
        ser.dtr = False
        ser.rts = False
        ser.open()
        self._ser = ser
        return self

    def close(self) -> None:
        """Close the port and the log file."""
        if self._ser is not None:
            try:
                self._ser.close()
            except (serial.SerialException, OSError):
                pass
            self._ser = None
        if self.log_file is not None:
            self.log_file.close()
            self.log_file = None

    def __enter__(self) -> Console:
        return self.open() if self._ser is None else self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def reset(self) -> None:
        """Pulse RTS (EN) low for 100 ms."""
        self._ser.rts = True
        time.sleep(0.1)
        self._ser.rts = False

    def _reconnect(self) -> None:
        if self._ser is not None:
            try:
                self._ser.close()
            except (serial.SerialException, OSError):
                pass
        deadline = time.monotonic() + RECONNECT_SECONDS
        while time.monotonic() < deadline:
            try:
                self.open()
            except (serial.SerialException, OSError):
                time.sleep(0.1)
                continue
            self._emit_raw("-- port reconnected --")
            return
        raise XeweError(f"{self.port} disappeared and did not come back within {RECONNECT_SECONDS:.0f} s", EXIT_FAIL)

    # ------------------------------------------------------------------ reading

    def _emit_raw(self, text: str) -> None:
        if self.echo:
            print(text, file=self.out, flush=True)

    def _emit(self, line: str) -> None:
        if self.echo or self.log_file:
            stamped = f"{stamp()}  {line}"
            if self.echo:
                print(stamped, file=self.out, flush=True)
            if self.log_file:
                self.log_file.write(stamped + "\n")
                self.log_file.flush()

    def poll(self) -> list[str]:
        """Read what is available (waits at most the port timeout) and return completed lines."""
        try:
            data = self._ser.read(max(1, getattr(self._ser, "in_waiting", 0) or 0))
        except (serial.SerialException, OSError):
            self._reconnect()
            return []
        if not data:
            return []
        self._buffer += data
        new: list[str] = []
        while b"\n" in self._buffer:
            raw, self._buffer = self._buffer.split(b"\n", 1)
            line = raw.decode("utf-8", errors="replace").replace("\r", "")
            self.lines.append(line)
            self._emit(line)
            new.append(line)
        return new

    def send(self, cmd: str) -> None:
        """Write ``cmd`` + newline; later ``expect`` calls only look at lines after this point."""
        self._ser.write((cmd + "\n").encode("utf-8"))
        self._ser.flush()
        self._cursor = len(self.lines)
        self._emit(f"> {cmd}")

    def expect(self, pattern: str, timeout: float = 10) -> re.Match[str]:
        """Wait for a line matching ``pattern`` (searched) after the last send/expect."""
        rx = re.compile(pattern)
        deadline = time.monotonic() + timeout
        while True:
            while self._cursor < len(self.lines):
                line = self.lines[self._cursor]
                self._cursor += 1
                m = rx.search(line)
                if m:
                    return m
            if time.monotonic() >= deadline:
                tail = "\n".join(self.lines[-TAIL_LINES:])
                raise ExpectTimeout(f"no line matching {pattern!r} within {timeout} s; last lines:\n{tail}")
            self.poll()

    def command(self, cmd: str, expect: str, timeout: float = 10) -> re.Match[str]:
        """``send(cmd)`` then ``expect(expect, timeout)``."""
        self.send(cmd)
        return self.expect(expect, timeout)

    def collect(self, silence: float = SILENCE_SECONDS, limit: float = 10) -> list[str]:
        """Read until ``silence`` seconds pass without data (or ``limit``); return the new lines."""
        start = len(self.lines)
        end = time.monotonic() + limit
        quiet_since = time.monotonic()
        while time.monotonic() < end:
            if self.poll():
                quiet_since = time.monotonic()
            elif time.monotonic() - quiet_since >= silence:
                break
        self._cursor = len(self.lines)
        return self.lines[start:]

    def drain(self) -> list[str]:
        """Discard pending output (anything until 500 ms of silence)."""
        return self.collect()

    def listen(self, duration: float | None = None) -> None:
        """Print lines until ``duration`` seconds pass (forever when None)."""
        end = None if duration is None else time.monotonic() + duration
        while end is None or time.monotonic() < end:
            self.poll()


def wait_for_port(port: str, exists: Callable[[str], bool], timeout: float = RECONNECT_SECONDS) -> bool:
    """Wait until ``exists(port)`` (a port re-enumerating after reset); True if it came back."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if exists(port):
            return True
        time.sleep(0.1)
    log.warning("%s did not reappear within %.0f s", port, timeout)
    return False


def serial_main(
    port_flag: str | None,
    select_board: Callable[[str | None], Any],
    baud: int = 115200,
    reset: bool = False,
    send: str | None = None,
    expect: str | None = None,
    timeout: float = 10,
    duration: float | None = None,
    log_path: Path | None = None,
    require_board: bool = False,
    factory: Callable[[], Any] = serial.Serial,
) -> int:
    """``xewe serial``: listen, or send one command and wait for a regex."""
    board = select_board(port_flag)
    if board is None:
        result(f"{NO_BOARD}; nothing to listen to")
        if require_board:
            log.error("--require-board: %s", NO_BOARD)
            return EXIT_NO_BOARD
        return EXIT_OK
    with Console(board.port, baud, factory=factory, echo=True, log_path=log_path) as console:
        if reset:
            console.reset()
        if send is None:
            try:
                console.listen(duration)
            except KeyboardInterrupt:
                pass
            return EXIT_OK
        start = time.monotonic()
        console.send(send)
        if expect is None:
            console.collect()
            return EXIT_OK
        try:
            console.expect(expect, timeout)
        except ExpectTimeout:
            log.error("no match for %r within %s s; last lines:", expect, timeout)
            for line in console.lines[-TAIL_LINES:]:
                log.error("  %s", line)
            return EXIT_FAIL
        result(f"match  {expect!r} after {time.monotonic() - start:.2f} s")
        return EXIT_OK
