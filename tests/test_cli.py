import subprocess
import sys
from pathlib import Path

import pytest

from xewe import __version__
from xewe.cli import main
from xewe.env.project import Paths

COMMANDS = ["setup", "build", "flash", "serial", "provision", "test", "run", "boards", "modules", "manifest", "clean", "doctor", "release"]
SUBCOMMANDS = ["modules list", "modules select", "modules validate", "modules generate", "manifest show", "manifest update"]


def test_every_command_has_help(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    for cmd in COMMANDS:
        assert cmd in out
    for cmd in [*COMMANDS, *SUBCOMMANDS]:
        with pytest.raises(SystemExit) as exc:
            main([*cmd.split(), "--help"])
        assert exc.value.code == 0


def test_build_help_shows_chip(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        main(["build", "--help"])
    out = capsys.readouterr().out
    assert "--chip" in out and "--dry-run" in out


def test_version_flag(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        main(["--version"])
    assert __version__ in capsys.readouterr().out


def test_python_dash_m() -> None:
    proc = subprocess.run([sys.executable, "-m", "xewe", "--version"], capture_output=True, text=True)
    assert proc.returncode == 0 and "0.1.1" in proc.stdout


@pytest.mark.parametrize("argv", [["build", "--chip", "esp32"], ["build", "--chip", "c3", "--all-chips"], ["nope"]])
def test_usage_errors_exit_2(project: Paths, argv: list[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(argv)
    assert exc.value.code == 2


def test_no_project_exit_2(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    assert main(["build"]) == 2


def test_verbose_prints_commands(project: Paths, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--verbose", "build", "--chip", "c3"]) == 0
    err = capsys.readouterr().err
    assert "$ " in err and "compile --fqbn" in err and "Sketch uses" in err
