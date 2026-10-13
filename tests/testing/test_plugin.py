import hashlib
import json
import sys
import xml.etree.ElementTree as ET
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import serial as pyserial

from conftest import BIN, DROP, PORT, FakeSerial, UnpluggedSerial, write_tests
from xewe.board import boards, serialio
from xewe.board.boards import Board
from xewe.build import compile as build
from xewe.build import flash, releasecard
from xewe.cli import main
from xewe.env.project import Paths
from xewe.report import XeWeError
from xewe.testing import plugin


def _wifi_tests(p: Paths) -> None:
    """Tests in the wifi module of the modules checkout, copied to build/modules/tests/wifi/ by generate."""
    write_tests(p.root.parent / "modules-src" / "modules" / "wifi")
    assert main(["modules", "generate"]) == 0
    assert (p.module_tests("wifi") / "unit" / "test_logic.py").is_file()


def _run(pytester: pytest.Pytester, p: Paths, *args: str) -> pytest.RunResult:
    return pytester.runpytest(
        "--xewe-project", str(p.root), "--xewe-chip", "c3", "-p", "no:cacheprovider", str(p.root / "tests"), *args
    )


def test_no_board_reports_compiled_not_run(pytester: pytest.Pytester, project_with_tests: Paths) -> None:
    res = _run(pytester, project_with_tests)
    res.assert_outcomes(passed=1, skipped=1)
    assert res.ret == 0
    res.stdout.fnmatch_lines(
        [
            "*= compiled, not run (no board attached, chip c3) =*",
            "*test_fw.py::test_status",
            "xewe test: 1 unit passed, 1 compiled, not run, 0 failed",
        ]
    )
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


