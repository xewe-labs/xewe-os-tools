"""Serial console on top of pyserial: listen with timestamps, send, expect (SPEC §8)."""

from __future__ import annotations

import datetime as dt
import os
import queue
import re
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, TextIO

import serial

try:
    import termios
except ImportError:  # Windows
    termios = None  # type: ignore[assignment]

from xewe.report import BOARD_DISABLED, EXIT_FAIL, EXIT_NO_BOARD, EXIT_OK, EXIT_USAGE, NO_BOARD, XeWeError, board_disabled, log, result

RECONNECT_SECONDS = 5.0
SILENCE_SECONDS = 0.5
TAIL_LINES = 20
MASK = "********"
BOOT_READY = r"System Setup Complete"
"""End-of-boot banner (``Os::begin``)."""
BOOT_UNPROVISIONED = r"Name your device"
"""First-boot prompt (``System::begin_routines_init``): the board has not been provisioned."""
BOOT_ROM = r"^ESP-ROM:|^rst:0x"
"""First lines the ROM prints after any chip reset."""
BOOT_GRACE_SECONDS = 2.0
"""``--send``: no boot output this long after opening means no boot is in progress (the open does not reset)."""
BOOT_TIMEOUT_SECONDS = 90.0
"""``--send``: default ``--boot-timeout`` (Wi-Fi + NTP can take ~30 s before the banner)."""
INTERACTIVE_HINT = "interactive: type a command and press Enter; Ctrl-C to exit"
SEND_CHUNK_BYTES = 900
"""Largest single write. The S3/C3/C6 USB console (HWCDC, USB-Serial/JTAG) has a 1024-byte RX queue
(``SerialPortConfig::rx_buffer_size``) and no flow control: bytes that arrive while it is full are
dropped before the firmware sees them (a 1037-byte write lost its last command on an S3). Longer
writes are split into pieces of at most this size with ``SEND_CHUNK_PAUSE`` between them."""
SEND_CHUNK_PAUSE = 0.05
"""Seconds between two pieces of a long write: time for the firmware's ``loop()`` to drain the queue."""
PROBE_COMMAND = "$system uid"
"""Liveness probe: every XeWe OS image answers it with a ``uid64 ...`` line."""
PROBE_REPLY = r"^uid64 "
PORT_SETTLE_SECONDS = 0.5
"""Default for ``wait_for_port``: a re-enumerated port must stay present this long (``--settle``,
``XEWE_SETTLE``)."""


class ExpectTimeout(AssertionError):
    """``expect`` saw no matching line in time; the message carries the last lines."""


def stamp() -> str:
    """Local time as ``HH:MM:SS.mmm``."""
    now = dt.datetime.now()
    return now.strftime("%H:%M:%S.") + f"{now.microsecond // 1000:03d}"


