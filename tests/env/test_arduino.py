from pathlib import Path

import pytest

from xewe.build import chips
from xewe.cli import main
from xewe.env import arduino
from xewe.env.project import Paths

GOLDEN_FQBN = {
    "c3": "esp32:esp32:esp32c3:CDCOnBoot=cdc,CPUFreq=160,DebugLevel=none,EraseFlash=all,FlashMode=qio,FlashSize=4M,PartitionScheme=no_ota,UploadSpeed=921600",
    "c6": "esp32:esp32:esp32c6:CDCOnBoot=cdc,CPUFreq=160,DebugLevel=none,EraseFlash=all,FlashMode=qio,FlashSize=4M,PartitionScheme=no_ota,UploadSpeed=921600",
    "s3": "esp32:esp32:esp32s3:CDCOnBoot=cdc,CPUFreq=160,DebugLevel=none,EraseFlash=all,FlashMode=qio,FlashSize=4M,PartitionScheme=no_ota,UploadSpeed=921600",
}


@pytest.mark.parametrize("chip", ["c3", "c6", "s3"])
def test_fqbn_golden(chip: str) -> None:
    assert chips.get(chip).fqbn == GOLDEN_FQBN[chip]
    assert "JTAGAdapter" not in chips.get(chip).fqbn


@pytest.mark.parametrize("chip", ["c3", "c6", "s3"])
def test_compile_argv_per_chip(chip: str, tmp_path: Path) -> None:
    p = Paths(tmp_path / "xewe-os")
    argv = arduino.compile_argv("/x/bin/arduino-cli-1.5.1", chips.get(chip), p, p.root)
    assert argv == [
        "/x/bin/arduino-cli-1.5.1", "compile",
        "--fqbn", GOLDEN_FQBN[chip],
        "--build-path", f"build/builds/{chip}/cache",
        "--libraries", "build/libraries",
        "--library", "build/modules",
        "--library", f"build/builds/{chip}/gen/XeWeBuildInfo",
        "--warnings", "default",
        "--jobs", "0",
        ".",
    ]


@pytest.mark.parametrize("chip", ["c3", "c6", "s3"])
def test_dry_run_prints_exact_command(project: Paths, chip: str, capsys: pytest.CaptureFixture[str],
                                      fake_cli: object) -> None:
    assert main(["build", "--chip", chip, "--dry-run"]) == 0
    out = capsys.readouterr().out.strip()
    assert out.endswith(
        f"compile --fqbn {GOLDEN_FQBN[chip]} --build-path build/builds/{chip}/cache --libraries build/libraries "
        f"--library build/modules --library build/builds/{chip}/gen/XeWeBuildInfo --warnings default --jobs 0 ."
    )
    assert fake_cli() == []  # nothing ran
    assert not project.out_dir(chip).exists()


def test_dry_run_all_chips_without_setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                         capsys: pytest.CaptureFixture[str], no_ports: None) -> None:
    from conftest import write_project

    p = write_project(tmp_path / "renamed-repo", ino="xewe-os")
    monkeypatch.chdir(p.root)
    assert main(["build", "--all-chips", "--dry-run"]) == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 3
    cli = tmp_path / "xewe-home" / "build-tools" / "bin" / "arduino-cli-1.5.1"  # the shared toolchain's cli
    assert lines[0].startswith(f"{cli} compile --fqbn " + GOLDEN_FQBN["c3"])
    assert all(line.endswith("build/tmp/sketch/xewe-os") for line in lines)


def test_env_is_isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p = Paths(tmp_path / "proj")
    tools = tmp_path / "xewe-home" / "build-tools"
    env = arduino.env(p, p.default_arduino_data)
    assert env["ARDUINO_DIRECTORIES_DATA"] == str(tools / "arduino15")
    assert env["ARDUINO_DIRECTORIES_USER"] == str(tools / "arduino-user")
    assert env["ARDUINO_DIRECTORIES_DOWNLOADS"] == str(tools / "downloads")
    monkeypatch.setenv("XEWE_CACHE", str(tmp_path / "cache"))  # still overrides the downloads dir
    assert arduino.env(p, p.default_arduino_data)["ARDUINO_DIRECTORIES_DOWNLOADS"] == str(tmp_path / "cache")
    assert env["ARDUINO_BOARD_MANAGER_ADDITIONAL_URLS"].endswith("package_esp32_index.json")
    assert ".arduino15" not in " ".join(v for k, v in env.items() if k.startswith("ARDUINO_"))


def test_parse_core_list() -> None:
    assert arduino.parse_core_list('{"platforms":[{"id":"esp32:esp32","installed_version":"3.3.12"}]}') == "3.3.12"
    assert arduino.parse_core_list('{"platforms":[]}') is None
    assert arduino.parse_core_list("{}") is None
