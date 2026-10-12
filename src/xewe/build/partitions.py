"""Partition schemes: the ``PartitionScheme`` FQBN option and its app partition size (doc/spec.md §7).

The schemes and their CSV files come from the installed esp32 core: ``boards.txt`` names, per
board, each ``menu.PartitionScheme.<id>`` and its ``build.partitions`` (the CSV stem under
``tools/partitions/``). A scheme is usable when the chip's board offers it, its CSV exists and every
partition ends inside the 4 MB the fixed ``FlashSize=4M`` option builds for.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from xewe.build import chips
from xewe.report import EXIT_NOT_SETUP, EXIT_USAGE, XeWeError

SCHEME_RE = re.compile(r"^[A-Za-z0-9_]+$")
"""What an FQBN option value may hold (no ``,``, ``:`` or ``=``)."""
_SIZE_RE = re.compile(r"^(0x[0-9a-fA-F]+|\d+)([KkMm]?)$")


@dataclass(frozen=True)
class Scheme:
    """A partition scheme resolved for one chip."""

    name: str
    csv: str
    app_size: int


def platform_dir(arduino_data: Path, esp32_version: str) -> Path:
    """The installed esp32 core: ``<arduino data>/packages/esp32/hardware/esp32/<version>``."""
    return arduino_data / "packages" / "esp32" / "hardware" / "esp32" / esp32_version


def offered(boards_txt: str, board: str) -> dict[str, str]:
    """``{scheme id: CSV stem}`` of every ``<board>.menu.PartitionScheme.<id>.build.partitions``."""
    prefix = f"{board}.menu.PartitionScheme."
    out: dict[str, str] = {}
    for line in boards_txt.splitlines():
        key, sep, value = line.strip().partition("=")
        if not sep or not key.startswith(prefix) or not key.endswith(".build.partitions"):
            continue
        out[key[len(prefix) : -len(".build.partitions")]] = value.strip()
    return out


def _number(text: str) -> int | None:
    m = _SIZE_RE.match(text.strip())
    if not m:
        return None
    n = int(m[1], 0)
    return n * {"": 1, "k": 1024, "m": 1024 * 1024}[m[2].lower()]


def parse_csv(text: str) -> list[tuple[str, str, int, int]]:
    """``(name, type, offset, size)`` of every partition in an ESP-IDF partition CSV.

    An empty offset follows the previous partition (aligned to 64 KB for an app, as ESP-IDF does)."""
    rows: list[tuple[str, str, int, int]] = []
    end = 0x9000
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        cells = [c.strip() for c in line.split(",")]
        if len(cells) < 5:
            continue
        name, ptype = cells[0], cells[1]
        size = _number(cells[4])
        if size is None:
            continue
        offset = _number(cells[3]) if cells[3] else None
        if offset is None:
            offset = (end + 0xFFFF) & ~0xFFFF if ptype == "app" else end
        rows.append((name, ptype, offset, size))
        end = offset + size
    return rows


def _first_app(rows: list[tuple[str, str, int, int]]) -> int | None:
    return next((size for _, ptype, _, size in rows if ptype == "app"), None)


def app_size(csv_text: str) -> int | None:
    """Size of the first app partition (the one the image boots from), None if there is none."""
    return _first_app(parse_csv(csv_text))


def resolve(platform: Path, chip: chips.Chip, scheme: str) -> Scheme | None:
    """``scheme`` checked against the installed core for ``chip``, with its app partition size.

    Exit 2 when the core does not offer it for the chip, or it needs more than 4 MB of flash.
    Without an installed core: None for the default scheme (arduino-cli checks the size, as
    before), exit 3 for any other.
    """
    boards = platform / "boards.txt"
    if not boards.is_file():
        if scheme == chips.DEFAULT_PARTITION_SCHEME:
            return None
        raise XeWeError(f"{boards} not found; run ./setup.sh", EXIT_NOT_SETUP)
    schemes = offered(boards.read_text(encoding="utf-8", errors="replace"), chip.board)
    usable: dict[str, Scheme] = {}
    why: dict[str, str] = {}
    for name, stem in schemes.items():
        csv = platform / "tools" / "partitions" / f"{stem}.csv"
        if not stem:
            why[name] = "takes the sketch's own partitions.csv, which xewe does not support"
            continue
        if not csv.is_file():
            why[name] = f"has no {stem}.csv in the core"
            continue
        rows = parse_csv(csv.read_text(encoding="utf-8", errors="replace"))
        size = _first_app(rows)
        if size is None:
            why[name] = f"has no app partition in {stem}.csv"
        elif max(offset + n for _, _, offset, n in rows) > chips.FLASH_SIZE:
            why[name] = "needs more than 4 MB of flash"
        else:
            usable[name] = Scheme(name, stem, size)
    if scheme not in usable:
        raise XeWeError(
            f"xewe.toml: [build] partition_scheme '{scheme}' {why.get(scheme, 'is not offered')} for {chip.board}"
            f" by esp32 core {platform.name} (choose one of: {', '.join(sorted(usable))})",
            EXIT_USAGE,
        )
    return usable[scheme]
