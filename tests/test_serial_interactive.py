"""Interactive console (``xewe serial``/``xewe run`` without ``--send`` on a terminal)."""

import io
import re
import sys
import threading

from pathlib import Path

import pytest

from conftest import FakeSerial
from xewe import serialio
from xewe.boards import Board
from xewe.cli import main
from xewe.project import Paths
from xewe.serialio import Console

HINT = "interactive: type a command and press Enter; Ctrl-C to exit"


class FakeStdin:
    """Terminal stand-in: ``readline`` returns ``lines`` in order, then EOF ("") or blocks until released."""

    def __init__(self, lines: list[str], tty: bool = True, eof: bool = True) -> None:
        self.lines = list(lines)
        self.tty = tty
        self.eof = eof
        self.release = threading.Event()
        self.reads = 0

    def isatty(self) -> bool:
        return self.tty

    def readline(self) -> str:
        self.reads += 1
        if self.lines:
            return self.lines.pop(0)
        if not self.eof:
            self.release.wait(5)
        return ""


@pytest.fixture
def stdin(monkeypatch: pytest.MonkeyPatch):
    made: list[FakeStdin] = []

    def make(lines: list[str], **kw: bool) -> FakeStdin:
        s = FakeStdin(lines, **kw)
        made.append(s)
        monkeypatch.setattr(sys, "stdin", s)
        return s
    yield make
    for s in made:
        s.release.set()


def _select(board: Board | None):
    return lambda port: board


def test_typed_line_reaches_port_with_newline(stdin, capsys: pytest.CaptureFixture[str]) -> None:
    stdin(["$system status\n", "\n", "help"])  # then EOF (Ctrl-D)
    fake = FakeSerial()
    assert serialio.serial_main(None, _select(Board("/dev/x")), factory=lambda: fake) == 0
    assert bytes(fake.written) == b"$system status\n\nhelp\n"  # empty line sent as-is
    out = capsys.readouterr().out
    assert HINT in out and "> $system status" not in out  # the firmware echoes it; no "> cmd" line


def test_eof_ends_session_exit_0(stdin) -> None:
    stdin([])
    fake = FakeSerial(incoming=b"boot\n")
    assert serialio.serial_main(None, _select(Board("/dev/x")), factory=lambda: fake) == 0  # no --duration
    assert bytes(fake.written) == b""


def test_ctrl_c_exit_0(stdin) -> None:
    stdin([], eof=False)

    class InterruptSerial(FakeSerial):
        def read(self, n: int = 1) -> bytes:
            raise KeyboardInterrupt
    fake = InterruptSerial()
    assert serialio.serial_main(None, _select(Board("/dev/x")), factory=lambda: fake) == 0


def test_no_input_sends_nothing(stdin, capsys: pytest.CaptureFixture[str]) -> None:
    s = stdin(["$system status\n"])
    fake = FakeSerial()
    assert serialio.serial_main(None, _select(Board("/dev/x")), duration=0.2, factory=lambda: fake,
                                no_input=True) == 0
    assert bytes(fake.written) == b"" and s.reads == 0
    assert HINT not in capsys.readouterr().out


def test_non_tty_stdin_is_listen_only(stdin, capsys: pytest.CaptureFixture[str]) -> None:
    s = stdin(["$system status\n"], tty=False)
    fake = FakeSerial(incoming=b"boot\n")
    assert serialio.serial_main(None, _select(Board("/dev/x")), duration=0.2, factory=lambda: fake) == 0
    assert bytes(fake.written) == b"" and s.reads == 0
    out = capsys.readouterr().out
    assert HINT not in out and "boot" in out


def test_duration_ends_interactive_session(stdin) -> None:
    stdin(["a\n"], eof=False)
    fake = FakeSerial()
    assert serialio.serial_main(None, _select(Board("/dev/x")), duration=0.3, factory=lambda: fake) == 0
    assert bytes(fake.written) == b"a\n"


def test_interactive_reconnects_on_port_drop() -> None:
    fake = FakeSerial(incoming=b"after\n")
    fake.fail_reads = 1
    out = io.StringIO()
    src = FakeStdin(["a\n"], eof=False)
    console = Console("/dev/x", factory=lambda: fake, echo=True, out=out).open()
    try:
        console.interact(duration=0.3, stdin=src)  # type: ignore[arg-type]
    finally:
        src.release.set()
    assert "-- port reconnected --" in out.getvalue()
    assert console.lines == ["after"] and bytes(fake.written) == b"a\n"


def test_send_retries_after_port_drop() -> None:
    class WriteDropSerial(FakeSerial):
        def write(self, data: bytes) -> int:
            if self.__dict__.get("drop"):
                self.__dict__["drop"] = False
                raise OSError(6, "Device not configured")
            return super().write(data)
    fake = WriteDropSerial()
    fake.__dict__["drop"] = True
    console = Console("/dev/x", factory=lambda: fake, echo=True, out=io.StringIO()).open()
    console.interact(stdin=FakeStdin(["a\n"]))  # type: ignore[arg-type]
    assert bytes(fake.written) == b"a\n"


def test_cli_no_input_flag(project: Paths) -> None:
    assert main(["serial", "--no-input"]) == 0  # no board: nothing to listen to
    with pytest.raises(SystemExit) as exc:
        main(["run", "--no-input", "--help"])
    assert exc.value.code == 0


# ---------------------------------------------------------------- RT3: raw console, stdin flush

STAMPED = re.compile(r"^\d\d:\d\d:\d\d\.\d{3}  ")
BANNER = b"| System Setup Complete |\n"


