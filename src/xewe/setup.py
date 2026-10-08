"""``xewe setup``: install everything a project needs into ``build/`` (SPEC §6).

Each step is skipped when what it would install is already there at the wanted version/ref
(``--force`` redoes the source checkouts). ``build/build_config.toml`` is removed at the start
and written last, so an interrupted setup is detected by every other command (exit 3).
"""

from __future__ import annotations

import datetime as dt
import os
import re
import shutil
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from xewe import __version__, arduino, config, esptool, fetch, lockfile, modules, pins
from xewe.lockfile import Source
from xewe.project import Paths
from xewe.report import EXIT_FAIL, EXIT_NOT_SETUP, EXIT_OK, EXIT_USAGE, XeweError, log, result

CORE_LIBRARY = "XeWeCore"


@dataclass
class SetupOptions:
    """Command-line options of ``xewe setup``."""

    latest: bool = False
    modules: str | None = None
    force: bool = False
    core_source: Path | None = None
    modules_source: Path | None = None
    arduino_data: Path | None = None


def _env_path(name: str) -> Path | None:
    value = os.environ.get(name)
    return Path(value).expanduser().resolve() if value else None


def _free_bytes(path: Path) -> int:
    probe = path
    while not probe.exists():
        probe = probe.parent
    return shutil.disk_usage(probe).free


def _install_source(
    label: str, src: Source, ref: str, dest: Path, local: Path | None, prev: dict[str, Any], force: bool
) -> dict[str, str]:
    """Clone ``src.repo`` at ``ref`` into ``dest`` (or copy ``local``); return the install record."""
    if local is not None:
        only = None
        if dest.name == "xewe-os-modules" and not (local / "modules").is_dir() and not (local / "module.properties").is_file():
            only = sorted(d.name for d in local.glob(f"{modules.LEGACY_PREFIX}*") if d.is_dir())
        log.info("%s: copying local source %s", label, local)
        fetch.copy_tree(local, dest, only)
        return {"ref": ref, "commit": fetch.head_commit(local), "source": f"local:{local}"}
    if (
        not force
        and dest.is_dir()
        and prev.get("ref") == ref
        and prev.get("source") == src.repo
        and prev.get("commit") == fetch.head_commit(dest)
    ):
        log.debug("%s: %s already at %s", label, dest.name, ref)
        return dict(prev)
    log.info("%s: cloning %s at %s", label, src.repo, ref)
    commit = fetch.clone(src.repo, ref, dest)
    return {"ref": ref, "commit": commit, "source": src.repo}


def _menu(registry: modules.Registry) -> list[str]:
    """Numbered module menu on stderr; returns the chosen slugs."""
    slugs = registry.slugs()
    print("Available modules:", file=sys.stderr)
    for i, slug in enumerate(slugs, 1):
        print(f"  {i:2d}. {slug:<16} {registry.by_slug[slug].props.get('description', '')}", file=sys.stderr)
    choice = input("Modules to install (numbers or names, 'all'; Enter for none): ").strip()
    if choice in modules.NONE_WORDS:
        return []
    if choice == "all":
        return slugs
    out = []
    for word in re.split(r"[,\s]+", choice):
        if word.isdigit():
            if not 1 <= int(word) <= len(slugs):
                raise XeweError(f"no module number {word}", EXIT_USAGE)
            out.append(slugs[int(word) - 1])
        elif word:
            registry.get(word)
            out.append(word)
    return out


def _legacy_version_hint(p: Paths) -> None:
    state = p.build / "version_state"
    if not state.is_file():
        return
    values = dict(line.split("=", 1) for line in state.read_text().splitlines() if "=" in line)
    version = f"{values.get('MAJOR', '0')}.{values.get('MINOR', '0')}.{values.get('PATCH', '0')}"
    log.info('legacy build/version_state found: put  version = "%s"  under [project] in xewe.lock '
             "(the file is no longer used and was not deleted)", version)


