import shutil
from pathlib import Path

import pytest

from conftest import write_project
from xewe import config
from xewe.cli import main
from xewe.project import Paths, find_root
from xewe.report import XeweError


def test_find_root_walks_up(tmp_path: Path) -> None:
    p = write_project(tmp_path / "proj")
    deep = p.root / "src" / "a" / "b"
    deep.mkdir(parents=True)
    assert find_root(start=deep) == p.root


def test_find_root_explicit_and_missing(tmp_path: Path) -> None:
    p = write_project(tmp_path / "proj")
    assert find_root(str(p.root)) == p.root
    with pytest.raises(XeweError) as exc:
        find_root(start=tmp_path)
    assert exc.value.code == 2
    with pytest.raises(XeweError):
        find_root(str(tmp_path))


def test_project_flag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    p = write_project(tmp_path / "proj")
    monkeypatch.chdir(tmp_path)
    assert main(["--project", str(p.root), "lock", "show"]) == 0
    assert "core" in capsys.readouterr().out


def test_stored_paths_are_relative(project: Paths) -> None:
    text = project.build_config.read_text()
    assert str(project.root) not in text
    cfg = config.load(project)
    assert cfg is not None and cfg.path(project, "arduino_data") == project.build / "arduino15"


def test_project_moved_after_setup_still_works(project: Paths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                               capsys: pytest.CaptureFixture[str]) -> None:
    moved = tmp_path / "elsewhere" / "xewe-os"
    moved.parent.mkdir()
    shutil.move(str(project.root), str(moved))
    monkeypatch.chdir(moved)
    assert main(["build", "--chip", "c3"]) == 0
    assert (moved / "build/out/c3/2.0.15-c3-xewe-os.bin").is_file()
    assert str(project.root) not in (moved / "build/out/c3/meta.json").read_text()
