"""Interactive console (``xewe serial``/``xewe run`` without ``--send`` on a terminal)."""

import io
import sys
import threading

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
    assert HINT in out and out.count("> $system status") == 1  # echoed once, by send


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
