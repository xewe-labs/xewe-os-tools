"""``xewe flash`` and ``xewe run``: build if stale, write the merged image with esptool (SPEC §8).

``xewe run`` erases the whole flash (NVS too) first by default, so every run is a true first boot
(``--keep-nvs`` skips the erase); ``xewe flash`` keeps NVS unless ``--erase`` is given.
"""

from __future__ import annotations

import struct
import tempfile
from pathlib import Path

from xewe.board import boards
from xewe.board.boards import Board
from xewe.board.serialio import Console, console_session, wait_for_port
from xewe.build import chips
from xewe.build import compile as build
from xewe.env import config, esptool
from xewe.env.project import Paths
from xewe.modules.lockfile import Lock
from xewe.report import EXIT_FAIL, EXIT_OK, EXIT_USAGE, XeWeError, board_disabled, board_disabled_exit, log, no_board, result

DEFAULT_BAUD = 921600
FALLBACK_BAUD = 460800

ERASED_HINT = "flash erased: first boot"
KEPT_HINT = "nvs kept"
"""Suffixes of the ``flashed`` line: whether the board starts with a first boot or keeps its name,
Wi-Fi and module choices."""

PARTITION_TABLE_OFFSET = 0x8000
"""Where the ESP-IDF/Arduino partition table sits in a merged image (all ESP32 chips)."""
PARTITION_ENTRY = struct.Struct("<2sBBII16sI")
PARTITION_MAGIC = b"\xaa\x50"
PARTITION_TYPE_DATA = 1


def flash_segments(image: bytes) -> list[tuple[int, bytes]]:
    """(offset, bytes) pieces of a merged image to write, leaving out data partitions it carries
    nothing for.

    The merged image spans the whole flash, so writing it at 0x0 fills NVS (provisioning, Wi-Fi
    credentials), SPIFFS and coredump with 0xFF: a flash "without erase" would still wipe them.
    Every data partition whose bytes in the image are all 0xFF is skipped; everything else
    (bootloader, partition table, otadata, app) is written. Without a readable partition table
    the whole image is one piece at 0x0.
    """
    spans: list[tuple[int, int]] = []
    for i in range(PARTITION_TABLE_OFFSET, min(len(image), PARTITION_TABLE_OFFSET + 0xC00), PARTITION_ENTRY.size):
        entry = image[i:i + PARTITION_ENTRY.size]
        if len(entry) < PARTITION_ENTRY.size or entry[:2] != PARTITION_MAGIC:
            break
        _, ptype, _, offset, size, _, _ = PARTITION_ENTRY.unpack(entry)
        end = min(offset + size, len(image))
        if ptype == PARTITION_TYPE_DATA and offset < end and image[offset:end].count(0xFF) == end - offset:
            spans.append((offset, end))
    pieces: list[tuple[int, bytes]] = []
    pos = 0
    for start, end in sorted(spans):
        if start > pos:
            pieces.append((pos, image[pos:start]))
        pos = max(pos, end)
    if pos < len(image):
        pieces.append((pos, image[pos:]))
    return pieces


def esptool_cmd(p: Paths) -> list[str]:
    """esptool command for this project (override, recorded path, or core glob)."""
    cfg = config.load(p)
    if cfg is None:
        return esptool.command(p)
    return esptool.command(p, cfg.path(p, "esptool"), cfg.arduino_data(p))


def ensure_built(
    p: Paths, lock: Lock, chip: str, no_build: bool = False, defines: dict[str, str] | None = None
) -> Path:
    """Path of the chip's merged image, building it first when missing, older than a source, or
    built from other inputs (``--define`` values, version, chip, FQBN options; see ``build_key``)."""
    defines = defines or {}
    binary = p.out_dir(chip) / build.binary_name(lock.version, chip, build.project_name(p, lock))
    if build.is_up_to_date(p, binary, build.build_key(chip, lock.version, defines)):
        result(f"build  {chip}  up to date ({p.rel(binary)})")
        return binary
    if no_build:
        if binary.is_file():
            return binary
        raise XeWeError(f"{p.rel(binary)} missing and --no-build given", EXIT_FAIL)
    res = build.build_chip(p, lock, chip, defines)
    if not res.ok or res.binary is None:
        raise XeWeError(f"build failed for {chip}", EXIT_FAIL)
    return res.binary


