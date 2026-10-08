from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import serial as pyserial

from conftest import FakeSerial
from xewe import boards, flash, serialio
from xewe.boards import Board
from xewe.cli import main
from xewe.project import Paths
from xewe.testing import plugin

TESTS = '''
import pytest


@pytest.mark.host
def test_logic():
    assert 1 + 1 == 2


def test_status(serial):
    serial.command("$system status", expect="Uptime", timeout=1)
'''


@pytest.fixture
def proj(project: Paths) -> Paths:
    (project.root / "tests").mkdir()
    (project.root / "tests" / "test_fw.py").write_text(TESTS)
    return project


def _run(pytester: pytest.Pytester, p: Paths, *args: str) -> pytest.RunResult:
    return pytester.runpytest("--xewe-project", str(p.root), "--xewe-chip", "c3", "-p", "no:cacheprovider",
                              str(p.root / "tests"), *args)


def test_no_board_reports_compiled_not_run(pytester: pytest.Pytester, proj: Paths) -> None:
    res = _run(pytester, proj)
    res.assert_outcomes(passed=1, skipped=1)
    assert res.ret == 0
    res.stdout.fnmatch_lines([
        "*= compiled, not run (no board attached, chip c3) =*",
        "*test_fw.py::test_status",
        "xewe test: 1 host passed, 1 compiled, not run, 0 failed",
    ])
    assert (proj.out / "c3/2.0.15-c3-xewe-os.bin").is_file()  # hardware tests still compile


def test_require_board_fails(pytester: pytest.Pytester, proj: Paths) -> None:
    res = _run(pytester, proj, "--xewe-require-board")
    res.assert_outcomes(passed=1, errors=1)
    assert res.ret == 1  # plain pytest; `xewe test --require-board` maps this run to 4
    res.stdout.fnmatch_lines(["*compiled, not run: no board attached (--require-board)*"])


def test_xewe_test_require_board_exits_4(proj: Paths, monkeypatch: pytest.MonkeyPatch) -> None:
    assert main(["test", "--require-board"]) == 4
    monkeypatch.setenv("XEWE_REQUIRE_BOARD", "1")
    assert main(["test"]) == 4
    assert main(["test", "--host-only"]) == 0  # no hardware test selected: nothing needs the board


def test_host_only_deselects_hardware(pytester: pytest.Pytester, proj: Paths) -> None:
    res = _run(pytester, proj, "-m", "host")
    res.assert_outcomes(passed=1, deselected=1)
    assert not (proj.out / "c3").exists()


