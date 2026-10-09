"""``xewe manifest show`` and ``xewe manifest update``: the refs of ``xewe.toml`` (SPEC §3, §4)."""

from __future__ import annotations

import difflib
import json
from dataclasses import asdict, dataclass

from xewe.env import config, fetch
from xewe.env.project import Paths
from xewe.modules import lockfile
from xewe.modules import registry as modules
from xewe.modules.lockfile import Lock
from xewe.report import EXIT_OK, EXIT_USAGE, XeweError, log, result

SOURCES = ("core", "modules", "tools")


@dataclass
class Row:
    """One manifest entry compared with what setup installed."""

    name: str
    manifest: str
    installed: str
    commit: str
    source: str
    drift: bool
    origin: str = ""
    """Libraries only: where the wanted ref comes from (``xewe.toml`` or ``modules catalogue``)."""
    branch: str = ""
    """Ref ``latest`` only: the default branch it resolved to at setup."""
    remote: str = ""
    """Ref ``latest`` only: the remote head commit now ("" when not checked or unreachable)."""

    @property
    def installed_text(self) -> str:
        """The installed ref as shown: ``latest (main@abc1234)`` for a ``latest`` ref."""
        rec = {"ref": self.installed, "branch": self.branch, "commit": self.commit}
        return fetch.describe(rec) if self.installed else "-"


def libraries(p: Paths, lock: Lock, cfg: config.BuildConfig | None) -> list[modules.Library]:
    """The library plan setup would install now (manifest ``[libraries]`` win over the modules catalogue)."""
    checkout = modules.checkout(p, lock.modules.ref, cfg.path(p, "modules") if cfg else None)
    try:
        order = modules.Registry.load(checkout).resolve(lock.selected, quiet=True)
        catalogue = modules.load_catalogue(checkout)
    except XeweError:
        order, catalogue = [], {}
    plan, _ = modules.library_plan({k: (s.repo, s.ref) for k, s in lock.libraries.items()}, order, catalogue)
    return plan


def _remote(repo: str) -> str:
    """Head commit of ``repo``'s default branch now, or "" when it cannot be reached."""
    try:
        return fetch.remote_head(repo)[1]
    except XeweError as exc:
        log.warning("cannot check the remote head of %s: %s", repo, exc)
        return ""


def rows(p: Paths, lock: Lock, remote: bool = True) -> list[Row]:
    """Manifest refs vs build_config.toml records; drift when they differ or the source is local.

    A ``latest`` ref also drifts when the default branch head moved since setup (one
    ``git ls-remote`` per ``latest`` entry; ``remote=False`` skips that check)."""
    cfg = config.load(p)
    inst = cfg.installed if cfg else {}
    out: list[Row] = []

    def add(name: str, want: str, rec: dict[str, str] | str | None, origin: str = "", repo: str = "") -> None:
        if isinstance(rec, dict):
            have, commit, source = rec.get("ref", ""), rec.get("commit", ""), rec.get("source", "")
            branch = rec.get("branch", "")
        else:
            have, commit, source, branch = rec or "", "", "", ""
        drift = have != want or source.startswith("local:")
        head = ""
        if fetch.is_latest(want) and not drift and remote and repo:
            head = _remote(repo)
            drift = bool(head) and head != commit
        out.append(Row(name, want, have, commit, source, drift, origin, branch, head))

    for name in SOURCES:
        add(name, lock.source(name).ref, inst.get(name), repo=lock.source(name).repo)
    for lib in libraries(p, lock, cfg):
        add(f"libraries.{lib.name}", lib.ref, inst.get("libraries", {}).get(lib.name), lib.origin, lib.repo)

    add("arduino_cli", lock.arduino_cli_version, inst.get("arduino_cli"))
    add("esp32", lock.esp32_version, inst.get("esp32"))
    return out


def show(p: Paths, lock: Lock, as_json: bool = False) -> int:
    """Print the comparison table (``!`` marks drift; a ``latest`` row shows ``latest (main@abc1234)``
    and, when the default branch moved since setup, the remote head)."""
    table = rows(p, lock)
    if as_json:
        result(json.dumps([{**asdict(r), "installed_text": r.installed_text} for r in table], indent=2))
        return EXIT_OK
    w_name = max([22, *(len(r.name) for r in table)])
    w_inst = max([12, *(len(r.installed_text) for r in table)])
    result(f"  {'entry':<{w_name}} {'manifest':<12} {'installed':<{w_inst}} commit")
    for r in table:
        mark = "!" if r.drift else " "
        notes = [r.origin] if r.origin else []
        if r.source.startswith("local:"):
            notes.append(r.source)
        if r.remote and r.remote != r.commit:
            notes.append(f"remote {r.branch or fetch.DEFAULT_BRANCH}@{r.remote[:7]}: re-run ./setup.sh")
        extra = f"  ({', '.join(notes)})" if notes else ""
        result(f"{mark} {r.name:<{w_name}} {r.manifest:<12} {r.installed_text:<{w_inst}} {r.commit[:12] or '-'}{extra}")
    return EXIT_OK


def update(p: Paths, lock: Lock, names: list[str], to: str | None = None) -> int:
    """Resolve the newest tag (or ``--to``) for each named source and rewrite xewe.toml.

    This is also how a ``latest`` ref is frozen (newest tag) and set back (``--to latest``)."""
    names = names or list(SOURCES)
    for name in names:
        if name not in SOURCES:
            raise XeweError(f"unknown manifest entry '{name}' (expected core, modules or tools)", EXIT_USAGE)
    if to is not None and len(names) != 1:
        raise XeweError("--to needs exactly one of core, modules, tools", EXIT_USAGE)
    before = lockfile.dumps(lock)
    for name in names:
        src = lock.source(name)
        src.ref = to or fetch.resolve_latest(src.repo)
    after = lockfile.dumps(lock)
    if before == after:
        log.info("xewe.toml is already up to date")
        return EXIT_OK
    lockfile.save(lock, p.lock)
    if to is not None and fetch.is_latest(to):
        log.info("%s now tracks the default branch head (latest); `xewe manifest update %s` freezes it at the "
                 "newest tag", names[0], names[0])
    for line in difflib.unified_diff(before.splitlines(), after.splitlines(), "xewe.toml", "xewe.toml", lineterm=""):
        result(line)
    log.info("run ./setup.sh to install the new refs")
    return EXIT_OK
