"""``xewe release --version X.Y.Z``: matrix build into ``static/firmware/releases/<version>/`` (SPEC §11).

Git and gh commands are printed, never run.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import tarfile
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from xewe.build import chips
from xewe.build import compile as build
from xewe.env.project import Paths
from xewe.modules import lockfile
from xewe.modules.lockfile import Lock
from xewe.report import EXIT_FAIL, EXIT_OK, EXIT_USAGE, XeweError, log, result

NOTES_HEADER = "Release Notes for Version {version}\n===================================\n\n"
_CHIP_RE = re.compile(r"^chip$", re.IGNORECASE)
_NOTES_RE = re.compile(r"^_build_notes$", re.IGNORECASE)


@dataclass
class Row:
    """One release-matrix row.

    ``defines`` hold the cells verbatim (what the old flow wrote into Config.h); meta.json types
    them like the old JSON payload (integers/true/false bare, see :func:`xewe.build.compile.typed`).
    """

    chip: str
    defines: dict[str, str]
    folders: list[str]
    notes: str = ""


@dataclass
class Matrix:
    """Parsed release_matrix.csv (plain comma split, as the old release.sh)."""

    map_header: list[str]
    rows: list[Row] = field(default_factory=list)


def _version_tuple(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in v.split("."))


def _folder(value: str) -> str:
    name = value.replace('"', "").replace("\\", "").replace(" ", "_")
    return name or "empty"


def parse_matrix(text: str) -> Matrix:
    """Parse release_matrix.csv: CHIP required (any case), _BUILD_NOTES is notes only."""
    lines = [ln.replace("\r", "") for ln in text.splitlines()]
    if not lines:
        raise XeweError("release matrix is empty", EXIT_USAGE)
    headers = lines[0].split(",")
    chip_idx = next((i for i, h in enumerate(headers) if _CHIP_RE.match(h)), -1)
    notes_idx = next((i for i, h in enumerate(headers) if _NOTES_RE.match(h)), -1)
    if chip_idx < 0:
        raise XeweError("release matrix has no CHIP column", EXIT_USAGE)
    matrix = Matrix([h for i, h in enumerate(headers) if i != notes_idx])
    for line in lines[1:]:
        cells = line.split(",")
        if not cells[0]:
            continue

        def cell(i: int) -> str:
            return cells[i] if 0 <= i < len(cells) else ""

        chip = cell(chip_idx)
        chips.get(chip)
        defines: dict[str, str] = {}
        folders: list[str] = []
        for i, key in enumerate(headers):
            if i == notes_idx:
                continue
            folders.append(_folder(cell(i)))
            if i != chip_idx:
                defines[key] = cell(i)
        matrix.rows.append(Row(chip, defines, folders, cell(notes_idx)))
    return matrix


def default_matrix() -> Matrix:
    """One row per chip: c3, c6, s3."""
    return Matrix(["CHIP"], [Row(c, {}, [c]) for c in chips.ALL_CHIPS])


def publish_commands(version: str) -> list[str]:
    """The commands that publish the release (printed for a human to run)."""
    rel = f"static/firmware/releases/{version}"
    return [
        f"git add xewe.toml {rel}",
        f'git commit -m "release {version}"',
        f'git tag -a v{version} -m "Release {version}"',
        f"git push origin v{version}",
        f"gh release create v{version} static/firmware/releases/firmware-{version}.tar.gz --verify-tag "
        f"--title v{version} --notes-file {rel}/release_notes.txt",
    ]


def _check_tree(p: Paths) -> None:
    """Warn about uncommitted changes other than xewe.toml (read-only ``git status``)."""
    if shutil.which("git") is None or not (p.root / ".git").exists():
        return
    proc = subprocess.run(["git", "status", "--porcelain"], cwd=p.root, capture_output=True, text=True)
    dirty = [ln for ln in proc.stdout.splitlines() if ln[3:].strip() != "xewe.toml"]
    if dirty:
        log.warning("uncommitted changes in the working tree:\n%s", "\n".join(dirty))


def _edit_notes(version: str) -> str:
    editor = os.environ.get("EDITOR") or "vi"
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        f.write(NOTES_HEADER.format(version=version))
        path = Path(f.name)
    try:
        subprocess.run([*shlex.split(editor), str(path)], check=False)
        return "".join(path.read_text().splitlines(keepends=True)[2:])
    finally:
        path.unlink(missing_ok=True)


def release(
    p: Paths,
    lock: Lock,
    version: str,
    matrix_file: Path | None = None,
    notes_file: Path | None = None,
    builder: Callable[..., build.BuildResult] = build.build_chip,
) -> int:
    """Build every matrix row with ``version`` and lay the artifacts out for publishing."""
    if not lockfile.VERSION_RE.match(version):
        raise XeweError(f"--version must be X.Y.Z, got '{version}'", EXIT_USAGE)
    if _version_tuple(version) < _version_tuple(lock.version):
        raise XeweError(f"{version} is less than the current version {lock.version}", EXIT_USAGE)
    _check_tree(p)

    if matrix_file is None:
        if (p.root / "release_matrix.csv").is_file():
            matrix_file = p.root / "release_matrix.csv"
        elif (p.build / "release_matrix.csv").is_file():
            matrix_file = p.build / "release_matrix.csv"
            log.warning("build/release_matrix.csv is the old location; move it to the project root")
    matrix = parse_matrix(matrix_file.read_text(encoding="utf-8")) if matrix_file else default_matrix()

    releases = p.root / "static" / "firmware" / "releases"
    version_dir = releases / version
    version_dir.mkdir(parents=True, exist_ok=True)
    notes = notes_file.read_text(encoding="utf-8") if notes_file else _edit_notes(version)
    (version_dir / "release_notes.txt").write_text(notes, encoding="utf-8")
    (version_dir / "firmware_map.csv").write_text(",".join(matrix.map_header) + "\n", encoding="utf-8")

    for row in matrix.rows:
        dest = version_dir.joinpath(*row.folders)
        result(f"release  {row.chip}  {p.rel(dest)}")
        res = builder(p, lock, row.chip, row.defines, version=version)
        if not res.ok:
            log.error("release stopped: build failed for %s", row.chip)
            return EXIT_FAIL
        dest.mkdir(parents=True, exist_ok=True)
        out = p.out_dir(row.chip)
        bin_name = build.binary_name(version, row.chip, build.project_name(p, lock))
        for name in (bin_name, "manifest.json", "meta.json"):
            shutil.move(str(out / name), str(dest / name))
        if row.notes.strip():
            (dest / "build_notes.txt").write_text(row.notes + "\n", encoding="utf-8")
        meta_path = dest / "meta.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        rel_dir = f"{p.root.name}/{p.rel(dest)}"
        artifacts = meta.setdefault("artifacts", {})
        artifacts["path_rel_binary"] = f"{rel_dir}/{bin_name}"
        artifacts["path_rel_manifest_json"] = f"{rel_dir}/manifest.json"
        artifacts["path_rel_meta_json"] = f"{rel_dir}/meta.json"
        meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")

    lock.version = version
    lockfile.save(lock, p.lock)
    archive = releases / f"firmware-{version}.tar.gz"
    with tarfile.open(archive, "w:gz") as tf:
        tf.add(version_dir, arcname=version)
    result(f"release {version} ready in {p.rel(version_dir)}; to publish, run:")
    for cmd in publish_commands(version):
        result(f"  {cmd}")
    return EXIT_OK