def test_compile_failure_fails_hardware_tests(pytester: pytest.Pytester, proj: Paths,
                                              monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAKE_ARDUINO_COMPILE_FAIL", "all")
    res = _run(pytester, proj)
    res.assert_outcomes(passed=1, errors=1)
    assert res.ret == 1
    res.stdout.fnmatch_lines(["*build failed for c3*"])


def test_no_tests_collected_is_success(pytester: pytest.Pytester, project: Paths) -> None:
    (project.root / "tests").mkdir()
    res = _run(pytester, project)
    assert res.ret == 0


def test_xewe_test_cli_runs_project_and_module_tests(proj: Paths, capsys: pytest.CaptureFixture[str]) -> None:
    mod_tests = proj.modules_checkout / "modules" / "wifi" / "tests"
    mod_tests.mkdir()
    (mod_tests / "test_fw.py").write_text(TESTS)  # same basename as the project test: importlib mode
    assert main(["test"]) == 0
    out = capsys.readouterr().out
    assert "xewe test: 2 host passed, 2 compiled, not run, 0 failed" in out
    assert main(["test", "--require-board"]) == 4  # like flash/serial: board required, none found
    assert main(["test", "--module", "pins"]) == 2
    assert main(["test", "--module", "wifi", "--host-only"]) == 0


def test_xewe_test_all_chips(proj: Paths, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["test", "--all-chips", "--", "-q"]) == 0
    out = capsys.readouterr().out
    for chip in ("c3", "c6", "s3"):
        assert f"no board attached, chip {chip}" in out
        assert (proj.out / chip).is_dir()


def test_xewe_test_without_tests(project: Paths, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["test"]) == 0
    assert "no tests found" in capsys.readouterr().out


def test_hardware_marker_is_automatic(pytester: pytest.Pytester, proj: Paths) -> None:
    res = _run(pytester, proj, "-m", "hardware", "--collect-only", "-q")
    res.stdout.fnmatch_lines(["*test_fw.py::test_status"])
    assert "test_logic" not in res.stdout.str()


def test_extra_args_only_for_test(project: Path) -> None:
    assert main(["build", "--", "-q"]) == 2


# --------------------------------------------------------------------------- board present (faked)

PORT = "/dev/ttyFAKE"
DROP = object()
BOOTED = b"ESP-ROM:esp32c3\r\nSystem Setup Complete\r\n"


class BootSerial(FakeSerial):
    """FakeSerial that feeds ``chunks`` one per read; ``DROP`` raises like a vanished USB port."""

    def __init__(self, chunks: list[Any], script: Callable[[bytes], bytes] | None = None) -> None:
        super().__init__(script=script)
        self.chunks = list(chunks)
        self.opens = 0

    def open(self) -> None:
        super().open()
        self.opens += 1

    def read(self, n: int = 1) -> bytes:
        if self.chunks:
            chunk = self.chunks.pop(0)
            if chunk is DROP:
                raise pyserial.SerialException("device reports readiness to read but returned no data")
            self.rx += chunk
            n = max(n, len(self.rx))
        return super().read(n)


def pinger() -> Callable[[bytes], bytes]:
    """Answers each ``ping`` with ``pong N`` followed by a late line ``late N``."""
    count = [0]

    def script(data: bytes) -> bytes:
        if data != b"ping\n":
            return b""
        count[0] += 1
        return f"pong {count[0]}\nlate {count[0]}\n".encode()

    return script


@pytest.fixture
def board_attached(monkeypatch: pytest.MonkeyPatch) -> Callable[[Callable[[], Any]], None]:
    """A c3 board on PORT; flashing is a no-op; returns a setter for the serial factory."""
    monkeypatch.setattr(boards, "select", lambda *a, **k: Board(PORT, "c3"))
    monkeypatch.setattr(flash, "esptool_cmd", lambda p: ["esptool"])
    monkeypatch.setattr(flash, "write_image", lambda *a, **k: None)
    monkeypatch.setattr(FakeSerial, "instances", [])

    def use(factory: Callable[[], Any]) -> None:
        monkeypatch.setattr(serialio.serial, "Serial", factory)

    return use


SESSION_TESTS = """
def test_one(serial):
    serial.command("ping", expect="pong 1", timeout=1)


def test_two(serial):
    assert serial.lines == [], serial.lines  # boot log and test_one's late output are gone
    serial.command("ping", expect="pong 2", timeout=1)
    assert "pong 1" not in serial.lines and "late 1" not in serial.lines
"""


def _write_tests(p: Paths, text: str) -> None:
    (p.root / "tests").mkdir()
    (p.root / "tests" / "test_fw.py").write_text(text)


def test_one_console_per_session(pytester: pytest.Pytester, project: Paths, board_attached) -> None:
    _write_tests(project, SESSION_TESTS)
    board_attached(lambda: BootSerial([BOOTED], script=pinger()))
    res = _run(pytester, project)
    res.assert_outcomes(passed=2)
    assert len(FakeSerial.instances) == 1  # one open for the whole session
    fake = FakeSerial.instances[0]
    assert fake.port == PORT and fake.opens == 1 and not fake.is_open  # closed at session end
    assert fake.rts_history[-2:] == [True, False]  # reset pulsed after open so the banner is seen
    assert bytes(fake.written) == b"ping\nping\n"


def test_boot_waits_through_first_boot_reboot(pytester: pytest.Pytester, project: Paths, board_attached) -> None:
    _write_tests(project, SESSION_TESTS)
    fake = BootSerial([b"Initial Setup Complete\r\n", b"Rebooting...\r\n", DROP, DROP, b"", BOOTED],
                      script=pinger())
    board_attached(lambda: fake)  # reconnects reopen the same port
    res = _run(pytester, project)
    res.assert_outcomes(passed=2)
    assert fake.opens == 3  # the port dropped twice during the reboot and was reopened


def test_boot_unprovisioned_fails_with_runbook_hint(pytester: pytest.Pytester, project: Paths, board_attached) -> None:
    _write_tests(project, SESSION_TESTS)
    board_attached(lambda: BootSerial([b"Name your device (max 32 chars):\r\n", BOOTED]))
    res = _run(pytester, project)
    res.assert_outcomes(errors=2)
    res.stdout.fnmatch_lines(["*board on /dev/ttyFAKE is unprovisioned*RUNBOOK section 3*"])
    assert not FakeSerial.instances[0].is_open


def test_boot_silence_times_out(pytester: pytest.Pytester, project: Paths, board_attached,
                                monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(plugin, "BOOT_TIMEOUT_SECONDS", 0.3)
    _write_tests(project, SESSION_TESTS)
    board_attached(lambda: BootSerial([b"ESP-ROM:esp32c3\r\n"]))
    res = _run(pytester, project)
    res.assert_outcomes(errors=2)
    res.stdout.fnmatch_lines(["*did not finish booting within 0.3 s (no 'System Setup Complete')*"])


def test_port_not_back_after_flash_maps_to_exit_4(project: Paths, board_attached,
                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    from xewe.report import EXIT_NO_BOARD, XeweError

    def gone(*a: Any, **k: Any) -> None:
        raise XeweError(f"{PORT} did not come back", EXIT_NO_BOARD)

    _write_tests(project, SESSION_TESTS)
    monkeypatch.setattr(flash, "write_image", gone)
    assert main(["test"]) == 4