class FakeTermios:
    TCIFLUSH = 0

    class error(Exception):
        pass

    def __init__(self) -> None:
        self.calls: list[tuple[int, int]] = []

    def tcflush(self, fd: int, queue: int) -> None:
        self.calls.append((fd, queue))


@pytest.fixture
def fake_termios(monkeypatch: pytest.MonkeyPatch) -> FakeTermios:
    t = FakeTermios()
    monkeypatch.setattr(serialio, "termios", t)
    return t


def _board_lines(out: str) -> list[str]:
    return [line for line in out.splitlines() if "System Setup Complete" in line]


def test_interactive_prints_raw_lines(stdin, fake_termios: FakeTermios, capsys: pytest.CaptureFixture[str]) -> None:
    stdin(["$system status\n"])
    fake = FakeSerial(incoming=BANNER)
    assert serialio.serial_main(None, _select(Board("/dev/x")), factory=lambda: fake) == 0
    out = capsys.readouterr().out
    assert _board_lines(out) == ["| System Setup Complete |"]
    assert not any(line.startswith("> ") for line in out.splitlines())


def test_interactive_timestamps_flag(stdin, fake_termios: FakeTermios, capsys: pytest.CaptureFixture[str]) -> None:
    stdin([], eof=False)
    fake = FakeSerial(incoming=BANNER)
    assert serialio.serial_main(None, _select(Board("/dev/x")), duration=0.3, factory=lambda: fake,
                                timestamps=True) == 0
    [line] = _board_lines(capsys.readouterr().out)
    assert STAMPED.match(line) and line.endswith("  | System Setup Complete |")


def test_interactive_log_stays_timestamped(stdin, fake_termios: FakeTermios, tmp_path: Path,
                                           capsys: pytest.CaptureFixture[str]) -> None:
    stdin(["$system status\n"])
    fake = FakeSerial(incoming=BANNER)
    logf = tmp_path / "serial.log"
    assert serialio.serial_main(None, _select(Board("/dev/x")), factory=lambda: fake, log_path=logf) == 0
    logged = logf.read_text().splitlines()
    assert logged and all(STAMPED.match(line) for line in logged)
    assert any(line.endswith("  | System Setup Complete |") for line in logged)
    assert any(line.endswith("  > $system status") for line in logged)  # the log keeps what was sent
    assert "> $system status" not in capsys.readouterr().out


def test_send_mode_keeps_echo_and_timestamps(stdin, fake_termios: FakeTermios,
                                             capsys: pytest.CaptureFixture[str]) -> None:
    stdin([])
    fake = FakeSerial(script=lambda data: b"ok\n" if data == b"$system status\n" else b"")
    assert serialio.serial_main(None, _select(Board("/dev/x")), send="$system status", expect="ok",
                                factory=lambda: fake) == 0
    out = capsys.readouterr().out.splitlines()
    echo = [line for line in out if line.endswith("  > $system status")]
    assert len(echo) == 1 and STAMPED.match(echo[0])
    assert any(STAMPED.match(line) and line.endswith("  ok") for line in out)
    assert fake_termios.calls == []  # --send never flushes the terminal


def test_listen_only_stays_timestamped(stdin, fake_termios: FakeTermios, capsys: pytest.CaptureFixture[str]) -> None:
    stdin([], tty=False)
    fake = FakeSerial(incoming=BANNER)
    assert serialio.serial_main(None, _select(Board("/dev/x")), duration=0.2, factory=lambda: fake) == 0
    [line] = _board_lines(capsys.readouterr().out)
    assert STAMPED.match(line)
    assert fake_termios.calls == []


def test_flush_on_interactive_start(stdin, fake_termios: FakeTermios, capsys: pytest.CaptureFixture[str]) -> None:
    s = stdin([])
    s.fileno = lambda: 7  # type: ignore[method-assign]
    order: list[str] = []
    real_tcflush = fake_termios.tcflush
    fake_termios.tcflush = lambda fd, q: (order.append("flush"), real_tcflush(fd, q))  # type: ignore[method-assign]
    real_result = serialio.result
    serialio.result = lambda msg: (order.append(msg), real_result(msg))  # type: ignore[assignment]
    try:
        assert serialio.serial_main(None, _select(Board("/dev/x")), factory=lambda: FakeSerial()) == 0
    finally:
        serialio.result = real_result
    assert fake_termios.calls == [(7, FakeTermios.TCIFLUSH)]
    assert order[:2] == ["flush", HINT]  # hint printed after the flush


def test_no_flush_with_no_input_or_non_tty(stdin, fake_termios: FakeTermios) -> None:
    s = stdin([])
    s.fileno = lambda: 7  # type: ignore[method-assign]
    assert serialio.serial_main(None, _select(Board("/dev/x")), duration=0.1, factory=lambda: FakeSerial(),
                                no_input=True) == 0
    assert serialio.flush_input(FakeStdin([], tty=False)) is False  # type: ignore[arg-type]
    assert fake_termios.calls == []


def test_flush_input_without_termios(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(serialio, "termios", None)  # non-POSIX
    assert serialio.flush_input(FakeStdin([])) is False  # type: ignore[arg-type]


def test_cli_timestamps_flag(project: Paths) -> None:
    assert main(["serial", "--timestamps", "--no-input"]) == 0  # no board: nothing to listen to
    with pytest.raises(SystemExit) as exc:
        main(["run", "--timestamps", "--help"])
    assert exc.value.code == 0
