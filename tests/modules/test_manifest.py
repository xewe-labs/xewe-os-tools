"""``xewe manifest show`` / ``update``."""

import json

import pytest

from xewe.cli import main
from xewe.env import fetch
from xewe.env.project import Paths
from xewe.modules import lockfile


def test_show_marks_drift(project: Paths, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["manifest", "show"]) == 0
    out = capsys.readouterr().out
    assert "  core " in out and "\n! " not in out
    lock = lockfile.load(project.lock)
    lock.core.ref = "1.1.0"
    lockfile.save(lock, project.lock)
    assert main(["manifest", "show", "--json"]) == 0
    rows = {r["name"]: r for r in json.loads(capsys.readouterr().out)}
    assert rows["core"]["drift"] and not rows["modules"]["drift"]


def test_update(project: Paths, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(fetch, "git", lambda *a, **k: f"{'0' * 40}\trefs/tags/1.2.0\n{'0' * 40}\trefs/tags/1.10.0\n")
    assert main(["manifest", "update", "core"]) == 0
    assert '+ref = "1.10.0"' in capsys.readouterr().out
    assert lockfile.load(project.lock).core.ref == "1.10.0"
    assert main(["manifest", "update", "modules", "--to", "v2.0.0"]) == 0
    assert lockfile.load(project.lock).modules.ref == "v2.0.0"
    assert main(["manifest", "update", "--to", "x"]) == 2
    assert main(["manifest", "update", "bogus"]) == 2


# --- the ref `latest`

SHA = "f15739255f78b21d7fb5b25f378a3f072ed61794"
MOVED = "0123456789abcdef0123456789abcdef01234567"


def _track_latest(project: Paths, commit: str) -> None:
    """xewe.toml [core] ref = "latest", and setup recorded main@commit."""
    from xewe.env import config

    lock = lockfile.load(project.lock)
    lock.core.ref = "latest"
    lockfile.save(lock, project.lock)
    cfg = config.load(project)
    assert cfg is not None
    cfg.installed["core"] = {"ref": "latest", "branch": "main", "commit": commit, "source": lock.core.repo}
    config.save(project, cfg)


def test_show_latest_and_remote_drift(project: Paths, monkeypatch: pytest.MonkeyPatch,
                                      capsys: pytest.CaptureFixture[str]) -> None:
    _track_latest(project, SHA)
    head = {"sha": SHA}
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(fetch, "git", lambda *a, **k: calls.append(a) or f"ref: refs/heads/main\tHEAD\n{head['sha']}\tHEAD\n")
    assert main(["manifest", "show"]) == 0
    out = capsys.readouterr().out
    assert "latest (main@f157392)" in out and "\n! " not in out
    assert calls == [("ls-remote", "--symref", "https://github.com/xewe-labs/xewe-os-core", "HEAD")]
    head["sha"] = MOVED  # the owner pushed to main
    assert main(["manifest", "show"]) == 0
    line = next(r for r in capsys.readouterr().out.splitlines() if " core " in r)
    assert line.startswith("! ") and "remote main@0123456" in line
    assert main(["manifest", "show", "--json"]) == 0
    core = next(r for r in json.loads(capsys.readouterr().out) if r["name"] == "core")
    assert core["drift"] and core["remote"] == MOVED and core["installed_text"] == "latest (main@f157392)"


def test_show_latest_unreachable_is_not_drift(project: Paths, monkeypatch: pytest.MonkeyPatch,
                                              capsys: pytest.CaptureFixture[str]) -> None:
    _track_latest(project, SHA)

    def offline(*a: str, **k: object) -> str:
        raise fetch.XeweError("git ls-remote failed: offline")

    monkeypatch.setattr(fetch, "git", offline)
    assert main(["manifest", "show"]) == 0
    assert "\n! " not in capsys.readouterr().out


def test_update_freezes_latest_and_to_latest_sets_it_back(project: Paths, monkeypatch: pytest.MonkeyPatch,
                                                          capsys: pytest.CaptureFixture[str]) -> None:
    _track_latest(project, SHA)
    monkeypatch.setattr(fetch, "git", lambda *a, **k: f"{'0' * 40}\trefs/tags/1.2.0\n{'0' * 40}\trefs/tags/1.10.0\n")
    assert main(["manifest", "update", "core"]) == 0
    assert '-ref = "latest"' in capsys.readouterr().out
    assert lockfile.load(project.lock).core.ref == "1.10.0"
    for name in ("core", "modules", "tools"):
        assert main(["manifest", "update", name, "--to", "latest"]) == 0
        assert lockfile.load(project.lock).source(name).ref == "latest"
