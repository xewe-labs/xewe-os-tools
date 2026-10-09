import json
import subprocess
import tarfile
from pathlib import Path

import pytest

from xewe.build import release
from xewe.cli import main
from xewe.env.project import Paths
from xewe.modules import lockfile
from xewe.report import XeweError

REL = "static/firmware/releases"


@pytest.fixture
def no_git_writes(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Record every subprocess.run of the release module; anything but `git status` fails the test."""
    seen: list[list[str]] = []
    real = subprocess.run

    def guarded(argv, *a, **k):  # type: ignore[no-untyped-def]
        seen.append(list(argv))
        assert argv[0] != "git" or argv[1] == "status", f"release ran {argv}"
        return real(argv, *a, **k)

    monkeypatch.setattr(release.subprocess, "run", guarded)
    return seen


@pytest.fixture
def notes(tmp_path: Path) -> Path:
    path = tmp_path / "notes.txt"
    path.write_text("Faster boot.\n")
    return path


def test_default_matrix_release(project: Paths, notes: Path, no_git_writes: list[list[str]],
                                capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["release", "--version", "2.1.0", "--notes", str(notes)]) == 0
    base = project.root / REL / "2.1.0"
    for chip in ("c3", "c6", "s3"):
        files = sorted(f.name for f in (base / chip).iterdir())
        assert files == [f"2.1.0-{chip}-xewe-os.bin", "manifest.json", "meta.json"]
        meta = json.loads((base / chip / "meta.json").read_text())
        assert meta["version"] == "2.1.0"
        assert meta["artifacts"]["path_rel_binary"] == f"xewe-os/{REL}/2.1.0/{chip}/2.1.0-{chip}-xewe-os.bin"
        assert not any(k.startswith("path_abs") for k in meta["artifacts"])
    assert (base / "firmware_map.csv").read_text() == "CHIP\n"
    assert (base / "release_notes.txt").read_text() == "Faster boot.\n"
    assert lockfile.load(project.lock).version == "2.1.0"
    with tarfile.open(project.root / REL / "firmware-2.1.0.tar.gz") as tf:
        assert "2.1.0/c3/manifest.json" in tf.getnames()
    out = capsys.readouterr().out
    for cmd in release.publish_commands("2.1.0"):
        assert f"  {cmd}" in out
    assert f"gh release create v2.1.0 {REL}/firmware-2.1.0.tar.gz --verify-tag" in out
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
    assert (base / "firmware_map.csv").read_text() == "CHIP,LED_PIN,NAME\n"


def test_parse_matrix_requires_chip() -> None:
    with pytest.raises(XeweError):
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
    assert release._edit_notes("3.0.0") == "\nLine one.\n"
