import json
import subprocess
import sys
from pathlib import Path

import pytest

from xewe import __version__, fetch, lockfile
from xewe.cli import main
from xewe.project import Paths

COMMANDS = ["setup", "build", "flash", "serial", "test", "run", "boards", "modules", "lock", "clean", "doctor", "release"]
SUBCOMMANDS = ["modules list", "modules select", "modules validate", "modules generate", "lock show", "lock update"]


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
    assert proc.returncode == 0 and "0.1.0" in proc.stdout


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


def test_clean(project: Paths) -> None:
    assert main(["build", "--chip", "c3"]) == 0
    assert main(["clean"]) == 0
    assert not project.out.exists() and not project.gen.exists() and not project.cache.exists()
    assert project.build_config.exists()
    project.venv.mkdir()
    assert main(["clean", "--all", "--modules"]) == 0
    assert sorted(c.name for c in project.build.iterdir()) == [".venv"]
    assert not project.src_modules.exists()
    assert main(["build"]) == 3


def test_lock_show_marks_drift(project: Paths, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["lock", "show"]) == 0
    out = capsys.readouterr().out
    assert "  core " in out and "\n! " not in out
    lock = lockfile.load(project.lock)
    lock.core.ref = "1.1.0"
    lockfile.save(lock, project.lock)
    assert main(["lock", "show", "--json"]) == 0
    rows = {r["name"]: r for r in json.loads(capsys.readouterr().out)}
    assert rows["core"]["drift"] and not rows["modules"]["drift"]


def test_lock_update(project: Paths, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(fetch, "git", lambda *a, **k: f"{'0' * 40}\trefs/tags/1.2.0\n{'0' * 40}\trefs/tags/1.10.0\n")
    assert main(["lock", "update", "core"]) == 0
    assert '+ref = "1.10.0"' in capsys.readouterr().out
    assert lockfile.load(project.lock).core.ref == "1.10.0"
    assert main(["lock", "update", "modules", "--to", "v2.0.0"]) == 0
    assert lockfile.load(project.lock).modules.ref == "v2.0.0"
    assert main(["lock", "update", "--to", "x"]) == 2
    assert main(["lock", "update", "bogus"]) == 2
