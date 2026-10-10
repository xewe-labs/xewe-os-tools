"""arduino-cli: install, isolated environment, esp32 core install, compile command line."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tarfile
import time
import zipfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

try:
    import fcntl
except ImportError:  # Windows: no advisory lock
    fcntl = None  # type: ignore[assignment]

from xewe.build.chips import Chip
from xewe.env import fetch, pins
from xewe.env.project import Paths
from xewe.report import EXIT_NOT_SETUP, XeWeError, log, verbose

CORE_ID = "esp32:esp32"
RETRY_DELAYS = (10, 20, 40, 80)  # between the 5 install attempts
MAX_RESCUES = 10
_HEAD_RE = re.compile(r'Head "([^"]+)"')


@contextmanager
def toolchain_lock(p: Paths) -> Iterator[None]:
    """Exclusive ``fcntl.flock`` on ``build-tools/.lock`` while installing into the shared toolchain,
    so two projects running setup at once do not install the cli or the core over each other."""
    p.toolchain_lock.parent.mkdir(parents=True, exist_ok=True)
    with p.toolchain_lock.open("a") as handle:
        if fcntl is not None:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                log.info("waiting for another setup to finish with %s", p.build_tools)
                fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(handle, fcntl.LOCK_UN)


def env(p: Paths, arduino_data: Path) -> dict[str, str]:
    """Environment for every arduino-cli call: nothing is read from ~/.arduino15 or ~/Arduino."""
    out = dict(os.environ)
    out.update(
        {
            "ARDUINO_DIRECTORIES_DATA": str(arduino_data),
            "ARDUINO_DIRECTORIES_USER": str(p.arduino_user),
            "ARDUINO_DIRECTORIES_DOWNLOADS": str(p.downloads),
            "ARDUINO_BOARD_MANAGER_ADDITIONAL_URLS": pins.ESP32_INDEX_URL,
            "ARDUINO_NETWORK_CONNECTION_TIMEOUT": "300s",
            "ARDUINO_UPDATER_ENABLE_NOTIFICATION": "false",
        }
    )
    return out


def cli_override() -> Path | None:
    """``XEWE_ARDUINO_CLI``: use this arduino-cli instead of the shared ``bin/arduino-cli-<version>``."""
    value = os.environ.get("XEWE_ARDUINO_CLI")
    return Path(value).expanduser().resolve() if value else None


def default_cli(p: Paths, version: str = pins.ARDUINO_CLI_VERSION) -> Path:
    """``build-tools/bin/arduino-cli-<version>`` (``.exe`` on Windows): one binary per pinned version."""
    return p.bin / (f"arduino-cli-{version}.exe" if os.name == "nt" else f"arduino-cli-{version}")


def run(argv: list[str], env_vars: dict[str, str], cwd: Path | None = None, timeout: float | None = None) -> subprocess.CompletedProcess[str]:
    """Run a command, merging stderr into stdout; ``--verbose`` echoes the command and output."""
    log.debug("$ %s", " ".join(argv))
    try:
        proc = subprocess.run(
            argv, env=env_vars, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=timeout
        )
    except FileNotFoundError:
        raise XeWeError(f"{argv[0]} not found; run ./setup.sh", EXIT_NOT_SETUP) from None
    if verbose() and proc.stdout:
        log.debug(proc.stdout.rstrip())
    return proc


def cli_version(cli: Path, env_vars: dict[str, str]) -> str | None:
    """Version printed by ``arduino-cli version`` (None if it does not run).

    Even ``version`` writes ``inventory.yaml`` into the data directory, so it gets the isolated env.
    """
    try:
        proc = subprocess.run([str(cli), "version"], env=env_vars, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    m = re.search(r"Version:\s*(\S+)", proc.stdout)
    return m[1] if proc.returncode == 0 and m else None


def install_cli(p: Paths, version: str, env_vars: dict[str, str]) -> Path:
    """Download arduino-cli ``version`` into the shared ``bin/`` (sha256-verified) and return its path.

    Nothing is downloaded when ``bin/arduino-cli-<version>`` already reports that version.
    """
    target = default_cli(p, version)
    if target.is_file() and cli_version(target, env_vars) == version:
        return target
    plat, ext = fetch.arduino_cli_platform()
    url = pins.ARDUINO_CLI_URL.format(version=version, platform=plat, ext=ext)
    name = url.rsplit("/", 1)[1]
    sums = fetch.parse_checksums(fetch.fetch_text(pins.ARDUINO_CLI_CHECKSUMS_URL.format(version=version)))
    if name not in sums:
        raise XeWeError(f"{name} is not listed in the arduino-cli {version} checksums")
    archive = fetch.download(url, p.downloads / "arduino-cli" / name, sums[name])
    p.bin.mkdir(parents=True, exist_ok=True)
    member = "arduino-cli.exe" if os.name == "nt" else "arduino-cli"
    tmp = target.with_name(target.name + ".tmp")
    if ext == "zip":
        with zipfile.ZipFile(archive) as zf, zf.open(member) as src, tmp.open("wb") as dst:
            shutil.copyfileobj(src, dst)
    else:
        with tarfile.open(archive) as tf:
            fileobj = tf.extractfile(member)
            if fileobj is None:
                raise XeWeError(f"{name} has no {member}")
            with fileobj, tmp.open("wb") as dst:
                shutil.copyfileobj(fileobj, dst)
    tmp.chmod(0o755)
    os.replace(tmp, target)
    got = cli_version(target, env_vars)
    if got != version:
        raise XeWeError(f"{target} reports version {got!r}, expected {version}", EXIT_NOT_SETUP)
    return target


def parse_core_list(output: str) -> str | None:
    """Installed esp32:esp32 version from ``core list --format json`` output."""
    try:
        data = json.loads(output)
    except json.JSONDecodeError:
        return None
    platforms = data.get("platforms", []) if isinstance(data, dict) else data
    for plat in platforms or []:
        if isinstance(plat, dict) and plat.get("id") == CORE_ID:
            return plat.get("installed_version") or plat.get("installed")
    return None


def core_version(cli: Path, env_vars: dict[str, str]) -> str | None:
    """Installed esp32 core version, or None."""
    proc = run([str(cli), "core", "list", "--format", "json"], env_vars)
    return parse_core_list(proc.stdout) if proc.returncode == 0 else None


def install_core(
    cli: Path, env_vars: dict[str, str], version: str, sleep: Callable[[float], None] = time.sleep
) -> None:
    """``core update-index`` + ``core install esp32:esp32@version`` with retries and URL rescue.

    A ``Head "<url>"`` failure (connection reset on a large archive) is rescued by downloading
    the URL with urllib into the arduino-cli downloads directory, then retrying.
    """
    proc = run([str(cli), "core", "update-index"], env_vars)
    if proc.returncode != 0:
        raise XeWeError(f"arduino-cli core update-index failed:\n{proc.stdout.strip()}")
    staging = Path(env_vars["ARDUINO_DIRECTORIES_DOWNLOADS"]) / "packages"
    attempts = rescues = 0
    while True:
        log.info("installing %s@%s (several GB on first install)", CORE_ID, version)
        proc = run([str(cli), "core", "install", f"{CORE_ID}@{version}"], env_vars)
        if proc.returncode == 0:
            break
        m = _HEAD_RE.search(proc.stdout)
        if m and rescues < MAX_RESCUES:
            rescues += 1
            url = m[1]
            log.warning("arduino-cli could not fetch %s; downloading it directly", url)
            fetch.download(url, staging / url.rsplit("/", 1)[1])
            continue
        if attempts >= len(RETRY_DELAYS):
            raise XeWeError(f"arduino-cli core install failed:\n{proc.stdout.strip()[-2000:]}")
        delay = RETRY_DELAYS[attempts]
        attempts += 1
        log.warning("core install failed (attempt %d of %d); retrying in %d s", attempts, len(RETRY_DELAYS) + 1, delay)
        sleep(delay)
    got = core_version(cli, env_vars)
    if got != version:
        raise XeWeError(f"{CORE_ID} reports {got!r} after install, expected {version}", EXIT_NOT_SETUP)


def config_flags(chip: Chip, p: Paths, sketch_dir: Path) -> str:
    """``compiler.cpp.extra_flags`` of the compile: every C++ translation unit (the sketch, the
    modules, every library and the esp32 core) starts with the project's ``Config.h``, so a value
    set there reaches the module sources too. The ``-I`` lets its ``#include <XeWeBuildInfo.h>``
    resolve in the core as well, which arduino-cli compiles with the core and variant include
    paths only. Paths are absolute and quoted; arduino-cli splits the recipe on unquoted spaces."""
    gen = p.gen_dir(chip.name) / "XeWeBuildInfo" / "src"
    return f'-I "{gen}" -include "{sketch_dir / "Config.h"}"'


def compile_argv(cli: str, chip: Chip, p: Paths, sketch_dir: Path) -> list[str]:
    """The exact ``arduino-cli compile`` argv for ``chip`` (paths relative to the project root)."""
    return [
        cli,
        "compile",
        "--fqbn",
        chip.fqbn,
        "--build-path",
        p.rel(p.cache_dir(chip.name)),
        "--libraries",
        p.rel(p.libraries),
        "--library",
        p.rel(p.modules),
        "--library",
        p.rel(p.gen_dir(chip.name) / "XeWeBuildInfo"),
        "--build-property",
        f"compiler.cpp.extra_flags={config_flags(chip, p, sketch_dir)}",
        "--warnings",
        "default",
        "--jobs",
        "0",
        p.rel(sketch_dir) if sketch_dir != p.root else ".",
    ]
