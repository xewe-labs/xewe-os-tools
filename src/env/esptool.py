"""The esptool bundled with the pinned esp32 core (``XEWE_ESPTOOL`` overrides it)."""

from __future__ import annotations

import os
import re
import shlex
import subprocess
from pathlib import Path

from xewe.env.project import Paths
from xewe.report import EXIT_NOT_SETUP, XeWeError, log

_EXE = "esptool.exe" if os.name == "nt" else "esptool"


def find_in(arduino_data: Path) -> Path | None:
    """Newest ``packages/esp32/tools/esptool_py/*/esptool`` under an arduino data dir."""
    found = sorted((arduino_data / "packages" / "esp32" / "tools" / "esptool_py").glob(f"*/{_EXE}"))
    return found[-1] if found else None


def command(p: Paths, recorded: Path | None = None, arduino_data: Path | None = None) -> list[str]:
    """Command prefix to run esptool: XEWE_ESPTOOL, else the recorded path, else a glob."""
    override = os.environ.get("XEWE_ESPTOOL")
    if override:
        return shlex.split(override)
    if recorded is not None and recorded.is_file():
        return [str(recorded)]
    found = find_in(arduino_data or p.default_arduino_data)
    if found is None:
        raise XeWeError("esptool not found in the esp32 core; run ./setup.sh (or set XEWE_ESPTOOL)", EXIT_NOT_SETUP)
    return [str(found)]


def run(cmd: list[str], args: list[str], timeout: float | None = None) -> subprocess.CompletedProcess[str]:
    """Run esptool with ``args``; stdout+stderr merged."""
    argv = [*cmd, *args]
    log.debug("$ %s", shlex.join(argv))
    try:
        return subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=timeout)
    except FileNotFoundError:
        raise XeWeError(f"{cmd[0]} not found; run ./setup.sh (or set XEWE_ESPTOOL)", EXIT_NOT_SETUP) from None
    except OSError as exc:
        raise XeWeError(f"{cmd[0]} cannot run on this host: {exc}", EXIT_NOT_SETUP) from None


def version(cmd: list[str]) -> str | None:
    """``esptool version`` output's version number, or None if it does not run."""
    try:
        proc = run(cmd, ["version"], timeout=60)
    except (XeWeError, subprocess.TimeoutExpired):
        return None
    m = re.search(r"(\d+\.\d+(?:\.\d+)?\S*)", proc.stdout)
    return m[1] if proc.returncode == 0 and m else None


def parse_chip(output: str) -> str | None:
    """Chip name from esptool output: ``c3``/``c6``/``s3``, another ``ESP32-xx`` id, or None."""
    m = re.search(r"ESP32-([A-Z0-9]+)", output)
    if not m:
        return "esp32" if re.search(r"Chip (?:is|type:)\s*ESP32\b", output) else None
    return m[1].lower()


def chip_id(cmd: list[str], port: str) -> str | None:
    """Probe the chip on ``port`` (resets the board)."""
    try:
        proc = run(cmd, ["--port", port, "--before", "default-reset", "--after", "hard-reset", "chip-id"], timeout=20)
    except subprocess.TimeoutExpired:
        log.warning("esptool chip-id on %s timed out", port)
        return None
    return parse_chip(proc.stdout) if proc.returncode == 0 else None
