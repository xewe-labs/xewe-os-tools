"""``xewe release-card``: the generated release card of a repo (doc/spec.md §14).

``init`` starts a run in a logs folder (``releases/<version>/logs/run.json``: run id, repo, version,
base commit, tested-tree fingerprint). ``record`` runs one release step, writes its redacted output
to ``<n>-<kind>[-<chip>].log`` and adds the step to run.json. ``render`` turns the run into
``release_card.md`` and refuses (exit 1, draft in the logs folder) when the run does not prove the
release. ``verify`` checks a committed card against the tree, the local logs or a CI run.
``fingerprint`` prints the tested-tree fingerprint (``--card --check``: is this the card's tree?).

The tools never run a git write: the fingerprint uses ``git ls-files`` and ``git hash-object``
without ``-w``.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import platform
import re
import secrets
import shlex
import subprocess
import sys
import time
import tomllib
import urllib.parse
import xml.etree.ElementTree as ET
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from functools import lru_cache
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape as xml_escape

from xewe import __version__
from xewe.build import chips
from xewe.env import dotenv
from xewe.report import EXIT_FAIL, EXIT_NOT_SETUP, EXIT_OK, EXIT_USAGE, XeWeError, log, result

SCHEMA = 1
RUN_FILE = "run.json"
DRAFT_NAME = "release_card.draft.md"
RUN_FILES = frozenset({RUN_FILE, "run.tmp", DRAFT_NAME})
STEP_FILE_RE = re.compile(r"^\d+-")
"""A step file of a run folder (``<n>-<kind>…``); with ``RUN_FILES`` all that ``init`` may delete."""
KINDS = ("setup", "validate", "unit", "board", "flash", "build", "check")
PER_CHIP_KINDS = frozenset({"board", "flash", "build"})
FORMATS = ("junit", "core-host")
DEFAULT_EXCLUDES = ("releases/",)
FIRMWARE_REQUIRE = ("setup", "validate", "unit", "build", "board")
NO_FIRMWARE_REQUIRE = ("setup", "unit")
TAIL_LINES = 20
SUMMARY_WIDTH = 120
SIZE_TOLERANCE_PERCENT = 0.5
NOT_RUN = "compiled, not run"
"""The plugin's no-board skip reason prefix (``xewe.testing.plugin.NOT_RUN``)."""

SECRET_KEY_RE = re.compile(r"(?i)(PSW|PASS|PASSWORD|SECRET|TOKEN|KEY|SSID)")
"""Keys whose values the redactor removes (dotenv file and environment)."""
NOT_SECRET_VALUES = frozenset({"true", "false", "none", "null", "yes"})
MIN_SECRET_LENGTH = 4
WORD_MATCH_BELOW = 8
"""In a structured copy (JUnit, JSON) a secret shorter than this is replaced only as a whole word."""
CORE_HOST_RE = re.compile(r"^(ok  |FAIL) (\S.*?)\s*$")
"""A result line of xewe-os-core's host test runner (``tests/unit/test/test_main.cpp``)."""
CORE_STD_RE = re.compile(r"^== (\S+): tests\s*$")
"""``tests/unit/run.sh`` section header before each run of the tests (one per -std)."""
SKETCH_RE = re.compile(r"Sketch uses (\d+) bytes(?: \((\d+)%\))?(?:.*?Maximum is (\d+) bytes)?")
GLOBALS_RE = re.compile(r"Global variables use (\d+) bytes(?:.*?Maximum is (\d+) bytes)?")
ENV_GATE_RE = re.compile(r"\b[A-Z][A-Z0-9]*_[A-Z0-9_]+\b")
"""A CI skip reason that names an environment variable (UPPER_SNAKE, case-sensitive)."""
ENV_GATE_WORDS_RE = re.compile(r"not set|golden", re.IGNORECASE)
"""A CI skip reason that says a setting is "not set" or names a golden dir."""

PASS = "PASS"
PASS_HARDWARE = "PASS (hardware)"
PASS_COMPILE = "PASS (compile only)"
SKIP_NOT_RUN = "SKIP (compiled, not run)"
SKIP = "SKIP"
XFAIL = "XFAIL"
FAIL = "FAIL"
ERROR = "ERROR"
RESULTS = (PASS, PASS_HARDWARE, PASS_COMPILE, SKIP_NOT_RUN, SKIP, XFAIL, FAIL, ERROR)
"""The result vocabulary of the card (doc/spec.md §14)."""
PASSES = frozenset({PASS, PASS_HARDWARE, PASS_COMPILE})

DATA_PREFIX = "<!-- xewe-card-data "
DATA_RE = re.compile(r"^<!-- xewe-card-data (\{.*\}) -->$")
"""The data block: always the card's last line (``read_card``)."""

CARD_TEMPLATE = """\
{draft}# {title} {version} release card

| Field | Value |
|---|---|
| Repository | {repo} |
| Release | {version} (tag `{tag}`) |
| Card generated | {generated}, run `{run_id}`, xewe-os-tools {tools_version} (`{tools_commit}`) |
| Tested commit | `{commit}` |
| Tested tree | `{tree}` |
| Result | {result_line} |

## 1. Release notes

{notes}

## 2. Metadata

### Pins

{pins}

### Toolchain and host

{toolchain}

### Images

{images}

## 3. Boards

{boards}

## 4. Validator and checks

{checks}

## 5. Tests

### Runs

{runs}

### Every test

{tests}

## 6. Local logs

{local_logs}

## 7. Not covered

{not_covered}

## 8. Retries

{retries}

"""
"""The card body (doc/spec.md §14). Every field is filled by ``render_card``, which appends the data
block as the last line; tables end without a blank line."""


# --------------------------------------------------------------------------- small helpers


def _now() -> str:
    return dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _clock() -> float:
    return time.monotonic()


def _run_id() -> str:
    return dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(2)


def _host() -> dict[str, str]:
    return {"os": f"{platform.system()} {platform.machine()}", "python": platform.python_version()}


