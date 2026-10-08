"""``xewe flash`` and ``xewe run``: build if stale, write the merged image with esptool (SPEC §8)."""

from __future__ import annotations

from pathlib import Path

from xewe import boards, build, chips, config, esptool
from xewe.boards import Board
from xewe.lockfile import Lock
from xewe.project import Paths
from xewe.report import EXIT_FAIL, EXIT_OK, EXIT_USAGE, XeweError, log, no_board, result
from xewe.serialio import Console, wait_for_port

DEFAULT_BAUD = 921600
FALLBACK_BAUD = 460800


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
        raise XeweError(f"{p.rel(binary)} missing and --no-build given", EXIT_FAIL)
    res = build.build_chip(p, lock, chip, defines)
    if not res.ok or res.binary is None:
        raise XeweError(f"build failed for {chip}", EXIT_FAIL)
    return res.binary


def write_image(cmd: list[str], board: Board, chip: str, binary: Path, baud: int = DEFAULT_BAUD, erase: bool = False) -> None:
    """erase (optional) + write-flash at 0x0, retrying once at 460800 baud.

    esptool hard-resets the board after each command, which re-enumerates a native-USB port, so
    after erase-flash and after write-flash we wait until the port is back and stable
    (``wait_for_port``; exit 4 when it does not return).
    """
    esp_chip = chips.get(chip).esptool_id
    base = ["--chip", esp_chip, "--port", board.port]
    if erase:
        proc = esptool.run(cmd, [*base, "erase-flash"])
        if proc.returncode != 0:
            raise XeweError(f"esptool erase-flash failed:\n{proc.stdout.strip()[-2000:]}")
        wait_for_port(board.port, boards.port_exists)
    bauds = [baud] + ([FALLBACK_BAUD] if baud == DEFAULT_BAUD else [])
    for i, rate in enumerate(bauds):
        args = [*base, "--baud", str(rate), "--before", "default-reset", "--after", "hard-reset",
                "write-flash", "0x0", str(binary)]
        proc = esptool.run(cmd, args)
        if proc.returncode == 0:
            break
        if i + 1 < len(bauds):
            log.warning("esptool failed at %d baud; retrying at %d", rate, bauds[i + 1])
        else:
            raise XeweError(f"esptool write-flash failed:\n{proc.stdout.strip()[-2000:]}")
    wait_for_port(board.port, boards.port_exists)


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
) -> tuple[int, Board | None]:
    """Select the board, build if needed, flash. Returns (exit code, board or None)."""
    board = boards.select(p, chip=chip_flag, port=port, esptool_cmd=lambda: esptool_cmd(p))
    chip = boards.resolve_chip(chip_flag, board, lock.chip)
    chips.get(chip)
    binary = ensure_built(p, lock, chip, no_build, defines)
    if board is None:
        return no_board(chip, p.rel(binary), require_board), None
    if board.chip and board.chip != chip:
        raise XeweError(f"board on {board.port} is {board.chip}, selected chip is {chip}", EXIT_USAGE)
    write_image(esptool_cmd(p), board, chip, binary, baud, erase)
    result(f"flashed  {chip}  {board.port}  {p.rel(binary)}")
    return EXIT_OK, board


def run(
    p: Paths, lock: Lock, chip_flag: str | None, port: str | None, defines: dict[str, str], no_serial: bool, baud: int = 115200
) -> int:
    """build (when stale for these ``defines``) -> flash -> serial (what run.sh calls)."""
    code, board = flash_with_board(p, lock, chip_flag, port, defines=defines)
    if code != EXIT_OK or board is None or no_serial:
        return code
    with Console(board.port, baud, echo=True) as console:
        try:
            console.listen()
        except KeyboardInterrupt:
            pass
    return EXIT_OK
