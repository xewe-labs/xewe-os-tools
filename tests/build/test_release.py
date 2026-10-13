import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

from conftest import FIXTURES
from xewe.build import compile as build
from xewe.build import release, releasecard
from xewe.build.compile import BuildResult
from xewe.cli import main
from xewe.env.project import Paths
from xewe.modules import lockfile
from xewe.report import XeWeError

REL = "static/firmware/releases"


@pytest.fixture
def no_git_writes(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Record every subprocess.run of the release module; anything but `git status` fails the test."""
    seen: list[list[str]] = []
    real = subprocess.run

    def guarded(argv, *a, **k):  # type: ignore[no-untyped-def]
        seen.append(list(argv))
        assert argv[0] != "git" or argv[1] in ("status", "rev-parse"), f"release ran {argv}"
        return real(argv, *a, **k)

    monkeypatch.setattr(release.subprocess, "run", guarded)
    return seen


@pytest.fixture
def notes(tmp_path: Path) -> Path:
    path = tmp_path / "notes.txt"
    path.write_text("Faster boot.\n")
    return path


def test_default_matrix_release(
    project: Paths, notes: Path, no_git_writes: list[list[str]], capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["release", "--version", "2.1.0", "--notes", str(notes)]) == 0
    base = project.root / REL / "2.1.0"
    for chip in ("c3", "c6", "s3"):
        files = sorted(f.name for f in (base / chip).iterdir())
        assert files == [f"2.1.0-{chip}-xewe-os.bin", "manifest.json", "meta.json"]
        meta = json.loads((base / chip / "meta.json").read_text())
        assert meta["version"] == "2.1.0"
        assert meta["artifacts"]["path_rel_binary"] == f"xewe-os/{REL}/2.1.0/{chip}/2.1.0-{chip}-xewe-os.bin"
        assert not any(k.startswith("path_abs") for k in meta["artifacts"])
        # the resolved commits from build_config.toml [installed], each next to its ref
        assert meta["core_ref"] == "1.0.0" and meta["core_commit"] == "abc"
        assert meta["modules_ref"] == "v1.0.0" and meta["modules_commit"] == "def"
        assert meta["tools_ref"] == "v0.1.1" and meta["tools_commit"] == "-"
        assert meta["project_commit"] == "-"  # tmp project is not a git checkout
        assert meta["libraries"] == {}
        keys = list(meta)
        assert keys[keys.index("core_ref") - 1 : keys.index("tools_commit") + 1] == [
            "project_commit",
            "core_ref",
            "core_commit",
            "modules_ref",
            "modules_commit",
            "tools_ref",
            "tools_commit",
        ]
        bin_path = base / chip / f"2.1.0-{chip}-xewe-os.bin"
        assert meta["artifacts"]["size"] == bin_path.stat().st_size
        assert meta["artifacts"]["sha256"] == hashlib.sha256(bin_path.read_bytes()).hexdigest()
        assert meta["artifacts"]["asset_name"] == f"2.1.0-{chip}-xewe-os.bin"
        assert meta["artifacts"]["meta_asset"] == f"{chip}_meta.json"
        assert meta["artifacts"]["manifest_asset"] == f"{chip}_manifest.json"
        assert meta["static_ram"] == 41234 and meta["static_ram_max"] == 327680
    header, rows = release.read_map(base / "firmware_map.csv")
    assert header == ["CHIP", "_PATH", "_ASSET", "_SIZE", "_SHA256", "_META", "_MANIFEST", "_BUILD_NOTES"]
    assert [(r["CHIP"], r["_PATH"], r["_ASSET"], r["_META"], r["_MANIFEST"], r["_BUILD_NOTES"]) for r in rows] == [
        (c, c, f"2.1.0-{c}-xewe-os.bin", f"{c}_meta.json", f"{c}_manifest.json", "") for c in ("c3", "c6", "s3")
    ]
    assert (base / "release_notes.txt").read_text() == "Faster boot.\n"
    assert lockfile.load(project.lock).version == "2.1.0"
    with tarfile.open(project.root / REL / "firmware-2.1.0.tar.gz") as tf:
        assert "2.1.0/c3/manifest.json" in tf.getnames() and "2.1.0/firmware_map.csv" in tf.getnames()
    out = capsys.readouterr().out
    for cmd in release.publish_commands("2.1.0"):
        assert f"  {cmd}" in out
    assert "gh release create 2.1.0 build/release/2.1.0/assets/* --verify-tag" in out
    assert "git push origin 2.1.0" in out and " v2.1.0" not in out
    assert not any(a[0] == "git" for a in no_git_writes)


def test_matrix_columns_typing_and_notes(project: Paths, notes: Path, no_git_writes: list[list[str]]) -> None:
    (project.root / "release_matrix.csv").write_text(
        'CHIP,LED_PIN,NAME,_BUILD_NOTES\r\nc3,8,"My Net",first\r\ns3,true,,\r\n\r\n'
    )
    assert main(["release", "--version", "2.0.15", "--notes", str(notes)]) == 0
    base = project.root / REL / "2.0.15"
    c3 = base / "c3" / "8" / "My_Net"
    s3 = base / "s3" / "true" / "empty"
    assert (c3 / "build_notes.txt").read_text() == "first\n"
    assert not (s3 / "build_notes.txt").exists()
    assert json.loads((c3 / "meta.json").read_text())["config"] == {"LED_PIN": 8, "NAME": '"My Net"'}
    assert json.loads((s3 / "meta.json").read_text())["config"] == {"LED_PIN": True, "NAME": ""}
    lines = (base / "firmware_map.csv").read_text().splitlines()
    assert lines[0] == "CHIP,LED_PIN,NAME,_PATH,_ASSET,_SIZE,_SHA256,_META,_MANIFEST,_BUILD_NOTES"
    c3_bin = (c3 / "2.0.15-c3-xewe-os.bin").read_bytes()
    # the matrix cells verbatim (csv quoting), then the folder path and the flat asset names
    assert lines[1] == (
        f'c3,8,"""My Net""",c3/8/My_Net,c3_8_My_Net_2.0.15-c3-xewe-os.bin,{len(c3_bin)},'
        f"{hashlib.sha256(c3_bin).hexdigest()},c3_8_My_Net_meta.json,c3_8_My_Net_manifest.json,first"
    )
    assert lines[2].startswith("s3,true,,s3/true/empty,s3_true_empty_2.0.15-s3-xewe-os.bin,")
    assert lines[2].endswith(",s3_true_empty_meta.json,s3_true_empty_manifest.json,")
    assert len(lines) == 3


def test_parse_matrix_requires_chip() -> None:
    with pytest.raises(XeWeError):
        release.parse_matrix("BOARD\nc3\n")
    matrix = release.parse_matrix("chip,X\nc6,1\n")
    assert matrix.rows[0].chip == "c6" and matrix.rows[0].defines == {"X": "1"}


@pytest.mark.parametrize("version", ["2.0.14", "2.1", "v2.1.0"])
def test_version_checks(project: Paths, notes: Path, version: str) -> None:
    assert main(["release", "--version", version, "--notes", str(notes)]) == 2


def test_legacy_matrix_location(project: Paths, notes: Path, caplog: pytest.LogCaptureFixture) -> None:
    (project.build / "release_matrix.csv").write_text("CHIP,_BUILD_NOTES\nc3,\n")
    assert main(["release", "--version", "2.0.16", "--notes", str(notes)]) == 0
    assert "old location" in caplog.text
    assert (project.root / REL / "2.0.16" / "c3").is_dir()
    assert not (project.root / REL / "2.0.16" / "c6").exists()


def test_failed_build_stops_release(project: Paths, notes: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAKE_ARDUINO_COMPILE_FAIL", "esp32c6")
    assert main(["release", "--version", "2.1.0", "--notes", str(notes)]) == 1
    assert lockfile.load(project.lock).version == "2.0.15"


def test_editor_notes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    editor = tmp_path / "editor.sh"
    editor.write_text('#!/bin/sh\necho "Line one." >> "$1"\n')
    editor.chmod(0o755)
    monkeypatch.setenv("EDITOR", str(editor))
    monkeypatch.setattr(release, "interactive", lambda: True)  # on a terminal (test_release_notes.py: off one)
    assert release._edit_notes("3.0.0") == "\nLine one.\n"


def test_provenance_records_library_and_project_commits(
    project: Paths, notes: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from xewe.env import config

    cfg = config.load(project)
    assert cfg is not None
    cfg.installed["libraries"] = {
        "ArduinoJson": {
            "ref": "v7.4.3",
            "commit": "77771d3",
            "source": "https://github.com/bblanchon/ArduinoJson",
            "origin": "xewe.toml",
        },
        "Broken": {"ref": "main"},
    }
    del cfg.installed["tools"]
    config.save(project, cfg)
    monkeypatch.setattr(release.fetch, "head_commit", lambda path: "f00d" if path == project.root else "-")
    assert (
        main(
            [
                "release",
                "--version",
                "2.1.0",
                "--notes",
                str(notes),
            ]
        )
        == 0
    )
    meta = json.loads((project.root / REL / "2.1.0" / "c6" / "meta.json").read_text())
    assert meta["project_commit"] == "f00d"
    assert meta["libraries"] == {
        "ArduinoJson": {"ref": "v7.4.3", "commit": "77771d3"},
        "Broken": {"ref": "main", "commit": "-"},
    }
    assert meta["tools_ref"] == "" and meta["tools_commit"] == "-"
    assert meta["core_commit"] == "abc"


def test_with_provenance_keeps_ref_fallback() -> None:
    prov = {
        "project_commit": "-",
        "core_ref": "",
        "core_commit": "-",
        "modules_ref": "latest",
        "modules_commit": "c337b20",
        "tools_ref": "latest",
        "tools_commit": "1762",
        "libraries": {},
    }
    meta = {"version": "2.1.0", "core_ref": "1.2.0", "modules_ref": "x", "modules_version": "0.0.0+c337b20"}
    out = release._with_provenance(meta, prov)
    assert list(out) == [
        "version",
        "project_commit",
        "core_ref",
        "core_commit",
        "modules_ref",
        "modules_commit",
        "tools_ref",
        "tools_commit",
        "modules_version",
        "libraries",
    ]
    assert out["core_ref"] == "1.2.0" and out["modules_ref"] == "latest"


MATRIX_43 = FIXTURES / "release_matrix_43.csv"  # the product's matrix: c3 7, c6 14, s3 22 rows


def fake_builder(p: Paths, lock: lockfile.Lock, chip: str, defines: dict[str, str], version: str) -> BuildResult:
    """What build_chip leaves in build/builds/<chip>/out/, without arduino-cli (one bin per define set)."""
    out = p.out_dir(chip)
    out.mkdir(parents=True, exist_ok=True)
    name = build.binary_name(version, chip, build.project_name(p, lock))
    (out / name).write_bytes(f"{chip} {sorted(defines.items())}".encode())
    (out / "manifest.json").write_text(
        build.MANIFEST_TEMPLATE.format(name="xewe-led-os", version=version, family=chip.upper(), binary=name)
    )
    meta = {"type": chip, "config": defines, "core_ref": "1.0.0", "modules_ref": "v1.0.0", "artifacts": {}}
    meta["artifacts"] = {"binary_filename": name}
    (out / "meta.json").write_text(json.dumps(meta))
    return BuildResult(chip, True, out / name)


@pytest.fixture
def led_os(project: Paths, notes: Path) -> Paths:
    """The test project renamed to xewe-led-os with the product's 43-row release matrix."""
    lock = lockfile.load(project.lock)
    lock.name = "xewe-led-os"
    lockfile.save(lock, project.lock)
    shutil.copy(MATRIX_43, project.root / "release_matrix.csv")
    return project


def _release(p: Paths, notes: Path, **kw: object) -> int:
    lock = lockfile.load(p.lock)
    return release.release(p, lock, "3.1.0", notes_file=notes, builder=fake_builder, **kw)  # type: ignore[arg-type]


def _assets(p: Paths) -> dict[str, bytes]:
    """The staged assets by name (the tarball left out: its mtimes differ between runs)."""
    assets = p.build / "release" / "3.1.0" / "assets"
    return {f.name: f.read_bytes() for f in sorted(assets.iterdir()) if not f.name.endswith(".tar.gz")}


def test_matrix_43_rows_and_asset_names(led_os: Paths, notes: Path) -> None:
    assert _release(led_os, notes) == 0
    base = led_os.root / REL / "3.1.0"
    header, rows = release.read_map(base / "firmware_map.csv")
    assert header == [
        "CHIP",
        "XEWE_MODULE_LED_PIN_DATA",
        "XEWE_MODULE_LED_PIN_CLOCK",
        *release.MAP_COLUMNS,
    ]
    assert len(rows) == 43
    assert [r["CHIP"] for r in rows].count("c3") == 7 and [r["CHIP"] for r in rows].count("s3") == 22
    # matrix order, not chip order
    assert [r["_PATH"] for r in rows[:6]] == ["c3/0/9", "c6/14/2", "s3/9/3", "c3/4/3", "c6/20/19", "s3/13/12"]
    v2 = rows[4]
    assert v2 == {
        "CHIP": "c6",
        "XEWE_MODULE_LED_PIN_DATA": "20",
        "XEWE_MODULE_LED_PIN_CLOCK": "19",
        "_PATH": "c6/20/19",
        "_ASSET": "c6_20_19_3.1.0-c6-xewe-led-os.bin",
        "_SIZE": str((base / "c6/20/19/3.1.0-c6-xewe-led-os.bin").stat().st_size),
        "_SHA256": hashlib.sha256((base / "c6/20/19/3.1.0-c6-xewe-led-os.bin").read_bytes()).hexdigest(),
        "_META": "c6_20_19_meta.json",
        "_MANIFEST": "c6_20_19_manifest.json",
        "_BUILD_NOTES": "Led Dock V2 pinout",
    }
    assert rows[-1]["_ASSET"] == "s3_48_12_3.1.0-s3-xewe-led-os.bin"
    assert rows[-1]["_BUILD_NOTES"] == "SuperMini built in aLED (WS2812B)"
    assert len({r["_ASSET"] for r in rows}) == 43
    meta = json.loads((base / "c6/20/19/meta.json").read_text())
    assert meta["artifacts"]["asset_name"] == "c6_20_19_3.1.0-c6-xewe-led-os.bin"
    assert meta["artifacts"]["meta_asset"] == "c6_20_19_meta.json"
    assert meta["artifacts"]["manifest_asset"] == "c6_20_19_manifest.json"
    assert meta["artifacts"]["sha256"] == v2["_SHA256"] and str(meta["artifacts"]["size"]) == v2["_SIZE"]

    assets = _assets(led_os)
    assert len(assets) == 43 * 3 + 1  # + firmware_map.csv; the tarball makes 131 files
    assert (led_os.build / "release" / "3.1.0" / "assets" / "firmware-3.1.0.tar.gz").is_file()
    assert assets["c6_20_19_3.1.0-c6-xewe-led-os.bin"] == (base / "c6/20/19/3.1.0-c6-xewe-led-os.bin").read_bytes()
    assert json.loads(assets["c6_20_19_meta.json"]) == meta
    manifest = json.loads(assets["c6_20_19_manifest.json"])
    assert manifest["builds"][0]["parts"] == [{"path": "c6_20_19_3.1.0-c6-xewe-led-os.bin", "offset": 0}]
    # the folder copy keeps the plain bin name (the tarball layout)
    folder_manifest = json.loads((base / "c6/20/19/manifest.json").read_text())
    assert folder_manifest["builds"][0]["parts"][0]["path"] == "3.1.0-c6-xewe-led-os.bin"
    assert assets["firmware_map.csv"] == (base / "firmware_map.csv").read_bytes()


def test_per_chip_then_assemble_equals_one_run(led_os: Paths, notes: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert _release(led_os, notes) == 0
    base = led_os.root / REL / "3.1.0"
    single_map = (base / "firmware_map.csv").read_text()
    single_assets = _assets(led_os)
    shutil.rmtree(base)
    shutil.rmtree(led_os.build / "release")
    (led_os.root / REL / "firmware-3.1.0.tar.gz").unlink()

    for chip in ("s3", "c3", "c6"):  # job order does not matter
        assert _release(led_os, notes, chip=chip) == 0
    assert not (base / "firmware_map.csv").exists()
    assert not (led_os.root / REL / "firmware-3.1.0.tar.gz").exists()  # --chip: no tarball
    assert not (led_os.build / "release").exists()  # --chip: no assets
    assert len(release.read_map(base / "firmware_map.c6.csv")[1]) == 14
    assert "--assemble" in capsys.readouterr().out

    assert _release(led_os, None, assemble=True) == 0  # notes from the chip runs
    assert (base / "firmware_map.csv").read_text() == single_map
    assert _assets(led_os) == single_assets
    assert sorted(f.name for f in base.glob("firmware_map*")) == ["firmware_map.csv"]
    with tarfile.open(led_os.root / REL / "firmware-3.1.0.tar.gz") as tf:
        names = tf.getnames()
    assert "3.1.0/firmware_map.csv" in names and not any(n.endswith((".c3.csv", ".c6.csv", ".s3.csv")) for n in names)
    assert (base / "release_notes.txt").read_text() == "Faster boot.\n"


def test_assemble_refuses_a_missing_chip(led_os: Paths, notes: Path, caplog: pytest.LogCaptureFixture) -> None:
    assert _release(led_os, notes, assemble=True) == 1
    assert "no per-chip maps" in caplog.text
    assert _release(led_os, notes, chip="c3") == 0
    assert _release(led_os, notes, chip="s3") == 0
    assert _release(led_os, notes, assemble=True) == 1
    assert "no build for matrix row c6/14/2" in caplog.text
    assert not (led_os.root / REL / "3.1.0" / "firmware_map.csv").exists()


def test_assemble_refuses_a_part_from_another_matrix(
    led_os: Paths, notes: Path, caplog: pytest.LogCaptureFixture
) -> None:
    for chip in ("c3", "c6", "s3"):
        assert _release(led_os, notes, chip=chip) == 0
    (led_os.root / "release_matrix.csv").write_text("CHIP,XEWE_MODULE_LED_PIN_DATA\nc3,0\n")
    assert _release(led_os, notes, assemble=True) == 1
    assert "another release matrix" in caplog.text


def test_chip_and_assemble_usage(led_os: Paths, notes: Path) -> None:
    common = ["release", "--version", "3.1.0", "--notes", str(notes)]
    with pytest.raises(SystemExit) as exc:
        main([*common, "--chip", "c3", "--assemble"])
    assert exc.value.code == 2
    (led_os.root / "release_matrix.csv").write_text("CHIP\nc3\n")
    assert main([*common, "--chip", "s3"]) == 2  # no s3 row


def test_default_matrix_chip_run_via_cli(project: Paths, notes: Path) -> None:
    assert main(["release", "--version", "2.1.0", "--notes", str(notes), "--chip", "c6"]) == 0
    _, rows = release.read_map(project.root / REL / "2.1.0" / "firmware_map.c6.csv")
    assert [(r["CHIP"], r["_ASSET"], r["_META"]) for r in rows] == [("c6", "2.1.0-c6-xewe-os.bin", "c6_meta.json")]
    assert not (project.root / REL / "2.1.0" / "c3").exists()


def test_asset_names() -> None:
    assert release.asset_names(["c6", "20", "19"], "3.1.0-c6-xewe-led-os.bin") == (
        "c6_20_19_3.1.0-c6-xewe-led-os.bin",
        "c6_20_19_meta.json",
        "c6_20_19_manifest.json",
    )
    assert release.asset_names(["c3"], "3.1.0-c3-xewe-os.bin") == (
        "3.1.0-c3-xewe-os.bin",
        "c3_meta.json",
        "c3_manifest.json",
    )


def test_stage_assets_listing_and_checks(project: Paths, notes: Path) -> None:
    assert main(["release", "--version", "3.1.0", "--notes", str(notes)]) == 0
    base = project.root / REL / "3.1.0"
    out = project.build / "release" / "3.1.0" / "assets"
    names = [f.name for f in release.stage_assets(base, out, project.root / REL / "firmware-3.1.0.tar.gz")]
    assert names == [
        "3.1.0-c3-xewe-os.bin",
        "3.1.0-c6-xewe-os.bin",
        "3.1.0-s3-xewe-os.bin",
        "c3_manifest.json",
        "c3_meta.json",
        "c6_manifest.json",
        "c6_meta.json",
        "firmware-3.1.0.tar.gz",
        "firmware_map.csv",
        "s3_manifest.json",
        "s3_meta.json",
    ]
    (out / "stale.bin").write_bytes(b"x")
    assert "stale.bin" not in [f.name for f in release.stage_assets(base, out)]  # emptied first
    (base / "c6" / "3.1.0-c6-xewe-os.bin").write_bytes(b"changed")
    with pytest.raises(XeWeError, match="does not match its firmware_map.csv row"):
        release.stage_assets(base, out)


def test_stage_assets_refuses_two_rows_with_one_asset_name(tmp_path: Path) -> None:
    """Folders a_b + c and a + b_c join to the same prefix: refused before anything is written."""
    one, two = release.asset_names(["a_b", "c"], "x.bin"), release.asset_names(["a", "b_c"], "x.bin")
    assert one == two
    header = ["CHIP", "_PATH", "_ASSET", "_SIZE", "_SHA256", "_META", "_MANIFEST"]
    rows = [["c3", "a_b/c", *one[:1], "1", "0" * 64, *one[1:]], ["c3", "a/b_c", *two[:1], "1", "0" * 64, *two[1:]]]
    release.write_map(tmp_path / release.MAP_NAME, header, rows)
    out = tmp_path / "assets"
    with pytest.raises(XeWeError, match="rows a_b/c and a/b_c both name the asset a_b_c_x.bin") as exc:
        release.stage_assets(tmp_path, out)
    assert exc.value.code == 1 and not out.exists()


def test_publish_commands_never_stage_firmware() -> None:
    cmds = release.publish_commands("3.1.0")
    assert cmds[0] == "git add xewe.toml"
    assert not any("static/firmware" in c for c in cmds if c.startswith("git "))
    assert cmds[-1] == (
        "gh release create 3.1.0 build/release/3.1.0/assets/* --verify-tag --title 3.1.0 "
        "--notes-file static/firmware/releases/3.1.0/release_notes.txt"
    )
    with_card = release.publish_commands("3.1.0", card=True)[-1]
    assert "assets/* releases/3.1.0/release_card.md --verify-tag" in with_card


def test_card_added_to_publish_line(project: Paths, notes: Path, capsys: pytest.CaptureFixture[str]) -> None:
    card = project.root / "releases" / "2.1.0" / "release_card.md"
    card.parent.mkdir(parents=True)
    card.write_text("# card\n")
    assert main(["release", "--version", "2.1.0", "--notes", str(notes), "--chip", "c3"]) == 0
    assert main(["release", "--version", "2.1.0", "--assemble"]) == 1  # c6 and s3 not built
    for chip in ("c6", "s3"):
        assert main(["release", "--version", "2.1.0", "--notes", str(notes), "--chip", chip]) == 0
    assert main(["release", "--version", "2.1.0", "--assemble"]) == 0
    assert "assets/* releases/2.1.0/release_card.md --verify-tag" in capsys.readouterr().out


WORKFLOWS = Path(__file__).resolve().parents[2] / ".github" / "workflows"
WORKFLOW = WORKFLOWS / "xewe-release.yml"


def _step_script(name: str, workflow: Path = WORKFLOW) -> str:
    """The ``run: |`` block of the first step called ``name`` in ``workflow``, dedented."""
    lines = workflow.read_text().splitlines()
    start = lines.index(f"      - name: {name}")
    run = next(i for i in range(start, len(lines)) if lines[i].strip() == "run: |")
    indent = len(lines[run]) - len(lines[run].lstrip()) + 2
    body: list[str] = []
    for line in lines[run + 1 :]:
        if line.strip() and len(line) - len(line.lstrip()) < indent:
            break
        body.append(line[indent:])
    return "\n".join(body) + "\n"


@pytest.mark.parametrize(
    ("tag", "expected"),
    [
        ("3.0.0", {"tag": "3.0.0", "version": "3.0.0", "base": "3.0.0", "prerelease": "false"}),
        ("3.0.0-rc1", {"tag": "3.0.0-rc1", "version": "3.0.0-rc1", "base": "3.0.0", "prerelease": "true"}),
        ("v3.0.0", {"tag": "v3.0.0", "version": "3.0.0", "base": "3.0.0", "prerelease": "false"}),
    ],
)
def test_workflow_version_from_tag(tmp_path: Path, tag: str, expected: dict[str, str]) -> None:
    (tmp_path / "xewe.toml").write_text('schema = 1\n[project]\nname = "x"\nversion = "3.0.0"\n')
    out = tmp_path / "out"
    env = {"PATH": os.environ["PATH"], "GITHUB_REF_NAME": tag, "GITHUB_REF_TYPE": "tag", "GITHUB_OUTPUT": str(out)}
    proc = subprocess.run(["bash", "-c", _step_script("Version from the tag")], cwd=tmp_path, env=env)
    assert proc.returncode == 0
    assert dict(ln.split("=", 1) for ln in out.read_text().splitlines()) == expected


@pytest.mark.parametrize(("tag", "message"), [("3.0", "is not X.Y.Z"), ("3.0.1", "does not match")])
def test_workflow_version_from_tag_rejects(tmp_path: Path, tag: str, message: str) -> None:
    (tmp_path / "xewe.toml").write_text('schema = 1\n[project]\nname = "x"\nversion = "3.0.0"\n')
    env = {"PATH": os.environ["PATH"], "GITHUB_REF_NAME": tag, "GITHUB_REF_TYPE": "tag", "GITHUB_OUTPUT": "/dev/null"}
    proc = subprocess.run(
        ["bash", "-c", _step_script("Version from the tag")], cwd=tmp_path, env=env, capture_output=True, text=True
    )
    assert proc.returncode == 1 and message in proc.stdout


def test_workflow_branch_commit_uses_lfs_and_skips_ci() -> None:
    script = _step_script("Commit to the releases branch")
    lfs = script.index("git lfs install --local")
    assert lfs < script.index("git worktree add") and "export GIT_LFS_SKIP_SMUDGE=1" in script
    assert "check-attr filter" in script  # a .bin routed to LFS must be staged as a pointer
    assert '[skip ci]"' in script
    # the folder lands at static/firmware/releases/<version>/ of the branch, the rest of it kept
    assert 'cp -R "$rel/$VERSION" "$w/$rel/"' in script and "rel=static/firmware/releases" in script


def _run_step(script: str, cwd: Path, env: dict[str, str]) -> tuple[subprocess.CompletedProcess[str], dict[str, str]]:
    out = cwd / "github_output"
    out.write_text("")
    full = {"PATH": os.environ["PATH"], "GITHUB_OUTPUT": str(out), **env}
    proc = subprocess.run(["bash", "-c", script], cwd=cwd, env=full, capture_output=True, text=True)
    return proc, dict(ln.split("=", 1) for ln in out.read_text().splitlines() if "=" in ln)


@pytest.mark.parametrize(
    ("matrix", "chips"),
    [
        (None, '["c3", "c6", "s3"]'),
        (MATRIX_43.read_text(), '["c3", "c6", "s3"]'),
        ("chip,X,_BUILD_NOTES\r\ns3,1,a\r\n,skipped\r\nc3,2,\r\ns3,3,\r\n", '["s3", "c3"]'),
        ('CHIP,NAME,_BUILD_NOTES\nC6,"Lamp, desk",x\nS3,"a, b",\n', '["c6", "s3"]'),  # CSV quoting, any case
    ],
)
def test_workflow_plan_chips(tmp_path: Path, matrix: str | None, chips: str) -> None:
    if matrix is not None:
        (tmp_path / "release_matrix.csv").write_text(matrix)
    proc, out = _run_step(_step_script("Chips in the release matrix"), tmp_path, {})
    assert proc.returncode == 0, proc.stderr
    assert out == {"chips": chips}


FAKE_GH = """#!/bin/sh
[ "$1 $2 $3 $4" = "release list --exclude-pre-releases --exclude-drafts" ] || { echo "unexpected gh $*" >&2; exit 2; }
printf '%s' "$FAKE_TAGS"
"""


@pytest.mark.parametrize(
    ("base", "pre", "tags", "latest"),
    [
        ("3.1.0", "false", "3.0.0\n2.2.2\nv2.3.0\n", "true"),
        ("3.1.0", "false", "3.1.0\n3.0.0\n", "true"),  # a rerun of the newest
        ("3.0.2", "false", "3.1.0\n3.0.1\n", "false"),  # a patch of an older line
        ("3.1.0", "false", "3.10.0\n", "false"),  # version order, not text order
        ("3.1.0", "false", "", "true"),  # the first release
        ("3.1.0", "false", "2.3.8-legacy\nnot-a-version\n", "true"),
        ("3.2.0", "true", "3.1.0\n", "false"),  # a pre-release is never latest
    ],
)
def test_workflow_latest_flag(tmp_path: Path, base: str, pre: str, tags: str, latest: str) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "gh").write_text(FAKE_GH)
    (bin_dir / "gh").chmod(0o755)
    env = {"PATH": f"{bin_dir}:{os.environ['PATH']}", "BASE": base, "PRE": pre, "FAKE_TAGS": tags}
    proc, out = _run_step(_step_script("Choose the latest flag"), tmp_path, env)
    assert proc.returncode == 0, proc.stderr
    assert out == {"latest": latest}


def test_workflow_jobs_and_assets() -> None:
    text = WORKFLOW.read_text()
    jobs = text[text.index("\njobs:\n") :]
    assert [ln[2:-1] for ln in jobs.splitlines() if ln.startswith("  ") and not ln.startswith("   ")] == [
        "plan",
        "build",
        "assemble",
    ]
    inputs = text[text.index("    inputs:") : text.index("\npermissions:")]
    assert "      publish_branch:\n" in inputs
    assert inputs.split("publish_branch:")[1].split("releases_branch:")[0].count("default: false") == 1
    assert "      timeout_minutes:\n" in inputs and "default: 90" in inputs
    assert jobs.count("timeout-minutes: ${{ inputs.timeout_minutes }}") == 3
    assert "fail-fast: false" in jobs and "fromJSON(needs.plan.outputs.chips)" in jobs
    build_job = jobs[jobs.index("\n  build:") : jobs.index("\n  assemble:")]
    assert 'xewe release --version "$BASE" --chip "$CHIP" --notes' in build_job
    assert "xewe test --no-board --chip" in build_job and "--junitxml=" in build_job
    assert "junit_family=xunit1" in build_job
    # assemble: card check (on the pristine checkout), merge, finish, then publish; the branch
    # commit only when opted in
    assemble = jobs[jobs.index("\n  assemble:") :]
    order = [
        "release-card verify --ci --card",
        "- name: Download the chip jobs' release rows",
        "- name: xewe release --assemble",
        "- name: Finish the release folder",
        "- name: Choose the latest flag",
        "- name: GitHub Release",
        "- name: Commit to the releases branch",
    ]
    assert [assemble.index(s) for s in order] == sorted(assemble.index(s) for s in order)
    assert "if: inputs.publish_branch" in assemble
    publish = _step_script("GitHub Release")
    assert 'files=("build/release/$BASE/assets"/*)' in publish and 'card="releases/$BASE/release_card.md"' in publish
    assert '"--latest=$LATEST"' in publish and "upload" in publish and "--clobber" in publish
    assert ".log" not in publish and "build-ci" not in text  # no logs are published


CARD_JOB_WORKFLOWS = ["xewe-core-tests.yml", "xewe-modules-tests.yml", "ci.yml"]
"""The workflows with a release card job (ci.yml: the tools' own card)."""


FAKE_GIT = """#!/usr/bin/env python3
# read-only git for a scratch tree: every file but github_output (the step's $GITHUB_OUTPUT, which
# lives in $RUNNER_TEMP on a runner) is tracked; ls-files (with pathspecs, -s), hash-object
# --stdin-paths and rev-parse --show-toplevel
import fnmatch, hashlib, os, sys
def blob(path):
    data = open(path, "rb").read()
    return hashlib.sha1(b"blob %d\\0" % len(data) + data).hexdigest()
args = sys.argv[1:]
if args[:1] == ["-C"]:
    os.chdir(args[1])
    args = args[2:]
files = sorted(
    os.path.relpath(os.path.join(d, f)).replace(os.sep, "/")
    for d, _, fs in os.walk(".")
    for f in fs
    if f != "github_output"
)
if args == ["ls-files", "-s", "-z"]:
    for f in files:
        mode = "100755" if os.access(f, os.X_OK) else "100644"
        sys.stdout.write(f"{mode} {blob(f)} 0\\t{f}\\0")
elif args[:1] == ["ls-files"]:
    specs = args[args.index("--") + 1 :] if "--" in args else []
    hits = [f for f in files if not specs or any(fnmatch.fnmatch(f, s) for s in specs)]
    sys.stdout.write("".join(f + "\\0" for f in hits))
elif args == ["hash-object", "--stdin-paths"]:
    for path in sys.stdin.read().splitlines():
        print(blob(path))
elif args == ["rev-parse", "--show-toplevel"]:
    print(os.getcwd())
else:
    sys.exit(f"unexpected git {args}")
"""


@pytest.mark.parametrize("workflow", CARD_JOB_WORKFLOWS)
@pytest.mark.parametrize("matches", [True, False])
def test_card_job_finds_the_card_of_this_tree(tmp_path: Path, workflow: str, matches: bool) -> None:
    """The card job finds cards with `xewe release-card fingerprint --card --check` (the tools'
    own algorithm, the one `init` records and `verify` recomputes), not a copy of it."""
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.cpp").write_text("int a;\n")
    (root / "tests" / "unit").mkdir(parents=True)
    (root / "tests" / "unit" / "run.sh").write_text("#!/bin/sh\n")
    (root / "tests" / "unit" / "run.sh").chmod(0o755)
    module_card = root / "modules" / "led" / "releases" / "3.1.0" / "release_card.md"
    module_card.parent.mkdir(parents=True)
    other = {"schema": 1, "tree": "sha256:" + "1" * 64, "tests": [], "exclude": ["releases/", "modules/*/releases"]}
    module_card.write_text(f"# a card of another tree\n\n<!-- xewe-card-data {json.dumps(other)} -->\n")
    card = root / "releases" / "3.1.0" / "release_card.md"
    card.parent.mkdir(parents=True)
    card.write_text("placeholder\n")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "git").write_text(FAKE_GIT)
    (bin_dir / "git").chmod(0o755)
    excludes = ["releases/", "modules/*/releases"]
    path = f"{bin_dir}:{os.environ['PATH']}"
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("PATH", path)
        tree = releasecard.tree_fingerprint(root, excludes) if matches else "sha256:" + "0" * 64
    data = {"schema": 1, "tree": tree, "tests": [], "exclude": excludes}
    card.write_text(f"# card\n\n<!-- xewe-card-data {json.dumps(data)} -->\n")  # the card is excluded
    venv_bin = tmp_path / "runner" / "venv" / "bin"
    venv_bin.mkdir(parents=True)
    (venv_bin / "python").write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
    (venv_bin / "python").chmod(0o755)
    env = {"PATH": path, "RUNNER_TEMP": str(tmp_path / "runner")}
    proc, out = _run_step(_step_script("Release card for this tree", WORKFLOWS / workflow), root, env)
    assert proc.returncode == 0, proc.stderr
    assert out == {"cards": "releases/3.1.0/release_card.md" if matches else ""}, proc.stdout
    if not matches:
        assert "no release card for this tree" in proc.stdout
    text = (WORKFLOWS / workflow).read_text()
    card_job = text[text.index("\n  card:") :]
    assert "hashlib" not in card_job  # no second implementation of the fingerprint
    assert "release-card verify --ci --unit-only --card" in card_job
    assert card_job.count("if: steps.find.outputs.cards != ''") == 2


