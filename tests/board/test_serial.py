import io
import re
from pathlib import Path

import pytest

from conftest import FakeClock, FakeSerial, select_board
from xewe.board import serialio
from xewe.board.boards import Board
from xewe.board.serialio import Console, ExpectTimeout, wait_for_port
from xewe.cli import main
from xewe.env.project import Paths
from xewe.report import EXIT_NO_BOARD, XeWeError

STAMP = r"\d\d:\d\d:\d\d\.\d{3}  "


@pytest.fixture
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """``serialio`` sleeps return at once (reset pulses, reconnect back-off)."""
    monkeypatch.setattr(serialio.time, "sleep", lambda s: None)


def responder(reply: bytes):
    def script(data: bytes) -> bytes:
        return reply if data == b"$system status\n" else b""
    return script


def test_open_does_not_assert_dtr_rts() -> None:
    fake = FakeSerial()
    Console("/dev/x", factory=lambda: fake).open()
    assert fake.is_open and fake.dtr is False and fake.rts is False  # both low once open
    assert fake.dsrdtr is False and fake.rtscts is False and fake.baudrate == 115200


def test_open_dtr_high_rts_low_at_open() -> None:
    fake = FakeSerial()
    Console("/dev/x", factory=lambda: fake).open()
    assert fake.dtr_at_open is True and fake.rts_at_open is False


def test_open_never_passes_through_reset_pattern() -> None:
    fake = FakeSerial()
    Console("/dev/x", factory=lambda: fake).open()
    assert fake.line_history  # the open and the DTR drop were recorded
    assert (False, True) not in fake.line_history  # DTR=0 & RTS=1 resets the chip
    assert fake.line_history[-1] == (False, False)


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


def test_reconnect() -> None:
    fake = FakeSerial(incoming=b"after\n")
    fake.fail_reads = 1
    out = io.StringIO()
    console = Console("/dev/x", factory=lambda: fake, echo=True, out=out).open()
    console.listen(duration=0.2)
    assert "-- port reconnected --" in out.getvalue()
    assert console.lines == ["after"]


def test_reset_pulses_rts(no_sleep: None) -> None:
    fake = FakeSerial()
    console = Console("/dev/x", factory=lambda: fake).open()
    fake.rts_history.clear()
    console.reset()
    assert fake.rts_history == [True, False]


class RtsDropSerial(FakeSerial):
    """Raises on the first RTS assert, like a native-USB port vanishing as the reset lands."""

    def __setattr__(self, name: str, value: object) -> None:
        if name == "rts" and value is True and self.__dict__.get("armed"):
            self.__dict__["armed"] = False
            raise serialio.serial.SerialException("write failed: device disconnected")
        super().__setattr__(name, value)


def test_reset_survives_port_drop(no_sleep: None) -> None:
    fake = RtsDropSerial()
    out = io.StringIO()
    console = Console("/dev/x", factory=lambda: fake, echo=True, out=out).open()
    fake.__dict__["armed"] = True
    console.reset()
    assert "-- port reconnected --" in out.getvalue()
    assert fake.is_open


def test_clear_forgets_lines() -> None:
    fake = FakeSerial(incoming=b"old\n")
    console = Console("/dev/x", factory=lambda: fake).open()
    console.collect(silence=0.05)
    console.clear()
    fake.rx += b"new\n"
    assert console.expect("new|old", timeout=1)[0] == "new" and console.lines == ["new"]