class Console:
    """Line-oriented serial console.

    ``factory`` builds an unopened ``serial.Serial``-like object (default ``serial.Serial``,
    looked up at open time so tests can substitute a fake). With ``echo`` every line is printed to ``out``,
    prefixed by a timestamp unless ``timestamps`` is False (the interactive console prints raw lines);
    every line is appended to ``log_path`` with its timestamp either way.
    Any line (read or sent) containing one of ``secrets`` is replaced by ``MASK`` everywhere:
    in ``lines``, on ``out``, in the log and in ``ExpectTimeout`` messages.
    """

    def __init__(
        self,
        port: str,
        baud: int = 115200,
        factory: Callable[[], Any] | None = None,
        echo: bool = False,
        out: TextIO | None = None,
        log_path: Path | None = None,
        secrets: list[str] | None = None,
        timestamps: bool = True,
    ) -> None:
        self.port = port
        self.timestamps = timestamps
        self.baud = baud
        self.factory = factory
        self.echo = echo
        self.out = out or sys.stdout
        self.log_file = log_path.open("a", encoding="utf-8") if log_path else None
        self.lines: list[str] = []
        self._cursor = 0
        self._buffer = b""
        self._ser: Any = None
        self.secrets = [s for s in (secrets or []) if s]

    def mask(self, line: str) -> str:
        """``line``, or ``MASK`` when it contains a secret."""
        return MASK if any(s in line for s in self.secrets) else line

    # ------------------------------------------------------------------ connection

    def open(self) -> Console:
        """Open the port without resetting the board; it ends with DTR and RTS both low.

        The control lines must never pass through DTR=0 & RTS=1: on native USB (USB-Serial/JTAG,
        ``303a:1001``) that state resets the chip (``rst:0x15 (USB_UART_CHIP_RESET)``), and on a
        UART bridge's auto-reset circuit it pulls EN low. The kernel's cdc_acm raises DTR and RTS
        on open, then pyserial's ``open()`` applies the configured DTR first and RTS second, so
        configuring ``dtr=False, rts=False`` would go 1/1 -> 0/1 (reset) -> 0/0. Instead configure
        DTR high and RTS low (open goes 1/1 -> 1/1 -> 1/0, which the chip ignores), then drop DTR
        after open (1/0 -> 0/0). ``reset()`` produces 0/1 on purpose.

        With board access disabled (``XEWE_NO_BOARD``) nothing is opened: exit 4.
        """
        if board_disabled():
            raise XeWeError(BOARD_DISABLED, EXIT_NO_BOARD)
        ser = (self.factory or serial.Serial)()
        ser.port = self.port
        ser.baudrate = self.baud
        ser.timeout = 0.05
        ser.dsrdtr = False
        ser.rtscts = False
        ser.dtr = True   # applied first by open(): DTR stays high as cdc_acm left it
        ser.rts = False  # applied second: 1/1 -> 1/0, never 0/1
        ser.open()
        ser.dtr = False  # 1/0 -> 0/0: both lines low, board untouched
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
        """Pulse RTS (EN) low for 100 ms.

        With DTR low this is DTR=0 & RTS=1, the state that resets the chip (on native USB,
        USB-Serial/JTAG ``303a:1001``: ``rst:0x15 (USB_UART_CHIP_RESET)``). ``open()`` itself
        avoids that state, so the board only resets here, on ``--reset``, or when flashing.

        On native USB (S3/C3/C6 USB-Serial/JTAG) the reset makes the port vanish and come back;
        if the port errors during the pulse it is reopened (``_reconnect``).
        """
        try:
            self._ser.rts = True
            time.sleep(0.1)
            self._ser.rts = False
        except (serial.SerialException, OSError):
            self._reconnect()

    def clear(self) -> None:
        """Forget the captured lines; the next ``expect`` looks only at lines read after this."""
        self.lines.clear()
        self._cursor = 0

    def mark(self) -> None:
        """Keep the captured lines; the next ``expect`` looks only at lines read after this."""
        self._cursor = len(self.lines)

    def reopen(self, pause: float = 0.2) -> None:
        """Close the port, wait ``pause`` seconds, open it again (retrying for ``RECONNECT_SECONDS``).

        A host can keep a stale endpoint after a native-USB board restarts (the open port then
        reads nothing although the board runs); a fresh open binds to the re-enumerated device.
        The open does not reset the board (see ``open``). Raises ``XeWeError`` when the port does
        not come back.
        """
        if self._ser is not None:
            try:
                self._ser.close()
            except (serial.SerialException, OSError):
                pass
            self._ser = None
        time.sleep(pause)
        self._reconnect()

    def probe(self, timeout: float = 15.0, tries: int = 3) -> bool:
        """True when the board answers ``PROBE_COMMAND`` within ``timeout`` seconds.

        The probe is sent up to ``tries`` times, evenly spread over ``timeout`` (a board that is
        still finishing a boot discards input typed before its prompt). Lines read stay in ``lines``.
        """
        for _ in range(max(1, tries)):
            try:
                self.command(PROBE_COMMAND, PROBE_REPLY, timeout / max(1, tries))
                return True
            except ExpectTimeout:
                continue
            except (serial.SerialException, OSError, XeWeError):
                return False
        return False

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
        raise XeWeError(f"{self.port} disappeared and did not come back within {RECONNECT_SECONDS:.0f} s", EXIT_FAIL)

    # ------------------------------------------------------------------ reading

    def _emit_raw(self, text: str) -> None:
        if self.echo:
            print(text, file=self.out, flush=True)

    def _emit(self, line: str, show: bool = True) -> None:
        if self.echo or self.log_file:
            stamped = f"{stamp()}  {line}"
            if self.echo and show:
                print(stamped if self.timestamps else line, file=self.out, flush=True)
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
            line = self.mask(raw.decode("utf-8", errors="replace").replace("\r", ""))
            self.lines.append(line)
            self._emit(line)
            new.append(line)
        return new

    def send(self, cmd: str, show: bool = True) -> None:
        """Write ``cmd`` + newline; later ``expect`` calls only look at lines after this point.

        The ``> cmd`` line goes to the log always and to ``out`` only with ``show`` (the interactive
        console passes False: the terminal shows the typed text and the firmware echoes it).
        Writes longer than ``SEND_CHUNK_BYTES`` are split (see ``write``).
        """
        self.write((cmd + "\n").encode("utf-8"))
        self._cursor = len(self.lines)
        self._emit(f"> {self.mask(cmd)}", show)

    def write(self, data: bytes, chunk: int = SEND_CHUNK_BYTES, pause: float = SEND_CHUNK_PAUSE) -> None:
        """Write raw ``data`` (nothing logged), in pieces of at most ``chunk`` bytes with ``pause``
        seconds between them, so one burst never overruns the board's 1024-byte USB RX queue
        (``SEND_CHUNK_BYTES``). ``chunk=0`` writes everything at once (for overflow tests)."""
        step = chunk if chunk > 0 else max(1, len(data))
        for i in range(0, len(data), step):
            if i:
                time.sleep(pause)
            self._ser.write(data[i:i + step])
            self._ser.flush()

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

    def interact(self, duration: float | None = None, stdin: TextIO | None = None) -> None:
        """``listen``, and send every line typed on ``stdin`` (default ``sys.stdin``); returns on EOF.

        Plain line input (no raw mode): a daemon thread blocks in ``readline`` and queues the lines;
        this loop polls the port (reconnecting like ``listen``) and sends what was queued. The
        terminal already shows the typed text and the firmware echoes the command, so no ``> cmd``
        line is printed (it still goes to the log).
        """
        typed: queue.Queue[str | None] = queue.Queue()
        src = stdin or sys.stdin

        def reader() -> None:
            for line in iter(src.readline, ""):
                typed.put(line.rstrip("\r\n"))
            typed.put(None)  # EOF (Ctrl-D)

        threading.Thread(target=reader, name="xewe-stdin", daemon=True).start()
        end = None if duration is None else time.monotonic() + duration
        while end is None or time.monotonic() < end:
            self.poll()
            while not typed.empty():
                line = typed.get_nowait()
                if line is None:
                    return
                try:
                    self.send(line, show=False)
                except (serial.SerialException, OSError):  # port dropped since the last poll
                    self._reconnect()
                    self.send(line, show=False)