def test_tools_ci_card_job() -> None:
    """ci.yml checks the tools' own card: the tools from this checkout, pytest's JUnit file (one
    Python) as the CI evidence, then `verify --ci --unit-only`."""
    text = (WORKFLOWS / "ci.yml").read_text()
    test_job = text[text.index("\n  test:") : text.index("\n  actionlint:")]
    assert '--junitxml="$RUNNER_TEMP/card/junit/tests-unit.xml" -o junit_family=xunit1' in test_job
    assert "if: matrix.python == '3.12'" in test_job and "name: card-inputs" in test_job
    card_job = text[text.index("\n  card:") :]
    order = ["needs: test", 'pip" install -q .', "fingerprint --card", "name: card-inputs", "verify --ci --unit-only"]
    assert [card_job.index(s) for s in order] == sorted(card_job.index(s) for s in order)
    assert "tools_ref" not in card_job and "git+https" not in card_job


@pytest.mark.parametrize("workflow", CARD_JOB_WORKFLOWS)
def test_card_job_fails_when_the_fingerprint_cannot_run(tmp_path: Path, workflow: str) -> None:
    """Tools without `release-card fingerprint` (argparse exit 2) fail the job instead of skipping it."""
    root = tmp_path / "repo"
    (root / "releases" / "3.1.0").mkdir(parents=True)
    (root / "releases" / "3.1.0" / "release_card.md").write_text("card\n")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "git").write_text(FAKE_GIT)
    (bin_dir / "git").chmod(0o755)
    venv_bin = tmp_path / "runner" / "venv" / "bin"
    venv_bin.mkdir(parents=True)
    (venv_bin / "python").write_text("#!/bin/sh\nexit 2\n")
    (venv_bin / "python").chmod(0o755)
    env = {"PATH": f"{bin_dir}:{os.environ['PATH']}", "RUNNER_TEMP": str(tmp_path / "runner")}
    proc, out = _run_step(_step_script("Release card for this tree", WORKFLOWS / workflow), root, env)
    assert proc.returncode == 2 and "fingerprint failed on releases/3.1.0/release_card.md (exit 2)" in proc.stdout
    assert out == {}


