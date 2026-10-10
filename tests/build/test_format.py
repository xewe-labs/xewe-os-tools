import json
import os
import sys
from pathlib import Path

import pytest

from conftest import FAKES, write_project
from xewe.build import format as fmt
from xewe.cli import main
from xewe.env.project import Paths

ALIGNED = "int  a   = 1;\nlong bb  = 2;\n"
UNALIGNED = "int a = 1;\nlong bb = 2;\n"


@pytest.fixture
def proj(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Paths:
    """A project with C++ in every default place (and in places that must be skipped)."""
    p = write_project(tmp_path / "proj")
    for rel in ("Config.h", "src/Foo/Foo.h", "src/Foo/Foo.cpp", "tests/unit/test_foo.cpp", "examples/A/A.ino",
                "build/libraries/X/X.h", "src/Modules.h", ".hidden/h.h", "tests/board/skip.cpp", "src/notes.txt"):
        (p.root / rel).parent.mkdir(parents=True, exist_ok=True)
        (p.root / rel).write_text(ALIGNED)
    (p.root / "proj.ino").write_text(UNALIGNED)  # the sketch, already formatted
    monkeypatch.setenv("XEWE_CLANG_FORMAT", str(FAKES / "clang-format"))
    monkeypatch.setenv("FAKE_CLANG_FORMAT_LOG", str(tmp_path / "cf.jsonl"))
    monkeypatch.setattr(fmt, "ruff_available", lambda: False)
    monkeypatch.chdir(p.root)
    return p


def _calls(tmp_path: Path) -> list[list[str]]:
    log = tmp_path / "cf.jsonl"
    return [json.loads(ln) for ln in log.read_text().splitlines()] if log.exists() else []


def test_collect_defaults(proj: Paths) -> None:
    files = [proj.rel(f) for f in fmt.collect(proj, [], fmt.CPP_SUFFIXES, fmt.CPP_DIRS)]
    assert sorted(files) == ["Config.h", "examples/A/A.ino", "proj.ino", "src/Foo/Foo.cpp", "src/Foo/Foo.h",
                             "tests/unit/test_foo.cpp"]


def test_check_lists_and_changes_nothing(proj: Paths, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["format", "--check"]) == 1
    out = capsys.readouterr().out
    assert "would reformat src/Foo/Foo.h\n" in out and "would reformat proj.ino" not in out
    assert "format: 6 C++ and 0 Python files, 5 would reformat" in out
    assert (proj.root / "src/Foo/Foo.h").read_text() == ALIGNED
    assert (proj.root / "build/libraries/X/X.h").read_text() == ALIGNED


def test_format_rewrites_then_check_is_clean(proj: Paths, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    os.chmod(proj.root / "Config.h", 0o640)
    assert main(["format"]) == 0
    assert (proj.root / "src/Foo/Foo.h").read_text() == UNALIGNED
    assert (proj.root / "Config.h").stat().st_mode & 0o777 == 0o640
    assert (proj.root / "src/Modules.h").read_text() == ALIGNED  # generated: never touched
    assert (proj.root / "build/libraries/X/X.h").read_text() == ALIGNED
    assert main(["format", "--check"]) == 0
    assert "0 would reformat" in capsys.readouterr().out
    # the project's .clang-format is not there: the bundled style goes inline
    assert all(c[0].startswith("--style={BasedOnStyle: LLVM, IndentWidth: 4,") for c in _calls(tmp_path))


def test_project_clang_format_and_paths(proj: Paths, tmp_path: Path) -> None:
    (proj.root / ".clang-format").write_text("BasedOnStyle: LLVM\n")
    assert main(["format", "src/Foo/Foo.h"]) == 0
    assert _calls(tmp_path) == [[f"--style=file:{proj.root / '.clang-format'}", str(proj.root / "src/Foo/Foo.h")]]
    assert (proj.root / "src/Foo/Foo.cpp").read_text() == ALIGNED
    assert main(["format", "nope.h"]) == 2


def test_clang_format_failure(proj: Paths, capsys: pytest.CaptureFixture[str]) -> None:
    (proj.root / "src/Foo/Foo.h").write_text("FAKE_CLANG_FORMAT_FAIL\n")
    assert main(["format", "--check"]) == 1
    assert "clang-format failed on src/Foo/Foo.h" in capsys.readouterr().err


def test_bundled_style_matches_the_rules() -> None:
    style = fmt.inline_style()
    for rule in ("AlignConsecutiveDeclarations: None", "AlignTrailingComments: false", "AlignOperands: DontAlign",
                 "AlignAfterOpenBracket: DontAlign", "SortIncludes: false", "PointerAlignment: Left",
                 "ColumnLimit: 100", "AllowShortFunctionsOnASingleLine: Empty"):
        assert rule in style
    assert fmt.inline_style("# c\nA: 1\n\nB: x\n") == "{A: 1, B: x}"


def test_find_order(proj: Paths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("XEWE_CLANG_FORMAT")
    monkeypatch.setattr(fmt.sys, "executable", str(tmp_path / "venv" / "bin" / "python"))
    bindir = tmp_path / "pathbin"
    bindir.mkdir()
    monkeypatch.setenv("PATH", str(bindir))
    with pytest.raises(fmt.XeWeError) as exc:
        fmt.find_clang_format(proj)
    assert exc.value.code == 3 and "pip install clang-format" in str(exc.value)
    shipped = proj.default_arduino_data / "packages/esp32/tools/llvm/1.0/bin/clang-format"
    shipped.parent.mkdir(parents=True)
    shutil_copy(FAKES / "clang-format", shipped)
    assert fmt.find_clang_format(proj) == shipped
    venv = tmp_path / "venv" / "bin" / "clang-format"
    venv.parent.mkdir(parents=True)
    shutil_copy(FAKES / "clang-format", venv)
    assert fmt.find_clang_format(proj) == venv
    shutil_copy(FAKES / "clang-format", bindir / "clang-format")
    assert fmt.find_clang_format(proj) == bindir / "clang-format"
    monkeypatch.setenv("XEWE_CLANG_FORMAT", str(tmp_path / "missing"))
    with pytest.raises(fmt.XeWeError):
        fmt.find_clang_format(proj)


def shutil_copy(src: Path, dst: Path) -> None:
    dst.write_bytes(src.read_bytes())
    os.chmod(dst, 0o755)


def test_python_through_ruff(proj: Paths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                             capsys: pytest.CaptureFixture[str]) -> None:
    (proj.root / "tests/board/skip.cpp").unlink()
    (proj.root / "tests/board/test_x.py").write_text("x=1\n")
    (proj.root / "build/skip.py").write_text("x=1\n")
    fake = tmp_path / "ruff.py"
    fake.write_text(
        "import json, os, sys\n"
        "open(os.environ['RUFF_LOG'], 'a').write(json.dumps(sys.argv[1:]) + '\\n')\n"
        "if '--check' in sys.argv:\n"
        "    print('Would reformat: ' + sys.argv[-1]); sys.exit(1)\n"
    )
    monkeypatch.setenv("RUFF_LOG", str(tmp_path / "ruff.jsonl"))
    monkeypatch.setattr(fmt, "RUFF", [sys.executable, str(fake)])
    assert main(["format", "--check", "tests/board"]) == 0  # ruff missing: Python skipped with a note
    monkeypatch.setattr(fmt, "ruff_available", lambda: True)
    assert main(["format", "--check", "tests/board"]) == 1
    assert "would reformat tests/board/test_x.py" in capsys.readouterr().out
    assert main(["format", "tests/board"]) == 0
    argvs = [json.loads(ln) for ln in (tmp_path / "ruff.jsonl").read_text().splitlines()]
    assert argvs == [["format", "--check", str(proj.root / "tests/board/test_x.py")],
                     ["format", "--quiet", str(proj.root / "tests/board/test_x.py")]]