def interactive_input(no_input: bool = False, stdin: TextIO | None = None) -> TextIO | None:
    """The stream to read commands from: ``stdin`` when it is a terminal and not ``no_input``, else None."""
    src = stdin or sys.stdin
    if no_input or src is None or not src.isatty():
        return None
    return src


def flush_input(src: TextIO) -> bool:
    """Discard keystrokes typed before now (during flash/boot) on terminal ``src``; True when flushed.

    Without this they would be glued to the first command (``n$system``). POSIX only.
    """
    if termios is None or not src.isatty():
        return False
    try:
        termios.tcflush(src.fileno(), termios.TCIFLUSH)
    except (AttributeError, OSError, ValueError, termios.error):
        return False
    return True


def console_session(
    console: Console, duration: float | None = None, no_input: bool = False, timestamps: bool = False,
) -> None:
    """``xewe serial``/``xewe run`` without ``--send``: interactive on a terminal, else listen only.

    Interactive mode flushes pending terminal input, then prints the board's lines raw (no
    timestamp unless ``timestamps``) and no ``> cmd`` echo; the ``--log`` file keeps timestamps.
    Listen-only output is timestamped as before. Ctrl-C and EOF on stdin both end the session normally.
    """
    src = interactive_input(no_input)
    try:
        if src is None:
            console.listen(duration)
        else:
            flush_input(src)
            console.timestamps = timestamps
            result(INTERACTIVE_HINT)
            console.interact(duration, src)
    except KeyboardInterrupt:
        pass


