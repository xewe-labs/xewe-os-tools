from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from conftest import DROP, PORT, FakeSerial, write_tests
from xewe.board import boards, serialio
from xewe.board.boards import Board
from xewe.build import flash
from xewe.cli import main
from xewe.env.project import Paths
from xewe.testing import plugin


def _wifi_tests(p: Paths) -> None:
    """Tests in the wifi module of the modules checkout, copied to build/modules/tests/wifi/ by generate."""
    write_tests(p.root.parent / "modules-src" / "modules" / "wifi")
    assert main(["modules", "generate"]) == 0
    assert (p.module_tests("wifi") / "unit" / "test_logic.py").is_file()


def _run(pytester: pytest.Pytester, p: Paths, *args: str) -> pytest.RunResult:
    return pytester.runpytest("--xewe-project", str(p.root), "--xewe-chip", "c3", "-p", "no:cacheprovider",
                              str(p.root / "tests"), *args)


def test_no_board_reports_compiled_not_run(pytester: pytest.Pytester, project_with_tests: Paths) -> None:
    res = _run(pytester, project_with_tests)
    res.assert_outcomes(passed=1, skipped=1)
    assert res.ret == 0
    res.stdout.fnmatch_lines([
        "*= compiled, not run (no board attached, chip c3) =*",
        "*test_fw.py::test_status",
        "xewe test: 1 unit passed, 1 compiled, not run, 0 failed",
    ])
    assert (project_with_tests.out_dir("c3") / "2.0.15-c3-xewe-os.bin").is_file()  # board tests still compile


def test_require_board_fails(pytester: pytest.Pytester, project_with_tests: Paths) -> None:
    res = _run(pytester, project_with_tests, "--xewe-require-board")
    res.assert_outcomes(passed=1, errors=1)
    assert res.ret == 1  # plain pytest; `xewe test --require-board` maps this run to 4
    res.stdout.fnmatch_lines(["*compiled, not run: no board attached (--require-board)*"])


def test_xewe_test_require_board_exits_4(project_with_tests: Paths, monkeypatch: pytest.MonkeyPatch) -> None:
    assert main(["test", "--require-board"]) == 4
    monkeypatch.setenv("XEWE_REQUIRE_BOARD", "1")
    assert main(["test"]) == 4
    assert main(["test", "--unit-only"]) == 0  # no board test selected: nothing needs the board


def test_unit_marker_deselects_board(pytester: pytest.Pytester, project_with_tests: Paths) -> None:
    res = _run(pytester, project_with_tests, "-m", "unit")
    res.assert_outcomes(passed=1, deselected=1)
    assert not project_with_tests.out_dir("c3").exists()


