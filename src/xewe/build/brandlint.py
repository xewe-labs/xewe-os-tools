"""``xewe brand-lint [DIR...]``: fail on banned spellings of the brand (see ``xewe-os/doc/naming.md``).

Scans every text file under each ``DIR`` (default ``.``) and prints ``<path>:<line>:<text>`` for
each line with a banned spelling. Skipped: the directories ``EXCLUDE_DIRS``, symlinks met while
walking, binary files (a NUL byte or not UTF-8), the naming page itself (``naming.md`` in any case;
it lists the banned spellings as examples) and dotenv files, whose lines are never printed.
Exit 0 and ``brand-lint: clean`` on stdout when nothing matched; exit 1 with the hits on stderr
otherwise; exit 2 when a ``DIR`` does not exist.
"""

from __future__ import annotations

import os
import re
import sys
from collections.abc import Iterator
from pathlib import Path

from xewe.env import dotenv
from xewe.report import EXIT_FAIL, EXIT_OK, EXIT_USAGE

PATTERN = re.compile(r"Xe" r"we[A-Za-z]|\bxe" r"we os\b|Xe" r"we OS")
"""The banned spellings (split so this file does not match itself)."""
EXCLUDE_DIRS = frozenset({".git", "build", ".venv", "static", "__pycache__", "history", "test_logs"})
NAMING_PAGE = "naming.md"


def _skipped_file(name: str) -> bool:
    return name.lower() == NAMING_PAGE or name == dotenv.FILENAME or name.startswith(dotenv.FILENAME + ".")


def _files(top: str) -> Iterator[str]:
    """Files under ``top`` in a stable order (``top`` itself when it is a file)."""
    if not os.path.isdir(top):
        if not _skipped_file(os.path.basename(top)):
            yield top
        return
    for dirpath, dirnames, filenames in os.walk(top):
        dirnames[:] = sorted(d for d in dirnames if d not in EXCLUDE_DIRS)
        for name in sorted(filenames):
            path = os.path.join(dirpath, name)
            if not _skipped_file(name) and not os.path.islink(path):
                yield path


def _text(path: str) -> str | None:
    try:
        data = Path(path).read_bytes()
    except OSError:
        return None
    if b"\0" in data:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def hits(dirs: list[str]) -> list[str]:
    """``<path>:<line>:<text>`` for every line under ``dirs`` with a banned spelling."""
    out: list[str] = []
    for top in dirs:
        for path in _files(top):
            text = _text(path)
            if text is None:
                continue
            for n, line in enumerate(text.splitlines(), 1):
                if PATTERN.search(line):
                    out.append(f"{path}:{n}:{line}")
    return out


def brand_lint(dirs: list[str]) -> int:
    """``xewe brand-lint``: see the module docstring for output and exit codes."""
    dirs = dirs or ["."]
    missing = [d for d in dirs if not os.path.exists(d)]
    if missing:
        for d in missing:
            print(f"brand-lint: {d}: no such file or directory", file=sys.stderr)
        return EXIT_USAGE
    found = hits(dirs)
    if found:
        print("brand-lint: banned spellings found (see xewe-os/doc/naming.md):", file=sys.stderr)
        print("\n".join(found), file=sys.stderr)
        return EXIT_FAIL
    print("brand-lint: clean")
    return EXIT_OK
