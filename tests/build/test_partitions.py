import json
import shutil
from pathlib import Path

import pytest

from conftest import ESP32_CORE
from xewe.build import chips, partitions
from xewe.build import compile as build
from xewe.cli import main
from xewe.env.project import Paths
from xewe.modules import lockfile
from xewe.report import XeWeError


def set_scheme(p: Paths, scheme: str) -> None:
    """Add ``[build] partition_scheme`` to the project's xewe.toml."""
    p.lock.write_text(p.lock.read_text() + f'\n[build]\npartition_scheme = "{scheme}"\n')


def compile_fqbns(calls: list[dict]) -> list[str]:
    return [c["argv"][2] for c in calls if c["argv"][0] == "compile"]


@pytest.mark.parametrize(
    ("csv", "size"),
    [("no_ota", 0x200000), ("huge_app", 0x300000), ("default", 0x140000), ("min_spiffs", 0x1E0000)],
)
def test_app_size_from_the_core_csv(csv: str, size: int) -> None:
    assert partitions.app_size((ESP32_CORE / "tools" / "partitions" / f"{csv}.csv").read_text()) == size


def test_parse_csv_suffixes_and_empty_offsets() -> None:
    rows = partitions.parse_csv(
        "# Name, Type, SubType, Offset, Size\nnvs, data, nvs, , 20K,\nfactory, app, factory, , 3M,\n"
    )
    assert rows == [("nvs", "data", 0x9000, 0x5000), ("factory", "app", 0x10000, 0x300000)]
    assert partitions.app_size("nvs, data, nvs, 0x9000, 0x5000,\n") is None


def test_offered_reads_one_board() -> None:
    schemes = partitions.offered((ESP32_CORE / "boards.txt").read_text(), "esp32c6")
    assert schemes["huge_app"] == "huge_app" and schemes["no_ota"] == "no_ota" and schemes["custom"] == ""


@pytest.mark.parametrize("chip", ["c3", "c6", "s3"])
def test_resolve_per_chip(chip: str) -> None:
    core = ESP32_CORE
    assert partitions.resolve(core, chips.get(chip), "no_ota") == partitions.Scheme("no_ota", "no_ota", 0x200000)
    assert partitions.resolve(core, chips.get(chip), "huge_app") == partitions.Scheme("huge_app", "huge_app", 0x300000)
    assert partitions.resolve(core, chips.get(chip), "min_spiffs") == partitions.Scheme(
        "min_spiffs", "min_spiffs", 0x1E0000
    )


@pytest.mark.parametrize(
    ("scheme", "message"),
    [
        ("bogus", "'bogus' is not offered for esp32c6 by esp32 core 3.3.12"),
        ("default_8MB", "'default_8MB' needs more than 4 MB of flash"),
        ("custom", "'custom' takes the sketch's own partitions.csv, which xewe does not support"),
    ],
)
def test_resolve_rejects(tmp_path: Path, scheme: str, message: str) -> None:
    core = tmp_path / "3.3.12"
    shutil.copytree(ESP32_CORE, core)
    with pytest.raises(XeWeError) as exc:
        partitions.resolve(core, chips.get("c6"), scheme)
    assert exc.value.code == 2
    assert message in str(exc.value)
    assert "choose one of: default, huge_app, min_spiffs, no_ota, noota_3g" in str(exc.value)


def test_resolve_without_core(tmp_path: Path) -> None:
    assert partitions.resolve(tmp_path / "none", chips.get("c3"), "no_ota") is None
    with pytest.raises(XeWeError) as exc:
        partitions.resolve(tmp_path / "none", chips.get("c3"), "huge_app")
    assert exc.value.code == 3


# --- xewe.toml [build] partition_scheme


def test_default_scheme_is_no_ota() -> None:
    lock = lockfile.parse("schema = 1\n")
    assert lock.partition_scheme is None and lock.scheme == "no_ota"
    assert "[build]" not in lockfile.dumps(lock)
    assert chips.get("c3").fqbn == chips.get("c3").fqbn_for("no_ota")


def test_scheme_round_trips() -> None:
    lock = lockfile.parse('[project]\nversion = "1.0.0"\n\n[build]\npartition_scheme = "huge_app"\n')
    assert lock.scheme == "huge_app"
    out = lockfile.dumps(lock)
    assert '[build]\npartition_scheme = "huge_app"\n' in out
    assert out.index("[project]") < out.index("[build]") < out.index("[core]")
    assert lockfile.parse(out) == lock


