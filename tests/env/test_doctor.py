from pathlib import Path

import pytest

from conftest import write_project
from xewe.cli import main
from xewe.env import doctor
from xewe.env.project import Paths
from xewe.modules import lockfile


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


@pytest.mark.parametrize("installed", [True, False])
def test_rosetta_check_on_apple_silicon(monkeypatch: pytest.MonkeyPatch, installed: bool) -> None:
    monkeypatch.setattr(doctor.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(doctor.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(doctor, "_rosetta_installed", lambda: installed)
    c = doctor._rosetta()
    assert c is not None and c.name == "rosetta"
    if installed:
        assert c.level == doctor.OK
    else:
        assert c.level == doctor.WARN
        assert c.detail == ("Rosetta 2 is not installed; the esp32 core's ctags binary needs it: "
                            "softwareupdate --install-rosetta --agree-to-license")


@pytest.mark.parametrize("system,machine", [("Darwin", "x86_64"), ("Linux", "arm64"), ("Linux", "x86_64")])
def test_rosetta_check_skipped_elsewhere(monkeypatch: pytest.MonkeyPatch, system: str, machine: str) -> None:
    monkeypatch.setattr(doctor.platform, "system", lambda: system)
    monkeypatch.setattr(doctor.platform, "machine", lambda: machine)
    monkeypatch.setattr(doctor, "_rosetta_installed", lambda: pytest.fail("probe must not run"))
    assert doctor._rosetta() is None


@pytest.mark.parametrize("codes,expected", [([0], True), ([1, 0], True), ([1, 1], False)])
def test_rosetta_probe(monkeypatch: pytest.MonkeyPatch, codes: list[int], expected: bool) -> None:
    seen: list[list[str]] = []

    class Proc:
        def __init__(self, rc: int) -> None:
            self.returncode = rc

    def fake_run(cmd: list[str], **kw: object) -> Proc:
        seen.append(cmd)
        return Proc(codes[len(seen) - 1])
    monkeypatch.setattr(doctor.subprocess, "run", fake_run)
    assert doctor._rosetta_installed() is expected
    assert seen[0] == ["/usr/bin/pgrep", "-q", "oahd"]
    if len(seen) > 1:
        assert seen[1] == ["arch", "-x86_64", "/usr/bin/true"]


def test_doctor_prints_rosetta_warning(project: Paths, monkeypatch: pytest.MonkeyPatch,
                                       capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(doctor.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(doctor.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(doctor, "_rosetta_installed", lambda: False)
    main(["doctor"])
    assert "warn   rosetta        Rosetta 2 is not installed" in capsys.readouterr().out