def test_compile_failure_fails_board_tests(
    pytester: pytest.Pytester, project_with_tests: Paths, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_ARDUINO_COMPILE_FAIL", "all")
    res = _run(pytester, project_with_tests)
    res.assert_outcomes(passed=1, errors=1)
    assert res.ret == 1
    res.stdout.fnmatch_lines(["*build failed for c3*"])


def test_no_tests_collected_is_success(pytester: pytest.Pytester, project: Paths) -> None:
    (project.root / "tests").mkdir()
    res = _run(pytester, project)
    assert res.ret == 0


def test_xewe_test_cli_runs_project_and_module_tests(
    project_with_tests: Paths, capsys: pytest.CaptureFixture[str]
) -> None:
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
    fake = FakeSerial(
        chunks=[b"Initial Setup Complete\r\n", b"Rebooting...\r\n", DROP, DROP, b"", BOOTED], script=pinger()
    )
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


def test_boot_silence_reopens_then_ends_session(
    pytester: pytest.Pytester, project: Paths, board_attached, monkeypatch: pytest.MonkeyPatch
) -> None:
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


def test_board_gone_from_usb_at_boot_ends_session(
    pytester: pytest.Pytester, project: Paths, board_attached, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The post-flash reset unplugs the board; the boot wait reads a closed handle."""
    monkeypatch.setattr(plugin, "BOOT_TIMEOUT_SECONDS", 0.3)
    monkeypatch.setattr(serialio, "RECONNECT_SECONDS", 0.1)
    monkeypatch.setattr(UnpluggedSerial, "gone", False)

    class Unplugs(UnpluggedSerial):
        def __setattr__(self, name: str, value: Any) -> None:
            super().__setattr__(name, value)
            if name == "rts" and value and self.__dict__.get("is_open"):  # the reset pulse: the device leaves the bus
                UnpluggedSerial.gone = True
                raise pyserial.SerialException("write failed: [Errno 19] No such device")

    write_tests(project.root, SESSION_TESTS, unit=None)
    board_attached(Unplugs)
    res = _run(pytester, project)
    res.assert_outcomes()
    assert res.ret == pytest.ExitCode.TESTS_FAILED
    res.stdout.fnmatch_lines(["*board on /dev/ttyFAKE disappeared from USB*power-cycle it (unplug and replug)*"])
    assert "TypeError" not in res.stdout.str() and "silent after a restart" not in res.stdout.str()


def test_no_flash_skips_the_image_write(project: Paths, board_attached, monkeypatch: pytest.MonkeyPatch) -> None:
    def must_not_flash(*a: Any, **k: Any) -> None:
        raise AssertionError("flashed despite --no-flash")

    (project.root / "tests" / "board").mkdir(parents=True)
    (project.root / "tests" / "board" / "test_no_flash.py").write_text(SESSION_TESTS)  # a name no other test imported
    board_attached(lambda: FakeSerial(chunks=[BOOTED], script=pinger()))
    monkeypatch.setattr(flash, "write_image", must_not_flash)
    only = ["--", "-k", "test_one or test_two"]  # not the project's module tests
    assert main(["test", "--no-flash", *only]) == 0
    assert main(["test", *only]) == 1  # without the flag the image is written


def test_boot_silence_but_board_answers_after_reopen(
    pytester: pytest.Pytester, project: Paths, board_attached, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stale endpoint: the first open reads nothing, the reopened port talks."""
    monkeypatch.setattr(plugin, "BOOT_TIMEOUT_SECONDS", 0.3)
    write_tests(project.root, SESSION_TESTS, unit=None)
    stale, fresh = FakeSerial(), FakeSerial(script=uid_answerer(pinger()))
    fakes = iter([stale, fresh])
    board_attached(lambda: next(fakes))
    res = _run(pytester, project)
    res.assert_outcomes(passed=2)
    assert not stale.is_open and bytes(fresh.written) == b"$system uid\nping\nping\n"


def test_boot_silence_then_first_boot_prompt_after_reopen_fails(
    pytester: pytest.Pytester, project: Paths, board_attached, monkeypatch: pytest.MonkeyPatch
) -> None:
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


def test_silent_board_after_a_test_timeout_ends_session(
    pytester: pytest.Pytester, project: Paths, board_attached, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A restart that never prints again fails one test, not every later one."""
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


def test_timeout_on_a_live_board_continues(
    pytester: pytest.Pytester, project: Paths, board_attached, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A test that waited for the wrong text: the probe answers, the next tests run."""
    write_tests(project.root, RESTART_TESTS, unit=None)
    board_attached(lambda: FakeSerial(chunks=[BOOTED], script=uid_answerer(pinger())))
    res = _run(pytester, project)
    res.assert_outcomes(passed=2, failed=1)
    assert len(FakeSerial.instances) == 2  # reopened once, after test_restart only


def test_port_not_back_after_flash_maps_to_exit_4(
    project: Paths, board_attached, monkeypatch: pytest.MonkeyPatch
) -> None:
    from xewe.report import EXIT_NO_BOARD, XeWeError

    def gone(*a: Any, **k: Any) -> None:
        raise XeWeError(f"{PORT} did not come back", EXIT_NO_BOARD)

    write_tests(project.root, SESSION_TESTS, unit=None)
    monkeypatch.setattr(flash, "write_image", gone)
    assert main(["test"]) == 4


PIN_TESTS = """
import os

import pytest


@pytest.mark.unit
def test_pins_from_dotenv():
    assert os.environ["XEWE_TEST_BUTTONS_PIN"] == "7"
    assert os.environ["XEWE_TEST_PINS_ADC_PIN"] == "3"  # the real environment won
"""


def test_dotenv_pins_reach_module_tests(
    pytester: pytest.Pytester, project: Paths, monkeypatch: pytest.MonkeyPatch
) -> None:
    (project.root / "tests" / "unit").mkdir(parents=True)
    (project.root / "tests" / "unit" / "test_pins_env.py").write_text(PIN_TESTS)
    dotfile = project.root / ("." + "env")
    dotfile.write_text("XEWE_TEST_BUTTONS_PIN=7\nXEWE_TEST_PINS_ADC_PIN=5\n")
    monkeypatch.setenv("XEWE_TEST_PINS_ADC_PIN", "3")
    res = _run(pytester, project)
    res.assert_outcomes(passed=1)


# --------------------------------------------------------------------------- JUnit properties (release card)

CARD_TESTS = """
import pytest


def test_status(serial):
    serial.command("ping", expect="pong 1", timeout=1)


def test_compiles(compiled):
    assert compiled.is_file()


@pytest.mark.board
def test_marked_only():
    pass
"""


def _junit(path: Path) -> dict[str, tuple[dict[str, str], list[str]]]:
    """name -> (properties, child tags) of every testcase."""
    out = {}
    for case in ET.parse(path).getroot().iter("testcase"):
        props = {p.get("name", ""): p.get("value", "") for p in case.iter("property")}
        out[case.get("name", "")] = (props, [c.tag for c in case if c.tag != "properties"])
    return out


def _run_junit(pytester: pytest.Pytester, p: Paths, xml: Path, *args: str) -> pytest.RunResult:
    return _run(pytester, p, "-o", "junit_family=xunit1", f"--junitxml={xml}", *args)


def test_junit_properties_without_a_board(pytester: pytest.Pytester, project: Paths, tmp_path: Path) -> None:
    write_tests(project.root, CARD_TESTS)
    xml = tmp_path / "out.xml"
    res = _run_junit(pytester, project, xml)
    res.assert_outcomes(passed=3, skipped=1)
    res.stdout.fnmatch_lines(["xewe test: 1 unit passed, 1 compiled, not run, 0 failed, 2 board passed"])
    cases = _junit(xml)
    assert cases["test_logic"][0] == {
        "xewe_kind": "unit",
        "xewe_needs": "none",
        "xewe_nodeid": "tests/unit/test_logic.py::test_logic",
        "xewe_chip": "c3",
    }
    status, tags = cases["test_status"]
    assert tags == ["skipped"] and status["xewe_kind"] == "board" and status["xewe_needs"] == "hardware"
    assert "xewe_board" not in status
    compiles, tags = cases["test_compiles"]
    assert tags == [] and compiles["xewe_kind"] == "board" and compiles["xewe_needs"] == "compile"
    assert "xewe_board" not in compiles
    marked, _ = cases["test_marked_only"]
    assert marked["xewe_kind"] == "board" and marked["xewe_needs"] == "none"
    skipped = ET.parse(xml).getroot().find(".//testcase[@name='test_status']/skipped")
    assert skipped is not None and skipped.get("message") == "compiled, not run: no board attached"


def test_junit_properties_under_xunit2_too(
    pytester: pytest.Pytester, project_with_tests: Paths, tmp_path: Path
) -> None:
    xml = tmp_path / "out.xml"
    _run(pytester, project_with_tests, f"--junitxml={xml}")
    assert _junit(xml)["test_logic"][0]["xewe_kind"] == "unit"


def _banner_board(project: Paths, stamp: str | None) -> Callable[[], FakeSerial]:
    """A booted board whose banner shows ``stamp`` (None: meta.json's own timestamp)."""

    def factory() -> FakeSerial:
        meta = json.loads((project.out_dir("c3") / "meta.json").read_text())
        shown = stamp or meta["timestamp_param"]
        banner = f"| xewe-os | Version 2.0.15\r\n| Build Timestamp {shown} |\r\n".encode()
        return FakeSerial(chunks=[b"ESP-ROM:esp32c3\r\n" + banner + b"System Setup Complete\r\n"], script=pinger())

    return factory


@pytest.mark.parametrize(("stamp", "match"), [(None, "true"), ("2020-01-01T00:00:00Z", "false")])
def test_junit_board_properties_on_a_fake_board(
    pytester: pytest.Pytester,
    project: Paths,
    board_attached: Callable[[Callable[[], Any]], None],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    stamp: str | None,
    match: str,
) -> None:
    write_tests(project.root, CARD_TESTS)
    board_attached(_banner_board(project, stamp))
    monkeypatch.setattr(boards, "select", lambda *a, **k: Board(PORT, "c3", serial_number="A0:F2:62:F1:5E:DC"))
    xml = tmp_path / "out.xml"
    res = _run_junit(pytester, project, xml)
    res.assert_outcomes(passed=4)
    image = project.out_dir("c3") / "2.0.15-c3-xewe-os.bin"
    sha = hashlib.sha256(image.read_bytes()).hexdigest()
    cases = _junit(xml)
    status, tags = cases["test_status"]
    assert tags == []
    assert status["xewe_board"] == f"c3 sn=A0:F2:62:F1:5E:DC port={PORT}"
    assert status["xewe_fw_sha256"] == sha
    assert status["xewe_fw_match"] == match
    assert "xewe_board" not in cases["test_compiles"][0]  # compile only, even with a board attached
    assert "xewe_board" not in cases["test_logic"][0]


def test_junit_board_properties_without_a_serial_number(
    pytester: pytest.Pytester, project: Paths, board_attached: Callable[[Callable[[], Any]], None], tmp_path: Path
) -> None:
    write_tests(project.root, CARD_TESTS, unit=None)
    board_attached(lambda: FakeSerial(chunks=[BOOTED], script=pinger()))
    xml = tmp_path / "out.xml"
    _run_junit(pytester, project, xml).assert_outcomes(passed=3)
    status = _junit(xml)["test_status"][0]
    assert status["xewe_board"] == f"c3 sn=- port={PORT}"
    assert status["xewe_fw_match"] == "unknown"  # no Build Timestamp in the banner


def test_junit_nodeid_of_a_module_test_is_its_source_path(project_with_tests: Paths, tmp_path: Path) -> None:
    _wifi_tests(project_with_tests)
    xml = tmp_path / "out.xml"
    assert main(["test", "--unit-only", "--", "-o", "junit_family=xunit1", f"--junitxml={xml}"]) == 0
    ids = sorted(props["xewe_nodeid"] for props, _ in _junit_all(xml))
    assert ids == ["modules/wifi/tests/unit/test_logic.py::test_logic", "tests/unit/test_logic.py::test_logic"]


def _junit_all(path: Path) -> list[tuple[dict[str, str], list[str]]]:
    return [
        ({p.get("name", ""): p.get("value", "") for p in case.iter("property")}, [c.tag for c in case])
        for case in ET.parse(path).getroot().iter("testcase")
    ]


def test_banner_match() -> None:
    assert plugin.banner_match(["Build Timestamp 2026-10-12T21:30:48Z"], Path("/nonexistent/x.bin")) == "unknown"


def test_banner_match_reads_meta(tmp_path: Path) -> None:
    (tmp_path / "meta.json").write_text(json.dumps({"timestamp_param": "2026-10-12T21:30:48Z"}))
    image = tmp_path / "x.bin"
    old = "│ Build Timestamp 2026-10-12T20:00:00Z │"
    assert plugin.banner_match([old, "Build Timestamp 2026-10-12T21:30:48Z"], image) == "true"  # the last boot wins
    assert plugin.banner_match(["Build Timestamp 2026-10-12T21:30:48Z", old], image) == "false"
    assert plugin.banner_match(["Version 3.0.0"], image) == "unknown"


# --------------------------------------------------------------------------- xewe test --define
# `xewe test --define` and XEWE_TEST_DEFINES: the session builds, reuses and flashes the image for
# those defines (`compiled` checks build.stamp like `xewe flash`).

BUILD_INFO = "XeWeBuildInfo/src/XeWeBuildInfo.h"
STAMPED_TEST = """
def test_stamped(compiled):
    assert compiled.is_file()
"""


@pytest.fixture
def stamped(project: Paths) -> Paths:
    """``project`` with one board test that needs only the ``compiled`` image."""
    (project.root / "tests" / "board").mkdir(parents=True)
    (project.root / "tests" / "board" / "test_stamped.py").write_text(STAMPED_TEST)
    return project


@pytest.fixture
def build_calls(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, str]]:
    """The ``defines`` of every ``build_chip`` call (the build still runs, with the fake arduino-cli)."""
    seen: list[dict[str, str]] = []
    real = build.build_chip

    def spy(p: Paths, lock: Any, chip: str, defines: dict[str, str] | None = None, *a: Any, **k: Any) -> Any:
        seen.append(dict(defines or {}))
        return real(p, lock, chip, defines, *a, **k)

    monkeypatch.setattr(build, "build_chip", spy)
    return seen


def _header(p: Paths) -> str:
    return (p.gen_dir("c3") / BUILD_INFO).read_text()


def test_define_reaches_build_chip(stamped: Paths, build_calls: list[dict[str, str]]) -> None:
    assert main(["test", "--define", "XEWE_MODULE_LED_PIN_DATA=48", "--define", "LED_COUNT=8"]) == 0
    assert build_calls == [{"XEWE_MODULE_LED_PIN_DATA": "48", "LED_COUNT": "8"}]
    assert "#define XEWE_MODULE_LED_PIN_DATA 48" in _header(stamped)
    stamp = (stamped.root / BIN).parent / build.STAMP_NAME
    key = build.build_key("c3", "2.0.15", {"XEWE_MODULE_LED_PIN_DATA": "48", "LED_COUNT": "8"})
    assert stamp.read_text().strip() == key


def test_bad_define_is_usage_error(stamped: Paths, build_calls: list[dict[str, str]]) -> None:
    assert main(["test", "--define", "1BAD=x"]) == 2
    assert main(["test", "--define", "NOVALUE"]) == 2
    assert build_calls == []


def test_same_stamp_is_not_rebuilt(stamped: Paths, build_calls: list[dict[str, str]]) -> None:
    assert main(["build", "--chip", "c3", "--define", "XEWE_MODULE_LED_PIN_DATA=48"]) == 0
    built = build_calls.pop()
    image = (stamped.root / BIN).read_bytes()
    assert main(["test", "--define", "XEWE_MODULE_LED_PIN_DATA=48"]) == 0
    assert build_calls == []  # the stamp matches: the image from `xewe build` is reused
    assert (stamped.root / BIN).read_bytes() == image
    assert main(["test"]) == 0  # other defines (none): stale, rebuilt
    assert build_calls == [{}]
    assert "XEWE_MODULE_LED_PIN_DATA" not in _header(stamped)
    assert built == {"XEWE_MODULE_LED_PIN_DATA": "48"}


def test_env_default_and_flag_override(
    stamped: Paths, build_calls: list[dict[str, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(plugin.DEFINES_VAR, "XEWE_MODULE_LED_PIN_DATA=48  LED_COUNT=8")
    assert main(["test"]) == 0
    assert build_calls == [{"XEWE_MODULE_LED_PIN_DATA": "48", "LED_COUNT": "8"}]
    assert main(["test"]) == 0
    assert len(build_calls) == 1  # same defines from the environment: up to date
    assert main(["test", "--define", "XEWE_MODULE_LED_PIN_DATA=13"]) == 0
    assert build_calls[-1] == {"XEWE_MODULE_LED_PIN_DATA": "13"}  # the flag replaces the variable
    monkeypatch.setenv(plugin.DEFINES_VAR, "LED_COUNT")
    assert main(["test"]) == 2  # a bad item in the variable is a usage error too
    assert len(build_calls) == 2


def test_dotenv_default(stamped: Paths, build_calls: list[dict[str, str]]) -> None:
    (stamped.root / ("." + "env")).write_text(f"{plugin.DEFINES_VAR}=XEWE_MODULE_LED_PIN_DATA=48\n")
    assert main(["test"]) == 0
    assert build_calls == [{"XEWE_MODULE_LED_PIN_DATA": "48"}]


def test_session_defines() -> None:
    env = {plugin.DEFINES_VAR: " A=1\tB=x=y  C= "}
    assert plugin.session_defines([], env) == {"A": "1", "B": "x=y", "C": ""}
    assert plugin.session_defines(["D=2"], env) == {"D": "2"}
    assert plugin.session_defines([], {}) == {}
    with pytest.raises(XeWeError, match=f"^{plugin.DEFINES_VAR}: --define expects KEY=VALUE") as exc:
        plugin.session_defines([], {plugin.DEFINES_VAR: "A=1 2B=3"})
    assert exc.value.code == 2


def test_session_flashes_the_define_image(
    stamped: Paths, build_calls: list[dict[str, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """With a board: the image built with the defines is flashed, not a default one."""
    assert main(["build", "--chip", "c3", "--define", "XEWE_MODULE_LED_PIN_DATA=48"]) == 0
    build_calls.clear()
    flashed: list[Path] = []

    def write_image(cmd: list[str], board: Board, chip: str, binary: Path, *a: Any, **k: Any) -> None:
        flashed.append(binary)

    monkeypatch.setattr(boards, "select", lambda *a, **k: Board(PORT, "c3"))
    monkeypatch.setattr(flash, "esptool_cmd", lambda p: ["esptool"])
    monkeypatch.setattr(flash, "write_image", write_image)
    monkeypatch.setattr(serialio.serial, "Serial", lambda: FakeSerial(chunks=[b"System Setup Complete\r\n"]))
    board_tests = stamped.root / "tests" / "board"
    (board_tests / "test_stamped.py").unlink()
    (board_tests / "test_flashed.py").write_text("def test_flashed(firmware):\n    assert firmware.chip == 'c3'\n")
    assert main(["test", "--define", "XEWE_MODULE_LED_PIN_DATA=48"]) == 0
    assert build_calls == []
    assert flashed == [stamped.root / BIN]
    assert "#define XEWE_MODULE_LED_PIN_DATA 48" in _header(stamped)


@pytest.mark.parametrize("prebuilt", [True, False], ids=["reused", "built"])
def test_defines_session_fw_sha_is_the_flashed_image(
    stamped: Paths, build_calls: list[dict[str, str]], monkeypatch: pytest.MonkeyPatch, prebuilt: bool
) -> None:
    """Release card (doc/spec.md §14): with defines, the JUnit ``xewe_fw_sha256`` of a board test is
    the sha256 of the image the session flashed, whether ``compiled`` reused it (``build.stamp``
    matches) or built it, so render's fw sha check against the flash step does not fire."""
    if prebuilt:
        assert main(["build", "--chip", "c3", "--define", "XEWE_MODULE_LED_PIN_DATA=48"]) == 0
    build_calls.clear()
    flashed: list[bytes] = []

    def write_image(cmd: list[str], board: Board, chip: str, binary: Path, *a: Any, **k: Any) -> None:
        flashed.append(binary.read_bytes())

    monkeypatch.setattr(boards, "select", lambda *a, **k: Board(PORT, "c3", serial_number="AA:BB"))
    monkeypatch.setattr(flash, "esptool_cmd", lambda p: ["esptool"])
    monkeypatch.setattr(flash, "write_image", write_image)
    monkeypatch.setattr(serialio.serial, "Serial", lambda: FakeSerial(chunks=[b"System Setup Complete\r\n"]))
    board_tests = stamped.root / "tests" / "board"
    (board_tests / "test_stamped.py").unlink()
    (board_tests / "test_flashed.py").write_text("def test_flashed(firmware):\n    assert firmware.chip == 'c3'\n")
    junit = stamped.root / "build" / "junit.xml"
    args = ["test", "--define", "XEWE_MODULE_LED_PIN_DATA=48", "--"]
    assert main([*args, f"--junitxml={junit}", "-o", "junit_family=xunit1"]) == 0
    assert build_calls == ([] if prebuilt else [{"XEWE_MODULE_LED_PIN_DATA": "48"}])
    (image,) = flashed
    assert image == (stamped.root / BIN).read_bytes()
    props = {p.get("name"): p.get("value") for p in ET.parse(junit).iter("property")}
    assert props["xewe_kind"] == "board" and props["xewe_board"] == f"c3 sn=AA:BB port={PORT}"
    # the flash step's fw_sha256 (`release-card record --kind flash`) is the sha256 of out/<bin>
    assert props["xewe_fw_sha256"] == hashlib.sha256(image).hexdigest()
    (row,) = releasecard.parse_junit(junit, "board-c3")
    assert row.result == releasecard.PASS_HARDWARE  # what render compares with the flash step's sha


def test_recorded_flash_and_test_session_agree_on_the_image(
    stamped: Paths, build_calls: list[dict[str, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The prepare flow per chip (doc/spec.md §14): `release-card record --kind flash -- xewe flash
    --chip C --define …`, then `xewe test --chip C --define …`. The session reuses the image the
    flash step built (no build) and its JUnit `xewe_fw_sha256` is the step's `fw_sha256`, so render's
    fw sha check passes. The flash command runs as a subprocess with board access off (it builds,
    then exits 4)."""
    logs = stamped.root / "releases" / "2.0.15" / "logs"
    logs.mkdir(parents=True)
    (logs / releasecard.RUN_FILE).write_text(json.dumps({"run_id": "r", "started": "", "steps": []}))
    monkeypatch.setenv("XEWE_NO_BOARD", "1")
    flash_cmd = [sys.executable, "-m", "xewe", "flash", "--chip", "c3", "--define", "XEWE_MODULE_LED_PIN_DATA=48"]
    record = ["release-card", "record", "--dir", str(logs), "--root", str(stamped.root), "--kind", "flash"]
    assert main([*record, "--chip", "c3", "--", *flash_cmd]) == 1  # xewe flash: built, board access off (4)
    (step,) = json.loads((logs / releasecard.RUN_FILE).read_text())["steps"]
    assert step["exit"] == 4 and step["firmware"] == BIN
    assert step["fw_sha256"] == hashlib.sha256((stamped.root / BIN).read_bytes()).hexdigest()
    assert json.loads((logs / step["meta"]).read_text())["config"] == {"XEWE_MODULE_LED_PIN_DATA": 48}
    monkeypatch.delenv("XEWE_NO_BOARD")

    flashed: list[bytes] = []

    def write_image(cmd: list[str], board: Board, chip: str, binary: Path, *a: Any, **k: Any) -> None:
        flashed.append(binary.read_bytes())

    monkeypatch.setattr(boards, "select", lambda *a, **k: Board(PORT, "c3", serial_number="AA:BB"))
    monkeypatch.setattr(flash, "esptool_cmd", lambda p: ["esptool"])
    monkeypatch.setattr(flash, "write_image", write_image)
    monkeypatch.setattr(serialio.serial, "Serial", lambda: FakeSerial(chunks=[b"System Setup Complete\r\n"]))
    board_tests = stamped.root / "tests" / "board"
    (board_tests / "test_stamped.py").unlink()
    (board_tests / "test_flashed.py").write_text("def test_flashed(firmware):\n    assert firmware.chip == 'c3'\n")
    junit = stamped.root / "build" / "junit.xml"
    args = ["test", "--chip", "c3", "--define", "XEWE_MODULE_LED_PIN_DATA=48", "--"]
    assert main([*args, f"--junitxml={junit}", "-o", "junit_family=xunit1"]) == 0
    assert build_calls == []  # the image the flash step built
    props = {p.get("name"): p.get("value") for p in ET.parse(junit).iter("property")}
    assert props["xewe_fw_sha256"] == step["fw_sha256"] == hashlib.sha256(flashed[0]).hexdigest()
