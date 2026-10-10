"""``xewe format [--check] [PATH...]``: clang-format the project's C++, ruff-format its Python (SPEC §3).

AI-first style: no column alignment anywhere (runs of alignment whitespace cost tokens, break
exact-match edits and turn a one-line change into a realigned block). The style is the project's
``.clang-format`` (the template ships one) or, without it, ``BUNDLED_STYLE`` (the same rules).

Default files: ``*.h *.hpp *.c *.cpp *.ino`` directly in the project root (the sketch,
``Config.h``) and under ``src/``, ``examples/`` and ``tests/unit/``; never ``build/``, hidden
directories or the generated ``src/Modules.h``. ``PATH`` arguments (files or directories) replace
that set. Python files (``*.py``, same places plus ``tests/``) go through ``ruff format`` when ruff
is installed in the tools venv (``pip install 'xewe-os-tools[dev]'``); otherwise they are skipped
with a note.

The clang-format binary: ``XEWE_CLANG_FORMAT``, else ``clang-format`` on ``PATH``, else one in the
tools venv (``pip install clang-format``), else one shipped inside the shared toolchain's esp32 core
(``build-tools/arduino15/packages/esp32/tools/``), else exit 3 with the install hint.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from collections.abc import Iterable
from pathlib import Path

from xewe.env.project import Paths, write_atomic
from xewe.report import EXIT_FAIL, EXIT_NOT_SETUP, EXIT_OK, EXIT_USAGE, XeWeError, log, result

CPP_SUFFIXES = frozenset({".h", ".hpp", ".c", ".cpp", ".ino"})
CPP_DIRS = ("src", "examples", "tests/unit")
PY_DIRS = ("src", "examples", "tests")
SKIP_DIRS = frozenset({"build", "__pycache__", "node_modules"})
GENERATED = ("src/Modules.h",)

INSTALL_HINT = ("clang-format not found: install it (apt install clang-format, brew install clang-format, "
                "or build/tools/.venv/bin/python -m pip install clang-format), or set XEWE_CLANG_FORMAT")

BUNDLED_STYLE = """\
BasedOnStyle: LLVM
IndentWidth: 4
ContinuationIndentWidth: 4
AccessModifierOffset: -4
ColumnLimit: 100
UseTab: Never
AlignConsecutiveAssignments: None
AlignConsecutiveBitFields: None
AlignConsecutiveDeclarations: None
AlignConsecutiveMacros: None
AlignArrayOfStructures: None
AlignEscapedNewlines: DontAlign
AlignTrailingComments: false
AlignOperands: DontAlign
AlignAfterOpenBracket: DontAlign
BreakBeforeBraces: Attach
PointerAlignment: Left
DerivePointerAlignment: false
AllowShortFunctionsOnASingleLine: Empty
SortIncludes: false
"""
"""The fallback style (the template's ``.clang-format`` without comments), passed inline."""

RUFF = [sys.executable, "-m", "ruff"]
"""How ruff is run (the tools venv's own interpreter); tests replace it."""


def inline_style(text: str = BUNDLED_STYLE) -> str:
    """``BUNDLED_STYLE`` as clang-format's inline ``--style={key: value, ...}`` (works on every version)."""
    pairs = [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    return "{" + ", ".join(pairs) + "}"


def find_clang_format(p: Paths) -> Path:
    """The clang-format to run (see the module docstring for the order); exit 3 when there is none."""
    override = os.environ.get("XEWE_CLANG_FORMAT")
    if override:
        found = shutil.which(override)
        if found is None:
            raise XeWeError(f"XEWE_CLANG_FORMAT={override} is not an executable", EXIT_NOT_SETUP)
        return Path(found)
    on_path = shutil.which("clang-format")
    if on_path:
        return Path(on_path)
    venv = Path(sys.executable).parent / "clang-format"
    if venv.is_file() and os.access(venv, os.X_OK):
        return venv
    tools = p.default_arduino_data / "packages" / "esp32" / "tools"
    for pattern in ("*/bin/clang-format", "*/*/bin/clang-format", "*/*/*/bin/clang-format", "*/*/clang-format"):
        for cand in sorted(tools.glob(pattern)):
            if cand.is_file() and os.access(cand, os.X_OK):
                return cand
    raise XeWeError(INSTALL_HINT, EXIT_NOT_SETUP)


def _walk(path: Path, suffixes: Iterable[str]) -> list[Path]:
    """Files under ``path`` with one of ``suffixes``, skipping build/, hidden and cache directories."""
    if path.is_file():
        return [path] if path.suffix in suffixes else []
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(path):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith("."))
        found += [Path(dirpath) / f for f in sorted(filenames) if Path(f).suffix in suffixes]
    return found


def collect(p: Paths, paths: list[Path], suffixes: Iterable[str], dirs: tuple[str, ...]) -> list[Path]:
    """Files to format: the given ``paths`` (files or directories), else the project defaults."""
    suffixes = frozenset(suffixes)
    if paths:
        out: list[Path] = []
        for path in paths:
            path = path if path.is_absolute() else (Path.cwd() / path)
            if not path.exists():
                raise XeWeError(f"{path}: no such file or directory", EXIT_USAGE)
            out += _walk(path.resolve(), suffixes)
    else:
        out = sorted(f for f in p.root.iterdir() if f.is_file() and f.suffix in suffixes)
        for d in dirs:
            if (p.root / d).is_dir():
                out += _walk(p.root / d, suffixes)
    build = p.build.resolve()
    skip = {(p.root / g).resolve() for g in GENERATED}
    unique: dict[Path, None] = {}
    for f in out:
        r = f.resolve()
        if r not in skip and build not in r.parents:
            unique[f] = None
    return list(unique)


def _style(p: Paths) -> str:
    own = p.root / ".clang-format"
    if own.is_file():
        return f"--style=file:{own}"
    log.info("no .clang-format in the project; using the bundled XeWe style")
    return f"--style={inline_style()}"


def format_cpp(p: Paths, files: list[Path], check: bool) -> list[Path]:
    """Run clang-format over ``files``; return the files that change (rewritten unless ``check``)."""
    if not files:
        return []
    exe = find_clang_format(p)
    style = _style(p)
    changed: list[Path] = []
    for f in files:
        argv = [str(exe), style, str(f)]
        log.debug("$ %s", " ".join(argv))
        proc = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", check=False)
        if proc.returncode != 0:
            raise XeWeError(f"clang-format failed on {p.rel(f)}: {proc.stderr.strip()}", EXIT_FAIL)
        if proc.stdout != f.read_text(encoding="utf-8"):
            changed.append(f)
            if not check:
                mode = f.stat().st_mode
                write_atomic(f, proc.stdout)
                os.chmod(f, mode)
    return changed


def ruff_available() -> bool:
    """True when ruff is importable in the tools venv."""
    return importlib.util.find_spec("ruff") is not None


def format_python(p: Paths, files: list[Path], check: bool) -> list[Path]:
    """``ruff format`` (``--check``) over ``files``; return the files that change."""
    if not files:
        return []
    if not ruff_available():
        log.info("ruff is not installed; %d Python files not checked (pip install 'xewe-os-tools[dev]')", len(files))
        return []
    argv = [*RUFF, "format", "--check" if check else "--quiet", *map(str, files)]
    log.debug("$ %s", " ".join(argv))
    proc = subprocess.run(argv, capture_output=True, text=True, cwd=p.root, check=False)
    if proc.returncode not in (0, 1) or (proc.returncode == 1 and not check):
        raise XeWeError(f"ruff format failed: {(proc.stderr or proc.stdout).strip()}", EXIT_FAIL)
    if not check:
        return []  # ruff rewrote the files itself; it does not list them with --quiet
    prefix = "Would reformat: "
    return [Path(ln.removeprefix(prefix).strip()) for ln in proc.stdout.splitlines() if ln.startswith(prefix)]


def format_main(p: Paths, paths: list[Path], check: bool) -> int:
    """``xewe format``: exit 0 when nothing (more) to change, 1 with ``--check`` when files would change."""
    cpp = collect(p, paths, CPP_SUFFIXES, CPP_DIRS)
    py = collect(p, paths, {".py"}, PY_DIRS)
    changed = format_cpp(p, cpp, check) + format_python(p, py, check)
    verb = "would reformat" if check else "formatted"
    for f in changed:
        result(f"{verb} {p.rel(f if f.is_absolute() else p.root / f)}")
    result(f"format: {len(cpp)} C++ and {len(py)} Python files, {len(changed)} {verb}")
    return EXIT_FAIL if check and changed else EXIT_OK
