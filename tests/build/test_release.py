import json
import os
import subprocess
import tarfile
from pathlib import Path

import pytest

from xewe.build import release
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
    assert (base / "firmware_map.csv").read_text() == "CHIP\n"
    assert (base / "release_notes.txt").read_text() == "Faster boot.\n"
    assert lockfile.load(project.lock).version == "2.1.0"
    with tarfile.open(project.root / REL / "firmware-2.1.0.tar.gz") as tf:
        assert "2.1.0/c3/manifest.json" in tf.getnames()
    out = capsys.readouterr().out
    for cmd in release.publish_commands("2.1.0"):
        assert f"  {cmd}" in out
    assert f"gh release create 2.1.0 {REL}/firmware-2.1.0.tar.gz --verify-tag" in out
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
    assert (base / "firmware_map.csv").read_text() == "CHIP,LED_PIN,NAME\n"


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


WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "xewe-release.yml"


def _step_script(name: str) -> str:
    """The ``run: |`` block of the xewe-release.yml step called ``name``, dedented."""
    lines = WORKFLOW.read_text().splitlines()
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