def wait_for_banner(console: Console, pattern: str, timeout: float, reset: bool = True) -> re.Match[str]:
    """Optionally reset the board, then wait for a line matching ``pattern`` across port drops.

    A native-USB board's port vanishes on every reset (``reset()``, ``ESP.restart()`` after
    first boot); ``Console`` reopens it for ``RECONNECT_SECONDS`` and raises ``XeWeError`` when it
    stays gone longer. Here that is retried until the deadline. Raises ``ExpectTimeout`` when no
    line matched in ``timeout`` seconds, or the last ``XeWeError`` when the port was still gone.

    Lines captured before the reset (an earlier boot, e.g. the one the open itself triggered on
    native USB) are skipped, so a banner or prompt from that boot cannot match. ROM lines and a
    pattern printed twice (two boots) are harmless: the first match after the reset wins.
    """
    deadline = time.monotonic() + timeout
    if reset:
        console.mark()
        try:
            console.reset()
        except XeWeError:
            pass  # the port is gone for now; polling below keeps reconnecting until the deadline
    while True:
        try:
            return console.expect(pattern, timeout=max(0.0, deadline - time.monotonic()))
        except XeWeError:  # port gone longer than one reconnect window
            if time.monotonic() >= deadline:
                raise


def settle_seconds(value: float | None = None) -> float:
    """``value`` (``--settle``), else ``XEWE_SETTLE``, else ``PORT_SETTLE_SECONDS``."""
    if value is not None:
        return value
    env = os.environ.get("XEWE_SETTLE")
    if env:
        try:
            return float(env)
        except ValueError:
            raise XeWeError(f"XEWE_SETTLE must be a number of seconds, got {env!r}", EXIT_USAGE) from None
    return PORT_SETTLE_SECONDS


def wait_for_port(
    port: str,
    exists: Callable[[str], bool],
    timeout: float = 10.0,
    settle: float | None = None,
    renamed: Callable[[], str | None] | None = None,
) -> str:
    """Wait until ``exists(port)`` has held for ``settle`` seconds in a row; return the port name.

    A native-USB board re-enumerates after esptool resets it: the port can still be there for a
    moment, vanish, then come back. Only a port that stayed present for ``settle`` counts
    (``settle_seconds``: ``--settle``, ``XEWE_SETTLE``, default 0.5 s).

    ``renamed`` returns the name the same board has now when it differs (macOS can give a
    re-enumerated board another ``/dev/cu.usbmodem<N>``; ``boards.renamed_port`` matches it by USB
    serial number). When ``port`` stays away but ``renamed()`` names a port that is present,
    that name is returned (after the same settle). Raises ``XeWeError`` (exit 4) when neither
    happens within ``timeout`` seconds; the message names the new port when one was seen.
    """
    settle = settle_seconds(settle)
    deadline = time.monotonic() + timeout
    present_since: float | None = None
    current = port
    while True:
        now = time.monotonic()
        if exists(current):
            if present_since is None:
                present_since = now
            if now - present_since >= settle:
                return current
        else:
            present_since = None
            other = renamed() if renamed is not None else None
            if other and other != current and exists(other):
                current, present_since = other, now
        if now >= deadline:
            hint = "unplug and replug the board"
            other = renamed() if renamed is not None else None
            if other and other != port:
                hint = (f"the board came back as {other} (the host gave it a new name); "
                        f"pass --port {other}, or replug it")
            raise XeWeError(
                f"{port} did not come back (present for {settle:g} s) within {timeout:.0f} s after esptool reset it; "
                f"{hint}",
                EXIT_NO_BOARD,
            )
        time.sleep(0.05)


