"""The hard no-board switch (``--no-board`` / ``XEWE_NO_BOARD=1``) and ``-v`` after the command."""

from pathlib import Path

import pytest
from serial.tools import list_ports

from xewe import boards, serialio
from xewe.cli import main
from xewe.project import Paths
from xewe.report import BOARD_DISABLED, XeweError

BIN = "build/out/c3/2.0.15-c3-xewe-os.bin"
TESTS = '''
import pytest


@pytest.mark.host
def test_logic():
    assert 1 + 1 == 2


def test_status(serial):
    serial.command("$system status", expect="Uptime", timeout=1)
'''


@pytest.fixture
def attached(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> str:
    """One Espressif board that must never be looked at: listing ports fails the test."""
    port = tmp_path / "ttyACM0"
    port.write_text("")
    monkeypatch.setattr(list_ports, "comports", lambda: pytest.fail("ports listed with board access disabled"))
    return str(port)


@pytest.fixture
def disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XEWE_NO_BOARD", "1")


def test_discovery_returns_nothing(project: Paths, attached: str, disabled: None) -> None:
    assert boards.scan(project) == []
    assert boards.select(project) is None
    assert boards.select(project, port=attached) is None
    assert not boards.port_exists(attached)
    assert not project.boards_toml.exists()  # nothing written either


def test_console_never_opens(disabled: None, attached: str) -> None:
    with pytest.raises(XeweError) as exc:
        serialio.Console(attached).open()
    assert exc.value.code == 4 and str(exc.value) == BOARD_DISABLED


@pytest.mark.parametrize("flag", [True, False])
def test_flash_builds_then_exits_4(project: Paths, attached: str, fake_esptool, monkeypatch: pytest.MonkeyPatch,
                                   capsys: pytest.CaptureFixture[str], flag: bool) -> None:
    monkeypatch.setenv("XEWE_PORT", attached)
    if not flag:
        monkeypatch.setenv("XEWE_NO_BOARD", "1")
    assert main(["flash", *(["--no-board"] if flag else [])]) == 4
    captured = capsys.readouterr()
    assert f"compiled, not run: {BOARD_DISABLED} (c3, {BIN})" in captured.out
    assert f"error: {BOARD_DISABLED}" in captured.err
    assert (project.root / BIN).is_file()
    assert fake_esptool() == []


@pytest.mark.parametrize("argv", [["serial"], ["serial", "--send", "x"], ["provision"], ["run"], ["boards"],
                                  ["boards", "--json"]])
def test_board_commands_exit_4(project: Paths, attached: str, disabled: None, argv: list[str],
                               capsys: pytest.CaptureFixture[str]) -> None:
    assert main([*argv, "--port", attached] if argv[0] in ("serial", "provision", "run") else argv) == 4
    assert f"error: {BOARD_DISABLED}" in capsys.readouterr().err


@pytest.mark.parametrize("cmd", ["serial", "provision", "run", "boards", "flash"])
def test_no_board_flag_on_every_board_command(project: Paths, attached: str, cmd: str) -> None:
    assert main([cmd, "--no-board"]) == 4


def test_boards_override_still_works(project: Paths, disabled: None, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["boards", "--set-port", "/dev/ttyACM9"]) == 0
    assert "override: {'port': '/dev/ttyACM9'}" in capsys.readouterr().out


def test_build_is_unaffected(project: Paths, attached: str, disabled: None) -> None:
    assert main(["build", "--chip", "c3"]) == 0


@pytest.fixture
def proj(project: Paths) -> Paths:
    (project.root / "tests").mkdir()
    (project.root / "tests" / "test_fw.py").write_text(TESTS)
    return project


def test_xewe_test_no_board_is_compiled_not_run(proj: Paths, attached: str, capsys: pytest.CaptureFixture[str],
                                                monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XEWE_PORT", attached)
    assert main(["test", "--no-board"]) == 0
    out = capsys.readouterr().out
    assert f"compiled, not run ({BOARD_DISABLED}, chip c3)" in out
    assert "xewe test: 1 host passed, 1 compiled, not run, 0 failed" in out
    assert main(["test", "--no-board", "--require-board"]) == 4


def test_plugin_option(pytester: pytest.Pytester, proj: Paths, attached: str) -> None:
    res = pytester.runpytest("--xewe-project", str(proj.root), "--xewe-chip", "c3", "-p", "no:cacheprovider",
                             "--xewe-no-board", str(proj.root / "tests"))
    res.assert_outcomes(passed=1, skipped=1)
    assert res.ret == 0


# --- -v/--verbose after the command

def test_verbose_after_command(project: Paths, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["build", "--chip", "c3", "-v"]) == 0
    assert "compile --fqbn" in capsys.readouterr().err
    assert main(["build", "--chip", "c3", "--clean"]) == 0
    assert "compile --fqbn" not in capsys.readouterr().err
    assert main(["-v", "build", "--chip", "c3", "--clean"]) == 0  # the global flag is not reset by the subcommand
    assert "compile --fqbn" in capsys.readouterr().err


def test_verbose_after_test_and_nested_commands(proj: Paths) -> None:
    assert main(["test", "--host-only", "-v"]) == 0
    assert main(["test", "-v", "--host-only", "--", "-q"]) == 0
    assert main(["lock", "show", "--verbose"]) == 0
    assert main(["modules", "list", "-v"]) == 0
