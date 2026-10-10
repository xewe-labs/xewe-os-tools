"""xewe brand-lint: the banned spellings, the skipped places, output lines and exit codes."""

from pathlib import Path

import pytest

from xewe.build import brandlint
from xewe.cli import main
from xewe.env import dotenv

X = "Xe" + "we"
BANNED = [f"{X}OS", f"{X}Core", f"{X} OS", "xe" + "we os"]
ALLOWED = ["XeWe OS", "XeWeCore", "xewe-os", "xewe", "XEWE_PORT", f"{X} 2", "xe" + "we osx"]


@pytest.fixture
def tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    monkeypatch.chdir(root)
    return root


@pytest.mark.parametrize("word", BANNED)
def test_banned_spelling_fails(tree: Path, word: str, capsys: pytest.CaptureFixture[str]) -> None:
    (tree / "a.md").write_text(f"line one\nthe {word} name\n")
    assert main(["brand-lint"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ("brand-lint: banned spellings found (see xewe-os/doc/naming.md):\n"
                            f"./a.md:2:the {word} name\n")


def test_allowed_spellings_are_clean(tree: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tree / "a.md").write_text("\n".join(ALLOWED) + "\n")
    assert main(["brand-lint", "."]) == 0
    assert capsys.readouterr().out == "brand-lint: clean\n"


def test_skipped_places(tree: Path, capsys: pytest.CaptureFixture[str]) -> None:
    word = BANNED[0]
    for rel in (".git/x", "build/x", ".venv/x", "static/x", "__pycache__/x", "history/x", "test_logs/x",
                "sub/build/x", "doc/naming.md", "NAMING.md", "Naming.MD", dotenv.FILENAME, dotenv.FILENAME + ".local"):
        (tree / rel).parent.mkdir(parents=True, exist_ok=True)
        (tree / rel).write_text(word + "\n")
    (tree / "bin.dat").write_bytes(word.encode() + b"\0\n")
    (tree / "latin1.txt").write_bytes(word.encode() + b" \xe9\n")
    assert main(["brand-lint"]) == 0
    (tree / "doc" / "other.md").write_text(word + "\n")
    (tree / ".github").mkdir()
    (tree / ".github" / "ci.yml").write_text("# " + word + "\n")
    assert brandlint.hits(["."]) == ["./.github/ci.yml:1:# " + word, "./doc/other.md:1:" + word]
    capsys.readouterr()


def test_several_dirs_and_missing_dir(tree: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tree / "a").mkdir()
    (tree / "b").mkdir()
    (tree / "b" / "f.txt").write_text(BANNED[2] + "\n")
    assert main(["brand-lint", "a"]) == 0
    assert main(["brand-lint", "a", "b"]) == 1
    assert "b/f.txt:1:" + BANNED[2] in capsys.readouterr().err
    assert main(["brand-lint", "nope"]) == 2
    assert "brand-lint: nope: no such file or directory" in capsys.readouterr().err


def test_needs_no_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    assert main(["brand-lint"]) == 0


def test_the_tools_repo_is_clean() -> None:
    assert brandlint.hits([str(Path(__file__).resolve().parents[2])]) == []