@pytest.mark.parametrize("workflow", CARD_JOB_WORKFLOWS)
@pytest.mark.parametrize(
    "card_text",
    [
        "# hand-written, no data block\n",
        '# card\n\n<!-- xewe-card-data {"tree": "sha256:00", oops} -->\n',
        '<!-- xewe-card-data {"schema":1,"tree":"x","exclude":["releases/"]} -->\n\nnotes after the block\n',
    ],
    ids=["no-block", "bad-json", "block-not-last"],
)
def test_card_job_fails_on_a_card_it_cannot_read(tmp_path: Path, workflow: str, card_text: str) -> None:
    """Only "is not the card's" (exit 1) skips a card; a card whose data block is missing or not
    JSON is exit 2 and fails the job, so a malformed card never turns the CI comparison off."""
    root = tmp_path / "repo"
    (root / "releases" / "3.1.0").mkdir(parents=True)
    (root / "releases" / "3.1.0" / "release_card.md").write_text(card_text)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "git").write_text(FAKE_GIT)
    (bin_dir / "git").chmod(0o755)
    venv_bin = tmp_path / "runner" / "venv" / "bin"
    venv_bin.mkdir(parents=True)
    (venv_bin / "python").write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
    (venv_bin / "python").chmod(0o755)
    env = {"PATH": f"{bin_dir}:{os.environ['PATH']}", "RUNNER_TEMP": str(tmp_path / "runner")}
    proc, out = _run_step(_step_script("Release card for this tree", WORKFLOWS / workflow), root, env)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "fingerprint failed on releases/3.1.0/release_card.md (exit 2)" in proc.stdout
    assert out == {}


