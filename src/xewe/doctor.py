"""``xewe doctor``: report the state of the environment without changing it."""

from __future__ import annotations

import os
import platform
import shutil
import sys
from dataclasses import dataclass

try:
    import grp
except ImportError:  # Windows
    grp = None  # type: ignore[assignment]

from serial.tools import list_ports

from xewe import arduino, boards, build, config, esptool, lockcmd, pins
from xewe.lockfile import Lock
from xewe.project import Paths
from xewe.report import EXIT_NOT_SETUP, EXIT_OK, NO_BOARD, XeweError, log, result, verbose

OK, WARN, ERROR = "ok", "warn", "error"


@dataclass
class Check:
    """One doctor finding."""

    level: str
    name: str
    detail: str


def _serial_permissions() -> Check:
    if platform.system() != "Linux" or grp is None:
        return Check(OK, "serial access", "not checked on this OS")
    groups = {grp.getgrgid(g).gr_name for g in os.getgroups()}
    wanted = {"dialout", "uucp"}
    if groups & wanted:
        return Check(OK, "serial access", f"member of {', '.join(sorted(groups & wanted))}")
    present = [g for g in sorted(wanted) if _group_exists(g)]
    hint = f"sudo usermod -aG {present[0]} $USER, then log in again" if present else "no dialout/uucp group"
    return Check(WARN, "serial access", f"not in dialout/uucp ({hint})")


def _group_exists(name: str) -> bool:
    try:
        grp.getgrnam(name)
    except KeyError:
        return False
    return True


def checks(p: Paths, lock: Lock) -> list[Check]:
    """Run every check; never raises for a missing piece."""
    out = [Check(OK, "python", sys.version.split()[0])]
    git = shutil.which("git")
    out.append(Check(OK, "git", git) if git else Check(ERROR, "git", "not found (needed by setup)"))
    in_venv = sys.prefix != sys.base_prefix
    out.append(Check(OK if in_venv else WARN, "venv", sys.prefix if in_venv else "not running in a venv"))

    cfg = config.load(p)
    if cfg is None:
        out.append(Check(ERROR, "setup", "build/build_config.toml missing or incomplete; run ./setup.sh"))
    else:
        out.append(Check(OK, "setup", f"completed {cfg.setup_completed} by tools {cfg.tools_version}"))

    cli = build.cli_path(p, cfg)
    data = cfg.arduino_data(p) if cfg else p.default_arduino_data
    env_vars = arduino.env(p, data)
    cli_version = arduino.cli_version(cli, env_vars) if data.is_dir() else None
    if not data.is_dir():
        out.append(Check(ERROR, "arduino-cli", f"arduino data dir {data} missing"))
    elif cli_version is None:
        out.append(Check(ERROR, "arduino-cli", f"{p.rel(cli)} missing or does not run"))
    else:
        level = OK if cli_version == lock.arduino_cli_version else WARN
        out.append(Check(level, "arduino-cli", f"{cli_version} (lock wants {lock.arduino_cli_version})"))

    if cli_version is not None:
        core = arduino.core_version(cli, env_vars)
        level = OK if core == lock.esp32_version else ERROR
        out.append(Check(level, "esp32 core", f"{core or 'not installed'} in {data} (wants {lock.esp32_version})"))

    try:
        cmd = esptool.command(p, cfg.path(p, "esptool") if cfg else None, data)
        esp = esptool.version(cmd)
        out.append(Check(OK, "esptool", esp) if esp else Check(WARN, "esptool", f"{cmd[0]} does not run on this host"))
    except XeweError as exc:
        out.append(Check(WARN, "esptool", str(exc)))

    drift = [r.name for r in lockcmd.rows(p, lock) if r.drift]
    out.append(Check(WARN, "lock", f"installed differs from xewe.lock: {', '.join(drift)}") if drift
               else Check(OK, "lock", "installed matches xewe.lock"))

    probe = p.build if p.build.exists() else p.root
    free = shutil.disk_usage(probe).free
    out.append(Check(OK if free >= pins.MIN_FREE_DISK_BYTES else WARN, "disk", f"{free / 1024**3:.1f} GB free"))
    out.append(_serial_permissions())

    ports = [i for i in list_ports.comports() if i.vid is not None and boards.is_known(f"{i.vid:04x}", f"{i.pid:04x}")]
    out.append(Check(OK, "boards", ", ".join(i.device for i in ports) if ports else NO_BOARD))
    if verbose() and cli_version is not None:
        proc = arduino.run([str(cli), "board", "list", "--format", "json"], env_vars)
        log.debug("arduino-cli board list (cross-check):\n%s", proc.stdout.strip())
    return out


def doctor(p: Paths, lock: Lock) -> int:
    """Print the checks; exit 3 when any is an error, else 0."""
    found = checks(p, lock)
    for c in found:
        result(f"{c.level:<6} {c.name:<14} {c.detail}")
    return EXIT_NOT_SETUP if any(c.level == ERROR for c in found) else EXIT_OK