def sha256_file(path: Path) -> str:
    """Hex sha256 of a file."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _git(root: Path, *args: str, stdin: str | None = None) -> str:
    """Read-only git in ``root``; XeWeError when git fails."""
    try:
        proc = subprocess.run(["git", "-C", str(root), *args], input=stdin, capture_output=True, text=True, check=False)
    except FileNotFoundError:
        raise XeWeError("git not found", EXIT_FAIL) from None
    if proc.returncode != 0:
        raise XeWeError(f"git {' '.join(args)}: {proc.stderr.strip()}", EXIT_FAIL)
    return proc.stdout


def repo_root(start: Path | None = None) -> Path:
    """The git work tree containing ``start`` (default: the current directory)."""
    return Path(_git(start or Path.cwd(), "rev-parse", "--show-toplevel").strip())


def tools_commit() -> str:
    """Commit of these tools: ``<venv>/xewe-tools-commit`` (setup.sh), else HEAD of the source checkout, else "-"."""
    recorded = Path(sys.prefix) / "xewe-tools-commit"
    if recorded.is_file():
        return recorded.read_text(encoding="utf-8").strip() or "-"
    checkout = dotenv.package_checkout()
    if checkout is not None:
        try:
            return _git(checkout, "rev-parse", "HEAD").strip()
        except XeWeError:
            pass
    return "-"


@lru_cache(maxsize=64)
def _exclude_re(pattern: str) -> re.Pattern[str]:
    return re.compile(re.escape(pattern.rstrip("/")).replace(r"\*", "[^/]*") + "/")


def excluded(path: str, excludes: Iterable[str]) -> bool:
    """True when ``path`` is under one of ``excludes`` (folder prefixes; ``*`` matches one path part)."""
    return any(_exclude_re(pattern).match(path) for pattern in excludes)


def git_blob(data: bytes) -> str:
    """The git blob id of ``data`` (what ``git hash-object`` prints without filters)."""
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def tree_fingerprint(root: Path, excludes: Iterable[str]) -> str:
    """``sha256:<hex>`` over ``"<mode> <blob> <path>\\n"`` of every tracked path outside ``excludes``.

    Modes and submodules come from the index (``git ls-files -s``), so the value is the one a CI
    checkout of the same commit computes, whatever the file system says about exec bits. A
    regular file's blob is ``git hash-object`` of the working file (no ``-w``, so nothing is
    written; clean filters apply, so a Git LFS file counts as its pointer); a symlink's blob is
    that of its target text, hashed here; a submodule's is the commit in the index. A tracked path
    missing from the work tree counts as ``deleted``.
    """
    excludes = list(excludes)
    index: dict[str, tuple[str, str]] = {}
    for entry in _git(root, "ls-files", "-s", "-z").split("\0"):
        info, tab, path = entry.partition("\t")
        if tab and path and not excluded(path, excludes):
            mode, sha = info.split()[:2]
            index[path] = (mode, sha)
    paths = sorted(index)
    links: dict[str, str] = {}
    files: list[str] = []
    for p in paths:
        f = root / p
        if index[p][0] == "160000":
            continue
        if f.is_symlink():
            links[p] = git_blob(os.fsencode(os.readlink(f)))
        elif f.is_file():
            files.append(p)
    blobs = _git(root, "hash-object", "--stdin-paths", stdin="".join(p + "\n" for p in files)).split() if files else []
    by_path = dict(zip(files, blobs, strict=True))
    h = hashlib.sha256()
    for p in paths:
        mode, sha = index[p]
        if mode == "160000":
            line = f"160000 {sha} {p}\n"
        elif p in links:
            line = f"120000 {links[p]} {p}\n"
        elif p in by_path:
            line = f"{mode} {by_path[p]} {p}\n"
        else:
            line = f"000000 deleted {p}\n"
        h.update(line.encode("utf-8"))
    return "sha256:" + h.hexdigest()


def dirty_paths(root: Path, excludes: Iterable[str]) -> list[str]:
    """Changed or untracked (not ignored) paths outside ``excludes``, from ``git status --porcelain -z``
    (a rename or copy entry is followed by its original path, which counts too)."""
    excludes = list(excludes)
    fields = _git(root, "status", "--porcelain", "-z").split("\0")
    out = []
    i = 0
    while i < len(fields):
        entry = fields[i]
        i += 1
        if len(entry) < 4:
            continue
        paths = [entry[3:]]
        if "R" in entry[:2] or "C" in entry[:2]:
            paths += fields[i : i + 1]
            i += 1
        out += [p for p in paths if p and not excluded(p, excludes)]
    return out


def secret_forms(value: str) -> list[str]:
    """``value`` as it can appear in a log: raw, XML-escaped (a JUnit message), JSON-escaped and
    URL-encoded."""
    forms = [
        value,
        xml_escape(value, {'"': "&quot;", "'": "&apos;"}),
        xml_escape(value, {'"': "&quot;"}),
        xml_escape(value),
        json.dumps(value)[1:-1],
        json.dumps(value, ensure_ascii=False)[1:-1],
        urllib.parse.quote(value, safe=""),
        urllib.parse.quote_plus(value),
    ]
    return list(dict.fromkeys(forms))


class Redactor:
    """Replaces secret values with ``«redacted:<KEY>»`` and ``$HOME`` (as a whole path part) with ``~``.

    Every secret is also matched in its escaped and encoded forms (``secret_forms``). ``__call__``
    redacts raw text (a log, an argv) wherever a form occurs. ``value`` redacts one string of a
    structured copy (a JUnit text or attribute value, a JSON string): there a secret shorter than
    ``WORD_MATCH_BELOW`` is replaced only as a whole word, so a short SSID such as ``test`` never
    rewrites ``test_x``.
    """

    def __init__(self, secrets_by_key: Mapping[str, str], home: str | None = None) -> None:
        pairs: dict[str, str] = {}
        for key, value in secrets_by_key.items():
            if (
                SECRET_KEY_RE.search(key)
                and len(value) >= MIN_SECRET_LENGTH
                and value.lower() not in NOT_SECRET_VALUES
                and "\n" not in value
            ):
                for form in secret_forms(value):
                    pairs[form] = key
        self.pairs = sorted(pairs.items(), key=lambda kv: -len(kv[0]))
        self.patterns = [
            (re.compile(r"(?<!\w)" + re.escape(v) + r"(?!\w)" if len(v) < WORD_MATCH_BELOW else re.escape(v)), k)
            for v, k in self.pairs
        ]
        self.home = home if home and len(home) > 1 else None
        self.home_re = re.compile(re.escape(self.home) + r"(?![\w.-])") if self.home else None

    @classmethod
    def from_environment(cls, root: Path | None) -> Redactor:
        """Secrets from the dotenv file (doc/spec.md §13; read by the tools, never printed) and the environment."""
        values: dict[str, str] = {}
        try:
            path = dotenv.find(root)
            if path is not None:
                values.update(dotenv.load([path]))
        except XeWeError:
            pass
        for key, value in os.environ.items():
            values.setdefault(key, value)
        home = os.environ.get("HOME") or str(Path.home())
        return cls(values, home.rstrip("/") or None)

    def __call__(self, text: str) -> str:
        for value, key in self.pairs:
            if value in text:
                text = text.replace(value, f"«redacted:{key}»")
        return self.home_re.sub("~", text) if self.home_re else text

    def value(self, text: str) -> str:
        """One string of a structured copy (see the class)."""
        for pattern, key in self.patterns:
            replacement = f"«redacted:{key}»"
            text = pattern.sub(lambda _m: replacement, text)
        return self.home_re.sub("~", text) if self.home_re else text


# --------------------------------------------------------------------------- run.json


def _run_path(d: Path) -> Path:
    return d / RUN_FILE


def load_run(d: Path) -> dict[str, Any]:
    """run.json of the logs folder ``d``; exit 2 when there is none."""
    path = _run_path(d)
    try:
        run: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise XeWeError(f"{path}: no run; start one with `xewe release-card init`", EXIT_USAGE) from None
    except ValueError as exc:
        raise XeWeError(f"{path}: not JSON ({exc})", EXIT_USAGE) from None
    return run


def save_run(d: Path, run: dict[str, Any]) -> None:
    path = _run_path(d)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(run, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _logs_rel(d: Path, root: Path) -> str | None:
    try:
        return d.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return None


def run_folder_entries(d: Path, root: Path, repo: str, version: str) -> list[Path]:
    """The files of ``d`` that ``init`` may delete; exit 2 when ``d`` is not a run folder it may empty.

    ``d`` must lie inside ``root`` (not be the root or above it), hold no tracked file, and be
    missing, empty, or a previous run folder of the same repo and version: ``run.json`` naming them,
    and nothing but run files and step files (``<n>-…``), no subfolder."""
    rd, rr = d.resolve(), root.resolve()
    if rd == rr or rd in rr.parents:
        raise XeWeError(f"--dir {d}: is the repo root or above it; give the run folder, releases/<v>/logs", EXIT_USAGE)
    try:
        rel = rd.relative_to(rr).as_posix()
    except ValueError:
        raise XeWeError(f"--dir {d}: outside the repo {root}", EXIT_USAGE) from None
    if _git(root, "ls-files", "-z", "--", rel).strip("\0"):
        raise XeWeError(f"--dir {d}: holds tracked files; the run folder is releases/<v>/logs", EXIT_USAGE)
    if not d.exists():
        return []
    if not d.is_dir():
        raise XeWeError(f"--dir {d}: not a directory", EXIT_USAGE)
    entries = sorted(d.iterdir())
    if not entries:
        return []
    foreign = [
        f.name
        for f in entries
        if f.is_symlink() or not f.is_file() or not (f.name in RUN_FILES or STEP_FILE_RE.match(f.name))
    ]
    old = _read_json(_run_path(d))
    if foreign or old.get("repo") != repo or old.get("version") != version:
        why = f"it holds {', '.join(foreign[:5])}" if foreign else f"its {RUN_FILE} is not a run of {repo} {version}"
        raise XeWeError(
            f"--dir {d}: not a run folder ({why}); init empties only a previous run folder of the same "
            "repo and version, and deletes nothing here",
            EXIT_USAGE,
        )
    return entries


def init(
    d: Path, repo: str, version: str, tag: str | None, root: Path, excludes: list[str], resume: bool = False
) -> int:
    """Start a run in ``d`` (emptied, see ``run_folder_entries``), or with ``resume`` keep the run
    when commit and tree are unchanged."""
    entries = run_folder_entries(d, root, repo, version)
    commit = _git(root, "rev-parse", "HEAD").strip()
    rel = _logs_rel(d, root)
    all_excludes = list(dict.fromkeys([*DEFAULT_EXCLUDES, *excludes, *([rel + "/"] if rel else [])]))
    tree = tree_fingerprint(root, all_excludes)
    if resume and _run_path(d).is_file():
        old = load_run(d)
        if old.get("base_commit") == commit and old.get("tree") == tree:
            result(f"release-card: resuming run {old['run_id']} ({len(old.get('steps', []))} steps recorded)")
            return EXIT_OK
        log.warning(
            "release-card: cannot resume %s: base commit or tree changed; starting a new run", old.get("run_id")
        )
    d.mkdir(parents=True, exist_ok=True)
    for f in entries:
        f.unlink()
    run = {
        "schema": SCHEMA,
        "run_id": _run_id(),
        "started": _now(),
        "repo": repo,
        "version": version,
        "tag": tag or version,
        "base_commit": commit,
        "tree": tree,
        "exclude": all_excludes,
        "tools_version": __version__,
        "tools_commit": tools_commit(),
        "host": _host(),
        "steps": [],
    }
    save_run(d, run)
    result(f"release-card: run {run['run_id']} in {d} (tree {tree[:19]}…)")
    return EXIT_OK


# --------------------------------------------------------------------------- record


def step_label(kind: str, chip: str | None, name: str | None) -> str:
    return "-".join(x for x in (kind, chip, name) if x)


def _copy_redacted(src: Path, dest: Path, redact: Redactor) -> str:
    dest.write_text(redact(src.read_text(encoding="utf-8", errors="replace")), encoding="utf-8")
    return sha256_file(dest)


JUNIT_KEPT_ATTRIBUTES = frozenset({"name", "classname", "file", "line", "time"})
"""``<testcase>`` attributes that are test ids and numbers, never redacted."""
JUNIT_KEPT_PROPERTIES = frozenset(
    {"xewe_kind", "xewe_needs", "xewe_nodeid", "xewe_chip", "xewe_fw_sha256", "xewe_fw_match"}
)


def _copy_junit_redacted(src: Path, dest: Path, redact: Redactor) -> str:
    """A JUnit copy redacted value by value: element text and attribute values, never tag or
    attribute names, test ids or the plugin's id properties. A file that is not XML is copied as
    text (render then reports it)."""
    try:
        tree = ET.parse(src)
    except (ET.ParseError, OSError):
        return _copy_redacted(src, dest, redact)
    for el in tree.getroot().iter():
        if el.text:
            el.text = redact.value(el.text)
        if el.tail:
            el.tail = redact.value(el.tail)
        if el.tag == "testcase":
            kept = JUNIT_KEPT_ATTRIBUTES
        elif el.tag == "property":
            kept = frozenset({"name", "value"}) if el.get("name") in JUNIT_KEPT_PROPERTIES else frozenset({"name"})
        else:
            kept = frozenset()
        for name, value in list(el.attrib.items()):
            if name not in kept:
                el.set(name, redact.value(value))
    tree.write(dest, xml_declaration=True, encoding="utf-8")
    return sha256_file(dest)


def _redact_json(value: Any, redact: Redactor) -> Any:
    if isinstance(value, str):
        return redact.value(value)
    if isinstance(value, list):
        return [_redact_json(v, redact) for v in value]
    if isinstance(value, dict):
        return {k: _redact_json(v, redact) for k, v in value.items()}
    return value


def _copy_json_redacted(src: Path, dest: Path, redact: Redactor) -> str:
    """A meta.json copy with its string values redacted (keys and numbers kept); not JSON: as text."""
    try:
        data = json.loads(src.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _copy_redacted(src, dest, redact)
    dest.write_text(json.dumps(_redact_json(data, redact), indent=2) + "\n", encoding="utf-8")
    return sha256_file(dest)


def _shown_path(path: Path, root: Path, redact: Redactor) -> str:
    """``path`` relative to ``root`` when inside it, else redacted (``~`` for the home folder)."""
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return redact(str(path))


def _single_bin(folder: Path) -> Path | None:
    bins = sorted(folder.glob("*.bin"))
    return bins[0] if len(bins) == 1 else None


CANNOT_START = 127
"""The ``exit`` recorded for a command that could not be started."""


def _run_command(argv: list[str], log_path: Path, redact: Redactor, cwd: Path) -> int | None:
    """Run ``argv``; stdout and stderr, redacted, go to ``log_path`` and to our stdout. None when
    the command cannot be started."""
    try:
        proc = subprocess.Popen(argv, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    except OSError as exc:
        log_path.write_text(redact(f"cannot run {shlex.join(argv)}: {exc}\n"), encoding="utf-8")
        return None
    assert proc.stdout is not None
    with log_path.open("w", encoding="utf-8") as out:
        for raw in proc.stdout:
            line = redact(raw.decode("utf-8", errors="replace"))
            out.write(line)
            sys.stdout.write(line)
            sys.stdout.flush()
    return proc.wait()


def record(
    d: Path,
    kind: str,
    argv: list[str],
    chip: str | None = None,
    name: str | None = None,
    junit: Path | None = None,
    fmt: str | None = None,
    firmware: Path | None = None,
    meta: Path | None = None,
    root: Path | None = None,
    resume: bool = False,
) -> int:
    """Run one step, keep its redacted log in ``d`` and add it to run.json.

    Returns 0 when the command exited 0, 1 when it exited non-zero (its own code is in run.json and
    the ``exit N`` line), 3 when it could not be started."""
    if not argv:
        raise XeWeError("record: give the command after --", EXIT_USAGE)
    if kind in PER_CHIP_KINDS and not chip:
        raise XeWeError(f"record --kind {kind} needs --chip", EXIT_USAGE)
    if kind in ("unit", "board") and junit is None and fmt is None:
        raise XeWeError(f"record --kind {kind} needs --junit PATH or --format core-host (the test results)", EXIT_USAGE)
    run = load_run(d)
    root = root or repo_root()
    redact = Redactor.from_environment(root)
    shown_argv = [redact(a) for a in argv]
    label = step_label(kind, chip, name)
    key = [kind, chip or "", name or "", shown_argv]
    steps: list[dict[str, Any]] = run.setdefault("steps", [])
    earlier = [s for s in steps if s.get("key") == key]
    if resume and earlier and earlier[-1].get("exit") == 0:
        result(f"release-card: {earlier[-1]['name']} is already green in run {run['run_id']}; not run again")
        return EXIT_OK
    n = max((int(s["n"]) for s in steps), default=0) + 1
    stem = f"{n}-{label}"
    log_path = d / f"{stem}.log"
    junit_src = None if junit is None else junit if junit.is_absolute() else root / junit
    if junit_src is not None:
        junit_src.unlink(missing_ok=True)  # a file left by an earlier run must not count for this one
    start, t0 = _now(), _clock()
    result(f"release-card: {stem}: {shlex.join(shown_argv)}")
    status_code = _run_command(argv, log_path, redact, root)
    code = CANNOT_START if status_code is None else status_code
    seconds = round(_clock() - t0, 1)
    files: dict[str, str] = {log_path.name: sha256_file(log_path)}
    step: dict[str, Any] = {
        "n": n,
        "name": stem,
        "key": key,
        "kind": kind,
        "chip": chip,
        "label": name,
        "argv": shown_argv,
        "run_id": run["run_id"],
        "start": start,
        "end": _now(),
        "seconds": seconds,
        "exit": code,
        "attempt": len(earlier) + 1,
        "log": log_path.name,
        "format": fmt or ("junit" if junit else None),
    }
    if junit is not None and junit_src is not None:
        if junit_src.is_file():
            dest = d / f"{stem}.xml"
            files[dest.name] = _copy_junit_redacted(junit_src, dest, redact)
            step["junit"] = dest.name
        else:
            step["junit"] = None
            step["junit_missing"] = str(junit)
    if kind == "setup":
        config_file = root / "build" / "config" / "build_config.toml"
        if config_file.is_file():
            dest = d / f"{stem}.build_config.toml"
            files[dest.name] = _copy_redacted(config_file, dest, redact)
            step["build_config"] = dest.name
    if kind in ("build", "flash") and chip:
        out = root / "build" / "builds" / chip / "out"
        meta_src = meta if meta is not None else out / "meta.json"
        meta_src = meta_src if meta_src.is_absolute() else root / meta_src
        if meta_src.is_file():
            dest = d / f"{stem}.meta.json"
            files[dest.name] = _copy_json_redacted(meta_src, dest, redact)
            step["meta"] = dest.name
        compile_log = out / "compile.log"
        if kind == "build" and compile_log.is_file():
            dest = d / f"{stem}.compile.log"
            files[dest.name] = _copy_redacted(compile_log, dest, redact)
            step["compile_log"] = dest.name
        if kind == "flash":
            image = firmware if firmware is not None else _single_bin(out)
            image = image if image is None or image.is_absolute() else root / image
            if image is not None and image.is_file():
                step["firmware"] = _shown_path(image, root, redact)
                step["fw_sha256"] = sha256_file(image)
    step["files"] = files
    steps.append(step)
    save_run(d, run)
    status = "ok" if code == 0 else f"exit {code}"
    result(f"release-card: {stem}: {status} in {seconds:g} s")
    if status_code is None:
        return EXIT_NOT_SETUP
    return EXIT_OK if code == 0 else EXIT_FAIL


# --------------------------------------------------------------------------- parsing


@dataclass
class CardRow:
    """One test of one run (one row of the card's "Every test")."""

    id: str
    kind: str
    chip: str
    result: str
    detail: str
    run: str = ""
    props: dict[str, str] = field(default_factory=dict)

    @property
    def sort_key(self) -> tuple[str, str, str]:
        path, _, name = self.id.partition("::")
        return path, name, self.chip


def _first_line(text: str | None) -> str:
    return (text or "").strip().splitlines()[0].strip() if (text or "").strip() else ""


def _fallback_id(case: ET.Element) -> str:
    name = case.get("name", "")
    file = case.get("file")
    classname = case.get("classname", "")
    if file:
        parts = classname.split(".")
        module = Path(file).stem
        classes = parts[parts.index(module) + 1 :] if module in parts else []
        return "::".join([file, *classes, name])
    return f"{classname}::{name}" if classname else name


def classify(case: ET.Element, props: Mapping[str, str]) -> tuple[str, str]:
    """(card result, detail) of a JUnit ``<testcase>`` (doc/spec.md §14 mapping)."""
    error = case.find("error")
    if error is not None:
        return ERROR, _first_line(error.get("message") or error.text)
    failure = case.find("failure")
    if failure is not None:
        return FAIL, _first_line(failure.get("message") or failure.text)
    skipped = case.find("skipped")
    if skipped is not None:
        message = skipped.get("message") or ""
        if skipped.get("type") == "pytest.xfail":
            return XFAIL, _first_line(message)
        if message == "xfail-marked test passes unexpectedly":
            return FAIL, "XPASS(strict): " + message
        if NOT_RUN in message:
            _, sep, after = message.partition(NOT_RUN + ": ")
            return SKIP_NOT_RUN, _first_line(after if sep else message)
        return SKIP, _first_line(message)
    if props.get("xewe_kind") == "board":
        if props.get("xewe_board"):
            return PASS_HARDWARE, ""
        if props.get("xewe_needs") == "compile":
            return PASS_COMPILE, ""
        if props.get("xewe_needs") == "hardware":
            return ERROR, "hardware test passed without a board record"
    seconds = case.get("time")
    try:
        return PASS, f"{float(seconds or 0):.2f} s"
    except ValueError:
        return PASS, ""


def parse_junit(path: Path, run: str = "", chip: str | None = None) -> list[CardRow]:
    """Rows of a JUnit XML file (xunit1, with the plugin's ``xewe_*`` properties when present)."""
    try:
        tree = ET.parse(path)
    except (ET.ParseError, OSError) as exc:
        raise XeWeError(f"{path}: not a JUnit file ({exc})", EXIT_FAIL) from None
    rows = []
    for case in tree.getroot().iter("testcase"):
        props = {p.get("name", ""): p.get("value", "") for p in case.iter("property")}
        res, detail = classify(case, props)
        kind = props.get("xewe_kind") or "unit"
        row_chip = "-" if kind == "unit" else props.get("xewe_chip") or chip or "-"
        rows.append(CardRow(props.get("xewe_nodeid") or _fallback_id(case), kind, row_chip, res, detail, run, props))
    return rows


def parse_core_host(text: str, run: str = "", source: str = "tests/unit/run.sh") -> list[CardRow]:
    """Rows from the ``ok  |FAIL <name>`` lines of xewe-os-core's host tests; a ``== <std>: tests``
    header names the run, so each test appears once per -std as ``<source>::<name>[<std>]``."""
    rows = []
    std = ""
    for line in text.splitlines():
        header = CORE_STD_RE.match(line)
        if header:
            std = header[1]
            continue
        m = CORE_HOST_RE.match(line)
        if not m:
            continue
        test_id = f"{source}::{m[2]}" + (f"[{std}]" if std else "")
        if m[1] == "FAIL":
            rows.append(
                CardRow(test_id, "unit", "-", FAIL, f"failed checks: see {run}.log" if run else "failed checks")
            )
        else:
            rows.append(CardRow(test_id, "unit", "-", PASS, "", run))
    return rows


def parse_sizes(text: str) -> dict[str, int | None]:
    """Sizes from the last "Sketch uses" and "Global variables use" lines of a compile log."""
    out: dict[str, int | None] = {}
    sketch = list(SKETCH_RE.finditer(text))
    if sketch:
        m = sketch[-1]
        out["sketch_size"] = int(m[1])
        out["sketch_size_percent"] = int(m[2]) if m[2] else None
        out["sketch_max"] = int(m[3]) if m[3] else None
    ram = list(GLOBALS_RE.finditer(text))
    if ram:
        out["static_ram"] = int(ram[-1][1])
        out["static_ram_max"] = int(ram[-1][2]) if ram[-1][2] else None
    return out


# --------------------------------------------------------------------------- render


@dataclass
class Collected:
    """Everything the card shows, read from run.json and the files of the logs folder."""

    run: dict[str, Any]
    current: list[dict[str, Any]]
    retries: list[dict[str, Any]]
    rows: list[CardRow]
    images: dict[str, dict[str, Any]]
    flash: dict[str, dict[str, Any]]
    build_config: dict[str, Any]
    problems: list[str]


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def collect(d: Path, run: dict[str, Any]) -> Collected:
    """Latest attempt per step, earlier attempts, test rows, images, flash data; plus the problems
    found while reading (a changed file, a missing JUnit file)."""
    steps: list[dict[str, Any]] = list(run.get("steps", []))
    latest: dict[str, dict[str, Any]] = {}
    for s in steps:
        latest[json.dumps(s.get("key"))] = s
    current = sorted(latest.values(), key=lambda s: int(s["n"]))
    current_ns = {s["n"] for s in current}
    retries = [s for s in steps if s["n"] not in current_ns]
    problems: list[str] = []
    for s in steps:
        for fname, digest in s.get("files", {}).items():
            f = d / fname
            if not f.is_file():
                problems.append(f"{fname} of step {s['name']} is missing")
            elif sha256_file(f) != digest:
                problems.append(f"{fname} of step {s['name']} changed after it was recorded")
    rows: list[CardRow] = []
    images: dict[str, dict[str, Any]] = {}
    flash: dict[str, dict[str, Any]] = {}
    build_config: dict[str, Any] = {}
    for s in current:
        if s.get("junit_missing"):
            problems.append(f"step {s['name']}: JUnit file {s['junit_missing']} was not written")
        try:
            if s.get("junit"):
                rows += parse_junit(d / s["junit"], s["name"], s.get("chip"))
            elif s.get("format") == "core-host":
                rows += parse_core_host(_read_text(d / s["log"]), s["name"])
        except XeWeError as exc:
            problems.append(f"step {s['name']}: {exc}")
        if s["kind"] == "build" and s.get("chip"):
            meta = _read_json(d / s["meta"]) if s.get("meta") else {}
            sizes = parse_sizes(_read_text(d / (s.get("compile_log") or s["log"])))

            def pick(key: str, fallback: str) -> Any:
                return meta[key] if meta.get(key) is not None else sizes.get(fallback)

            image = {
                "fqbn": meta.get("fqbn"),
                "partition_scheme": meta.get("partition_scheme"),
                "app_partition_size": pick("app_partition_size", "sketch_max"),
                "sketch_size": pick("sketch_size", "sketch_size"),
                "sketch_size_percent": pick("sketch_size_percent", "sketch_size_percent"),
                "static_ram": pick("static_ram", "static_ram"),
                "static_ram_max": pick("static_ram_max", "static_ram_max"),
                "warnings": meta.get("warnings"),
                "config": meta.get("config", {}),
                "log": s.get("compile_log") or s["log"],
            }
            images[s["chip"]] = image
        if s["kind"] == "flash" and s.get("chip"):
            meta = _read_json(d / s["meta"]) if s.get("meta") else {}
            flash[s["chip"]] = {"step": s, "meta": meta}
        if s["kind"] == "setup" and s.get("build_config"):
            try:
                build_config = tomllib.loads((d / s["build_config"]).read_text(encoding="utf-8"))
            except (OSError, tomllib.TOMLDecodeError):
                build_config = {}
    return Collected(run, current, retries, rows, images, flash, build_config, problems)


def _defines(config: Mapping[str, Any]) -> str:
    def text(v: Any) -> str:
        return "true" if v is True else "false" if v is False else str(v)

    return " ".join(f"{k}={text(v)}" for k, v in sorted(config.items()))


def hardware_detail(row: CardRow, flash: Mapping[str, Mapping[str, Any]]) -> str:
    """``board <chip> sn=<id> fw <sha12> <defines>`` of a hardware pass."""
    board = row.props.get("xewe_board", "")
    chip, _, rest = board.partition(" ")
    sn = next((p for p in rest.split() if p.startswith("sn=")), "sn=-")
    sha = row.props.get("xewe_fw_sha256", "")[:12] or "-"
    defines = _defines(flash.get(row.chip, {}).get("meta", {}).get("config", {}))
    return " ".join(x for x in (f"board {chip or row.chip}", sn, f"fw {sha}", defines) if x)


def refusals(
    c: Collected,
    chip_list: list[str],
    hardware: list[str],
    require: list[str],
    tree_now: str | None,
    shown: list[CardRow] | None = None,
) -> list[str]:
    """Why no card may be written (doc/spec.md §14 refusal rules); empty when it may.

    ``shown`` (the rows a ``--tests`` card shows) is what the hardware rules look at; a FAIL or
    ERROR anywhere in the run refuses every card of it."""
    out = list(c.problems)
    run_id, started = c.run["run_id"], c.run["started"]
    if tree_now is not None and tree_now != c.run["tree"]:
        out.append(f"the tree changed since init ({c.run['tree'][:19]}… -> {tree_now[:19]}…)")
    for s in c.run.get("steps", []):
        if s.get("run_id") != run_id:
            out.append(f"step {s['name']} is from run {s.get('run_id')}, not {run_id}")
        elif s.get("start", "") < started:
            out.append(f"step {s['name']} started before run.json ({s.get('start')} < {started})")
    have = {(s["kind"], s.get("chip") or "") for s in c.current}
    for kind in require:
        if kind in PER_CHIP_KINDS:
            for chip in chip_list:
                if (kind, chip) not in have:
                    out.append(f"missing step: {kind} for {chip}")
        elif not any(k == kind for k, _ in have):
            out.append(f"missing step: {kind}")
    for chip in hardware:
        if ("flash", chip) not in have:
            out.append(f"missing step: flash for {chip} (--hardware)")
    for s in c.current:
        if s.get("exit") != 0:
            out.append(f"step {s['name']} exited {s.get('exit')}")
    runs_with_rows = {r.run for r in c.rows}
    for s in c.current:
        if s["kind"] in ("unit", "board") and not s.get("junit_missing") and s["name"] not in runs_with_rows:
            out.append(f"step {s['name']} produced no test rows")
    for r in c.rows:
        if r.result in (FAIL, ERROR):
            out.append(f"{r.result} {r.id} [{r.chip}]: {r.detail}")
        if r.result == PASS_HARDWARE:
            board_chip = r.props.get("xewe_board", "").split(" ", 1)[0]
            if board_chip != r.chip:
                out.append(f"{r.chip}: board record is {board_chip or '-'} ({r.id})")
    for chip in hardware:
        hw = [r for r in (c.rows if shown is None else shown) if r.chip == chip and r.result == PASS_HARDWARE]
        if not hw:
            out.append(f"{chip}: no PASS (hardware) row (--hardware {chip})")
            continue
        flashed = c.flash.get(chip, {}).get("step", {}).get("fw_sha256")
        shas = sorted({r.props.get("xewe_fw_sha256", "") for r in hw})
        if flashed and shas != [flashed]:
            out.append(
                f"{chip}: tested firmware {', '.join(x[:12] or '-' for x in shas)} is not the flashed {flashed[:12]}"
            )
        elif not flashed and ("flash", chip) in have:
            out.append(f"{chip}: the flash step recorded no firmware sha256")
        matches = sorted({r.props.get("xewe_fw_match", "unknown") for r in hw})
        if matches != ["true"]:
            out.append(
                f"{chip}: boot banner Build Timestamp does not match meta timestamp (fw_match {', '.join(matches)})"
            )
    return out


def _cell(value: Any) -> str:
    if value is None or value == "":
        return "-"
    return str(value).replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ").replace("\r", "")


def _code(value: str) -> str:
    return "`" + value.replace("`", "'").replace("|", "\\|").replace("\n", " ") + "`"


def _table(header: list[str], rows: list[list[Any]]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(_cell(v) for v in row) + " |" for row in rows]
    return "\n".join(lines)


def _fence(text: str, lang: str = "text") -> str:
    longest = max((len(m) for m in re.findall(r"`+", text)), default=0)
    ticks = "`" * max(3, longest + 1)
    body = text.rstrip("\n")
    return f"{ticks}{lang}\n{body}\n{ticks}" if body else f"{ticks}{lang}\n{ticks}"


def _number(value: Any) -> str:
    return f"{value:,}" if isinstance(value, int) and not isinstance(value, bool) else _cell(value)


def _seconds(value: Any) -> str:
    return f"{float(value):.1f} s" if isinstance(value, int | float) else "-"


def _summary_line(text: str) -> str:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    line = lines[-1] if lines else ""
    return line if len(line) <= SUMMARY_WIDTH else line[: SUMMARY_WIDTH - 1] + "…"


def _counts(rows: list[CardRow]) -> dict[str, int]:
    out = dict.fromkeys(RESULTS, 0)
    for r in rows:
        out[r.result] += 1
    return out


def _sn(rows: list[CardRow]) -> str:
    for r in rows:
        board = r.props.get("xewe_board", "")
        for part in board.split():
            if part.startswith("sn="):
                return part
    return ""


def result_line(rows: list[CardRow], chip_list: list[str]) -> str:
    """The one-line result of the header table."""
    total = _counts(rows)
    parts = [f"{total[PASS]} unit passed"]
    chips_seen = list(dict.fromkeys([*chip_list, *(r.chip for r in rows if r.kind == "board" and r.chip != "-")]))
    for chip in chips_seen:
        mine = [r for r in rows if r.chip == chip and r.kind == "board"]
        n = _counts(mine)
        bits = []
        if n[PASS_HARDWARE]:
            sn = _sn([r for r in mine if r.result == PASS_HARDWARE])
            bits.append(f"{n[PASS_HARDWARE]} passed on hardware" + (f" (board {sn})" if sn else ""))
        if n[PASS_COMPILE]:
            bits.append(f"{n[PASS_COMPILE]} compile only")
        if n[SKIP_NOT_RUN]:
            bits.append(f"{n[SKIP_NOT_RUN]} {NOT_RUN}")
        if bits:
            parts.append(f"{chip}: " + ", ".join(bits))
    parts.append(f"{total[SKIP]} skipped")
    if total[XFAIL]:
        parts.append(f"{total[XFAIL]} xfail")
    parts.append(f"{total[FAIL] + total[ERROR]} failed")
    return "; ".join(parts)


def _pins(build_config: Mapping[str, Any]) -> str:
    installed = build_config.get("installed", {}) if build_config else {}
    if not installed:
        return "No build_config.toml was recorded (no setup step in a project)."
    rows: list[list[Any]] = []
    for name, label in (("core", "xewe-os-core"), ("modules", "xewe-os-modules"), ("tools", "xewe-os-tools")):
        rec = installed.get(name)
        if isinstance(rec, dict):
            rows.append([label, rec.get("ref"), rec.get("commit"), rec.get("source")])
    for lib, rec in sorted(installed.get("libraries", {}).items()):
        if isinstance(rec, dict):
            rows.append([lib, rec.get("ref"), rec.get("commit"), rec.get("source")])
    return _table(["Component", "Ref", "Commit", "Source"], rows)


def _toolchain(run: Mapping[str, Any], build_config: Mapping[str, Any]) -> str:
    installed = build_config.get("installed", {}) if build_config else {}
    host = run.get("host", {})
    return _table(
        ["xewe-os-tools", "arduino-cli", "esp32 core", "Python", "Host"],
        [
            [
                run.get("tools_version"),
                installed.get("arduino_cli"),
                installed.get("esp32"),
                host.get("python"),
                host.get("os"),
            ]
        ],
    )


def _images(images: Mapping[str, Mapping[str, Any]], chip_list: list[str]) -> str:
    if not images:
        return "No firmware images were built in this run."
    order = [c for c in chip_list if c in images] + sorted(c for c in images if c not in chip_list)
    rows = []
    for chip in order:
        im = images[chip]
        size, app = im.get("sketch_size"), im.get("app_partition_size")
        percent = (
            f"{100 * size / app:.1f}"
            if isinstance(size, int) and isinstance(app, int) and app
            else im.get("sketch_size_percent")
        )
        rows.append(
            [
                chip,
                _code(im["fqbn"]) if im.get("fqbn") else "-",
                im.get("partition_scheme"),
                _number(app),
                _number(size),
                percent,
                _number(im.get("static_ram")),
                _number(im.get("static_ram_max")),
                im.get("warnings"),
                im.get("log"),
            ]
        )
    header = ["Chip", "FQBN", "Partition scheme", "App partition (B)", "Flash used (B)", "%"]
    header += ["Static RAM (B)", "RAM max (B)", "Warnings", "Log"]
    return _table(header, rows)


def _boards(c: Collected, hardware: list[str]) -> str:
    if not hardware:
        return "No board run (no --hardware chip)."
    rows = []
    for chip in hardware:
        hw = [r for r in c.rows if r.chip == chip and r.result == PASS_HARDWARE]
        board = hw[0].props.get("xewe_board", "") if hw else ""
        parts = dict(p.split("=", 1) for p in board.split()[1:] if "=" in p)
        fl = c.flash.get(chip, {})
        step, meta = fl.get("step", {}), fl.get("meta", {})
        matches = {r.props.get("xewe_fw_match", "unknown") for r in hw}
        match = "yes" if matches == {"true"} else "no" if "false" in matches else "unknown"
        rows.append(
            [
                chip,
                parts.get("sn"),
                parts.get("port"),
                _defines(meta.get("config", {})),
                _code(step["fw_sha256"]) if step.get("fw_sha256") else "-",
                meta.get("timestamp_param"),
                match,
                step.get("name"),
                _seconds(step.get("seconds")) if step else "-",
            ]
        )
    header = ["Chip", "Board id", "Port", "Defines", "Firmware sha256", "Meta timestamp", "Banner match"]
    header += ["Flash step", "Flash time"]
    return _table(header, rows)


def _checks(d: Path, c: Collected) -> str:
    steps = [s for s in c.current if s["kind"] in ("setup", "validate", "check")]
    if not steps:
        return "No setup, validator or check steps were recorded."
    rows, tails = [], []
    for s in steps:
        text = _read_text(d / s["log"])
        rows.append(
            [s["name"], _code(shlex.join(s["argv"])), s.get("exit"), _seconds(s.get("seconds")), _summary_line(text)]
        )
        tail = text.splitlines()[-TAIL_LINES:]
        tails.append(f"#### {s['name']} (last {TAIL_LINES} lines of {s['log']})\n\n{_fence(chr(10).join(tail))}")
    return _table(["Step", "Command", "Exit", "Time", "Summary line"], rows) + "\n\n" + "\n\n".join(tails)


def _runs(c: Collected) -> str:
    steps = [s for s in c.current if s["kind"] in ("unit", "board")]
    if not steps:
        return "No test runs were recorded."
    rows = []
    for s in steps:
        mine = [r for r in c.rows if r.run == s["name"]]
        n = _counts(mine)
        rows.append(
            [
                s["name"],
                _code(shlex.join(s["argv"])),
                s.get("chip"),
                _sn(mine),
                n[PASS],
                n[PASS_HARDWARE],
                n[PASS_COMPILE],
                n[SKIP_NOT_RUN],
                n[SKIP],
                n[XFAIL],
                n[FAIL] + n[ERROR],
                s.get("exit"),
            ]
        )
    header = ["Run", "Command", "Chip", "Board", "Unit passed", "Hardware passed", "Compile-only passed"]
    header += ["Compiled, not run", "Skipped (other)", "XFAIL", "Failed", "Exit"]
    return _table(header, rows)


def _every_test(c: Collected) -> str:
    if not c.rows:
        return "No tests."
    rows = []
    for r in sorted(c.rows, key=lambda r: r.sort_key):
        detail = hardware_detail(r, c.flash) if r.result == PASS_HARDWARE else r.detail
        if r.result == PASS_COMPILE and not detail:
            detail = f"built for {r.chip}, no board needed"
        rows.append([_code(r.id), r.kind, r.chip, r.result, detail])
    return _table(["Test", "Kind", "Chip", "Result", "Detail"], rows)


def local_logs(d: Path) -> dict[str, tuple[int, str]]:
    """Files of the logs folder (the draft card excepted): name -> (bytes, sha256)."""
    out = {}
    for f in sorted(d.iterdir(), key=lambda f: f.name):
        if f.is_file() and f.name != DRAFT_NAME and not f.name.endswith(".tmp"):
            out[f.name] = (f.stat().st_size, sha256_file(f))
    return out


def _local_logs(logs: Mapping[str, tuple[int, str]], logs_dir: str) -> str:
    rows = [[n, _number(size), _code(digest)] for n, (size, digest) in logs.items()]
    note = f"The record of this run in `{logs_dir}` (not committed, not published)."
    return note + "\n\n" + _table(["File", "Bytes", "sha256"], rows)


def _not_covered(c: Collected, chip_list: list[str], hardware: list[str]) -> str:
    no_hw = [chip for chip in chip_list if chip not in hardware]
    lines = [f"- Chips without a hardware run: {', '.join(no_hw) if no_hw else 'none'}."]
    groups: dict[tuple[str, str], list[str]] = {}
    for r in sorted(c.rows, key=lambda r: r.sort_key):
        if r.result in (SKIP, SKIP_NOT_RUN):
            label = f"{NOT_RUN}: {r.detail}" if r.result == SKIP_NOT_RUN else r.detail
            groups.setdefault((r.result, label or "no reason given"), []).append(
                r.id + ("" if r.chip == "-" else f" [{r.chip}]")
            )
    if groups:
        lines.append("- Skipped, by reason:")
        for (_, reason), ids in sorted(groups.items(), key=lambda kv: (kv[0][0] != SKIP, kv[0][1])):
            shown = ", ".join(_code(i) for i in ids[:3])
            more = f", and {len(ids) - 3} more" if len(ids) > 3 else ""
            lines.append(f"  - {_cell(reason)} ({len(ids)}): {shown}{more}")
    else:
        lines.append("- Skipped, by reason: none.")
    lines.append(
        "- Manual checks: tests/MANUAL.md and the BOARD-TEST-PLAN ids are not part of this card; "
        "the release notes name what was checked by hand."
    )
    return "\n".join(lines)


def _retries(c: Collected) -> str:
    if not c.retries:
        return "None."
    rows = [[s["name"], s.get("attempt"), s.get("exit"), _seconds(s.get("seconds")), s["log"]] for s in c.retries]
    return _table(["Earlier attempt", "Attempt", "Exit", "Time", "Log"], rows)


def card_data(
    c: Collected,
    logs: Mapping[str, tuple[int, str]],
    only: list[str] | None = None,
    chip_list: list[str] | None = None,
    hardware: list[str] | None = None,
    require: list[str] | None = None,
) -> dict[str, Any]:
    """The machine-readable block (for ``verify --ci``); ``only`` (``render --tests``) is kept as
    ``"only"`` so the CI comparison looks at the same tests. ``card_sha256`` is added by
    ``render_card``."""
    images = {
        chip: {
            "sketch_size": im.get("sketch_size"),
            "static_ram": im.get("static_ram"),
            "config": im.get("config", {}),
        }
        for chip, im in sorted(c.images.items())
    }
    data: dict[str, Any] = {
        "schema": SCHEMA,
        "tree": c.run["tree"],
        "run": c.run["run_id"],
        "tests": [[r.id, r.kind, r.chip, r.result] for r in sorted(c.rows, key=lambda r: r.sort_key)],
        "images": images,
        "exclude": c.run.get("exclude", []),
        "logs": {name: digest for name, (_, digest) in logs.items()},
        "repo": c.run["repo"],
        "version": c.run["version"],
        "commit": c.run["base_commit"],
        "chips": list(chip_list or []),
        "hardware": list(hardware or []),
        "require": list(require or []),
    }
    if only:
        data["only"] = list(only)
    return data


def data_comment(data: Mapping[str, Any]) -> str:
    text = json.dumps(data, separators=(",", ":"), ensure_ascii=True).replace(">", "\\u003e").replace("<", "\\u003c")
    return f"{DATA_PREFIX}{text} -->"


def card_digest(body: str, data: Mapping[str, Any]) -> str:
    """sha256 over the card body (everything before the data block) and the data block without
    ``card_sha256``: any edit to either after render changes it."""
    rest = {k: v for k, v in data.items() if k != "card_sha256"}
    canonical = json.dumps(rest, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(body.encode("utf-8") + canonical.encode("utf-8")).hexdigest()


def render_card(
    d: Path,
    c: Collected,
    notes: str,
    chip_list: list[str],
    hardware: list[str],
    title: str,
    logs_dir: str,
    generated: str,
    refused: list[str] | None = None,
    only: list[str] | None = None,
    require: list[str] | None = None,
) -> str:
    """The card text: the body (``CARD_TEMPLATE``), then the data block as the last line."""
    logs = local_logs(d)
    run = c.run
    draft = ""
    if refused:
        reasons = "\n".join(f"> - {_cell(r)}" for r in refused)
        draft = (
            f"> **DRAFT: refused, not a release card.** `xewe release-card render` refused because:\n>\n{reasons}\n\n"
        )
    body = CARD_TEMPLATE.format(
        draft=draft,
        title=title,
        version=run["version"],
        repo=run["repo"],
        tag=run.get("tag") or run["version"],
        generated=generated,
        run_id=run["run_id"],
        tools_version=run.get("tools_version", "-"),
        tools_commit=run.get("tools_commit", "-"),
        commit=run["base_commit"],
        tree=run["tree"],
        result_line=_cell(result_line(c.rows, chip_list)),
        notes=_fence(notes),
        pins=_pins(c.build_config),
        toolchain=_toolchain(run, c.build_config),
        images=_images(c.images, chip_list),
        boards=_boards(c, hardware),
        checks=_checks(d, c),
        runs=_runs(c),
        tests=_every_test(c),
        local_logs=_local_logs(logs, logs_dir),
        not_covered=_not_covered(c, chip_list, hardware),
        retries=_retries(c),
    )
    data = card_data(c, logs, only, chip_list, hardware, require)
    data["card_sha256"] = card_digest(body, data)
    return body + data_comment(data) + "\n"


def _chip_list(text: str | None) -> list[str]:
    items = [x.strip() for x in (text or "").split(",") if x.strip()]
    for x in items:
        if x not in chips.ALL_CHIPS:
            raise XeWeError(f"unknown chip '{x}' (known: {', '.join(chips.ALL_CHIPS)})", EXIT_USAGE)
    return list(dict.fromkeys(items))


def render(
    d: Path,
    notes_path: Path,
    out: Path,
    chip_text: str | None,
    hardware_text: str | None,
    require_text: str | None,
    title: str | None,
    root: Path | None,
    only: list[str] | None = None,
) -> int:
    """Write the card, or refuse: exit 1, reasons on stderr, ``<logs>/release_card.draft.md``.

    ``only`` (``--tests``): the card shows the tests whose id starts with one of these prefixes
    (one module's card out of the shared run of the modules repo)."""
    chip_list, hardware = _chip_list(chip_text), _chip_list(hardware_text)
    stray = [c for c in hardware if c not in chip_list]
    if stray:
        raise XeWeError(f"--hardware {', '.join(stray)} not in --chips", EXIT_USAGE)
    if require_text is None:
        require = list(FIRMWARE_REQUIRE if chip_list else NO_FIRMWARE_REQUIRE)
    else:
        require = [x.strip() for x in require_text.split(",") if x.strip()]
    unknown = [k for k in require if k not in KINDS]
    if unknown:
        raise XeWeError(f"--require: unknown kind {', '.join(unknown)} (kinds: {', '.join(KINDS)})", EXIT_USAGE)
    try:
        notes = notes_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise XeWeError(f"--notes {notes_path}: {exc.strerror}", EXIT_USAGE) from None
    run = load_run(d)
    c = collect(d, run)
    tree_now = tree_fingerprint(root, run.get("exclude", [])) if root is not None else None
    shown = [r for r in c.rows if r.id.startswith(tuple(only))] if only else None
    refused = refusals(c, chip_list, hardware, require, tree_now, shown)
    if DATA_PREFIX.strip() in notes:
        refused.append(
            f"{notes_path} contains `{DATA_PREFIX.strip()}`; the card's data block must be its only one "
            "(quote it some other way)"
        )
    if shown is not None:
        c = replace(c, rows=shown)
    name = title or run["repo"].rstrip("/").rsplit("/", 1)[-1]
    logs_dir = _shown_path(d, root, Redactor({}, os.environ.get("HOME"))) if root is not None else d.as_posix()
    text = render_card(d, c, notes, chip_list, hardware, name, logs_dir, _now(), refused, only, require)
    if refused:
        (d / DRAFT_NAME).write_text(text, encoding="utf-8")
        for reason in refused:
            log.error("refused: %s", reason)
        log.error("no card written; draft for diagnosis: %s", d / DRAFT_NAME)
        return EXIT_FAIL
    (d / DRAFT_NAME).unlink(missing_ok=True)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    result(f"release-card: {out} ({result_line(c.rows, chip_list)})")
    return EXIT_OK


# --------------------------------------------------------------------------- verify


def read_card(card: Path) -> tuple[str, dict[str, Any]]:
    """(body, data block) of a card. The block is read only from the card's last non-empty line,
    never from the embedded notes or logs; exit 2 when the card cannot be read or that line is not
    a data block of valid JSON."""
    try:
        text = card.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise XeWeError(f"{card}: {getattr(exc, 'strerror', None) or exc}", EXIT_USAGE) from None
    stripped = text.rstrip()
    start = stripped.rfind("\n") + 1
    m = DATA_RE.match(stripped[start:])
    if not m:
        raise XeWeError(f"{card}: no xewe-card-data block on its last line (not a generated release card)", EXIT_USAGE)
    try:
        data = json.loads(m[1])
    except ValueError as exc:
        raise XeWeError(f"{card}: the xewe-card-data block is not valid JSON ({exc})", EXIT_USAGE) from None
    if not isinstance(data, dict):
        raise XeWeError(f"{card}: the xewe-card-data block is not a JSON object", EXIT_USAGE)
    return text[:start], data


def read_card_data(card: Path) -> dict[str, Any]:
    """The data block of a card (``read_card``)."""
    return read_card(card)[1]


def card_edited(card: Path, body: str, data: Mapping[str, Any]) -> str | None:
    """Why the card is not as ``render`` wrote it (``card_sha256``), or None."""
    if not data.get("card_sha256"):
        return f"{card} has no card_sha256 in its data block; re-run render"
    if card_digest(body, data) != data["card_sha256"]:
        return f"{card} was edited after render (card_sha256 differs); re-run render"
    return None


def verify_local(card: Path, d: Path, root: Path) -> int:
    """Tag run: the card as rendered, clean tree with the card's fingerprint, the listed local
    logs, no testcard.md."""
    body, data = read_card(card)
    version = data.get("version", "<v>")
    problems = []
    edited = card_edited(card, body, data)
    if edited:
        problems.append(edited)
    if (card.parent / "testcard.md").exists():
        problems.append(f"{card.parent / 'testcard.md'} exists; the release card replaces it (remove it)")
    dirty = dirty_paths(root, data.get("exclude", []))
    if dirty:
        problems.append(f"the tree is not clean: {', '.join(dirty[:10])}")
    tree = tree_fingerprint(root, data.get("exclude", []))
    if tree != data.get("tree"):
        problems.append(f"tree {tree} is not the card's {data.get('tree')}")
    missing = []
    for name, digest in data.get("logs", {}).items():
        f = d / name
        if not f.is_file() or sha256_file(f) != digest:
            missing.append(name)
    if missing:
        problems.append(
            f"local logs missing or changed in {d}: {', '.join(missing[:10])}; re-run `--prepare {version}` here"
        )
    for p in problems:
        log.error("verify: %s", p)
    if problems:
        return EXIT_FAIL
    result(f"verify: {card} matches the tree ({tree[:19]}…) and its {len(data.get('logs', {}))} local logs")
    return EXIT_OK


def ci_rows(junit_dir: Path) -> list[CardRow]:
    """Rows of every ``*.xml`` (JUnit) and ``*.log``/``*.txt`` (core host text) under ``junit_dir``."""
    rows: list[CardRow] = []
    for f in sorted(junit_dir.rglob("*")):
        if f.suffix == ".xml":
            rows += parse_junit(f, f.stem)
        elif f.suffix in (".log", ".txt"):
            rows += parse_core_host(f.read_text(encoding="utf-8", errors="replace"), f.stem)
    return rows


def ci_images(meta_dir: Path) -> list[dict[str, Any]]:
    out = []
    for f in sorted(meta_dir.rglob("*.json")):
        meta = _read_json(f)
        if "sketch_size" in meta and "type" in meta:
            out.append(meta)
    return out


@dataclass
class Comparison:
    blocks: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    allowed: list[str] = field(default_factory=list)


def _env_gate(reason: str) -> bool:
    return NOT_RUN in reason or bool(ENV_GATE_RE.search(reason) or ENV_GATE_WORDS_RE.search(reason))


def compare(
    data: Mapping[str, Any],
    tree: str,
    rows: list[CardRow],
    metas: list[dict[str, Any]] | None,
    tolerance: float = SIZE_TOLERANCE_PERCENT,
    unit_only: bool = False,
) -> Comparison:
    """The CI comparison of doc/spec.md §14: blocks, warnings and allowed differences.

    A module card (``"only"`` in its data block) is compared with the CI tests under the same
    prefixes. ``unit_only`` (core and modules CI, which run no board session): only the card's
    ``unit`` rows are compared, with the CI's ``unit`` rows; the card's board rows are allowed."""
    cmp = Comparison()
    if tree != data.get("tree"):
        cmp.blocks.append(f"tree fingerprint {tree} is not the card's {data.get('tree')}")
    if "require" in data:
        cmp.allowed.append(
            f"card rendered with chips {','.join(data.get('chips', [])) or 'none'}, "
            f"hardware {','.join(data.get('hardware', [])) or 'none'}, require {','.join(data['require']) or 'none'}"
        )
    card_tests = [(t[0], t[1], t[2], t[3]) for t in data.get("tests", [])]
    scope = data.get("only")
    if scope:
        rows = [r for r in rows if r.id.startswith(tuple(scope))]
    if unit_only:
        board = sorted({t[0] for t in card_tests if t[1] != "unit"})
        card_tests = [t for t in card_tests if t[1] == "unit"]
        rows = [r for r in rows if r.kind == "unit"]
        if board:
            cmp.allowed.append(f"{len(board)} board tests on the card not compared (unit-only CI)")
    card_ids = {t[0] for t in card_tests}
    ci_ids = {r.id for r in rows}
    if card_ids != ci_ids:
        missing, extra = sorted(card_ids - ci_ids), sorted(ci_ids - card_ids)
        what = []
        if missing:
            what.append(f"{len(missing)} on the card only ({', '.join(missing[:5])})")
        if extra:
            what.append(f"{len(extra)} in CI only ({', '.join(extra[:5])})")
        cmp.blocks.append("collected tests differ: " + "; ".join(what))
    card_pass = {t[0] for t in card_tests if t[3] in PASSES}
    for r in rows:
        if r.result in (FAIL, ERROR):
            on_card = " (PASS on the card)" if r.id in card_pass else ""
            cmp.blocks.append(f"CI {r.result} {r.id}{on_card}: {r.detail}")
        elif r.result in (SKIP, SKIP_NOT_RUN) and r.id in card_pass:
            reason = f"{NOT_RUN}: {r.detail}" if r.result == SKIP_NOT_RUN else r.detail
            if _env_gate(reason):
                cmp.allowed.append(f"{r.id}: PASS on the card, CI skip ({reason})")
            else:
                cmp.warnings.append(f"{r.id}: PASS on the card, CI skip for another reason ({reason})")
    if metas is not None:
        for chip, im in sorted(data.get("images", {}).items()):
            config = im.get("config", {})
            same = [m for m in metas if m.get("type") == chip and m.get("config", {}) == config]
            if not same:
                cmp.allowed.append(f"{chip}: no CI image with the card's defines to compare")
                continue
            for key in ("sketch_size", "static_ram"):
                local, remote = im.get(key), same[0].get(key)
                if not isinstance(local, int) or not isinstance(remote, int) or local == remote:
                    continue
                diff = 100.0 * abs(remote - local) / local if local else 100.0
                text = f"{chip} {key}: card {local}, CI {remote} ({'+' if remote > local else '-'}{diff:.2f} %)"
                (cmp.warnings if diff <= tolerance else cmp.blocks).append(
                    text + ("" if diff <= tolerance else f", over the {tolerance:g} % tolerance")
                )
    return cmp


def verify_ci(
    card: Path, junit_dir: Path, meta_dir: Path | None, root: Path, tolerance: float, unit_only: bool = False
) -> int:
    """CI comparison: exit 1 on any block, warnings and allowed differences printed. ``card`` is any
    path: a committed card, or the card part of a release tag message that xewe-release.yml wrote."""
    body, data = read_card(card)
    if not junit_dir.is_dir():
        raise XeWeError(f"--junit-dir {junit_dir}: not a directory", EXIT_USAGE)
    if meta_dir is not None and not meta_dir.is_dir():
        raise XeWeError(f"--meta-dir {meta_dir}: not a directory", EXIT_USAGE)
    if tolerance < 0:
        raise XeWeError(f"--size-tolerance {tolerance:g}: must be 0 or more", EXIT_USAGE)
    metas = ci_images(meta_dir) if meta_dir is not None else None
    tree = tree_fingerprint(root, data.get("exclude", []))
    cmp = compare(data, tree, ci_rows(junit_dir), metas, tolerance, unit_only)
    edited = card_edited(card, body, data)
    if edited:
        cmp.blocks.append(edited)
    for line in cmp.allowed:
        result(f"allowed: {line}")
    for line in cmp.warnings:
        log.warning("%s", line)
    for line in cmp.blocks:
        log.error("block: %s", line)
    # a card in the tree (core, modules, the tools' ci.yml); the tag's card (xewe-release.yml) is a
    # file outside it and has no folder to look in
    in_tree = card.resolve().is_relative_to(root.resolve())
    if in_tree and (card.parent / "testcard.md").exists():
        log.error("block: %s exists; the release card replaces it", card.parent / "testcard.md")
        cmp.blocks.append("testcard.md")
    if cmp.blocks:
        return EXIT_FAIL
    result(f"verify --ci: {card} agrees with CI ({len(cmp.warnings)} warnings, {len(cmp.allowed)} allowed)")
    return EXIT_OK


def fingerprint(root: Path, card: Path | None, excludes: list[str], check: bool) -> int:
    """Print the tested-tree fingerprint, the one ``init`` records and ``verify`` recomputes.

    With ``card`` the card's own exclude list is used (exactly what ``verify`` does); ``check``
    then exits 1 when the tree is not the card's. The CI card jobs use this to find the card of
    the commit under test, so they share this algorithm instead of re-implementing it."""
    if card is not None:
        data = read_card_data(card)
        tree = tree_fingerprint(root, data.get("exclude", []))
        if check:
            if tree != data.get("tree"):
                result(f"{card}: tree {tree} is not the card's {data.get('tree')}")
                return EXIT_FAIL
            result(f"{card}: tree {tree} matches")
            return EXIT_OK
        result(tree)
        return EXIT_OK
    if check:
        raise XeWeError("fingerprint --check needs --card", EXIT_USAGE)
    result(tree_fingerprint(root, list(dict.fromkeys([*DEFAULT_EXCLUDES, *excludes]))))
    return EXIT_OK


# --------------------------------------------------------------------------- command line


def add_parser(sub: Any) -> None:
    """``xewe release-card`` and its actions (called by ``xewe.cli.parser``)."""
    sp = sub.add_parser(
        "release-card",
        help="record the release steps and write releases/<version>/release_card.md (doc/spec.md §14)",
        description="init starts a run in the logs folder, record runs one step (the command after --) and keeps its "
        "redacted log, render writes the card or refuses (exit 1), verify checks a card against the tree and "
        "the local logs, or with --ci against a CI run; fingerprint prints the tested-tree fingerprint (the CI card "
        "jobs use it to find the card of a commit). Needs no project; runs inside the repo's git work tree.",
    )
    rsub = sp.add_subparsers(dest="card_command", required=True, metavar="ACTION")

    def common(m: argparse.ArgumentParser, dir_required: bool = True) -> None:
        m.add_argument("--dir", type=Path, required=dir_required, metavar="D", help="logs folder, releases/<v>/logs")
        m.add_argument(
            "--root", type=Path, metavar="DIR", help="repo root (default: the git work tree of the current directory)"
        )

    m = rsub.add_parser("init", help="start a run: run.json with run id, base commit and tree fingerprint")
    common(m)
    m.add_argument("--repo", required=True, metavar="OWNER/NAME")
    m.add_argument("--version", required=True, dest="card_version", metavar="X.Y.Z")
    m.add_argument("--tag", metavar="TAG", help="default: the version")
    m.add_argument(
        "--exclude",
        action="append",
        default=[],
        metavar="DIR",
        help="also leave this folder out of the tree fingerprint (releases/ and the logs folder always are; * matches a part)",
    )
    m.add_argument("--resume", action="store_true", help="keep run.json when base commit and tree are unchanged")

    m = rsub.add_parser("record", help="run one step (the command after --) and record it")
    common(m)
    m.add_argument("--kind", required=True, choices=KINDS)
    m.add_argument("--chip", choices=chips.ALL_CHIPS, help="needed for board, flash and build")
    m.add_argument(
        "--name", metavar="LABEL", help="tells steps of one kind apart (log <n>-<kind>[-<chip>]-<LABEL>.log)"
    )
    m.add_argument("--junit", type=Path, metavar="PATH", help="the JUnit XML the command writes (pytest: xunit1)")
    m.add_argument("--format", dest="fmt", choices=FORMATS, help="test results in the log: core-host (ok/FAIL lines)")
    m.add_argument(
        "--firmware",
        type=Path,
        metavar="BIN",
        help="flash: the image written (default: the one bin in build/builds/<chip>/out)",
    )
    m.add_argument(
        "--meta", type=Path, metavar="FILE", help="build/flash: meta.json (default: build/builds/<chip>/out/meta.json)"
    )
    m.add_argument("--resume", action="store_true", help="do not run a step that is already green in this run")

    m = rsub.add_parser("render", help="write the card, or refuse (exit 1) and write logs/release_card.draft.md")
    common(m)
    m.add_argument("--notes", type=Path, required=True, metavar="FILE", help="notes.txt, embedded verbatim")
    m.add_argument("--out", type=Path, required=True, metavar="FILE", help="releases/<v>/release_card.md")
    m.add_argument("--chips", metavar="LIST", help="chips built in this run, e.g. c3,c6,s3 (none: no firmware)")
    m.add_argument("--hardware", metavar="LIST", help="chips that must have PASS (hardware) rows")
    m.add_argument(
        "--require",
        metavar="KINDS",
        help="step kinds that must be present (default with --chips: setup,validate,unit,build,board; else setup,unit)",
    )
    m.add_argument("--title", metavar="NAME", help="project name in the title (default: the repo name)")
    m.add_argument(
        "--tests",
        action="append",
        metavar="PREFIX",
        help="show only the tests whose id starts with PREFIX (repeatable), e.g. modules/led/ for one module's card",
    )

    m = rsub.add_parser("verify", help="check a committed card: tree, local logs; --ci: against a CI run")
    common(m, dir_required=False)
    m.add_argument(
        "--card", type=Path, required=True, metavar="FILE", help="the card (--ci: also the card part of a tag message)"
    )
    m.add_argument("--ci", action="store_true", help="compare with the CI run's JUnit files and image metas")
    m.add_argument("--junit-dir", type=Path, metavar="D", help="--ci: JUnit *.xml (and core host *.log) of the CI run")
    m.add_argument("--meta-dir", type=Path, metavar="D", help="--ci: meta.json files of the CI builds")
    m.add_argument(
        "--unit-only",
        action="store_true",
        help="--ci: CI ran unit tests only (core, modules): compare the card's unit rows, allow its board rows",
    )
    m.add_argument(
        "--size-tolerance",
        type=float,
        default=SIZE_TOLERANCE_PERCENT,
        metavar="PERCENT",
        help="--ci: an image size difference up to this is a warning, above it a block (default %(default)g)",
    )
    _fingerprint_parser(rsub)


def _fingerprint_parser(rsub: Any) -> None:
    m = rsub.add_parser(
        "fingerprint", help="print the tested-tree fingerprint; --card --check: exit 1 unless it is the card's"
    )
    m.add_argument(
        "--root", type=Path, metavar="DIR", help="repo root (default: the git work tree of the current directory)"
    )
    m.add_argument("--card", type=Path, metavar="FILE", help="use the card's exclude list (as verify does)")
    m.add_argument(
        "--exclude",
        action="append",
        default=[],
        metavar="DIR",
        help="without --card: also leave this folder out (releases/ always is; * matches a part)",
    )
    m.add_argument("--check", action="store_true", help="with --card: exit 0 when the tree is the card's, else 1")


def main(args: argparse.Namespace, extra: list[str]) -> int:
    """Run ``xewe release-card <action>``."""
    action = args.card_command
    if extra and action != "record":
        raise XeWeError("arguments after -- are only accepted by `release-card record`", EXIT_USAGE)
    root = args.root.resolve() if args.root else None
    if action == "init":
        return init(args.dir, args.repo, args.card_version, args.tag, root or repo_root(), args.exclude, args.resume)
    if action == "record":
        return record(
            args.dir,
            args.kind,
            extra,
            args.chip,
            args.name,
            args.junit,
            args.fmt,
            args.firmware,
            args.meta,
            root,
            args.resume,
        )
    if action == "render":
        return render(
            args.dir,
            args.notes,
            args.out,
            args.chips,
            args.hardware,
            args.require,
            args.title,
            root or repo_root(),
            args.tests,
        )
    if action == "verify":
        root = root or repo_root()
        if args.ci:
            if args.junit_dir is None:
                raise XeWeError("verify --ci needs --junit-dir", EXIT_USAGE)
            return verify_ci(args.card, args.junit_dir, args.meta_dir, root, args.size_tolerance, args.unit_only)
        return verify_local(args.card, args.dir or args.card.parent / "logs", root)
    if action == "fingerprint":
        return fingerprint(root or repo_root(), args.card, args.exclude, args.check)
    raise XeWeError(f"unknown release-card action {action}", EXIT_USAGE)
