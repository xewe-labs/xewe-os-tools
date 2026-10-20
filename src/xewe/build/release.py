"""``xewe release --version X.Y.Z``: matrix build into ``static/firmware/releases/<version>/`` (doc/spec.md §11).

One image per matrix row, ``firmware_map.csv`` with one row per image, and the GitHub Release assets
staged flat in ``build/release/<version>/assets/``. ``--chip C`` builds one chip's rows (one CI job per
chip); ``--assemble`` merges those per-chip maps. Git and gh commands are printed, never run.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from xewe.build import chips
from xewe.build import compile as build
from xewe.env import config, dotenv, fetch
from xewe.env.project import Paths
from xewe.modules import lockfile
from xewe.modules.lockfile import Lock
from xewe.report import EXIT_FAIL, EXIT_OK, EXIT_USAGE, XeWeError, log, result

NOTES_HEADER = "Release Notes for Version {version}\n===================================\n\n"
_CHIP_RE = re.compile(r"^chip$", re.IGNORECASE)
_NOTES_RE = re.compile(r"^_build_notes$", re.IGNORECASE)
# firmware_map.csv: the matrix columns (without _BUILD_NOTES), then these reserved columns
MAP_COLUMNS = ("_PATH", "_ASSET", "_SIZE", "_SHA256", "_META", "_MANIFEST", "_BUILD_NOTES")
MAP_NAME = "firmware_map.csv"
PART_MAP = "firmware_map.{chip}.csv"


@dataclass
class Row:
    """One release-matrix row.

    ``defines`` hold the cells verbatim (what the old flow wrote into Config.h); meta.json types
    them like the old JSON payload (integers/true/false bare, see :func:`xewe.build.compile.typed`).
    ``cells`` are the row's values in ``Matrix.map_header`` order (the leading firmware_map.csv cells).
    """

    chip: str
    defines: dict[str, str]
    folders: list[str]
    notes: str = ""
    cells: list[str] = field(default_factory=list)


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
        raise XeWeError("release matrix is empty", EXIT_USAGE)
    headers = lines[0].split(",")
    chip_idx = next((i for i, h in enumerate(headers) if _CHIP_RE.match(h)), -1)
    notes_idx = next((i for i, h in enumerate(headers) if _NOTES_RE.match(h)), -1)
    if chip_idx < 0:
        raise XeWeError("release matrix has no CHIP column", EXIT_USAGE)
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
        map_cells = [cell(i) for i in range(len(headers)) if i != notes_idx]
        matrix.rows.append(Row(chip, defines, folders, cell(notes_idx), map_cells))
    return matrix


def default_matrix() -> Matrix:
    """One row per chip: c3, c6, s3."""
    return Matrix(["CHIP"], [Row(c, {}, [c], cells=[c]) for c in chips.ALL_CHIPS])


def asset_names(folders: list[str], bin_name: str) -> tuple[str, str, str]:
    """The flat release-asset names of one row: (bin, meta, manifest).

    The prefix is the row's folders joined by ``_``. The bin keeps its own name when the row has one
    folder (``3.1.0-c3-xewe-os.bin``) and is prefixed otherwise (``c6_20_19_3.1.0-c6-xewe-led-os.bin``);
    meta and manifest are always prefixed (``c3_meta.json``, ``c6_20_19_manifest.json``).
    """
    prefix = "_".join(folders)
    bin_asset = f"{prefix}_{bin_name}" if len(folders) > 1 else bin_name
    return bin_asset, f"{prefix}_meta.json", f"{prefix}_manifest.json"


def map_header(matrix: Matrix) -> list[str]:
    """firmware_map.csv header: the matrix columns without _BUILD_NOTES, then :data:`MAP_COLUMNS`."""
    return [*matrix.map_header, *MAP_COLUMNS]


def write_map(path: Path, header: list[str], rows: list[list[str]]) -> None:
    """Write a firmware map with the csv module (minimal quoting, ``\\n`` line ends)."""
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(header)
    writer.writerows(rows)
    path.write_text(buf.getvalue(), encoding="utf-8")


def read_map(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    """(header, rows as dicts) of a firmware map written by :func:`write_map`."""
    reader = csv.reader(io.StringIO(path.read_text(encoding="utf-8")))
    header = next(reader, [])
    return header, [dict(zip(header, cells, strict=True)) for cells in reader if cells]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stage_assets(version_dir: Path, out_dir: Path, archive: Path | None = None) -> list[Path]:
    """Lay the GitHub Release assets out flat in ``out_dir`` (emptied first) from ``firmware_map.csv``.

    Per map row: the bin under ``_ASSET``, the row's meta.json under ``_META`` and its manifest.json
    under ``_MANIFEST`` with every part path set to the flat bin name (ESP Web Tools resolves it next
    to the manifest); then firmware_map.csv and ``archive`` (the tarball) when given. A bin whose
    size or sha256 differs from its map row fails with exit 1, as do two rows with one asset name
    (the ``_``-joined prefix is ambiguous: folders ``a_b``+``c`` and ``a``+``b_c``), checked before
    anything is written.
    """
    _, rows = read_map(version_dir / MAP_NAME)
    seen: dict[str, str] = {}
    for row in rows:
        for column in ("_ASSET", "_META", "_MANIFEST"):
            name = row[column]
            if name in seen:
                raise XeWeError(
                    f"firmware_map.csv: rows {seen[name]} and {row['_PATH']} both name the asset {name}; "
                    "change a matrix value so the folder names differ",
                    EXIT_FAIL,
                )
            seen[name] = row["_PATH"]
    shutil.rmtree(out_dir, ignore_errors=True)
    out_dir.mkdir(parents=True)
    for row in rows:
        folder = version_dir / row["_PATH"]
        meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
        binary = folder / meta["artifacts"]["binary_filename"]
        if not binary.is_file() or str(binary.stat().st_size) != row["_SIZE"] or _sha256(binary) != row["_SHA256"]:
            raise XeWeError(f"{binary} does not match its firmware_map.csv row (size or sha256)", EXIT_FAIL)
        shutil.copy2(binary, out_dir / row["_ASSET"])
        shutil.copy2(folder / "meta.json", out_dir / row["_META"])
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        for build_entry in manifest.get("builds", []):
            for part in build_entry.get("parts", []):
                part["path"] = row["_ASSET"]
        (out_dir / row["_MANIFEST"]).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    shutil.copy2(version_dir / MAP_NAME, out_dir / MAP_NAME)
    if archive is not None:
        shutil.copy2(archive, out_dir / archive.name)
    return sorted(out_dir.iterdir())


def publish_commands(version: str, card: bool = False) -> list[str]:
    """The commands that publish the release (printed for a human to run); tags are ``X.Y.Z``, no ``v``.

    The binaries are release assets only (``build/release/<v>/assets/``), never committed; ``card``
    adds the committed ``releases/<v>/release_card.md`` to the assets.
    """
    rel = f"static/firmware/releases/{version}"
    card_asset = f" releases/{version}/release_card.md" if card else ""
    return [
        "git add xewe.toml",
        f'git commit -m "release {version}"',
        f'git tag -a {version} -m "Release {version}"',
        f"git push origin {version}",
        f"gh release create {version} build/release/{version}/assets/*{card_asset} --verify-tag "
        f"--title {version} --notes-file {rel}/release_notes.txt",
    ]


def _commit(record: Any) -> str:
    """The commit of an ``[installed]`` record (``-`` when setup did not record one)."""
    return str(record.get("commit") or "-") if isinstance(record, dict) else "-"


def provenance(p: Paths, cfg: config.BuildConfig | None) -> dict[str, Any]:
    """What a release was built from: the project commit, and the commit each ``latest`` (or tag)
    ref of core, modules, tools and every library resolved to at setup time (``[installed]`` of
    build_config.toml). ``-`` marks an unknown commit (not a git checkout, or not recorded)."""
    installed = cfg.installed if cfg else {}
    out: dict[str, Any] = {"project_commit": fetch.head_commit(p.root)}
    for name in ("core", "modules", "tools"):
        record = installed.get(name, {})
        out[f"{name}_ref"] = str(record.get("ref", "")) if isinstance(record, dict) else ""
        out[f"{name}_commit"] = _commit(record)
    libs = installed.get("libraries", {})
    out["libraries"] = {
        lib: {"ref": str(rec.get("ref", "")), "commit": _commit(rec)}
        for lib, rec in sorted(libs.items())
        if isinstance(rec, dict)
    }
    return out


def _with_provenance(meta: dict[str, Any], prov: dict[str, Any]) -> dict[str, Any]:
    """``meta`` plus ``prov``, each ``*_commit`` right after its ``*_ref`` (key order is what people
    read): ``project_commit, core_ref, core_commit, modules_ref, modules_commit, tools_ref,
    tools_commit`` where ``core_ref`` was; ``libraries`` at the end."""
    group = {
        "core_ref": ("project_commit", "core_ref", "core_commit"),
        "modules_ref": ("modules_ref", "modules_commit", "tools_ref", "tools_commit"),
    }
    merged: dict[str, Any] = {}
    for key, value in meta.items():
        if key in group:
            merged.update((k, prov[k] or meta.get(k, "")) for k in group[key])
        elif key not in prov:
            merged[key] = value
    for key, value in prov.items():
        merged.setdefault(key, value)
    return merged


def _check_tree(p: Paths) -> None:
    """Warn about uncommitted changes other than xewe.toml (read-only ``git status``)."""
    if shutil.which("git") is None or not (p.root / ".git").exists():
        return
    proc = subprocess.run(["git", "status", "--porcelain"], cwd=p.root, capture_output=True, text=True)
    dirty = [ln for ln in proc.stdout.splitlines() if ln[3:].strip() != "xewe.toml"]
    if dirty:
        log.warning("uncommitted changes in the working tree:\n%s", "\n".join(dirty))


def check_no_sentinel(p: Paths) -> None:
    """Refuse (exit 2) a ``Config.h`` with an active ``INJECTED_FROM_DOTENV`` line: a release image
    is built from the committed ``Config.h`` and the matrix only, never from a dotenv file."""
    found = dotenv.sentinels(p.root / "Config.h")
    if found:
        lines = "\n".join(f"Config.h:{n}: {name} is {dotenv.SENTINEL}" for n, name in found)
        raise XeWeError(
            f"{lines}\na release takes no value from the dotenv file: set the value in Config.h or comment the line out",
            EXIT_USAGE,
        )


def interactive() -> bool:
    """True when ``$EDITOR`` may be opened for the notes: stdin is a terminal and
    ``XEWE_NONINTERACTIVE`` is not ``1``."""
    return os.environ.get("XEWE_NONINTERACTIVE") != "1" and sys.stdin is not None and sys.stdin.isatty()


def _edit_notes(version: str) -> str:
    """Open ``$EDITOR`` on a notes template; every path without ``--notes`` comes here, so the
    non-interactive refusal (exit 2) covers the build, ``--chip`` and ``--assemble`` alike."""
    if not interactive():
        raise XeWeError(
            "release notes: pass --notes FILE ($EDITOR is not opened when stdin is not a terminal "
            "or XEWE_NONINTERACTIVE=1)",
            EXIT_USAGE,
        )
    editor = os.environ.get("EDITOR") or "vi"
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        f.write(NOTES_HEADER.format(version=version))
        path = Path(f.name)
    try:
        subprocess.run([*shlex.split(editor), str(path)], check=False)
        return "".join(path.read_text().splitlines(keepends=True)[2:])
    finally:
        path.unlink(missing_ok=True)


def load_matrix(p: Paths, matrix_file: Path | None = None) -> Matrix:
    """``matrix_file``, else release_matrix.csv at the project root (or the old build/ location),
    else the default matrix."""
    if matrix_file is None:
        if (p.root / "release_matrix.csv").is_file():
            matrix_file = p.root / "release_matrix.csv"
        elif (p.build / "release_matrix.csv").is_file():
            matrix_file = p.build / "release_matrix.csv"
            log.warning("build/release_matrix.csv is the old location; move it to the project root")
    return parse_matrix(matrix_file.read_text(encoding="utf-8")) if matrix_file else default_matrix()


def _build_row(
    p: Paths,
    lock: Lock,
    version: str,
    version_dir: Path,
    row: Row,
    builder: Callable[..., build.BuildResult],
    prov: dict[str, Any] | None,
) -> tuple[list[str], dict[str, Any]] | None:
    """Build one row into its folder: (its firmware_map.csv cells, provenance), or None on a failed build."""
    dest = version_dir.joinpath(*row.folders)
    result(f"release  {row.chip}  {p.rel(dest)}")
    res = builder(p, lock, row.chip, row.defines, version=version)
    if not res.ok:
        log.error("release stopped: build failed for %s", row.chip)
        return None
    dest.mkdir(parents=True, exist_ok=True)
    out = p.out_dir(row.chip)
    bin_name = build.binary_name(version, row.chip, build.project_name(p, lock))
    for name in (bin_name, "manifest.json", "meta.json"):
        shutil.move(str(out / name), str(dest / name))
    if row.notes.strip():
        (dest / "build_notes.txt").write_text(row.notes + "\n", encoding="utf-8")
    meta_path = dest / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if prov is None:
        prov = provenance(p, config.load(p))
    meta = _with_provenance(meta, prov)
    rel_dir = f"{p.root.name}/{p.rel(dest)}"
    bin_asset, meta_asset, manifest_asset = asset_names(row.folders, bin_name)
    size, sha = (dest / bin_name).stat().st_size, _sha256(dest / bin_name)
    artifacts = meta.setdefault("artifacts", {})
    artifacts["path_rel_binary"] = f"{rel_dir}/{bin_name}"
    artifacts["path_rel_manifest_json"] = f"{rel_dir}/manifest.json"
    artifacts["path_rel_meta_json"] = f"{rel_dir}/meta.json"
    artifacts.update(size=size, sha256=sha, asset_name=bin_asset, meta_asset=meta_asset, manifest_asset=manifest_asset)
    meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    cells = [*row.cells, "/".join(row.folders), bin_asset, str(size), sha, meta_asset, manifest_asset, row.notes]
    return cells, prov


def _merge_parts(matrix: Matrix, version_dir: Path) -> list[list[str]] | None:
    """The rows of every ``firmware_map.<chip>.csv`` in matrix order; None (logged) when a matrix row
    has no build, a part has a row the matrix lacks, or a part was made from another matrix."""
    header = map_header(matrix)
    by_path: dict[str, list[str]] = {}
    parts = sorted(version_dir.glob(PART_MAP.format(chip="*")))
    if not parts:
        log.error("no per-chip maps in %s; run `xewe release --chip C` for every chip first", version_dir)
        return None
    for part in parts:
        part_header, rows = read_map(part)
        if part_header != header:
            log.error("%s was made from another release matrix (header %s)", part.name, ",".join(part_header))
            return None
        for r in rows:
            by_path[r["_PATH"]] = [r[h] for h in header]
    merged: list[list[str]] = []
    missing: list[str] = []
    for row in matrix.rows:
        cells = by_path.pop("/".join(row.folders), None)
        if cells is None:
            missing.append("/".join(row.folders))
        else:
            merged.append(cells)
    for path in missing:
        log.error("release not assembled: no build for matrix row %s", path)
    for path in by_path:
        log.error("release not assembled: built row %s is not in the release matrix", path)
    return None if missing or by_path else merged


def release(
    p: Paths,
    lock: Lock,
    version: str,
    matrix_file: Path | None = None,
    notes_file: Path | None = None,
    builder: Callable[..., build.BuildResult] = build.build_chip,
    chip: str | None = None,
    assemble: bool = False,
) -> int:
    """Build every matrix row with ``version``; write firmware_map.csv, the tarball and the staged assets.

    ``chip``: build only that chip's rows and write ``firmware_map.<chip>.csv`` (no tarball, no assets).
    ``assemble``: build nothing; merge the per-chip maps in matrix order, then tarball and assets.
    """
    if not lockfile.VERSION_RE.match(version):
        raise XeWeError(f"--version must be X.Y.Z, got '{version}'", EXIT_USAGE)
    if _version_tuple(version) < _version_tuple(lock.version):
        raise XeWeError(f"{version} is less than the current version {lock.version}", EXIT_USAGE)
    if chip is not None and assemble:
        raise XeWeError("--chip and --assemble do not go together", EXIT_USAGE)
    check_no_sentinel(p)
    _check_tree(p)
    matrix = load_matrix(p, matrix_file)

    releases = p.root / "static" / "firmware" / "releases"
    version_dir = releases / version
    header = map_header(matrix)
    if assemble:
        merged = _merge_parts(matrix, version_dir)
        if merged is None:
            return EXIT_FAIL
        if notes_file is not None or not (version_dir / "release_notes.txt").is_file():
            notes = notes_file.read_text(encoding="utf-8") if notes_file else _edit_notes(version)
            (version_dir / "release_notes.txt").write_text(notes, encoding="utf-8")
        rows = merged
    else:
        selected = [r for r in matrix.rows if chip is None or r.chip == chip]
        if not selected:
            raise XeWeError(f"the release matrix has no {chip} row", EXIT_USAGE)
        notes = notes_file.read_text(encoding="utf-8") if notes_file else _edit_notes(version)
        version_dir.mkdir(parents=True, exist_ok=True)
        (version_dir / "release_notes.txt").write_text(notes, encoding="utf-8")
        rows = []
        prov: dict[str, Any] | None = None
        for row in selected:
            built = _build_row(p, lock, version, version_dir, row, builder, prov)
            if built is None:
                return EXIT_FAIL
            cells, prov = built
            rows.append(cells)
        if chip is not None:
            write_map(version_dir / PART_MAP.format(chip=chip), header, rows)
            lock.version = version
            lockfile.save(lock, p.lock)
            result(f"release {version} {chip}: {len(rows)} images in {p.rel(version_dir)}")
            result(f"  when every chip is built: xewe release --version {version} --assemble")
            return EXIT_OK

    for part in version_dir.glob(PART_MAP.format(chip="*")):
        part.unlink()
    write_map(version_dir / MAP_NAME, header, rows)
    lock.version = version
    lockfile.save(lock, p.lock)
    archive = releases / f"firmware-{version}.tar.gz"
    with tarfile.open(archive, "w:gz") as tf:
        tf.add(version_dir, arcname=version)
    assets_dir = p.build / "release" / version / "assets"
    staged = stage_assets(version_dir, assets_dir, archive)
    result(f"release {version} ready in {p.rel(version_dir)}: {len(rows)} images")
    result(f"  {len(staged)} release assets in {p.rel(assets_dir)}; to publish, run:")
    card = (p.root / "releases" / version / "release_card.md").is_file()
    for cmd in publish_commands(version, card):
        result(f"  {cmd}")
    return EXIT_OK
