"""``xewe lock show`` and ``xewe lock update`` (SPEC §3, §4)."""

from __future__ import annotations

import difflib
import json
from dataclasses import asdict, dataclass

from xewe import config, fetch, lockfile, modules
from xewe.lockfile import Lock
from xewe.project import Paths
from xewe.report import EXIT_OK, EXIT_USAGE, XeweError, log, result

SOURCES = ("core", "modules", "tools")


@dataclass
class Row:
    """One lock entry compared with what setup installed."""

    name: str
    lock: str
    installed: str
    commit: str
    source: str
    drift: bool
    origin: str = ""
    """Libraries only: where the wanted ref comes from (``xewe.lock`` or ``modules catalogue``)."""


def libraries(p: Paths, lock: Lock, cfg: config.BuildConfig | None) -> list[modules.Library]:
    """The library plan setup would install now (lock ``[libraries]`` win over the modules catalogue)."""
    checkout = (cfg.path(p, "modules") if cfg else None) or p.modules_checkout
    try:
        order = modules.Registry.load(checkout).resolve(lock.selected, quiet=True)
        catalogue = modules.load_catalogue(checkout)
    except XeweError:
        order, catalogue = [], {}
    plan, _ = modules.library_plan({k: (s.repo, s.ref) for k, s in lock.libraries.items()}, order, catalogue)
    return plan


def rows(p: Paths, lock: Lock) -> list[Row]:
    """Lock refs vs build_config.toml records; drift when they differ or the source is local."""
    cfg = config.load(p)
    inst = cfg.installed if cfg else {}
    out: list[Row] = []

    def add(name: str, want: str, rec: dict[str, str] | str | None, origin: str = "") -> None:
        if isinstance(rec, dict):
            have, commit, source = rec.get("ref", ""), rec.get("commit", ""), rec.get("source", "")
        else:
            have, commit, source = rec or "", "", ""
        out.append(Row(name, want, have, commit, source, have != want or source.startswith("local:"), origin))

    for name in SOURCES:
        add(name, lock.source(name).ref, inst.get(name))
    for lib in libraries(p, lock, cfg):
        add(f"libraries.{lib.name}", lib.ref, inst.get("libraries", {}).get(lib.name), lib.origin)
    add("arduino_cli", lock.arduino_cli_version, inst.get("arduino_cli"))
    add("esp32", lock.esp32_version, inst.get("esp32"))
    return out


def show(p: Paths, lock: Lock, as_json: bool = False) -> int:
    """Print the comparison table (``!`` marks drift)."""
    table = rows(p, lock)
    if as_json:
        result(json.dumps([asdict(r) for r in table], indent=2))
        return EXIT_OK
    result(f"  {'entry':<22} {'lock':<12} {'installed':<12} commit")
    for r in table:
        mark = "!" if r.drift else " "
        notes = [r.origin] if r.origin else []
        if r.source.startswith("local:"):
            notes.append(r.source)
        extra = f"  ({', '.join(notes)})" if notes else ""
        result(f"{mark} {r.name:<22} {r.lock:<12} {r.installed or '-':<12} {r.commit[:12] or '-'}{extra}")
    return EXIT_OK


def update(p: Paths, lock: Lock, names: list[str], to: str | None = None) -> int:
    """Resolve the newest tag (or ``--to``) for each named source and rewrite xewe.lock."""
    names = names or list(SOURCES)
    for name in names:
        if name not in SOURCES:
            raise XeweError(f"unknown lock entry '{name}' (expected core, modules or tools)", EXIT_USAGE)
    if to is not None and len(names) != 1:
        raise XeweError("--to needs exactly one of core, modules, tools", EXIT_USAGE)
    before = lockfile.dumps(lock)
    for name in names:
        src = lock.source(name)
        src.ref = to or fetch.resolve_latest(src.repo)
    after = lockfile.dumps(lock)
    if before == after:
        log.info("xewe.lock is already up to date")
        return EXIT_OK
    lockfile.save(lock, p.lock)
    for line in difflib.unified_diff(before.splitlines(), after.splitlines(), "xewe.lock", "xewe.lock", lineterm=""):
        result(line)
    log.info("run ./setup.sh to install the new refs")
    return EXIT_OK
