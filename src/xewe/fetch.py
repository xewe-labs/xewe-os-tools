"""Downloads (urllib, ``.part`` + HTTP Range resume + sha256) and git wrappers."""

from __future__ import annotations

import hashlib
import os
import platform
import re
import shutil
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

from xewe.report import EXIT_FAIL, EXIT_NOT_SETUP, XeweError, log

TAG_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_CHUNK = 1 << 16


def arduino_cli_platform(system: str | None = None, machine: str | None = None) -> tuple[str, str]:
    """Return (asset platform, archive extension) of the arduino-cli release for this host."""
    system = system or platform.system()
    machine = (machine or platform.machine()).lower()
    arm = machine in ("aarch64", "arm64")
    x64 = machine in ("x86_64", "amd64")
    if system == "Linux" and x64:
        return "Linux_64bit", "tar.gz"
    if system == "Linux" and arm:
        return "Linux_ARM64", "tar.gz"
    if system == "Darwin" and arm:
        return "macOS_ARM64", "tar.gz"
    if system == "Darwin" and x64:
        return "macOS_64bit", "tar.gz"
    if system == "Windows" and x64:
        return "Windows_64bit", "zip"
    raise XeweError(f"no arduino-cli build for {system}/{machine}", EXIT_NOT_SETUP)


def parse_checksums(text: str) -> dict[str, str]:
    """Parse a ``<sha256>  <file>`` checksums file into {file: sha256}."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2 and re.fullmatch(r"[0-9a-fA-F]{64}", parts[0]):
            out[parts[1].lstrip("*")] = parts[0].lower()
    return out


def sha256_file(path: Path) -> str:
    """Hex sha256 of a file."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch_text(url: str, timeout: float = 60) -> str:
    """GET ``url`` and return the body as text."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.read().decode("utf-8")
    except (urllib.error.URLError, OSError) as exc:
        raise XeweError(f"cannot download {url}: {exc}") from None


def download(url: str, dest: Path, sha256: str | None = None, timeout: float = 60) -> Path:
    """Download ``url`` to ``dest`` through ``dest.part``, resuming a partial file.

    The file is renamed into place only after the checksum (when given) matches; a mismatch
    deletes the partial file and raises.
    """
    if dest.is_file() and (sha256 is None or sha256_file(dest) == sha256):
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    offset = part.stat().st_size if part.exists() else 0
    req = urllib.request.Request(url)
    if offset:
        req.add_header("Range", f"bytes={offset}-")
    log.debug("download %s -> %s (resume at %d)", url, dest, offset)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            mode = "ab" if offset and resp.status == 206 else "wb"
            with part.open(mode) as f:
                shutil.copyfileobj(resp, f, _CHUNK)
    except urllib.error.HTTPError as exc:
        if exc.code == 416 and offset:  # partial file already complete
            pass
        else:
            raise XeweError(f"cannot download {url}: {exc}") from None
    except (urllib.error.URLError, OSError) as exc:
        raise XeweError(f"cannot download {url}: {exc} (re-run to resume)") from None
    if sha256 is not None:
        got = sha256_file(part)
        if got != sha256.lower():
            part.unlink()
            raise XeweError(f"checksum mismatch for {url}: expected {sha256}, got {got}")
    os.replace(part, dest)
    return dest


# ---------------------------------------------------------------------------- git


def git(*args: str, cwd: Path | None = None, check: bool = True) -> str:
    """Run git (no terminal prompts) and return stdout."""
    if shutil.which("git") is None:
        raise XeweError("git is required (install it with your system package manager)", EXIT_NOT_SETUP)
    argv = ["git", *args]
    log.debug("$ %s", " ".join(argv))
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    proc = subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True)
    if check and proc.returncode != 0:
        raise XeweError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout


def latest_tag(tags: list[str]) -> str | None:
    """Highest ``vX.Y.Z``/``X.Y.Z`` tag by numeric tuple, or None."""
    best: tuple[tuple[int, int, int], str] | None = None
    for tag in tags:
        m = TAG_RE.match(tag)
        if m:
            key = (int(m[1]), int(m[2]), int(m[3]))
            if best is None or key > best[0]:
                best = (key, tag)
    return best[1] if best else None


def parse_ls_remote_tags(output: str) -> list[str]:
    """Tag names from ``git ls-remote --tags --refs`` output."""
    tags = []
    for line in output.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].startswith("refs/tags/"):
            tags.append(parts[1][len("refs/tags/"):])
    return tags


def resolve_latest(repo: str) -> str:
    """Newest release tag of ``repo``; the default branch HEAD (with a warning) if it has none."""
    tag = latest_tag(parse_ls_remote_tags(git("ls-remote", "--tags", "--refs", repo)))
    if tag:
        return tag
    head = git("ls-remote", "--symref", repo, "HEAD")
    m = re.search(r"ref: refs/heads/(\S+)\s+HEAD", head)
    branch = m[1] if m else "HEAD"
    log.warning("%s has no X.Y.Z tags; using the default branch %s", repo, branch)
    return branch


def head_commit(path: Path) -> str:
    """Commit of HEAD in ``path``, or ``-`` when it is not a git checkout."""
    if not (path / ".git").exists() or shutil.which("git") is None:
        return "-"
    out = git("rev-parse", "--verify", "-q", "HEAD", cwd=path, check=False).strip()
    return out or "-"


def clone(repo: str, ref: str, dest: Path) -> str:
    """Shallow-clone ``repo`` at ``ref`` into ``dest`` (via a temp dir + rename); return the commit."""
    tmp = dest.with_name(f".tmp-{dest.name}")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.parent.mkdir(parents=True, exist_ok=True)
    if SHA_RE.match(ref):
        git("init", "--quiet", str(tmp))
        git("remote", "add", "origin", repo, cwd=tmp)
        git("fetch", "--quiet", "--depth", "1", "origin", ref, cwd=tmp)
        git("checkout", "--quiet", ref, cwd=tmp)
    else:
        git("clone", "--quiet", "--depth", "1", "--branch", ref, repo, str(tmp))
    if dest.exists():
        shutil.rmtree(dest)
    os.replace(tmp, dest)
    return head_commit(dest)


def copy_tree(src: Path, dest: Path, only: list[str] | None = None) -> None:
    """Copy ``src`` to ``dest`` without ``.git`` (via a temp dir + rename).

    ``only`` restricts the copy to those top-level entries of ``src``.
    """
    if not src.is_dir():
        raise XeweError(f"local source {src} is not a directory", EXIT_FAIL)
    tmp = dest.with_name(f".tmp-{dest.name}")
    shutil.rmtree(tmp, ignore_errors=True)
    ignore = shutil.ignore_patterns(".git")
    if only is None:
        shutil.copytree(src, tmp, ignore=ignore, symlinks=True)
    else:
        tmp.mkdir(parents=True)
        for name in only:
            shutil.copytree(src / name, tmp / name, ignore=ignore, symlinks=True)
    if dest.exists():
        shutil.rmtree(dest)
    os.replace(tmp, dest)