@pytest.mark.parametrize(
    "text",
    [
        '[build]\npartition_scheme = ""\n',
        "[build]\npartition_scheme = 3\n",
        '[build]\npartition_scheme = "huge_app,UploadSpeed=115200"\n',
        '[build]\npartiton_scheme = "huge_app"\n',
        '[build]\nflash_size = "8M"\n',
    ],
)
def test_bad_scheme_key_is_usage_error(text: str) -> None:
    with pytest.raises(XeWeError) as exc:
        lockfile.parse(text)
    assert exc.value.code == 2


# --- xewe build with a scheme


def test_default_build_unchanged(project: Paths, esp32_core: Path, fake_cli) -> None:
    assert main(["build", "--chip", "c3"]) == 0
    assert compile_fqbns(fake_cli()) == [chips.get("c3").fqbn]
    meta = json.loads((project.out_dir("c3") / "meta.json").read_text())
    assert meta["partition_scheme"] == "no_ota" and meta["app_partition_size"] == 2097152
    stamp = (project.out_dir("c3") / build.STAMP_NAME).read_text().strip()
    assert stamp == build.build_key("c3", "2.0.15", {})


def test_huge_app_build(project: Paths, esp32_core: Path, fake_cli, capsys: pytest.CaptureFixture[str]) -> None:
    set_scheme(project, "huge_app")
    assert main(["build", "--chip", "c6"]) == 0
    fqbn = compile_fqbns(fake_cli())[-1]
    assert fqbn == (
        "esp32:esp32:esp32c6:CDCOnBoot=cdc,CPUFreq=160,DebugLevel=none,EraseFlash=all,FlashMode=qio,"
        "FlashSize=4M,PartitionScheme=huge_app,UploadSpeed=921600"
    )
    assert f"build  c6  {fqbn}" in capsys.readouterr().out
    meta = json.loads((project.out_dir("c6") / "meta.json").read_text())
    assert meta["fqbn"] == fqbn
    assert meta["partition_scheme"] == "huge_app" and meta["app_partition_size"] == 3145728
    key = build.build_key("c6", "2.0.15", {}, "huge_app")
    assert key != build.build_key("c6", "2.0.15", {})
    assert (project.out_dir("c6") / build.STAMP_NAME).read_text().strip() == key


def test_huge_app_dry_run(project: Paths, capsys: pytest.CaptureFixture[str]) -> None:
    set_scheme(project, "huge_app")
    assert main(["build", "--chip", "s3", "--dry-run"]) == 0
    assert "PartitionScheme=huge_app," in capsys.readouterr().out


def test_unknown_scheme_build_is_exit_2(
    project: Paths, esp32_core: Path, fake_cli, capsys: pytest.CaptureFixture[str]
) -> None:
    set_scheme(project, "huge")
    assert main(["build", "--chip", "c3"]) == 2
    assert "partition_scheme 'huge' is not offered for esp32c3" in capsys.readouterr().err
    assert compile_fqbns(fake_cli()) == []


def test_scheme_without_core_is_exit_3(project: Paths, fake_cli) -> None:
    set_scheme(project, "huge_app")
    assert main(["build", "--chip", "c3"]) == 3
    assert compile_fqbns(fake_cli()) == []


def test_image_larger_than_the_app_partition_fails(
    project: Paths, esp32_core: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    set_scheme(project, "noota_3g")  # 1 MB app; the fake compile reports 1123456 bytes
    assert main(["build", "--chip", "c3"]) == 1
    captured = capsys.readouterr()
    assert "FAIL   c3" in captured.out and "too big for noota_3g" in captured.out
    assert "the app partition of noota_3g holds 1048576" in captured.err


def test_scheme_change_makes_flash_rebuild(project: Paths, esp32_core: Path, fake_cli) -> None:
    from xewe.build import flash

    assert main(["build", "--chip", "c3"]) == 0
    set_scheme(project, "huge_app")
    lock = lockfile.load(project.lock)
    flash.ensure_built(project, lock, "c3")
    assert compile_fqbns(fake_cli())[-1] == chips.get("c3").fqbn_for("huge_app")


def test_release_keeps_scheme(project: Paths, esp32_core: Path, tmp_path: Path) -> None:
    set_scheme(project, "huge_app")
    notes = tmp_path / "notes.txt"
    notes.write_text("x\n")
    assert main(["release", "--version", "2.1.0", "--notes", str(notes)]) == 0
    meta = json.loads((project.root / "static/firmware/releases/2.1.0/c6/meta.json").read_text())
    assert meta["partition_scheme"] == "huge_app" and "PartitionScheme=huge_app" in meta["fqbn"]
    assert meta["app_partition_size"] == 3145728
    lock = lockfile.load(project.lock)
    assert lock.version == "2.1.0" and lock.scheme == "huge_app"  # the rewrite keeps [build]
