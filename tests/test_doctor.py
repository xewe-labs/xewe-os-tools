from pathlib import Path

import pytest

from conftest import write_project
from xewe import doctor, lockfile
from xewe.cli import main
from xewe.project import Paths


def test_doctor_reports_missing_pieces(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_ports: None,
                                       capsys: pytest.CaptureFixture[str]) -> None:
    p = write_project(tmp_path / "proj")
    monkeypatch.chdir(p.root)
    assert main(["doctor"]) == 3
    out = capsys.readouterr().out
    assert "error  setup" in out and "error  arduino-cli    arduino data dir" in out
    assert "no board attached" in out


def test_doctor_after_setup(project: Paths, fake_esptool: object, capsys: pytest.CaptureFixture[str]) -> None:
    project.default_arduino_data.mkdir(parents=True)
    (project.default_arduino_data / "fake-core.json").write_text('"3.3.12"')
    assert main(["doctor"]) == 0
    out = capsys.readouterr().out
    assert "ok     esp32 core" in out
    assert "ok     arduino-cli    1.5.1" in out
    assert "ok     esptool        5.3.1" in out


def test_checks_never_raise(project: Paths) -> None:
    checks = doctor.checks(project, lockfile.load(project.lock))
    assert {c.name for c in checks} >= {"python", "git", "setup", "toolchain", "arduino-cli", "esptool", "lock", "disk", "boards"}


def test_doctor_reports_shared_toolchain(project: Paths, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    tools = tmp_path / "xewe-home" / "build-tools"
    main(["doctor"])
    assert f"warn   toolchain      {tools} missing" in capsys.readouterr().out
    (tools / "bin").mkdir(parents=True)
    (tools / "bin" / "arduino-cli-1.5.1").write_text("")
    main(["doctor"])
    assert f"{tools}: arduino-cli 1.5.1 present, esp32 core 3.3.12 missing" in capsys.readouterr().out
    (tools / "arduino15" / "packages" / "esp32" / "hardware" / "esp32" / "3.3.12").mkdir(parents=True)
    main(["doctor"])
    assert f"ok     toolchain      {tools}: arduino-cli 1.5.1 present, esp32 core 3.3.12 present" in capsys.readouterr().out
