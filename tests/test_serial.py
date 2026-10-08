import io
import re
from pathlib import Path

import pytest

from conftest import FakeSerial
from xewe import serialio
from xewe.boards import Board
from xewe.cli import main
from xewe.project import Paths
from xewe.serialio import Console, ExpectTimeout

STAMP = r"\d\d:\d\d:\d\d\.\d{3}  "


def responder(reply: bytes):
    def script(data: bytes) -> bytes:
        return reply if data == b"$system status\n" else b""
    return script


def test_open_does_not_assert_dtr_rts() -> None:
    fake = FakeSerial()
    Console("/dev/x", factory=lambda: fake).open()
    assert fake.is_open and fake.dtr_at_open is False and fake.rts_at_open is False
    assert fake.dsrdtr is False and fake.rtscts is False and fake.baudrate == 115200


def test_listen_timestamps_and_cr_stripping(tmp_path: Path) -> None:
    fake = FakeSerial(incoming=b"boot\r\nready \xff\r\npartial")
    out = io.StringIO()
    console = Console("/dev/x", factory=lambda: fake, echo=True, out=out, log_path=tmp_path / "log.txt").open()
    console.listen(duration=0.2)
    console.close()
    lines = out.getvalue().splitlines()
    assert re.fullmatch(STAMP + "boot", lines[0])
    assert re.fullmatch(STAMP + "ready \ufffd", lines[1])
    assert len(lines) == 2  # "partial" has no newline yet
    assert console.lines == ["boot", "ready \ufffd"]
    assert (tmp_path / "log.txt").read_text().count("\n") == 2


def test_send_expect_match() -> None:
    fake = FakeSerial(script=responder(b"noise\r\nUptime: 00:03:12\r\n"))
    console = Console("/dev/x", factory=lambda: fake).open()
    m = console.command("$system status", r"Uptime: (\S+)", timeout=1)
    assert m[1] == "00:03:12"
    assert bytes(fake.written) == b"$system status\n"


def test_expect_timeout_carries_last_lines() -> None:
    fake = FakeSerial(incoming=b"".join(f"line {i}\n".encode() for i in range(30)))
    console = Console("/dev/x", factory=lambda: fake).open()
    with pytest.raises(ExpectTimeout) as exc:
        console.expect("never", timeout=0.2)
    assert "line 29" in str(exc.value) and "line 9\n" not in str(exc.value)
    assert isinstance(exc.value, AssertionError)


def test_reconnect(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeSerial(incoming=b"after\n")
    fake.fail_reads = 1
    out = io.StringIO()
    console = Console("/dev/x", factory=lambda: fake, echo=True, out=out).open()
    console.listen(duration=0.2)
    assert "-- port reconnected --" in out.getvalue()
    assert console.lines == ["after"]


def test_reset_pulses_rts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(serialio.time, "sleep", lambda s: None)
    fake = FakeSerial()
    console = Console("/dev/x", factory=lambda: fake).open()
    fake.rts_history.clear()
    console.reset()
    assert fake.rts_history == [True, False]


def _select(board: Board | None):
    return lambda port: board


def test_serial_main_expect(capsys: pytest.CaptureFixture[str]) -> None:
    fake = FakeSerial(script=responder(b"Uptime: 1\n"))
    code = serialio.serial_main(None, _select(Board("/dev/x")), send="$system status", expect="Uptime",
                                timeout=1, factory=lambda: fake)
    out = capsys.readouterr().out
    assert code == 0
    assert re.search(STAMP + r"> \$system status", out) and "match  'Uptime' after" in out


def test_serial_main_timeout_exit_1(caplog: pytest.LogCaptureFixture) -> None:
    fake = FakeSerial(script=responder(b"something else\n"))
    code = serialio.serial_main(None, _select(Board("/dev/x")), send="$system status", expect="Uptime",
                                timeout=0.2, factory=lambda: fake)
    assert code == 1
    assert "  something else" in caplog.text


def test_serial_main_send_without_expect() -> None:
    fake = FakeSerial(script=responder(b"a\nb\n"))
    assert serialio.serial_main(None, _select(Board("/dev/x")), send="$system status", factory=lambda: fake) == 0


def test_cli_serial_no_board(project: Paths, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["serial"]) == 0
    assert capsys.readouterr().out.strip() == "no board attached; nothing to listen to"
    assert main(["serial", "--require-board"]) == 4
    assert main(["serial", "--port", "/dev/does-not-exist"]) == 4