@pytest.mark.parametrize("workflow", CARD_JOB_WORKFLOWS)
def test_card_job_skips_only_on_another_tree(tmp_path: Path, workflow: str) -> None:
    """An exit 1 without "is not the card's" (any other failure of the tools) fails the job too."""
    root = tmp_path / "repo"
    (root / "releases" / "3.1.0").mkdir(parents=True)
    (root / "releases" / "3.1.0" / "release_card.md").write_text("card\n")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "git").write_text(FAKE_GIT)
    (bin_dir / "git").chmod(0o755)
    venv_bin = tmp_path / "runner" / "venv" / "bin"
    venv_bin.mkdir(parents=True)
    (venv_bin / "python").write_text("#!/bin/sh\necho 'error: git not found' >&2\nexit 1\n")
    (venv_bin / "python").chmod(0o755)
    env = {"PATH": f"{bin_dir}:{os.environ['PATH']}", "RUNNER_TEMP": str(tmp_path / "runner")}
    proc, out = _run_step(_step_script("Release card for this tree", WORKFLOWS / workflow), root, env)
    assert proc.returncode == 1 and "fingerprint failed on releases/3.1.0/release_card.md (exit 1)" in proc.stdout
    assert out == {}


def test_build_lint_rejects_committed_firmware() -> None:
    text = (WORKFLOWS / "xewe-build.yml").read_text()
    lint = text[text.index("\n  lint:") :]
    assert "continue-on-error: ${{ !inputs.strict_lint }}" in lint  # blocking with strict_lint
    script = _step_script("No committed firmware", WORKFLOWS / "xewe-build.yml")
    assert "git ls-files 'static/firmware/releases/**' ':!:static/firmware/releases/.gitkeep'" in script
    assert "exit 1" in script