def test_compile_failure_fails_board_tests(pytester: pytest.Pytester, project_with_tests: Paths,
                                              monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAKE_ARDUINO_COMPILE_FAIL", "all")
    res = _run(pytester, project_with_tests)
    res.assert_outcomes(passed=1, errors=1)
    assert res.ret == 1
    res.stdout.fnmatch_lines(["*build failed for c3*"])


def test_no_tests_collected_is_success(pytester: pytest.Pytester, project: Paths) -> None:
    (project.root / "tests").mkdir()
    res = _run(pytester, project)
    assert res.ret == 0


def test_xewe_test_cli_runs_project_and_module_tests(project_with_tests: Paths, capsys: pytest.CaptureFixture[str]) -> None:
    _wifi_tests(project_with_tests)  # same basenames as the project tests: importlib mode
    assert main(["test"]) == 0
    out = capsys.readouterr().out
    assert "xewe test: 2 unit passed, 2 compiled, not run, 0 failed" in out
    assert main(["test", "--require-board"]) == 4  # like flash/serial: board required, none found
    assert main(["test", "--module", "pins"]) == 2
    assert main(["test", "--module", "wifi", "--unit-only"]) == 0


def test_unit_only_selects_unit_tests(project_with_tests: Paths, capsys: pytest.CaptureFixture[str]) -> None:
    _wifi_tests(project_with_tests)
    assert main(["test", "--unit-only", "--", "-v"]) == 0
    out = capsys.readouterr().out
    assert "tests/unit/test_logic.py::test_logic PASSED" in out
    assert "build/modules/tests/wifi/unit/test_logic.py::test_logic PASSED" in out
    assert "test_fw.py" not in out  # board tests deselected
    assert "xewe test: 2 unit passed, 0 compiled, not run, 0 failed" in out
    assert not project_with_tests.out_dir("c3").exists()  # nothing was built


def test_module_board_tests_are_collected(project_with_tests: Paths, capsys: pytest.CaptureFixture[str]) -> None:
    _wifi_tests(project_with_tests)
    assert main(["test", "--module", "wifi", "--", "--collect-only", "-q", "-m", "board"]) == 0
    out = capsys.readouterr().out
    assert "build/modules/tests/wifi/board/test_fw.py::test_status" in out
    assert "test_logic" not in out


def test_xewe_test_all_chips(project_with_tests: Paths, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["test", "--all-chips", "--", "-q"]) == 0
    out = capsys.readouterr().out
    for chip in ("c3", "c6", "s3"):
        assert f"no board attached, chip {chip}" in out
        assert project_with_tests.out_dir(chip).is_dir()


def test_xewe_test_without_tests(project: Paths, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["test"]) == 0
    assert "no tests found" in capsys.readouterr().out


def test_board_marker_is_automatic(pytester: pytest.Pytester, project_with_tests: Paths) -> None:
    res = _run(pytester, project_with_tests, "-m", "board", "--collect-only", "-q")
    res.stdout.fnmatch_lines(["*test_fw.py::test_status"])
    assert "test_logic" not in res.stdout.str()


def test_extra_args_only_for_test(project: Path) -> None:
    assert main(["build", "--", "-q"]) == 2


# --------------------------------------------------------------------------- board present (faked)

BOOTED = b"ESP-ROM:esp32c3\r\nSystem Setup Complete\r\n"


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


def test_one_console_per_session(pytester: pytest.Pytester, project: Paths, board_attached) -> None:
    write_tests(project.root, SESSION_TESTS, unit=None)
    board_attached(lambda: FakeSerial(chunks=[BOOTED], script=pinger()))
    res = _run(pytester, project)
    res.assert_outcomes(passed=2)
    assert len(FakeSerial.instances) == 1  # one open for the whole session
    fake = FakeSerial.instances[0]
    assert fake.port == PORT and fake.opens == 1 and not fake.is_open  # closed at session end
    assert fake.rts_history[-2:] == [True, False]  # reset pulsed after open so the banner is seen
    assert bytes(fake.written) == b"ping\nping\n"


def test_boot_waits_through_first_boot_reboot(pytester: pytest.Pytester, project: Paths, board_attached) -> None:
    write_tests(project.root, SESSION_TESTS, unit=None)
    fake = FakeSerial(chunks=[b"Initial Setup Complete\r\n", b"Rebooting...\r\n", DROP, DROP, b"", BOOTED],
                      script=pinger())
    board_attached(lambda: fake)  # reconnects reopen the same port
    res = _run(pytester, project)
    res.assert_outcomes(passed=2)
    assert fake.opens == 3  # the port dropped twice during the reboot and was reopened


def test_boot_unprovisioned_fails_with_runbook_hint(pytester: pytest.Pytester, project: Paths, board_attached) -> None:
    write_tests(project.root, SESSION_TESTS, unit=None)
    board_attached(lambda: FakeSerial(chunks=[b"Name your device (max 32 chars):\r\n", BOOTED]))
    res = _run(pytester, project)
    res.assert_outcomes(errors=2)
    res.stdout.fnmatch_lines(["*board on /dev/ttyFAKE is unprovisioned*RUNBOOK section 3*"])
    assert not FakeSerial.instances[0].is_open


def uid_answerer(extra: Callable[[bytes], bytes] | None = None) -> Callable[[bytes], bytes]:
    """Answers the liveness probe ``$system uid`` (and whatever ``extra`` answers)."""
    def script(data: bytes) -> bytes:
        if data == b"$system uid\n":
            return b"uid64 0123456789abcdef\n"
        return extra(data) if extra else b""
    return script


def test_boot_silence_reopens_then_ends_session(pytester: pytest.Pytester, project: Paths, board_attached,
                                                monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(plugin, "BOOT_TIMEOUT_SECONDS", 0.3)
    monkeypatch.setattr(plugin, "PROBE_TIMEOUT_SECONDS", 0.3)
    write_tests(project.root, SESSION_TESTS, unit=None)
    board_attached(lambda: FakeSerial(chunks=[b"ESP-ROM:esp32c3\r\n"]))
    res = _run(pytester, project)
    res.assert_outcomes()  # pytest.exit: no test ran, none of them waits out its own timeout
    assert res.ret == pytest.ExitCode.TESTS_FAILED
    res.stdout.fnmatch_lines(["*board on /dev/ttyFAKE is silent after a restart*'$system uid'*power-cycle it*"])
    assert len(FakeSerial.instances) == 2  # the port was closed and opened again once
    assert bytes(FakeSerial.instances[1].written).count(b"$system uid\n") == 3


def test_boot_silence_but_board_answers_after_reopen(pytester: pytest.Pytester, project: Paths, board_attached,
                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    """A stale endpoint: the first open reads nothing, the reopened port talks."""
    monkeypatch.setattr(plugin, "BOOT_TIMEOUT_SECONDS", 0.3)
    write_tests(project.root, SESSION_TESTS, unit=None)
    stale, fresh = FakeSerial(), FakeSerial(script=uid_answerer(pinger()))
    fakes = iter([stale, fresh])
    board_attached(lambda: next(fakes))
    res = _run(pytester, project)
    res.assert_outcomes(passed=2)
    assert not stale.is_open and bytes(fresh.written) == b"$system uid\nping\nping\n"


def test_boot_silence_then_first_boot_prompt_after_reopen_fails(pytester: pytest.Pytester, project: Paths,
                                                                 board_attached,
                                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(plugin, "BOOT_TIMEOUT_SECONDS", 0.3)
    write_tests(project.root, SESSION_TESTS, unit=None)
    fakes = iter([FakeSerial(), FakeSerial(incoming=b"Name your device (ex: Kitchen Lights):\r\n")])
    board_attached(lambda: next(fakes))
    res = _run(pytester, project)
    res.assert_outcomes(errors=2)
    res.stdout.fnmatch_lines(["*board on /dev/ttyFAKE is unprovisioned*"])


RESTART_TESTS = """
def test_restart(serial):
    serial.send("restart")
    serial.expect("System Setup Complete", timeout=0.3)


def test_after(serial):
    serial.command("ping", expect="pong", timeout=1)


def test_after_2(serial):
    serial.command("ping", expect="pong", timeout=1)
"""


def test_silent_board_after_a_test_timeout_ends_session(pytester: pytest.Pytester, project: Paths, board_attached,
                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    """TV1 F7: a restart that never prints again fails one test, not every later one."""
    monkeypatch.setattr(plugin, "PROBE_TIMEOUT_SECONDS", 0.3)
    write_tests(project.root, RESTART_TESTS, unit=None)
    alive = [True]

    def board(data: bytes) -> bytes:
        if data == b"restart\n":
            alive[0] = False
        return uid_answerer(pinger())(data) if alive[0] else b""

    board_attached(lambda: FakeSerial(chunks=[BOOTED], script=board))
    res = _run(pytester, project)
    res.assert_outcomes(failed=1)
    assert res.ret == pytest.ExitCode.TESTS_FAILED
    res.stdout.fnmatch_lines(["*silent after a restart*power-cycle*"])


def test_timeout_on_a_live_board_continues(pytester: pytest.Pytester, project: Paths, board_attached,
                                           monkeypatch: pytest.MonkeyPatch) -> None:
    """A test that waited for the wrong text: the probe answers, the next tests run."""
    write_tests(project.root, RESTART_TESTS, unit=None)
    board_attached(lambda: FakeSerial(chunks=[BOOTED], script=uid_answerer(pinger())))
    res = _run(pytester, project)
    res.assert_outcomes(passed=2, failed=1)
    assert len(FakeSerial.instances) == 2  # reopened once, after test_restart only


def test_port_not_back_after_flash_maps_to_exit_4(project: Paths, board_attached,
                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    from xewe.report import EXIT_NO_BOARD, XeWeError

    def gone(*a: Any, **k: Any) -> None:
        raise XeWeError(f"{PORT} did not come back", EXIT_NO_BOARD)

    write_tests(project.root, SESSION_TESTS, unit=None)
    monkeypatch.setattr(flash, "write_image", gone)
    assert main(["test"]) == 4


PIN_TESTS = '''
import os

import pytest


@pytest.mark.unit
def test_pins_from_dotenv():
    assert os.environ["XEWE_TEST_BUTTONS_PIN"] == "7"
    assert os.environ["XEWE_TEST_PINS_ADC_PIN"] == "3"  # the real environment won
'''


def test_dotenv_pins_reach_module_tests(pytester: pytest.Pytester, project: Paths,
                                        monkeypatch: pytest.MonkeyPatch) -> None:
    (project.root / "tests" / "unit").mkdir(parents=True)
    (project.root / "tests" / "unit" / "test_pins_env.py").write_text(PIN_TESTS)
    dotfile = project.root / ("." + "env")
    dotfile.write_text("XEWE_TEST_BUTTONS_PIN=7\nXEWE_TEST_PINS_ADC_PIN=5\n")
    monkeypatch.setenv("XEWE_TEST_PINS_ADC_PIN", "3")
    res = _run(pytester, project)
    res.assert_outcomes(passed=1)
