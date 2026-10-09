"""Read, validate and write ``xewe.toml``, the project manifest (SPEC §4)."""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from xewe import pins, tomlw
from xewe.chips import CHIPS, DEFAULT_CHIP
from xewe.project import write_atomic
from xewe.report import EXIT_USAGE, XeweError

SCHEMA = 1
DEFAULT_HEADER = (
    "# xewe.toml: this firmware's manifest (pinned inputs). Edit by hand or with `xewe manifest update`.\n"
    "# ./setup.sh installs exactly these refs into build/."
)
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")

_ALLOWED: dict[str, set[str]] = {
    "project": {"name", "version", "chip"},
    "core": {"repo", "ref"},
    "modules": {"repo", "ref", "selected"},
    "tools": {"repo", "ref"},
    "toolchain": {"arduino_cli", "esp32"},
}
_TOP = {"schema", "libraries", *_ALLOWED}


@dataclass
class Source:
    """A git source pinned at a ref."""

    repo: str
    ref: str


@dataclass
class Lock:
    """Parsed contents of xewe.toml."""

    name: str | None = None
    version: str = "0.0.0"
    chip: str = DEFAULT_CHIP
    core: Source = field(default_factory=lambda: Source(pins.DEFAULT_CORE_REPO, pins.DEFAULT_CORE_REF))
    modules: Source = field(
        default_factory=lambda: Source(pins.DEFAULT_MODULES_REPO, pins.DEFAULT_MODULES_REF)
    )
    selected: list[str] = field(default_factory=list)
    tools: Source = field(default_factory=lambda: Source(pins.DEFAULT_TOOLS_REPO, pins.DEFAULT_TOOLS_REF))
    libraries: dict[str, Source] = field(default_factory=dict)
    arduino_cli: str | None = None
    esp32: str | None = None
    header: str = DEFAULT_HEADER

    @property
    def arduino_cli_version(self) -> str:
        """arduino-cli version to install ([toolchain] override or the tools default)."""
        return self.arduino_cli or pins.ARDUINO_CLI_VERSION

    @property
    def esp32_version(self) -> str:
        """esp32 core version to install ([toolchain] override or the tools default)."""
        return self.esp32 or pins.ESP32_CORE_VERSION

    def source(self, name: str) -> Source:
        """The core/modules/tools source by name."""
        if name not in ("core", "modules", "tools"):
            raise XeweError(f"unknown manifest entry '{name}' (expected core, modules or tools)", EXIT_USAGE)
        return getattr(self, name)


def _err(path: Path, msg: str) -> XeweError:
    return XeweError(f"{path.name}: {msg}", EXIT_USAGE)


def _str(path: Path, table: str, key: str, v: Any) -> str:
    if not isinstance(v, str) or not v:
        raise _err(path, f"[{table}] {key} must be a non-empty string")
    return v


def parse(text: str, path: Path = Path("xewe.toml")) -> Lock:
    """Parse and validate manifest text; unknown keys are an error."""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise _err(path, f"invalid TOML: {exc}") from None
    unknown = set(data) - _TOP
    if unknown:
        raise _err(path, f"unknown key(s): {', '.join(sorted(unknown))}")
    if data.get("schema", SCHEMA) != SCHEMA:
        raise _err(path, f"unsupported schema {data.get('schema')!r} (expected {SCHEMA})")
    for table, allowed in _ALLOWED.items():
        section = data.get(table, {})
        if not isinstance(section, dict):
            raise _err(path, f"[{table}] must be a table")
        bad = set(section) - allowed
        if bad:
            raise _err(path, f"unknown key(s) in [{table}]: {', '.join(sorted(bad))}")

    lock = Lock(header=tomlw.header_comments(text) or DEFAULT_HEADER)
    project = data.get("project", {})
    if "name" in project:
        lock.name = _str(path, "project", "name", project["name"])
    if "version" in project:
        lock.version = _str(path, "project", "version", project["version"])
        if not VERSION_RE.match(lock.version):
            raise _err(path, f"[project] version must be X.Y.Z, got '{lock.version}'")
    if "chip" in project:
        lock.chip = _str(path, "project", "chip", project["chip"])
        if lock.chip not in CHIPS:
            raise _err(path, f"[project] chip must be c3, c6 or s3, got '{lock.chip}'")

    for name in ("core", "modules", "tools"):
        section = data.get(name, {})
        src: Source = getattr(lock, name)
        if "repo" in section:
            src.repo = _str(path, name, "repo", section["repo"])
        if "ref" in section:
            src.ref = _str(path, name, "ref", section["ref"])

    selected = data.get("modules", {}).get("selected", [])
    if not isinstance(selected, list) or not all(isinstance(s, str) for s in selected):
        raise _err(path, "[modules] selected must be a list of slugs")
    lock.selected = list(selected)

    libs = data.get("libraries", {})
    if not isinstance(libs, dict):
        raise _err(path, "[libraries] must be a table")
    for lib, spec in libs.items():
        if not isinstance(spec, dict) or set(spec) != {"repo", "ref"}:
            raise _err(path, f"[libraries] {lib} must be {{ repo = \"...\", ref = \"...\" }}")
        lock.libraries[lib] = Source(
            _str(path, "libraries", f"{lib}.repo", spec["repo"]),
            _str(path, "libraries", f"{lib}.ref", spec["ref"]),
        )

    toolchain = data.get("toolchain", {})
    if "arduino_cli" in toolchain:
        lock.arduino_cli = _str(path, "toolchain", "arduino_cli", toolchain["arduino_cli"])
    if "esp32" in toolchain:
        lock.esp32 = _str(path, "toolchain", "esp32", toolchain["esp32"])
    return lock


def load(path: Path) -> Lock:
    """Read and validate ``path``."""
    if not path.is_file():
        raise XeweError(f"{path} not found", EXIT_USAGE)
    return parse(path.read_text(encoding="utf-8"), path)


def dumps(lock: Lock) -> str:
    """Serialise ``lock`` in the documented key order."""
    project: dict[str, Any] = {}
    if lock.name:
        project["name"] = lock.name
    project["version"] = lock.version
    project["chip"] = lock.chip
    data: dict[str, Any] = {
        "schema": SCHEMA,
        "project": project,
        "core": {"repo": lock.core.repo, "ref": lock.core.ref},
        "modules": {"repo": lock.modules.repo, "ref": lock.modules.ref, "selected": lock.selected},
        "tools": {"repo": lock.tools.repo, "ref": lock.tools.ref},
        "libraries": {k: {"repo": s.repo, "ref": s.ref} for k, s in lock.libraries.items()},
    }
    toolchain = {k: v for k, v in (("arduino_cli", lock.arduino_cli), ("esp32", lock.esp32)) if v}
    if toolchain:
        data["toolchain"] = toolchain
    return tomlw.dumps(data, header=lock.header)


def save(lock: Lock, path: Path) -> None:
    """Write ``lock`` to ``path`` atomically."""
    write_atomic(path, dumps(lock))