# --------------------------------------------------------------------------- notes without --notes
# `xewe release` without `--notes`: `$EDITOR` only on a terminal; otherwise exit 2 naming `--notes`.


class _Terminal:
    def isatty(self) -> bool:
        return True


@pytest.fixture
def editor(tmp_path: Path) -> Path:
    """An ``$EDITOR`` that leaves a marker file when it is run."""
    marker = tmp_path / "editor-ran"
    script = tmp_path / "editor.sh"
    script.write_text(f'#!/bin/sh\ntouch "{marker}"\necho "Notes." >> "$1"\n')
    script.chmod(0o755)
    return marker


def test_no_terminal_exits_2_without_editor(project: Paths, editor: Path, tmp_path: Path) -> None:
    env = {k: v for k, v in os.environ.items() if k != "XEWE_NONINTERACTIVE"}
    env["EDITOR"] = str(tmp_path / "editor.sh")
    proc = subprocess.run(
        [sys.executable, "-m", "xewe", "release", "--version", "2.1.0"],
        cwd=project.root,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 2, proc.stderr
    lines = proc.stderr.strip().splitlines()
    assert len(lines) == 1 and "--notes" in lines[0], proc.stderr
    assert not editor.exists()
    assert not (project.root / REL / "2.1.0").exists()  # stopped before anything was built or written


def test_noninteractive_variable_exits_2(
    project: Paths, editor: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(release.sys, "stdin", _Terminal())
    monkeypatch.setenv("EDITOR", str(tmp_path / "editor.sh"))
    monkeypatch.setenv("XEWE_NONINTERACTIVE", "1")
    assert main(["release", "--version", "2.1.0"]) == 2
    assert not editor.exists()
    monkeypatch.setenv("XEWE_NONINTERACTIVE", "0")
    assert main(["release", "--version", "2.1.0"]) == 0  # a terminal: the editor writes the notes
    assert editor.exists()
    assert (project.root / REL / "2.1.0" / "release_notes.txt").read_text() == "\nNotes.\n"


def test_notes_flag_needs_no_terminal(project: Paths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XEWE_NONINTERACTIVE", "1")
    notes = tmp_path / "notes.txt"
    notes.write_text("Faster boot.\n")
    assert main(["release", "--version", "2.1.0", "--notes", str(notes)]) == 0
    assert (project.root / REL / "2.1.0" / "release_notes.txt").read_text() == "Faster boot.\n"


def test_chip_and_assemble_are_covered(project: Paths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The check sits in the editor fallback, so ``--chip`` and ``--assemble`` are covered too;
    ``--assemble`` keeps the chip runs' notes and needs no ``--notes`` then."""
    monkeypatch.setenv("XEWE_NONINTERACTIVE", "1")
    version_dir = project.root / REL / "2.1.0"
    assert main(["release", "--version", "2.1.0", "--chip", "c3"]) == 2
    assert not version_dir.exists()  # stopped before the build and before the folder
    notes = tmp_path / "notes.txt"
    notes.write_text("Per chip.\n")
    for chip in ("c3", "c6", "s3"):
        assert main(["release", "--version", "2.1.0", "--chip", chip, "--notes", str(notes)]) == 0
    (version_dir / "release_notes.txt").unlink()
    assert main(["release", "--version", "2.1.0", "--assemble"]) == 2  # no notes anywhere
    assert not (version_dir / "firmware_map.csv").exists()
    assert sorted(f.name for f in version_dir.glob("firmware_map.*.csv")) == [
        "firmware_map.c3.csv",
        "firmware_map.c6.csv",
        "firmware_map.s3.csv",
    ]
    (version_dir / "release_notes.txt").write_text("Per chip.\n")
    assert main(["release", "--version", "2.1.0", "--assemble"]) == 0  # the chip runs' notes are kept
    assert (version_dir / "firmware_map.csv").is_file()
    assert (version_dir / "release_notes.txt").read_text() == "Per chip.\n"
