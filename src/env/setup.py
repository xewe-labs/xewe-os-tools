"""``xewe setup``: install the shared toolchain and everything a project needs into ``build/`` (SPEC §6).

The toolchain (arduino-cli, esp32 core, esptool) and the modules repo checkout
(``sources/xewe-os-modules/<ref>/``) go to ``~/.xewe-os/build-tools/`` (``XEWE_HOME`` overrides
``~/.xewe-os``) once per machine; the project's libraries, generated modules library and state go
to ``build/``. Each step is skipped when what it would install is already there at the wanted
version/ref (``--force`` redoes the source checkouts). ``build/config/build_config.toml`` is
removed at the start and written last, so an interrupted setup is detected by every other
command (exit 3).
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

from xewe import __version__
from xewe.env import arduino, config, esptool, fetch, pins
from xewe.env.project import Paths
from xewe.modules import lockfile
from xewe.modules import registry as modules
from xewe.modules.lockfile import Source
from xewe.report import EXIT_FAIL, EXIT_NOT_SETUP, EXIT_OK, EXIT_USAGE, XeweError, log, result

CORE_LIBRARY = "XeWeCore"

Head = tuple[str, str]
"""What the ref ``latest`` resolved to: (default branch, newest commit on it; "" when unknown)."""


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


def latest_head(label: str, repo: str, prev: Any) -> Head:
    """Resolve the ref ``latest`` of ``repo`` with ``git ls-remote --symref`` (default branch, else
    ``main``). Unreachable remote: keep the previously installed ``latest`` checkout, with a warning."""
    try:
        return fetch.remote_head(repo)
    except XeweError as exc:
        if isinstance(prev, dict) and prev.get("branch") and prev.get("source") == repo:
            log.warning("%s: cannot resolve latest of %s; keeping the installed %s@%s (%s)",
                        label, repo, prev["branch"], str(prev.get("commit", ""))[:7], exc)
            return str(prev["branch"]), str(prev.get("commit", ""))
        raise


def _follow_latest(label: str, repo: str, dest: Path, head: Head, force: bool) -> str:
    """Bring ``dest`` to the head of the default branch (like a branch ref: fetch + reset); return the commit.

    Nothing is fetched when the checkout already is at the resolved commit; a checkout of another
    repo or ref (or ``force``) is cloned again."""
    branch, want = head
    if not force and dest.is_dir() and fetch.remote_url(dest) in ("", repo):
        have = fetch.head_commit(dest)
        if want and have == want:
            log.debug("%s: %s already at %s@%s", label, dest.name, branch, want[:7])
            return have
        if fetch.current_branch(dest) == branch:
            log.info("%s: following %s (latest) in %s", label, branch, dest)
            return fetch.follow_branch(dest, branch)
    log.info("%s: cloning %s at %s (latest)", label, repo, branch)
    return fetch.clone(repo, branch, dest)


def _latest_record(label: str, branch: str, commit: str, source: str) -> dict[str, str]:
    """Install record of a ``latest`` ref (``branch`` is the default branch it resolved to)."""
    log.info("%s: %s -> %s@%s", label, fetch.LATEST, branch, commit[:7])
    return {"ref": fetch.LATEST, "branch": branch, "commit": commit, "source": source}


def _install_source(
    label: str, src: Source, ref: str, dest: Path, local: Path | None, prev: dict[str, Any], force: bool,
    head: Head | None = None,
) -> dict[str, str]:
    """Clone ``src.repo`` at ``ref`` into ``dest`` (or copy ``local``); return the install record.

    ``head`` (the resolved ``latest`` ref) follows the default branch on every run instead."""
    if local is not None:
        log.info("%s: copying local source %s", label, local)
        fetch.copy_tree(local, dest)
        return {"ref": ref, "commit": fetch.head_commit(local), "source": f"local:{local}"}
    if head is not None:
        return _latest_record(label, head[0], _follow_latest(label, src.repo, dest, head, force), src.repo)
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


def modules_checkout(
    p: Paths, src: Source, ref: str, local: Path | None, force: bool, head: Head | None = None,
) -> tuple[Path, dict[str, str]]:
    """The modules repo setup reads, and its install record.

    ``local`` (``XEWE_MODULES_SOURCE``) is used where it is, nothing copied. Otherwise the shared
    checkout ``build-tools/sources/xewe-os-modules/<ref>/`` (one per machine and ref) is cloned when
    missing (or with ``force``), and a branch is fetched and reset to the remote head on every
    setup; a tag or a commit is never fetched again; ``head`` (the resolved ``latest`` ref, checkout
    ``.../latest/``) follows the default branch like a branch. All under the toolchain lock.
    """
    if local is not None:
        if not local.is_dir():
            raise XeweError(f"local source {local} is not a directory", EXIT_FAIL)
        log.info("modules: using local source %s", local)
        return local, {"ref": ref, "commit": fetch.head_commit(local), "source": f"local:{local}"}
    dest = p.modules_checkout(ref)
    if head is not None:
        with arduino.toolchain_lock(p):
            commit = _follow_latest("modules", src.repo, dest, head, force)
        return dest, _latest_record("modules", head[0], commit, src.repo)
    with arduino.toolchain_lock(p):
        have = fetch.head_commit(dest) if dest.is_dir() else "-"
        if force or have == "-" or fetch.remote_url(dest) not in ("", src.repo):
            log.info("modules: cloning %s at %s into %s", src.repo, ref, dest)
            commit = fetch.clone(src.repo, ref, dest)
        elif fetch.current_branch(dest) == ref:
            log.info("modules: following branch %s in %s", ref, dest)
            commit = fetch.follow_branch(dest, ref)
        else:
            log.debug("modules: %s already at %s", dest, ref)
            commit = have
    return dest, {"ref": ref, "commit": commit, "source": src.repo}


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


def install_libraries(
    p: Paths, lock: lockfile.Lock, order: list[modules.Module], prev_libs: dict[str, Any], force: bool,
    checkout: Path,
) -> dict[str, dict[str, str]]:
    """Install the library plan (see :func:`modules.library_plan`; the lock wins) into ``build/libraries``;
    the catalogue is ``libraries.toml`` of the modules ``checkout``."""
    plan, warnings = modules.library_plan(
        {k: (s.repo, s.ref) for k, s in lock.libraries.items()}, order, modules.load_catalogue(checkout)
    )
    for warning in warnings:
        log.warning("%s", warning)
    libs: dict[str, dict[str, str]] = {}
    for lib in plan:
        if lib.needed_by:
            log.info("library %s: from %s (%s)", lib.name, lib.origin, lib.ref)
        prev_rec = {k: v for k, v in prev_libs.get(lib.name, {}).items() if k != "origin"}
        head = latest_head(lib.name, lib.repo, prev_rec) if fetch.is_latest(lib.ref) else None
        rec = _install_source(lib.name, Source(lib.repo, lib.ref), lib.ref, p.libraries / lib.name, None, prev_rec,
                              force, head)
        libs[lib.name] = {**rec, "origin": lib.origin}
    return libs


def _legacy_version_hint(p: Paths) -> None:
    state = p.build / "version_state"
    if not state.is_file():
        return
    values = dict(line.split("=", 1) for line in state.read_text().splitlines() if "=" in line)
    version = f"{values.get('MAJOR', '0')}.{values.get('MINOR', '0')}.{values.get('PATCH', '0')}"
    log.info('legacy build/version_state found: put  version = "%s"  under [project] in xewe.toml '
             "(the file is no longer used and was not deleted)", version)


def _shared_default(data: Path) -> bool:
    """True when ``data`` is a shared toolchain's ``build-tools/arduino15`` (not an explicit choice)."""
    return data.name == "arduino15" and data.parent.name == "build-tools"