def test_default_factory_is_looked_up_at_open(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeSerial()
    monkeypatch.setattr(serialio.serial, "Serial", lambda: fake)
    Console("/dev/x").open()
    assert fake.is_open and fake.port == "/dev/x"


def test_wait_for_port_waits_for_a_stable_port(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = FakeClock()
    monkeypatch.setattr(serialio, "time", clock)
    # still there just after esptool exits, gone while re-enumerating, back from t=2.0 on
    timeline = [(0.0, 0.3, True), (0.3, 2.0, False), (2.0, 99.0, True)]
    seen: list[float] = []

    def exists(port: str) -> bool:
        seen.append(clock.now)
        return next(present for start, end, present in timeline if start <= clock.now < end)

    wait_for_port("/dev/x", exists, timeout=10.0, settle=0.5)
    assert 2.5 <= clock.now < 2.6  # returned only after 0.5 s of presence following the drop
    assert any(0.3 <= t < 2.0 for t in seen)


def test_wait_for_port_never_appears_exit_4(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = FakeClock()
    monkeypatch.setattr(serialio, "time", clock)
    with pytest.raises(XeWeError) as exc:
        wait_for_port("/dev/x", lambda port: False, timeout=10.0)
    assert exc.value.code == EXIT_NO_BOARD and "/dev/x did not come back" in str(exc.value)
    assert 10.0 <= clock.now < 10.2


def test_wait_for_port_flapping_never_settles(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = FakeClock()
    monkeypatch.setattr(serialio, "time", clock)
    with pytest.raises(XeWeError):
        wait_for_port("/dev/x", lambda port: int(clock.now * 10) % 4 != 0, timeout=3.0, settle=0.5)


ROM = (b"ESP-ROM:esp32s3-20210327\r\nBuild:Mar 27 2021\r\n"
       b"rst:0x15 (USB_UART_CHIP_RESET),boot:0x28 (SPI_FAST_FLASH_BOOT)\r\n")
BANNER = b"XeWe OS 2.0.15\r\n"


def test_serial_main_expect(clock: FakeClock, capsys: pytest.CaptureFixture[str]) -> None:
    fake = FakeSerial(script=responder(b"Uptime: 1\n"))
    code = serialio.serial_main(None, select_board(Board("/dev/x")), send="$system status", expect="Uptime",
                                timeout=1, factory=lambda: fake)
    out = capsys.readouterr().out
    assert code == 0
    assert re.search(STAMP + r"> \$system status", out) and "match  'Uptime' after" in out


def test_serial_main_open_error_exit_1(caplog: pytest.LogCaptureFixture) -> None:
    """A wedged native-USB port (seen on the S3: ``OSError: [Errno 71] Protocol error`` when pyserial
    sets DTR at open) is an error message and exit 1, not a traceback."""
    fake = FakeSerial()

    def broken_open() -> None:
        raise OSError(71, "Protocol error")

    fake.open = broken_open  # type: ignore[method-assign]
    code = serialio.serial_main(None, select_board(Board("/dev/x")), send="$system status", factory=lambda: fake)
    assert code == 1
    assert "cannot open /dev/x: [Errno 71] Protocol error" in caplog.text


def test_serial_main_timeout_exit_1(clock: FakeClock, caplog: pytest.LogCaptureFixture) -> None:
    fake = FakeSerial(script=responder(b"something else\n"))
    code = serialio.serial_main(None, select_board(Board("/dev/x")), send="$system status", expect="Uptime",
                                timeout=0.2, factory=lambda: fake)
    assert code == 1
    assert "  something else" in caplog.text


def test_serial_main_send_without_expect(clock: FakeClock) -> None:
    fake = FakeSerial(script=responder(b"a\nb\n"))
    assert serialio.serial_main(None, select_board(Board("/dev/x")), send="$system status", factory=lambda: fake) == 0


def _after_boot(fake_ref: list[FakeSerial], reply: bytes):
    """Reply to ``$system status``; record whether the whole boot log had been read at that point."""
    sent_with_pending: list[bytes] = []

    def script(data: bytes) -> bytes:
        sent_with_pending.append(bytes(fake_ref[0].rx))
        return reply if data == b"$system status\n" else b""
    return script, sent_with_pending


@pytest.mark.parametrize("expect", [None, "Uptime"])
def test_serial_main_send_waits_for_boot(expect: str | None, capsys: pytest.CaptureFixture[str]) -> None:
    ref: list[FakeSerial] = []
    script, pending = _after_boot(ref, b"Uptime: 00:00:12\r\n")
    fake = FakeSerial(script=script, incoming=ROM + BANNER + b"wifi: connecting\r\nSystem Setup Complete\r\n")
    ref.append(fake)
    code = serialio.serial_main(None, select_board(Board("/dev/x")), send="$system status", expect=expect,
                                timeout=1, factory=lambda: fake)
    out = capsys.readouterr().out
    assert code == 0
    assert bytes(fake.written) == b"$system status\n"
    assert pending == [b""]  # written only once the whole boot log (banner included) was read
    assert re.search(r"^boot: System Setup Complete after \d+\.\d s$", out, re.M)
    order = [out.index(s) for s in ("ESP-ROM:", "  System Setup Complete", "> $system status", "Uptime: 00:00:12")]
    assert order == sorted(order)
    if expect:
        assert "match  'Uptime' after" in out


def test_serial_main_unprovisioned_exit_1(capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture) -> None:
    fake = FakeSerial(script=responder(b"Uptime: 1\n"),
                      incoming=ROM + BANNER + b"Name your device (ex: Kitchen Lights):\r\n> ")
    code = serialio.serial_main(None, select_board(Board("/dev/x")), send="$system status", expect="Uptime",
                                timeout=1, factory=lambda: fake)
    assert code == 1
    assert bytes(fake.written) == b""
    assert "board is unprovisioned; run xewe provision" in caplog.text
    assert "boot: 'Name your device'" in capsys.readouterr().out


def test_serial_main_silent_board_sends_after_grace(clock: FakeClock, capsys: pytest.CaptureFixture[str]) -> None:
    sent_at: list[float] = []

    def script(data: bytes) -> bytes:
        sent_at.append(clock.now)
        return b"Uptime: 1\n"
    fake = FakeSerial(script=script)
    code = serialio.serial_main(None, select_board(Board("/dev/x")), send="$system status", expect="Uptime",
                                timeout=1, factory=lambda: fake)
    assert code == 0
    assert len(sent_at) == 1 and serialio.BOOT_GRACE_SECONDS <= sent_at[0] < serialio.BOOT_GRACE_SECONDS + 0.1
    assert "boot: no boot output within 2 s of opening" in capsys.readouterr().out


def test_serial_main_boot_never_finishes_exit_1(clock: FakeClock, caplog: pytest.LogCaptureFixture) -> None:
    fake = FakeSerial(script=responder(b"Uptime: 1\n"), incoming=ROM + BANNER)
    code = serialio.serial_main(None, select_board(Board("/dev/x")), send="$system status", boot_timeout=5,
                                factory=lambda: fake)
    assert code == 1 and bytes(fake.written) == b""
    assert "nothing sent" in caplog.text and "  rst:0x15" in caplog.text
    assert 5.0 <= clock.now < 5.5


def test_serial_main_listen_does_not_wait(clock: FakeClock, capsys: pytest.CaptureFixture[str]) -> None:
    fake = FakeSerial(incoming=ROM)
    assert serialio.serial_main(None, select_board(Board("/dev/x")), duration=0.1, factory=lambda: fake) == 0
    out = capsys.readouterr().out
    assert "ESP-ROM:" in out and "\nboot: " not in out


class ResetBootSerial(FakeSerial):
    """Releasing RTS (end of ``reset()``) makes the board boot again and print ``boot``."""

    def __setattr__(self, name: str, value: object) -> None:
        super().__setattr__(name, value)
        if name == "rts" and value is False and self.__dict__.get("boot") is not None:
            self.rx += self.__dict__["boot"]


def test_wait_for_banner_ignores_the_open_reset_boot(no_sleep: None) -> None:
    fake = ResetBootSerial(incoming=ROM + BANNER + b"Name your device (ex: Kitchen Lights):\r\n")
    console = Console("/dev/x", factory=lambda: fake).open()
    console.collect(silence=0.05)  # the open-reset boot, already read before our reset
    fake.__dict__["boot"] = ROM + BANNER + b"System Setup Complete\r\nName your device\r\n"
    m = serialio.wait_for_banner(console, f"{serialio.BOOT_READY}|{serialio.BOOT_UNPROVISIONED}", 1)
    assert m[0] == serialio.BOOT_READY  # not the prompt from before the reset


def test_wait_for_banner_prompt_printed_twice(no_sleep: None) -> None:
    fake = ResetBootSerial()
    fake.__dict__["boot"] = ROM + b"Name your device (ex: Kitchen Lights):\r\n" + ROM + b"Name your device (ex: x):\r\n"
    console = Console("/dev/x", factory=lambda: fake).open()
    m = serialio.wait_for_banner(console, f"{serialio.BOOT_READY}|{serialio.BOOT_UNPROVISIONED}", 1)
    assert m[0] == serialio.BOOT_UNPROVISIONED


def test_cli_serial_no_board(project: Paths, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["serial"]) == 0
    assert capsys.readouterr().out.strip() == "no board attached; nothing to listen to"
    assert main(["serial", "--require-board"]) == 4
    assert main(["serial", "--port", "/dev/does-not-exist"]) == 4


def test_wait_for_port_follows_a_renamed_port(monkeypatch: pytest.MonkeyPatch) -> None:
    """macOS: the board re-enumerates as another cu.usbmodem<N> (same USB serial number)."""
    clock = FakeClock()
    monkeypatch.setattr(serialio, "time", clock)
    port = wait_for_port("/dev/cu.usbmodem101", lambda p: p == "/dev/cu.usbmodem1101" and clock.now > 1.0,
                         timeout=10.0, settle=0.5, renamed=lambda: "/dev/cu.usbmodem1101")
    assert port == "/dev/cu.usbmodem1101" and 1.5 <= clock.now < 1.7


def test_wait_for_port_names_the_new_port_when_it_never_settles(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = FakeClock()
    monkeypatch.setattr(serialio, "time", clock)
    with pytest.raises(XeWeError) as exc:
        wait_for_port("/dev/cu.usbmodem101", lambda p: False, timeout=2.0, renamed=lambda: "/dev/cu.usbmodem1101")
    assert exc.value.code == EXIT_NO_BOARD
    assert "came back as /dev/cu.usbmodem1101" in str(exc.value) and "--port /dev/cu.usbmodem1101" in str(exc.value)


def test_settle_from_flag_env_or_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("XEWE_SETTLE", raising=False)
    assert serialio.settle_seconds() == serialio.PORT_SETTLE_SECONDS == 0.5
    monkeypatch.setenv("XEWE_SETTLE", "2.5")
    assert serialio.settle_seconds() == 2.5
    assert serialio.settle_seconds(1.0) == 1.0  # --settle wins
    monkeypatch.setenv("XEWE_SETTLE", "slow")
    with pytest.raises(XeWeError):
        serialio.settle_seconds()


def test_settle_from_env_is_used(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = FakeClock()
    monkeypatch.setattr(serialio, "time", clock)
    monkeypatch.setenv("XEWE_SETTLE", "2")
    wait_for_port("/dev/x", lambda p: True, timeout=10.0)
    assert 2.0 <= clock.now < 2.1


def test_send_splits_long_writes(no_sleep) -> None:
    """The board's USB RX queue holds 1024 bytes and has no flow control: no write is longer than 900."""
    writes: list[bytes] = []
    fake = FakeSerial()
    fake.write = lambda data: writes.append(bytes(data)) or len(data)  # type: ignore[method-assign]
    c = Console("/dev/x", factory=lambda: fake).open()
    c.send("x" * 2000)
    assert [len(w) for w in writes] == [900, 900, 201]
    assert b"".join(writes) == b"x" * 2000 + b"\n"
    writes.clear()
    c.send("$system uid")
    assert writes == [b"$system uid\n"]  # short commands stay one write
    writes.clear()
    c.write(b"y" * 1500, chunk=0)  # overflow tests can still send one burst
    assert [len(w) for w in writes] == [1500]


def test_reopen_closes_and_opens_again(no_sleep) -> None:
    fakes = [FakeSerial(), FakeSerial()]
    it = iter(fakes)
    c = Console("/dev/x", factory=lambda: next(it)).open()
    c.reopen()
    assert not fakes[0].is_open and fakes[1].is_open and fakes[1].port == "/dev/x"
    assert fakes[1].line_history[-1] == (False, False)  # opened without a reset, like the first open


def test_probe(clock: FakeClock) -> None:
    alive = FakeSerial(script=lambda d: b"uid64 0123456789abcdef\n" if d == b"$system uid\n" else b"")
    assert Console("/dev/x", factory=lambda: alive).open().probe(timeout=1.5)
    silent = FakeSerial()
    assert not Console("/dev/x", factory=lambda: silent).open().probe(timeout=1.5, tries=3)
    assert bytes(silent.written) == b"$system uid\n" * 3