def run_setup(p: Paths, opts: SetupOptions, sleep: Callable[[float], None] = time.sleep) -> int:
    """Run every setup step in order."""
    old = config.load(p)
    prev = {} if old is None or opts.force else old.installed
    p.build.mkdir(parents=True, exist_ok=True)
    p.build_config.unlink(missing_ok=True)
    (p.build / ".gitignore").write_text("*\n", encoding="utf-8")
    cfg = config.BuildConfig(tools_version=__version__)

    # 1 preflight
    if shutil.which("git") is None:
        raise XeweError("git is required (install it with your system package manager)", EXIT_NOT_SETUP)

    # 2 lock + refs
    lock = lockfile.load(p.lock)
    core_local = opts.core_source or _env_path("XEWE_CORE_SOURCE")
    modules_local = opts.modules_source or _env_path("XEWE_MODULES_SOURCE")
    tools_local = _env_path("XEWE_TOOLS_SOURCE")
    locals_ = {"core": core_local, "modules": modules_local, "tools": tools_local}
    refs = {}
    for name in ("core", "modules", "tools"):
        src = lock.source(name)
        refs[name] = fetch.resolve_latest(src.repo) if opts.latest and locals_[name] is None else src.ref
        if refs[name] != src.ref:
            log.info("--latest: %s %s (lock says %s; xewe.lock is not changed)", name, refs[name], src.ref)

    # arduino-cli data dir: --arduino-data, XEWE_ARDUINO_DATA, the previous choice, build/arduino15
    data = (
        opts.arduino_data
        or _env_path("XEWE_ARDUINO_DATA")
        or (old.arduino_data(p) if old else None)
        or p.default_arduino_data
    )
    p.arduino_user.mkdir(parents=True, exist_ok=True)
    env_vars = arduino.env(p, data)

    # 3 arduino-cli
    override = arduino.cli_override()
    if override is not None:
        cli = override
        version = arduino.cli_version(cli, env_vars)
        if version is None:
            raise XeweError(f"XEWE_ARDUINO_CLI={cli} does not run", EXIT_NOT_SETUP)
        cfg.paths["arduino_cli"] = str(cli)
    else:
        version = lock.arduino_cli_version
        log.info("arduino-cli %s", version)
        cli = arduino.install_cli(p, version, env_vars)
        cfg.paths["arduino_cli"] = p.to_build(cli)
    cfg.installed["arduino_cli"] = version

    # 4 esp32 core
    want = lock.esp32_version
    if arduino.core_version(cli, env_vars) == want:
        log.info("esp32 core %s already installed in %s", want, data)
    else:
        if _free_bytes(data) < pins.MIN_FREE_DISK_BYTES:
            raise XeweError(f"less than 6 GB free for the esp32 core in {data}", EXIT_NOT_SETUP)
        data.mkdir(parents=True, exist_ok=True)
        arduino.install_core(cli, env_vars, want, sleep)
    cfg.paths["arduino_data"] = p.to_build(data)
    cfg.paths["arduino_user"] = p.to_build(p.arduino_user)
    cfg.installed["esp32"] = want

    # 5 esptool
    found = esptool.find_in(data)
    if found is not None:
        cfg.paths["esptool"] = p.to_build(found)
    try:
        cmd = esptool.command(p, found, data)
        esp_version = esptool.version(cmd)
    except XeweError:
        esp_version = None
    if esp_version is None:
        log.warning("the core's esptool does not run on this host; flashing needs XEWE_ESPTOOL (e.g. 'python -m esptool')")
    else:
        log.info("esptool %s", esp_version)

    # 6-8 sources
    cfg.paths["libraries"] = p.to_build(p.libraries)
    cfg.paths["modules"] = p.to_build(p.modules_checkout)
    cfg.installed["core"] = _install_source(
        "core", lock.core, refs["core"], p.libraries / CORE_LIBRARY, core_local, prev.get("core", {}), opts.force
    )
    libs: dict[str, dict[str, str]] = {}
    prev_libs = prev.get("libraries", {})
    for name, src in lock.libraries.items():
        libs[name] = _install_source(name, src, src.ref, p.libraries / name, None, prev_libs.get(name, {}), opts.force)
    cfg.installed["libraries"] = libs
    cfg.installed["modules"] = _install_source(
        "modules", lock.modules, refs["modules"], p.modules_checkout, modules_local, prev.get("modules", {}), opts.force
    )

    # 9 module selection (an empty selection is a valid project)
    registry = modules.Registry.load(p.modules_checkout)
    if not registry.all:
        where = modules_local or lock.modules.repo
        log.warning("no modules found in %s (looked for %s); continuing with zero modules",
                    where, registry.layout_note())
    if opts.modules is not None:
        lock.selected = modules.expand_selection(registry, opts.modules)
        lockfile.save(lock, p.lock)
    elif not lock.selected and sys.stdin.isatty() and registry.all:
        lock.selected = _menu(registry)
        lockfile.save(lock, p.lock)
    if not lock.selected:
        log.info("no modules selected (src/modules/Modules.h declares nothing; add some with `xewe modules select`)")

    # 10 generate
    mod = cfg.installed["modules"]
    order = modules.generate(p, registry, lock.selected, mod["source"], mod["ref"], mod["commit"])

    # 11 project files
    p.sketch_ino()
    if not (p.root / "Config.h").is_file():
        raise XeweError("Config.h missing in the project root (the template provides it)", EXIT_FAIL)
    _legacy_version_hint(p)

    # 12 build_config.toml
    if tools_local is not None:
        cfg.installed["tools"] = {"ref": refs["tools"], "commit": fetch.head_commit(tools_local), "source": f"local:{tools_local}"}
    else:
        cfg.installed["tools"] = {"ref": refs["tools"], "commit": fetch.head_commit(p.tools_checkout), "source": lock.tools.repo}
    cfg.setup_completed = dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    config.save(p, cfg)
    result(
        f"setup complete: arduino-cli {version}, esp32 {want}, core {cfg.installed['core']['ref']}, "
        f"modules {mod['ref']} ({', '.join(m.slug for m in order) or 'none'})"
    )
    return EXIT_OK