def run_setup(p: Paths, opts: SetupOptions, sleep: Callable[[float], None] = time.sleep) -> int:
    """Run every setup step in order."""
    old = config.load(p)
    prev = {} if old is None or opts.force else old.installed
    p.build.mkdir(parents=True, exist_ok=True)
    p.build_config.unlink(missing_ok=True)
    (p.build / ".gitignore").write_text("*\n", encoding="utf-8")
    p.config_dir.mkdir(parents=True, exist_ok=True)
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
    heads: dict[str, Head | None] = {}
    for name in ("core", "modules", "tools"):
        src = lock.source(name)
        refs[name] = fetch.resolve_latest(src.repo) if opts.latest and locals_[name] is None else src.ref
        if refs[name] != src.ref:
            log.info("--latest: %s %s (lock says %s; xewe.toml is not changed)", name, refs[name], src.ref)
        # ref "latest": the default branch head, resolved now (tools: setup.sh already checked it out)
        track = fetch.is_latest(refs[name]) and locals_[name] is None and name != "tools"
        heads[name] = latest_head(name, src.repo, (old.installed if old else {}).get(name)) if track else None

    # arduino-cli data dir: --arduino-data, XEWE_ARDUINO_DATA, the previous explicit choice, the shared
    # build-tools/arduino15 (a previously recorded shared default follows the current XEWE_HOME)
    previous = old.arduino_data(p) if old else None
    data = (
        opts.arduino_data
        or _env_path("XEWE_ARDUINO_DATA")
        or (previous if previous is not None and not _shared_default(previous) else None)
        or p.default_arduino_data
    )

    # 3 shared toolchain: build-tools/{arduino15,bin,arduino-user,downloads}, once per machine
    for d in (p.default_arduino_data, p.bin, p.arduino_user, p.downloads):
        d.mkdir(parents=True, exist_ok=True)
    log.info("shared toolchain %s", p.build_tools)
    env_vars = arduino.env(p, data)
    want = lock.esp32_version
    with arduino.toolchain_lock(p):
        # 3a arduino-cli
        override = arduino.cli_override()
        if override is not None:
            cli = override
            version = arduino.cli_version(cli, env_vars)
            if version is None:
                raise XeweError(f"XEWE_ARDUINO_CLI={cli} does not run", EXIT_NOT_SETUP)
        else:
            version = lock.arduino_cli_version
            log.info("arduino-cli %s", version)
            cli = arduino.install_cli(p, version, env_vars)

        # 4 esp32 core (several versions coexist in the shared arduino15)
        if arduino.core_version(cli, env_vars) == want:
            log.info("esp32 core %s already installed in %s", want, data)
        else:
            if _free_bytes(data) < pins.MIN_FREE_DISK_BYTES:
                raise XeweError(f"less than 6 GB free for the esp32 core in {data}", EXIT_NOT_SETUP)
            data.mkdir(parents=True, exist_ok=True)
            arduino.install_core(cli, env_vars, want, sleep)
    # build_config.toml: absolute for the shared toolchain (and overrides), relative inside build/
    cfg.paths["arduino_cli"] = p.to_build(cli)
    cfg.installed["arduino_cli"] = version
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
    cfg.installed["core"] = _install_source(
        "core", lock.core, refs["core"], p.libraries / CORE_LIBRARY, core_local, prev.get("core", {}), opts.force,
        heads["core"],
    )
    checkout, cfg.installed["modules"] = modules_checkout(
        p, lock.modules, refs["modules"], modules_local, opts.force, heads["modules"]
    )
    cfg.paths["modules"] = p.to_build(checkout)

    # 9 module selection (an empty selection is a valid project)
    registry = modules.Registry.load(checkout)
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
        log.info("no modules selected (src/Modules.h declares nothing; add some with `xewe modules select`)")

    # 10 generate
    mod = cfg.installed["modules"]
    order = modules.generate(p, registry, lock.selected, mod["source"], mod["ref"], mod["commit"])

    # 10b libraries: xewe.toml [libraries] plus the selected modules' depends_libraries from the catalogue
    cfg.installed["libraries"] = install_libraries(p, lock, order, prev.get("libraries", {}), opts.force, checkout)

    # 11 project files
    p.sketch_ino()
    if not (p.root / "Config.h").is_file():
        raise XeweError("Config.h missing in the project root (the template provides it)", EXIT_FAIL)
    _legacy_version_hint(p)

    # 12 build_config.toml
    if tools_local is not None:
        cfg.installed["tools"] = {"ref": refs["tools"], "commit": fetch.head_commit(tools_local), "source": f"local:{tools_local}"}
    elif fetch.is_latest(refs["tools"]):
        branch = fetch.current_branch(p.tools_checkout) if p.tools_checkout.is_dir() else None
        cfg.installed["tools"] = _latest_record(
            "tools", branch or fetch.DEFAULT_BRANCH, fetch.head_commit(p.tools_checkout), lock.tools.repo
        )
    else:
        cfg.installed["tools"] = {"ref": refs["tools"], "commit": fetch.head_commit(p.tools_checkout), "source": lock.tools.repo}
    cfg.setup_completed = dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    config.save(p, cfg)
    result(
        f"setup complete: arduino-cli {version}, esp32 {want}, core {fetch.describe(cfg.installed['core'])}, "
        f"modules {fetch.describe(mod)} ({', '.join(m.slug for m in order) or 'none'})"
    )
    return EXIT_OK
