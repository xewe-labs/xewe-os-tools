import hashlib
import http.server
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from xewe.env import fetch
from xewe.report import XeweError

PAYLOAD = bytes(range(256)) * 400


class _RangeHandler(http.server.BaseHTTPRequestHandler):
    requests: list[str | None] = []

    def do_GET(self) -> None:  # noqa: N802
        rng = self.headers.get("Range")
        _RangeHandler.requests.append(rng)
        start = int(rng.split("=")[1].rstrip("-")) if rng else 0
        body = PAYLOAD[start:]
        self.send_response(206 if rng else 200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        pass


@pytest.fixture
def server() -> Iterator[str]:
    _RangeHandler.requests = []
    httpd = http.server.HTTPServer(("127.0.0.1", 0), _RangeHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}/file.tar.gz"
    httpd.shutdown()


@pytest.mark.parametrize(("system", "machine", "asset"), [
    ("Linux", "x86_64", "Linux_64bit"),
    ("Linux", "aarch64", "Linux_ARM64"),
    ("Darwin", "arm64", "macOS_ARM64"),
    ("Darwin", "x86_64", "macOS_64bit"),
])
def test_asset_name(system: str, machine: str, asset: str) -> None:
    assert fetch.arduino_cli_platform(system, machine) == (asset, "tar.gz")


def test_unknown_host() -> None:
    with pytest.raises(XeweError):
        fetch.arduino_cli_platform("Linux", "riscv64")


def test_parse_checksums() -> None:
    sha = "a" * 64
    assert fetch.parse_checksums(f"{sha}  arduino-cli_1.5.1_Linux_ARM64.tar.gz\njunk\n") == {
        "arduino-cli_1.5.1_Linux_ARM64.tar.gz": sha
    }


def test_download_resumes_part_file(server: str, tmp_path: Path) -> None:
    dest = tmp_path / "file.tar.gz"
    (tmp_path / "file.tar.gz.part").write_bytes(PAYLOAD[:1000])
    fetch.download(server, dest, hashlib.sha256(PAYLOAD).hexdigest())
    assert dest.read_bytes() == PAYLOAD
    assert _RangeHandler.requests == ["bytes=1000-"]
    assert not (tmp_path / "file.tar.gz.part").exists()


def test_checksum_mismatch_rejected(server: str, tmp_path: Path) -> None:
    dest = tmp_path / "file.tar.gz"
    with pytest.raises(XeweError, match="checksum mismatch"):
        fetch.download(server, dest, "0" * 64)
    assert not dest.exists() and not (tmp_path / "file.tar.gz.part").exists()


def test_latest_tag_selection() -> None:
    out = "\n".join(f"{'0' * 40}\trefs/tags/{t}" for t in ["v1.2.0", "v1.10.0", "1.9.9", "v2.0.0-rc1", "nightly"])
    tags = fetch.parse_ls_remote_tags(out)
    assert fetch.latest_tag(tags) == "v1.10.0"
    assert fetch.latest_tag(["nightly"]) is None


def test_resolve_latest_falls_back_to_default_branch(monkeypatch: pytest.MonkeyPatch) -> None:
    answers = {"--tags": "", "--symref": "ref: refs/heads/main\tHEAD\n" + "0" * 40 + "\tHEAD\n"}
    monkeypatch.setattr(fetch, "git", lambda *a, **k: answers[a[1]])
    assert fetch.resolve_latest("https://example.invalid/repo") == "main"


def test_copy_tree_skips_git(tmp_path: Path) -> None:
    src = tmp_path / "src"
    (src / ".git").mkdir(parents=True)
    (src / "a.txt").write_text("a")
    fetch.copy_tree(src, tmp_path / "dest")
    assert (tmp_path / "dest" / "a.txt").exists() and not (tmp_path / "dest" / ".git").exists()


# --- the ref `latest`: resolution from `git ls-remote --symref <repo> HEAD`

SHA = "f15739255f78b21d7fb5b25f378a3f072ed61794"


def test_parse_ls_remote_head() -> None:
    out = f"ref: refs/heads/main\tHEAD\n{SHA}\tHEAD\n"
    assert fetch.parse_ls_remote_head(out) == ("main", SHA)
    assert fetch.parse_ls_remote_head(f"ref: refs/heads/dev/next\tHEAD\n{SHA}\tHEAD\n") == ("dev/next", SHA)
    assert fetch.parse_ls_remote_head(f"{SHA}\tHEAD\n") == ("main", SHA)  # no symref: main
    assert fetch.parse_ls_remote_head("") == ("main", "")


def test_remote_head_runs_ls_remote_symref(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(fetch, "git", lambda *a, **k: calls.append(a) or f"ref: refs/heads/trunk\tHEAD\n{SHA}\tHEAD\n")
    assert fetch.remote_head("https://example.invalid/repo") == ("trunk", SHA)
    assert calls == [("ls-remote", "--symref", "https://example.invalid/repo", "HEAD")]
    assert fetch.is_latest("latest") and not fetch.is_latest("main") and not fetch.is_latest("v1.0.0")


def test_describe_install_record() -> None:
    assert fetch.describe({"ref": "latest", "branch": "main", "commit": SHA}) == "latest (main@f157392)"
    assert fetch.describe({"ref": "v1.0.0", "commit": SHA}) == "v1.0.0"
    assert fetch.describe({"ref": "latest", "commit": SHA, "source": "local:/x"}) == "latest"
    assert fetch.describe(None) == "-"