def write_image(
    cmd: list[str], board: Board, chip: str, binary: Path, baud: int = DEFAULT_BAUD, erase: bool = False,
    settle: float | None = None,
) -> None:
    """erase (optional) + write-flash of the merged image, retrying once at 460800 baud.

    The image is written in pieces (``flash_segments``) so that data partitions the image leaves
    blank (NVS with the provisioning, SPIFFS, coredump) keep their contents; only ``--erase``
    clears them.

    esptool hard-resets the board after each command, which re-enumerates a native-USB port, so
    after erase-flash and after write-flash we wait until the port is back and stable
    (``wait_for_port``, ``settle`` seconds in a row; exit 4 when it does not return). When the
    board comes back under another name (macOS ``cu.usbmodem<N>``, matched by USB serial number),
    ``board.port`` is updated to it and a warning says so.
    """
    esp_chip = chips.get(chip).esptool_id
    base = ["--chip", esp_chip, "--port", board.port]
    if erase:
        proc = esptool.run(cmd, [*base, "erase-flash"])
        if proc.returncode != 0:
            raise XeWeError(f"esptool erase-flash failed:\n{proc.stdout.strip()[-2000:]}")
        _settle(board, settle)
    bauds = [baud] + ([FALLBACK_BAUD] if baud == DEFAULT_BAUD else [])
    image = binary.read_bytes()
    pieces = flash_segments(image)
    with tempfile.TemporaryDirectory(prefix="xewe-flash-") as tmp:
        if len(pieces) == 1 and pieces[0][0] == 0:
            files = ["0x0", str(binary)]
        else:
            files = []
            for offset, data in pieces:
                part = Path(tmp) / f"{binary.stem}-{offset:#x}.bin"
                part.write_bytes(data)
                files += [f"{offset:#x}", str(part)]
            log.debug("writing %s; data partitions left as they are (NVS kept)", " ".join(files[::2]))
        for i, rate in enumerate(bauds):
            args = [*base, "--baud", str(rate), "--before", "default-reset", "--after", "hard-reset",
                    "write-flash", *files]
            proc = esptool.run(cmd, args)
            if proc.returncode == 0:
                break
            if i + 1 < len(bauds):
                log.warning("esptool failed at %d baud; retrying at %d", rate, bauds[i + 1])
            else:
                raise XeWeError(f"esptool write-flash failed:\n{proc.stdout.strip()[-2000:]}")
    _settle(board, settle)


def _settle(board: Board, settle: float | None) -> None:
    """Wait for the re-enumerated port; follow a rename (``wait_for_port``)."""
    port = wait_for_port(board.port, boards.port_exists, settle=settle, renamed=lambda: boards.renamed_port(board))
    if port != board.port:
        log.warning("%s came back as %s after the reset (same USB serial number); using %s", board.port, port, port)
        board.port = port


def flash_with_board(
    p: Paths,
    lock: Lock,
    chip_flag: str | None = None,
    port: str | None = None,
    baud: int = DEFAULT_BAUD,
    erase: bool = False,
    no_build: bool = False,
    require_board: bool = False,
    defines: dict[str, str] | None = None,
    settle: float | None = None,
) -> tuple[int, Board | None]:
    """Select the board, build if needed, flash. Returns (exit code, board or None)."""
    board = boards.select(p, chip=chip_flag, port=port, esptool_cmd=lambda: esptool_cmd(p))
    chip = boards.resolve_chip(chip_flag, board, lock.chip)
    chips.get(chip)
    binary = ensure_built(p, lock, chip, no_build, defines)
    if board_disabled():
        return board_disabled_exit(chip, p.rel(binary)), None
    if board is None:
        return no_board(chip, p.rel(binary), require_board), None
    if board.chip and board.chip != chip:
        raise XeWeError(f"board on {board.port} is {board.chip}, selected chip is {chip}", EXIT_USAGE)
    write_image(esptool_cmd(p), board, chip, binary, baud, erase, settle)
    result(f"flashed  {chip}  {board.port}  {p.rel(binary)}  ({ERASED_HINT if erase else KEPT_HINT})")
    return EXIT_OK, board


def run(
    p: Paths, lock: Lock, chip_flag: str | None, port: str | None, defines: dict[str, str], no_serial: bool, baud: int = 115200,
    no_input: bool = False, timestamps: bool = False, erase: bool = True, settle: float | None = None,
) -> int:
    """build (when stale for these ``defines``) -> erase (unless ``erase`` is False, i.e. ``--keep-nvs``)
    -> flash -> serial console, interactive on a terminal (what run.sh calls)."""
    code, board = flash_with_board(p, lock, chip_flag, port, erase=erase, defines=defines, settle=settle)
    if code != EXIT_OK or board is None or no_serial:
        return code
    with Console(board.port, baud, echo=True) as console:
        console_session(console, no_input=no_input, timestamps=timestamps)
    return EXIT_OK