def wait_until_ready(console: Console, boot_timeout: float, grace: float = BOOT_GRACE_SECONDS) -> str:
    """Before ``--send``: wait out the boot the open (or ``--reset``) started.

    Returns ``"ready"`` (``System Setup Complete`` seen), ``"unprovisioned"`` (``Name your
    device`` seen) or ``"silent"`` (no ROM/boot line within ``grace`` seconds: the board was not
    reset, as with adapters that do not reset on open). Prints one ``boot: ...`` status line.
    Raises ``ExpectTimeout`` when the board started booting but printed neither line within
    ``boot_timeout`` seconds of the call.
    """
    start = time.monotonic()
    banner = f"{BOOT_READY}|{BOOT_UNPROVISIONED}"
    try:
        m = wait_for_banner(console, f"{BOOT_ROM}|{banner}", min(grace, boot_timeout), reset=False)
    except ExpectTimeout:
        result(f"boot: no boot output within {grace:g} s of opening; board was not reset, sending now")
        return "silent"
    if not re.fullmatch(banner, m[0]):
        m = wait_for_banner(console, banner, max(0.0, boot_timeout - (time.monotonic() - start)), reset=False)
    elapsed = time.monotonic() - start
    if m[0] == BOOT_UNPROVISIONED:
        result(f"boot: {BOOT_UNPROVISIONED!r} after {elapsed:.1f} s")
        return "unprovisioned"
    result(f"boot: {BOOT_READY} after {elapsed:.1f} s")
    console.collect(silence=SILENCE_SECONDS, limit=2)  # let the tail of the boot log pass
    return "ready"


def serial_main(
    port_flag: str | None,
    select_board: Callable[[str | None], Any],
    baud: int = 115200,
    reset: bool = False,
    send: str | None = None,
    expect: str | None = None,
    timeout: float = 10,
    boot_timeout: float = BOOT_TIMEOUT_SECONDS,
    duration: float | None = None,
    log_path: Path | None = None,
    require_board: bool = False,
    factory: Callable[[], Any] = serial.Serial,
    no_input: bool = False,
    timestamps: bool = False,
) -> int:
    """``xewe serial``: listen (interactive on a terminal, see ``console_session``), or send one
    command and wait for a regex.

    The port is opened without resetting the board (see ``Console.open``); ``--reset`` or
    flashing resets it. ``send`` first waits out any boot in progress (``wait_until_ready``).
    """
    board = select_board(port_flag)
    if board is None:
        result(f"{NO_BOARD}; nothing to listen to")
        if require_board:
            log.error("--require-board: %s", NO_BOARD)
            return EXIT_NO_BOARD
        return EXIT_OK
    console = Console(board.port, baud, factory=factory, echo=True, log_path=log_path)
    try:
        console.open()
    except (serial.SerialException, OSError) as exc:
        console.close()
        log.error("cannot open %s: %s; if this persists, unplug and replug the board (or toggle USB "
                  "passthrough)", board.port, exc)
        return EXIT_FAIL
    with console:
        if reset:
            console.reset()
        if send is None:
            console_session(console, duration, no_input, timestamps)
            return EXIT_OK
        try:
            state = wait_until_ready(console, boot_timeout)
        except ExpectTimeout:
            log.error("board started booting but printed neither %r nor %r within %g s; nothing sent; last lines:",
                      BOOT_READY, BOOT_UNPROVISIONED, boot_timeout)
            for line in console.lines[-TAIL_LINES:]:
                log.error("  %s", line)
            return EXIT_FAIL
        if state == "unprovisioned":
            log.error("board is unprovisioned; run xewe provision (nothing sent)")
            return EXIT_FAIL
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
