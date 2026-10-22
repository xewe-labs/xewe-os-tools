"""``xewe release-card`` (doc/spec.md §14): fingerprint, redaction, record, JUnit and core host
parsing, the card (golden), refusal rules, ``--resume``, ``verify`` and the CI comparison.

git is faked (``FakeGit``): the tests run no git at all, except one read-only fingerprint of this
checkout. Set ``RELEASECARD_WRITE_GOLDEN=1`` to rewrite ``fixtures/releasecard/golden_card.md``.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from conftest import FIXTURES
from xewe.build import releasecard as rc
from xewe.cli import main
from xewe.env import dotenv

CARD_FIXTURES = FIXTURES / "releasecard"
GOLDEN = CARD_FIXTURES / "golden_card.md"
HEAD = "1234567890abcdef1234567890abcdef12345678"
NOW = "2026-10-13T12:00:00Z"
SECRET = "hunter22secret"
REPO_ROOT = Path(__file__).resolve().parents[2]


def blob(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


class FakeGit:
    """Read-only git for a scratch tree: every file and symlink outside ``build/`` and the run
    folders (``…/logs/``, gitignored in every repo) is tracked; ``gitlinks`` adds submodules."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.head = HEAD
        self.status = ""
        self.gitlinks: dict[str, str] = {}
        self.calls: list[tuple[str, ...]] = []

    def tracked(self) -> list[str]:
        out = list(self.gitlinks)
        for dirpath, dirnames, filenames in os.walk(self.root):
            for name in [*filenames, *(d for d in dirnames if (Path(dirpath) / d).is_symlink())]:
                rel = (Path(dirpath) / name).relative_to(self.root).as_posix()
                if not rel.startswith(("build/", ".git/")) and "/logs/" not in rel and rel not in out:
                    out.append(rel)
        return sorted(out)

    def index_entry(self, rel: str) -> str:
        f = self.root / rel
        if rel in self.gitlinks:
            mode, sha = "160000", self.gitlinks[rel]
        elif f.is_symlink():
            mode, sha = "120000", blob(os.fsencode(os.readlink(f)))
        elif f.is_file():
            mode, sha = "100755" if os.access(f, os.X_OK) else "100644", blob(f.read_bytes())
        else:
            mode, sha = "100644", "0" * 40
        return f"{mode} {sha} 0\t{rel}\0"

    def __call__(self, root: Path, *args: str, stdin: str | None = None) -> str:
        self.calls.append(args)
        if args == ("rev-parse", "HEAD"):
            return self.head + "\n"
        if args == ("rev-parse", "--show-toplevel"):
            return str(self.root) + "\n"
        if args == ("ls-files", "-z"):
            return "".join(p + "\0" for p in self.tracked())
        if args == ("ls-files", "-s", "-z"):
            return "".join(self.index_entry(p) for p in self.tracked())
        if args[:3] == ("ls-files", "-z", "--"):
            return "".join(p + "\0" for p in self.tracked() if p == args[3] or p.startswith(args[3] + "/"))
        if args == ("hash-object", "--stdin-paths"):
            assert stdin is not None
            return "".join(blob((self.root / p).read_bytes()) + "\n" for p in stdin.splitlines())
        if args == ("status", "--porcelain", "-z"):
            return self.status
        raise AssertionError(f"unexpected git {args}")


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeGit:
    """A scratch repo (fake git, fixed clock and run id); cwd is its root."""
    root = tmp_path / "demo"
    (root / "src").mkdir(parents=True)
    (root / "xewe.toml").write_text('[project]\nname = "demo"\nversion = "3.1.0"\n')
    (root / "src" / "app.cpp").write_text("int main() { return 0; }\n")
    (root / "releases" / "3.1.0").mkdir(parents=True)
    (root / "releases" / "3.1.0" / "notes.txt").write_text("demo 3.1.0\n\nWhat changed: everything.\n")
    git = FakeGit(root)
    monkeypatch.setattr(rc, "_git", git)
    monkeypatch.setattr(rc, "_now", lambda: NOW)
    ticks = iter(range(0, 100000, 3))
    monkeypatch.setattr(rc, "_clock", lambda: next(ticks) / 2)
    monkeypatch.setattr(rc, "_run_id", lambda: "20261013T120000Z-abcd")
    monkeypatch.setattr(rc, "_host", lambda: {"os": "Linux aarch64", "python": "3.12.3"})
    monkeypatch.setattr(rc, "tools_commit", lambda: "c0ffee" * 6 + "c0ff")
    monkeypatch.setattr(rc, "__version__", "3.1.0")
    monkeypatch.setattr(
        rc.Redactor,
        "from_environment",
        classmethod(lambda cls, root: cls({"XEWE_WIFI_PSW": SECRET}, "/home/bench")),
    )
    monkeypatch.chdir(root)
    return git


STEP_SCRIPT = """\
import shutil
import sys
from pathlib import Path

name = sys.argv[1]
out = Path("build/out")
with (out / "count.txt").open("a") as f:
    f.write(name + "\\n")
text = out / (name + ".out")
print(text.read_text() if text.is_file() else name + ": ok", end="")
xml = out / (name + ".xml")
if xml.is_file():
    Path("build/junit").mkdir(parents=True, exist_ok=True)
    shutil.copy(xml, Path("build/junit") / (name + ".xml"))
fail = out / (name + ".exit")
if fail.is_file():
    code = int(fail.read_text())
    fail.unlink()
    sys.exit(code)
"""


class Bench:
    """Drives ``record`` for one scripted step: ``build/step <name>`` prints ``build/out/<name>.out``,
    copies ``build/out/<name>.xml`` to ``build/junit/<name>.xml`` and exits with ``<name>.exit`` once."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.logs = root / "releases" / "3.1.0" / "logs"
        self.out = root / "build" / "out"
        self.out.mkdir(parents=True, exist_ok=True)
        script = root / "build" / "step"
        script.write_text(f"#!{sys.executable}\n" + STEP_SCRIPT)
        script.chmod(0o755)

    def output(self, name: str, text: str, xml: str | None = None, exit_once: int | None = None) -> None:
        (self.out / f"{name}.out").write_text(text)
        if xml is not None:
            (self.out / f"{name}.xml").write_text(xml)
        if exit_once is not None:
            (self.out / f"{name}.exit").write_text(str(exit_once))

    def runs(self) -> list[str]:
        f = self.out / "count.txt"
        return f.read_text().split() if f.is_file() else []

    def init(self, *extra: str) -> int:
        return main(
            ["release-card", "init", "--dir", str(self.logs), "--repo", "xewe-labs/demo", "--version", "3.1.0", *extra]
        )

    def record(self, kind: str, name: str, *opts: str, junit: bool = False) -> int:
        args = ["release-card", "record", "--dir", str(self.logs), "--kind", kind, *opts]
        if junit:
            args += ["--junit", f"build/junit/{name}.xml"]
        return main([*args, "--", "build/step", name])

    def render(self, *opts: str) -> int:
        return main(
            [
                "release-card",
                "render",
                "--dir",
                str(self.logs),
                "--notes",
                "releases/3.1.0/notes.txt",
                "--out",
                "releases/3.1.0/release_card.md",
                *opts,
            ]
        )

    @property
    def card(self) -> Path:
        return self.root / "releases" / "3.1.0" / "release_card.md"

    @property
    def draft(self) -> Path:
        return self.logs / rc.DRAFT_NAME

    def run_json(self) -> dict[str, Any]:
        return json.loads((self.logs / rc.RUN_FILE).read_text())

    def edit_run(self, change: Callable[[dict[str, Any]], None]) -> None:
        run = self.run_json()
        change(run)
        (self.logs / rc.RUN_FILE).write_text(json.dumps(run, indent=2) + "\n")


def write_image(root: Path, chip: str, sketch: int, config: dict[str, Any] | None = None) -> str:
    """``build/builds/<chip>/out`` as ``xewe build`` leaves it; returns the image's sha256."""
    out = root / "build" / "builds" / chip / "out"
    out.mkdir(parents=True, exist_ok=True)
    image = out / f"3.1.0-{chip}-demo.bin"
    image.write_bytes(f"image {chip} {sketch} {sorted((config or {}).items())}".encode())
    meta = {
        "type": chip,
        "fqbn": f"esp32:esp32:esp32{chip}:CDCOnBoot=cdc,PartitionScheme=huge_app",
        "partition_scheme": "huge_app",
        "timestamp_param": "2026-10-13T11:58:00Z",
        "config": config or {},
        "app_partition_size": 3145728,
        "sketch_size": sketch,
        "sketch_size_percent": 35,
        "warnings": 0,
    }
    (out / "meta.json").write_text(json.dumps(meta, indent=2) + "\n")
    (out / "compile.log").write_text(
        f"Sketch uses {sketch} bytes (35%) of program storage space. Maximum is 3145728 bytes.\n"
        "Global variables use 45678 bytes (13%) of dynamic memory, leaving 282002 bytes for local variables. "
        "Maximum is 327680 bytes.\n"
    )
    return hashlib.sha256(image.read_bytes()).hexdigest()


BUILD_CONFIG = """\
# generated by xewe setup; do not edit
schema = 1
tools_version = "3.1.0"

[installed]
arduino_cli = "1.5.1"
esp32 = "3.3.12"
core = { ref = "3.1.0", commit = "207c2562023649c5fb53200365c9d58e9b86dbe3", source = "https://github.com/xewe-labs/xewe-os-core" }
modules = { ref = "3.1.0", commit = "3d5e319aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", source = "https://github.com/xewe-labs/xewe-os-modules" }
tools = { ref = "3.1.0", commit = "-", source = "https://github.com/xewe-labs/xewe-os-tools" }
libraries = { FastLED = { ref = "3.10.3", commit = "fa57ed", source = "https://github.com/FastLED/FastLED", origin = "modules" } }
"""


def board_xml(name: str, sha: str, match: str = "true") -> str:
    return (CARD_FIXTURES / name).read_text().replace("@FW_SHA256@", sha).replace("@FW_MATCH@", match)


def full_run(
    git: FakeGit, s3_sha_in_tests: str | None = None, s3_match: str = "true", s3_board_chip: str = "s3"
) -> Bench:
    """A product-like run: chips c3 and s3, hardware s3, one flash retry."""
    b = Bench(git.root)
    root = git.root
    (root / "build" / "config").mkdir(parents=True)
    (root / "build" / "config" / "build_config.toml").write_text(BUILD_CONFIG)
    assert b.init() == 0
    b.output("setup", f"installing into build/\nWi-Fi password {SECRET} stays in the dotenv file\nsetup complete\n")
    assert b.record("setup", "setup") == 0
    b.output("validate", "8 modules, 0 errors, 0 warnings\n")
    assert b.record("validate", "validate") == 0
    b.output("unit", "4 passed\n", (CARD_FIXTURES / "unit.xml").read_text())
    assert b.record("unit", "unit", junit=True) == 0
    write_image(root, "c3", 1123456)
    b.output("build-c3", "ok     c3  51 s  Sketch uses 1123456 bytes (35%)  0 warnings\n")
    assert b.record("build", "build-c3", "--chip", "c3") == 0
    write_image(root, "s3", 1234567)
    b.output("build-s3", "ok     s3  60 s  Sketch uses 1234567 bytes (39%)  0 warnings\n")
    assert b.record("build", "build-s3", "--chip", "s3") == 0
    sha = write_image(root, "s3", 1234600, {"XEWE_LED_PIN_DATA": 48})
    b.output("flash-s3", "A fatal error occurred: port vanished\n", exit_once=2)
    assert b.record("flash", "flash-s3", "--chip", "s3") == 1  # the command's exit 2 is in run.json
    b.output("flash-s3", "flashed s3 /dev/ttyACM0 (nvs kept)\n")
    assert b.record("flash", "flash-s3", "--chip", "s3") == 0
    s3_xml = board_xml("board-s3.xml", s3_sha_in_tests or sha, s3_match).replace("s3 sn=", s3_board_chip + " sn=")
    b.output("board-s3", "3 passed, 1 skipped\n", s3_xml)
    assert b.record("board", "board-s3", "--chip", "s3", junit=True) == 0
    b.output("board-c3", "1 passed, 3 skipped\n", (CARD_FIXTURES / "board-c3.xml").read_text())
    assert b.record("board", "board-c3", "--chip", "c3", junit=True) == 0
    b.output("format", "format: 12 C++ and 3 Python files, 0 would reformat\n")
    assert b.record("check", "format", "--name", "format") == 0
    b.output("brand-lint", "brand-lint: clean\n")
    assert b.record("check", "brand-lint", "--name", "brand-lint") == 0
    return b


# --------------------------------------------------------------------------- golden card


def test_golden_card(repo: FakeGit, capsys: pytest.CaptureFixture[str]) -> None:
    b = full_run(repo)
    assert b.render("--chips", "c3,s3", "--hardware", "s3") == 0, capsys.readouterr().err
    text = b.card.read_text()
    if os.environ.get("RELEASECARD_WRITE_GOLDEN") == "1":
        GOLDEN.write_text(text)
    assert text == GOLDEN.read_text()
    assert not b.draft.exists()
    assert SECRET not in text and "«redacted:XEWE_WIFI_PSW»" in text


def test_card_template_sections() -> None:
    headings = [line for line in rc.CARD_TEMPLATE.splitlines() if line.startswith("## ")]
    assert headings == [
        "## 1. Release notes",
        "## 2. Metadata",
        "## 3. Boards",
        "## 4. Validator and checks",
        "## 5. Tests",
        "## 6. Local logs",
        "## 7. Not covered",
        "## 8. Retries",
    ]
    assert "Re-cut" not in rc.CARD_TEMPLATE


def test_data_block_round_trip(repo: FakeGit) -> None:
    b = full_run(repo)
    assert b.render("--chips", "c3,s3", "--hardware", "s3") == 0
    data = rc.read_card_data(b.card)
    assert data["schema"] == 1 and data["run"] == "20261013T120000Z-abcd" and data["commit"] == HEAD
    assert data["tree"] == rc.tree_fingerprint(repo.root, data["exclude"])
    assert data["images"]["s3"] == {"sketch_size": 1234567, "static_ram": 45678, "config": {}}
    assert ["tests/board/test_fw.py::test_status", "board", "s3", "PASS (hardware)"] in data["tests"]
    assert ["tests/board/test_fw.py::test_status", "board", "c3", "SKIP (compiled, not run)"] in data["tests"]
    assert set(data["logs"]) == {f.name for f in b.logs.iterdir()}
    assert "-->" not in json.dumps(data)


# --------------------------------------------------------------------------- JUnit and core host parsing


def by_id(rows: list[rc.CardRow]) -> dict[str, rc.CardRow]:
    return {r.id: r for r in rows}


def test_junit_unit_pass_gated_skip_xfail() -> None:
    rows = by_id(rc.parse_junit(CARD_FIXTURES / "unit.xml", "3-unit"))
    assert rows["tests/unit/test_logic.py::test_logic"].result == rc.PASS
    assert rows["tests/unit/test_logic.py::test_logic"].detail == "0.00 s"
    assert rows["tests/unit/test_logic.py::test_logic"].chip == "-"  # unit rows carry no chip
    gated = rows["tests/unit/test_logic.py::test_golden_tree"]
    assert (gated.result, gated.detail) == (rc.SKIP, "XEWE_LED_OS_REPO not set")
    xfail = rows["tests/unit/test_logic.py::test_known_gap"]
    assert (xfail.result, xfail.detail) == (rc.XFAIL, "hex parser rejects #RGB (F9)")
    assert "modules/wifi/tests/unit/test_wifi.py::test_module_id" in rows


def test_junit_hardware_and_compile_only_pass() -> None:
    rows = by_id(rc.parse_junit(CARD_FIXTURES / "board-s3.xml", "7-board-s3", "s3"))
    hw = rows["tests/board/test_fw.py::test_status"]
    assert (hw.result, hw.kind, hw.chip) == (rc.PASS_HARDWARE, "board", "s3")
    assert hw.props["xewe_board"] == "s3 sn=A0:F2:62:F1:5E:DC port=/dev/ttyACM0"
    assert rows["tests/board/test_fw.py::test_compiles"].result == rc.PASS_COMPILE
    gated = rows["tests/board/test_ha.py::test_mqtt_round_trip"]
    assert (gated.result, gated.detail) == (rc.SKIP, "needs an MQTT broker (XEWE_TEST_HA_MQTT_HOST)")


def test_junit_compiled_not_run() -> None:
    rows = by_id(rc.parse_junit(CARD_FIXTURES / "board-c3.xml", "8-board-c3", "c3"))
    row = rows["tests/board/test_fw.py::test_status"]
    assert row.result == rc.SKIP_NOT_RUN
    assert row.detail == "no board attached (board access disabled (--no-board / XEWE_NO_BOARD))"
    assert rows["tests/board/test_fw.py::test_compiles"].result == rc.PASS_COMPILE


def test_junit_fail_error_and_strict_xpass() -> None:
    rows = by_id(rc.parse_junit(CARD_FIXTURES / "fail.xml"))
    fail = rows["tests/unit/test_logic.py::test_parse"]
    assert (fail.result, fail.detail) == (rc.FAIL, "AssertionError: assert 3 == 4")
    error = rows["tests/board/test_fw.py::test_status"]
    assert error.result == rc.ERROR
    assert error.detail == 'failed on setup with "Failed: build failed for s3; see build/builds/s3/out/compile.log"'
    assert rows["tests/unit/test_logic.py::test_strict_gap"].result == rc.FAIL


def test_junit_without_xewe_properties(tmp_path: Path) -> None:
    xml = tmp_path / "plain.xml"
    xml.write_text(
        '<testsuites><testsuite name="pytest"><testcase classname="tests.build.test_x.TestY" name="test_z" '
        'file="tests/build/test_x.py" line="3" time="0.5" /></testsuite></testsuites>'
    )
    (row,) = rc.parse_junit(xml)
    assert (row.id, row.kind, row.chip, row.result) == ("tests/build/test_x.py::TestY::test_z", "unit", "-", rc.PASS)


def test_junit_not_xml(tmp_path: Path) -> None:
    bad = tmp_path / "bad.xml"
    bad.write_text("not xml")
    with pytest.raises(rc.XeWeError, match="not a JUnit file"):
        rc.parse_junit(bad)


def test_core_host_ok_fail_lines() -> None:
    rows = rc.parse_core_host((CARD_FIXTURES / "core-host.log").read_text(), "3-unit")
    assert [(r.id, r.result) for r in rows] == [
        ("tests/unit/run.sh::cli_parses_words[c++17]", rc.PASS),
        ("tests/unit/run.sh::nvs_round_trip[c++17]", rc.PASS),
        ("tests/unit/run.sh::cli_parses_words[gnu++2b]", rc.PASS),
        ("tests/unit/run.sh::nvs_round_trip[gnu++2b]", rc.FAIL),
    ]
    assert rows[3].detail == "failed checks: see 3-unit.log"
    assert all(r.kind == "unit" and r.chip == "-" for r in rows)


def test_core_host_run_is_refused_on_a_fail(repo: FakeGit, capsys: pytest.CaptureFixture[str]) -> None:
    b = Bench(repo.root)
    assert b.init() == 0
    b.output("setup", "ok\n")
    assert b.record("setup", "setup") == 0
    b.output("unit", (CARD_FIXTURES / "core-host.log").read_text(), exit_once=1)
    assert b.record("unit", "unit", "--format", "core-host") == 1
    assert b.render() == 1
    err = capsys.readouterr().err
    assert "refused: step 2-unit exited 1" in err
    assert "refused: FAIL tests/unit/run.sh::nvs_round_trip[gnu++2b] [-]" in err
    assert "| `tests/unit/run.sh::cli_parses_words[c++17]` | unit | - | PASS |" in b.draft.read_text()


def test_parse_sizes() -> None:
    text = (
        "Sketch uses 1123456 bytes (35%) of program storage space. Maximum is 3145728 bytes.\n"
        "Global variables use 45678 bytes (13%) of dynamic memory, leaving 1 bytes for local variables. "
        "Maximum is 327680 bytes.\n"
    )
    assert rc.parse_sizes(text) == {
        "sketch_size": 1123456,
        "sketch_size_percent": 35,
        "sketch_max": 3145728,
        "static_ram": 45678,
        "static_ram_max": 327680,
    }
    assert rc.parse_sizes("nothing") == {}


# --------------------------------------------------------------------------- redaction


def test_redactor_secret_keys_and_home() -> None:
    r = rc.Redactor(
        {
            "XEWE_WIFI_PSW": "pa55word!",
            "XEWE_WIFI_SSID": "HomeNet",
            "GH_TOKEN": "ghp_abcdef",
            "API_KEY": "k3y-value",
            "XEWE_MQTT_PSW": "mqttpsw1",
            "XEWE_CORE_DEVICE_NAME": "Kitchen Lights",  # not a secret key
            "SHORT_PASS": "abc",  # too short to redact safely
            "FLAG_KEY": "true",  # a common word, left alone
        },
        "/home/me",
    )
    line = "join HomeNet with pa55word! ghp_abcdef k3y-value mqttpsw1 from /home/me/x for Kitchen Lights abc true"
    assert r(line) == (
        "join «redacted:XEWE_WIFI_SSID» with «redacted:XEWE_WIFI_PSW» «redacted:GH_TOKEN» «redacted:API_KEY» "
        "«redacted:XEWE_MQTT_PSW» from ~/x for Kitchen Lights abc true"
    )


def test_redactor_longest_value_first() -> None:
    r = rc.Redactor({"A_PASS": "secret", "B_PASS": "secret-longer"})
    assert r("secret-longer secret") == "«redacted:B_PASS» «redacted:A_PASS»"


def test_redactor_reads_the_dotenv_file_and_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "xewe.toml").write_text("")
    (tmp_path / dotenv.FILENAME).write_text("XEWE_WIFI_PSW=from-the-file\nXEWE_CORE_DEVICE_NAME=bench\n")
    monkeypatch.setenv("MY_SECRET", "from-the-env")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    r = rc.Redactor.from_environment(tmp_path)
    assert r(f"from-the-file from-the-env bench {tmp_path}/home/a") == (
        "«redacted:XEWE_WIFI_PSW» «redacted:MY_SECRET» bench ~/a"
    )


def test_record_stores_no_raw_secret(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "demo"
    root.mkdir()
    monkeypatch.setattr(rc, "_git", FakeGit(root))
    monkeypatch.chdir(root)
    (root / dotenv.FILENAME).write_text("XEWE_WIFI_PSW=sup3r-secret\n")
    monkeypatch.setenv("SERVICE_TOKEN", "tok-123456")
    logs = root / "releases" / "3.1.0" / "logs"
    assert main(["release-card", "init", "--dir", str(logs), "--repo", "x/demo", "--version", "3.1.0"]) == 0
    code = "print('sup3r-secret tok-123456'); import sys; sys.exit(3)"
    assert (
        main(["release-card", "record", "--dir", str(logs), "--kind", "check", "--", sys.executable, "-c", code]) == 1
    )
    assert json.loads((logs / rc.RUN_FILE).read_text())["steps"][0]["exit"] == 3
    text = (logs / "1-check.log").read_text()
    assert text == "«redacted:XEWE_WIFI_PSW» «redacted:SERVICE_TOKEN»\n"
    assert "sup3r-secret" not in (logs / rc.RUN_FILE).read_text()  # the argv is redacted too
    assert "tok-123456" not in (logs / rc.RUN_FILE).read_text()


# --------------------------------------------------------------------------- fingerprint


def test_fingerprint_is_stable(repo: FakeGit) -> None:
    first = rc.tree_fingerprint(repo.root, rc.DEFAULT_EXCLUDES)
    assert first.startswith("sha256:") and len(first) == 7 + 64
    assert rc.tree_fingerprint(repo.root, rc.DEFAULT_EXCLUDES) == first
    (repo.root / "releases" / "3.1.0" / "release_card.md").write_text("card")  # the release folder is left out
    (repo.root / "build").mkdir()
    (repo.root / "build" / "junk").write_text("ignored")  # not tracked
    assert rc.tree_fingerprint(repo.root, rc.DEFAULT_EXCLUDES) == first


@pytest.mark.parametrize(
    "change",
    [
        lambda root: (root / "src" / "app.cpp").write_text("int main() { return 1; }\n"),
        lambda root: (root / "src" / "app.cpp").chmod(0o755),
        lambda root: (root / "src" / "new.h").write_text("#pragma once\n"),
        lambda root: (root / "xewe.toml").rename(root / "xewe2.toml"),
    ],
    ids=["content", "mode", "added", "renamed"],
)
def test_fingerprint_changes(repo: FakeGit, change: Callable[[Path], Any]) -> None:
    before = rc.tree_fingerprint(repo.root, rc.DEFAULT_EXCLUDES)
    change(repo.root)
    assert rc.tree_fingerprint(repo.root, rc.DEFAULT_EXCLUDES) != before


def test_fingerprint_counts_a_deleted_tracked_file(repo: FakeGit, monkeypatch: pytest.MonkeyPatch) -> None:
    before = rc.tree_fingerprint(repo.root, rc.DEFAULT_EXCLUDES)
    tracked = repo.tracked()
    (repo.root / "src" / "app.cpp").unlink()
    monkeypatch.setattr(repo, "tracked", lambda: tracked)  # still in the index
    assert rc.tree_fingerprint(repo.root, rc.DEFAULT_EXCLUDES) != before


def test_excluded_patterns() -> None:
    assert rc.excluded("releases/3.1.0/notes.txt", ["releases/"])
    assert not rc.excluded("doc/releases.md", ["releases/"])
    assert rc.excluded("modules/led/releases/3.1.0/release_card.md", ["modules/*/releases"])
    assert not rc.excluded("modules/led/src/Led.h", ["modules/*/releases"])


@pytest.mark.skipif(not (REPO_ROOT / ".git").exists() or shutil.which("git") is None, reason="not a git checkout")
def test_fingerprint_of_this_checkout_with_real_git() -> None:
    """Read-only git (ls-files, hash-object without -w) on the tools checkout."""
    first = rc.tree_fingerprint(REPO_ROOT, rc.DEFAULT_EXCLUDES)
    assert first == rc.tree_fingerprint(REPO_ROOT, rc.DEFAULT_EXCLUDES)
    status = subprocess.run(["git", "-C", str(REPO_ROOT), "status", "--porcelain"], capture_output=True, text=True)
    assert status.returncode == 0


# --------------------------------------------------------------------------- init and record


def test_init_writes_run_json(repo: FakeGit) -> None:
    b = Bench(repo.root)
    (b.logs).mkdir(parents=True)
    (b.logs / rc.RUN_FILE).write_text(json.dumps({"repo": "xewe-labs/demo", "version": "3.1.0", "steps": []}))
    (b.logs / "3-unit.log").write_text("from an earlier run")
    (b.logs / rc.DRAFT_NAME).write_text("old draft")
    assert b.init("--exclude", "modules/*/releases") == 0
    run = b.run_json()
    assert run["run_id"] == "20261013T120000Z-abcd" and run["base_commit"] == HEAD and run["tag"] == "3.1.0"
    assert run["exclude"] == ["releases/", "modules/*/releases", "releases/3.1.0/logs/"]
    assert run["tree"] == rc.tree_fingerprint(repo.root, run["exclude"])
    assert run["repo"] == "xewe-labs/demo" and run["steps"] == []
    assert sorted(f.name for f in b.logs.iterdir()) == [rc.RUN_FILE]  # a new run starts empty


def test_record_files_and_firmware(repo: FakeGit) -> None:
    b = full_run(repo)
    run = b.run_json()
    names = [s["name"] for s in run["steps"]]
    assert names == [
        "1-setup",
        "2-validate",
        "3-unit",
        "4-build-c3",
        "5-build-s3",
        "6-flash-s3",
        "7-flash-s3",
        "8-board-s3",
        "9-board-c3",
        "10-check-format",
        "11-check-brand-lint",
    ]
    flash = run["steps"][6]
    assert flash["attempt"] == 2 and flash["exit"] == 0
    assert flash["firmware"] == "build/builds/s3/out/3.1.0-s3-demo.bin"
    assert flash["fw_sha256"] == hashlib.sha256((repo.root / flash["firmware"]).read_bytes()).hexdigest()
    assert flash["meta"] == "7-flash-s3.meta.json"
    assert run["steps"][0]["build_config"] == "1-setup.build_config.toml"
    assert run["steps"][3]["compile_log"] == "4-build-c3.compile.log"
    assert run["steps"][2]["junit"] == "3-unit.xml"
    for step in run["steps"]:
        for fname, digest in step["files"].items():
            assert rc.sha256_file(b.logs / fname) == digest


def test_record_needs_a_command_and_a_chip(repo: FakeGit) -> None:
    b = Bench(repo.root)
    assert b.init() == 0
    assert main(["release-card", "record", "--dir", str(b.logs), "--kind", "unit"]) == 2
    assert main(["release-card", "record", "--dir", str(b.logs), "--kind", "build", "--", "true"]) == 2
    assert main(["release-card", "render", "--dir", str(b.logs), "--notes", "x", "--out", "y", "--", "z"]) == 2


def test_record_without_init(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)  # not a git work tree: the missing run.json is reported first
    assert main(["release-card", "record", "--dir", str(tmp_path / "logs"), "--kind", "check", "--", "true"]) == 2


def test_record_command_not_found(repo: FakeGit) -> None:
    b = Bench(repo.root)
    assert b.init() == 0
    assert main(["release-card", "record", "--dir", str(b.logs), "--kind", "check", "--", "no-such-command-xyz"]) == 3
    assert "cannot run no-such-command-xyz" in (b.logs / "1-check.log").read_text()
    assert b.run_json()["steps"][0]["exit"] == rc.CANNOT_START  # 127 in run.json, 3 for the caller (X-07)


# --------------------------------------------------------------------------- refusal rules


def refused(capsys: pytest.CaptureFixture[str]) -> str:
    return capsys.readouterr().err


def test_refusal_writes_a_draft_and_no_card(repo: FakeGit, capsys: pytest.CaptureFixture[str]) -> None:
    b = full_run(repo)
    assert b.render("--chips", "c3,s3", "--hardware", "c3,s3") == 1
    err = refused(capsys)
    assert "refused: missing step: flash for c3 (--hardware)" in err
    assert "refused: c3: no PASS (hardware) row (--hardware c3)" in err
    assert not b.card.exists()
    draft = b.draft.read_text()
    assert draft.startswith("> **DRAFT: refused, not a release card.**")
    assert "> - c3: no PASS (hardware) row (--hardware c3)" in draft
    assert b.render("--chips", "c3,s3", "--hardware", "s3") == 0  # fixed: the card is written, the draft goes
    assert b.card.exists() and not b.draft.exists()


def test_refusal_stale_run_id(repo: FakeGit, capsys: pytest.CaptureFixture[str]) -> None:
    b = full_run(repo)
    b.edit_run(lambda run: run["steps"][2].update(run_id="20261001T000000Z-0000"))
    assert b.render("--chips", "c3,s3", "--hardware", "s3") == 1
    assert "refused: step 3-unit is from run 20261001T000000Z-0000, not 20261013T120000Z-abcd" in refused(capsys)


def test_refusal_step_before_run(repo: FakeGit, capsys: pytest.CaptureFixture[str]) -> None:
    b = full_run(repo)
    b.edit_run(lambda run: run["steps"][4].update(start="2026-10-12T09:00:00Z"))
    assert b.render("--chips", "c3,s3", "--hardware", "s3") == 1
    assert "refused: step 5-build-s3 started before run.json (2026-10-12T09:00:00Z < 2026-10-13T12:00:00Z)" in refused(
        capsys
    )


def test_refusal_log_changed_after_record(repo: FakeGit, capsys: pytest.CaptureFixture[str]) -> None:
    b = full_run(repo)
    (b.logs / "3-unit.xml").write_text((b.logs / "3-unit.xml").read_text().replace("test_logic", "test_other"))
    assert b.render("--chips", "c3,s3", "--hardware", "s3") == 1
    assert "refused: 3-unit.xml of step 3-unit changed after it was recorded" in refused(capsys)


def test_refusal_tree_changed_since_init(repo: FakeGit, capsys: pytest.CaptureFixture[str]) -> None:
    b = full_run(repo)
    (repo.root / "src" / "app.cpp").write_text("changed mid-run\n")
    assert b.render("--chips", "c3,s3", "--hardware", "s3") == 1
    assert "refused: the tree changed since init" in refused(capsys)


def test_refusal_missing_kind(repo: FakeGit, capsys: pytest.CaptureFixture[str]) -> None:
    b = Bench(repo.root)
    assert b.init() == 0
    b.output("unit", "ok\n", (CARD_FIXTURES / "unit.xml").read_text())
    assert b.record("unit", "unit", junit=True) == 0
    assert b.render("--chips", "c3") == 1
    err = refused(capsys)
    for missing in ("setup", "validate", "build for c3", "board for c3"):
        assert f"refused: missing step: {missing}" in err
    assert b.render("--chips", "c3", "--require", "unit") == 0  # the repo names what it needs
    assert b.render() == 1  # no firmware: setup and unit
    assert "refused: missing step: setup" in refused(capsys)


def test_refusal_failed_step_and_tests(repo: FakeGit, capsys: pytest.CaptureFixture[str]) -> None:
    b = Bench(repo.root)
    assert b.init() == 0
    b.output("setup", "ok\n")
    assert b.record("setup", "setup") == 0
    b.output("unit", "1 failed\n", (CARD_FIXTURES / "fail.xml").read_text(), exit_once=1)
    assert b.record("unit", "unit", junit=True) == 1
    assert b.render() == 1
    err = refused(capsys)
    assert "refused: step 2-unit exited 1" in err
    assert "refused: FAIL tests/unit/test_logic.py::test_parse [-]: AssertionError: assert 3 == 4" in err
    assert 'refused: ERROR tests/board/test_fw.py::test_status [s3]: failed on setup with "Failed: build failed' in err


def test_refusal_junit_not_written(repo: FakeGit, capsys: pytest.CaptureFixture[str]) -> None:
    b = Bench(repo.root)
    assert b.init() == 0
    b.output("setup", "ok\n")
    assert b.record("setup", "setup") == 0
    (repo.root / "build" / "junit").mkdir(parents=True)
    (repo.root / "build" / "junit" / "unit.xml").write_text((CARD_FIXTURES / "unit.xml").read_text())  # stale
    assert b.record("unit", "unit", junit=True) == 0  # the command wrote no JUnit file
    assert not (repo.root / "build" / "junit" / "unit.xml").exists()
    assert b.render() == 1
    assert "refused: step 2-unit: JUnit file build/junit/unit.xml was not written" in refused(capsys)


def test_refusal_fw_sha_mismatch(repo: FakeGit, capsys: pytest.CaptureFixture[str]) -> None:
    b = full_run(repo, s3_sha_in_tests="ab" * 32)
    assert b.render("--chips", "c3,s3", "--hardware", "s3") == 1
    assert "refused: s3: tested firmware abababababab is not the flashed" in refused(capsys)


def test_refusal_banner_mismatch(repo: FakeGit, capsys: pytest.CaptureFixture[str]) -> None:
    b = full_run(repo, s3_match="false")
    assert b.render("--chips", "c3,s3", "--hardware", "s3") == 1
    err = refused(capsys)
    assert "refused: s3: boot banner Build Timestamp does not match meta timestamp (fw_match false)" in err
    assert "| s3 | A0:F2:62:F1:5E:DC | /dev/ttyACM0 |" in b.draft.read_text()


def test_render_usage_errors(repo: FakeGit) -> None:
    b = full_run(repo)
    assert b.render("--chips", "c3", "--hardware", "s3") == 2
    assert b.render("--chips", "esp32") == 2
    assert b.render("--require", "lint") == 2
    assert main(["release-card", "render", "--dir", str(b.logs), "--notes", "nope.txt", "--out", "x.md"]) == 2


# --------------------------------------------------------------------------- --resume


def test_resume_reruns_only_steps_that_are_not_green(repo: FakeGit, capsys: pytest.CaptureFixture[str]) -> None:
    b = Bench(repo.root)
    assert b.init() == 0
    b.output("setup", "ok\n")
    assert b.record("setup", "setup") == 0
    b.output("unit", "flaky\n", (CARD_FIXTURES / "unit.xml").read_text(), exit_once=1)
    assert b.record("unit", "unit", junit=True) == 1
    assert b.runs() == ["setup", "unit"]

    assert b.init("--resume") == 0  # same commit and tree: the run is kept
    assert "resuming run 20261013T120000Z-abcd (2 steps recorded)" in capsys.readouterr().out
    assert b.record("setup", "setup", "--resume") == 0
    assert "1-setup is already green in run 20261013T120000Z-abcd; not run again" in capsys.readouterr().out
    assert b.record("unit", "unit", "--resume", junit=True) == 0
    assert b.runs() == ["setup", "unit", "unit"]  # only the red step ran again
    assert b.render() == 0
    card = b.card.read_text()
    assert "| 2-unit | 1 | 1 | 1.5 s | 2-unit.log |" in card  # the earlier attempt
    assert "| 3-unit | `build/step unit` | - | - | 2 | 0 | 0 | 0 | 1 | 1 | 0 | 0 |" in card


def test_resume_refused_after_a_change_starts_a_new_run(repo: FakeGit, monkeypatch: pytest.MonkeyPatch) -> None:
    b = Bench(repo.root)
    assert b.init() == 0
    b.output("setup", "ok\n")
    assert b.record("setup", "setup") == 0
    (repo.root / "src" / "app.cpp").write_text("edited\n")
    monkeypatch.setattr(rc, "_run_id", lambda: "20261013T130000Z-beef")
    assert b.init("--resume") == 0
    run = b.run_json()
    assert run["run_id"] == "20261013T130000Z-beef" and run["steps"] == []
    repo.head = "f" * 40
    assert b.init("--resume") == 0  # another base commit: new run too
    assert b.run_json()["base_commit"] == "f" * 40


def test_resume_without_a_run_starts_one(repo: FakeGit) -> None:
    b = Bench(repo.root)
    assert b.init("--resume") == 0
    assert b.run_json()["steps"] == []


# --------------------------------------------------------------------------- verify


def test_verify_local(repo: FakeGit, capsys: pytest.CaptureFixture[str]) -> None:
    b = full_run(repo)
    assert b.render("--chips", "c3,s3", "--hardware", "s3") == 0
    card = str(b.card)
    assert main(["release-card", "verify", "--card", card]) == 0
    assert "matches the tree" in capsys.readouterr().out

    repo.status = " M src/app.cpp\0"
    assert main(["release-card", "verify", "--card", card]) == 1
    assert "the tree is not clean: src/app.cpp" in capsys.readouterr().err
    repo.status = "?? releases/3.1.0/release_card.md\0"  # the release folder may be uncommitted
    assert main(["release-card", "verify", "--card", card]) == 0

    (b.logs / "3-unit.log").write_text("other\n")
    assert main(["release-card", "verify", "--card", card]) == 1
    assert "3-unit.log; re-run `--prepare 3.1.0` here" in capsys.readouterr().err
    shutil.rmtree(b.logs)
    assert main(["release-card", "verify", "--card", card]) == 1


def test_verify_local_refuses_testcard_and_other_tree(repo: FakeGit, capsys: pytest.CaptureFixture[str]) -> None:
    b = full_run(repo)
    assert b.render("--chips", "c3,s3", "--hardware", "s3") == 0
    (b.card.parent / "testcard.md").write_text("old")
    assert main(["release-card", "verify", "--card", str(b.card)]) == 1
    assert "testcard.md exists" in capsys.readouterr().err
    (b.card.parent / "testcard.md").unlink()
    (repo.root / "src" / "app.cpp").write_text("x\n")
    assert main(["release-card", "verify", "--card", str(b.card)]) == 1
    assert "is not the card's" in capsys.readouterr().err


def test_verify_card_without_data_block(repo: FakeGit, tmp_path: Path) -> None:
    card = tmp_path / "card.md"
    card.write_text("# hand-written\n")
    assert main(["release-card", "verify", "--card", str(card)]) == 2


# --------------------------------------------------------------------------- CI comparison


def card_data(**over: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "schema": 1,
        "tree": "sha256:aa",
        "run": "r",
        "tests": [
            ["tests/unit/test_logic.py::test_logic", "unit", "-", "PASS"],
            ["tests/board/test_fw.py::test_status", "board", "s3", "PASS (hardware)"],
            ["tests/board/test_fw.py::test_golden", "board", "s3", "PASS (hardware)"],
            ["tests/board/test_fw.py::test_wifi", "board", "s3", "PASS (hardware)"],
        ],
        "images": {"s3": {"sketch_size": 1000000, "static_ram": 40000, "config": {}}},
    }
    data.update(over)
    return data


def ci(test_id: str, result: str, detail: str = "", kind: str = "board") -> rc.CardRow:
    return rc.CardRow(test_id, kind, "s3", result, detail)


CI_OK = [
    ci("tests/unit/test_logic.py::test_logic", rc.PASS, kind="unit"),
    ci("tests/board/test_fw.py::test_status", rc.SKIP_NOT_RUN, "no board attached"),
    ci("tests/board/test_fw.py::test_golden", rc.SKIP, "XEWE_LED_OS_REPO not set"),
    ci("tests/board/test_fw.py::test_wifi", rc.PASS_COMPILE),
]


def meta(size: int, ram: int = 40000, chip: str = "s3", config: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"type": chip, "sketch_size": size, "static_ram": ram, "config": config or {}}


def test_ci_allows_not_run_and_env_gates() -> None:
    cmp = rc.compare(card_data(), "sha256:aa", CI_OK, [meta(1000000)])
    assert cmp.blocks == [] and cmp.warnings == []
    assert cmp.allowed == [
        "tests/board/test_fw.py::test_status: PASS on the card, CI skip (compiled, not run: no board attached)",
        "tests/board/test_fw.py::test_golden: PASS on the card, CI skip (XEWE_LED_OS_REPO not set)",
    ]


def test_ci_blocks_tree() -> None:
    cmp = rc.compare(card_data(), "sha256:bb", CI_OK, None)
    assert cmp.blocks == ["tree fingerprint sha256:bb is not the card's sha256:aa"]


def test_ci_blocks_other_test_ids() -> None:
    rows = [*CI_OK[:3], ci("tests/board/test_fw.py::test_new", rc.PASS)]
    (block,) = rc.compare(card_data(), "sha256:aa", rows, None).blocks
    assert block == (
        "collected tests differ: 1 on the card only (tests/board/test_fw.py::test_wifi); "
        "1 in CI only (tests/board/test_fw.py::test_new)"
    )


def test_ci_blocks_fail_and_error() -> None:
    rows = [*CI_OK[:3], ci("tests/board/test_fw.py::test_wifi", rc.FAIL, "boom")]
    assert rc.compare(card_data(), "sha256:aa", rows, None).blocks == [
        "CI FAIL tests/board/test_fw.py::test_wifi (PASS on the card): boom"
    ]
    data = card_data(tests=[*card_data()["tests"][:3], ["tests/board/test_fw.py::test_wifi", "board", "s3", "SKIP"]])
    rows = [*CI_OK[:3], ci("tests/board/test_fw.py::test_wifi", rc.ERROR, "setup")]
    assert rc.compare(data, "sha256:aa", rows, None).blocks == ["CI ERROR tests/board/test_fw.py::test_wifi: setup"]


def test_ci_warns_on_other_skips() -> None:
    rows = [*CI_OK[:3], ci("tests/board/test_fw.py::test_wifi", rc.SKIP, "flaky on the runner")]
    cmp = rc.compare(card_data(), "sha256:aa", rows, None)
    assert cmp.blocks == []
    assert cmp.warnings == [
        "tests/board/test_fw.py::test_wifi: PASS on the card, CI skip for another reason (flaky on the runner)"
    ]


@pytest.mark.parametrize(
    ("ci_size", "ci_ram", "blocks", "warnings"),
    [
        (1000000, 40000, [], []),
        (1003000, 40000, [], ["s3 sketch_size: card 1000000, CI 1003000 (+0.30 %)"]),
        (
            999000,
            40100,
            [],
            ["s3 sketch_size: card 1000000, CI 999000 (-0.10 %)", "s3 static_ram: card 40000, CI 40100 (+0.25 %)"],
        ),
        (1010000, 40000, ["s3 sketch_size: card 1000000, CI 1010000 (+1.00 %), over the 0.5 % tolerance"], []),
    ],
    ids=["equal", "warn", "warn-both", "block"],
)
def test_ci_image_sizes(ci_size: int, ci_ram: int, blocks: list[str], warnings: list[str]) -> None:
    cmp = rc.compare(card_data(), "sha256:aa", CI_OK, [meta(5, chip="c3"), meta(ci_size, ci_ram)])
    assert cmp.blocks == blocks and cmp.warnings == warnings


def test_ci_image_with_other_defines_is_not_compared() -> None:
    cmp = rc.compare(card_data(), "sha256:aa", CI_OK, [meta(2000000, config={"LED_PIN": 48})])
    assert cmp.blocks == [] and "s3: no CI image with the card's defines to compare" in cmp.allowed


def test_verify_ci_end_to_end(repo: FakeGit, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    b = full_run(repo)
    assert b.render("--chips", "c3,s3", "--hardware", "s3") == 0
    junit, metas = tmp_path / "ci-junit", tmp_path / "ci-meta"
    for d in (junit, metas):
        d.mkdir()
    shutil.copy(CARD_FIXTURES / "unit.xml", junit / "unit.xml")
    shutil.copy(CARD_FIXTURES / "board-c3.xml", junit / "board-c3.xml")
    no_board_s3 = (CARD_FIXTURES / "board-c3.xml").read_text().replace('value="c3"', 'value="s3"')
    (junit / "board-s3.xml").write_text(no_board_s3)
    (metas / "s3_meta.json").write_text(json.dumps(meta(1234567 + 1000, 45678, "s3")))
    args = ["release-card", "verify", "--ci", "--card", str(b.card), "--junit-dir", str(junit)]
    assert main([*args, "--meta-dir", str(metas)]) == 0
    captured = capsys.readouterr()
    assert "allowed: tests/board/test_fw.py::test_status: PASS on the card, CI skip (compiled, not run" in captured.out
    assert "s3 sketch_size: card 1234567, CI 1235567 (+0.08 %)" in captured.err
    assert main([*args, "--meta-dir", str(metas), "--size-tolerance", "0.05"]) == 1
    assert "block: s3 sketch_size" in capsys.readouterr().err
    (junit / "unit.xml").write_text((CARD_FIXTURES / "fail.xml").read_text())
    assert main(args) == 1
    assert "block: collected tests differ" in capsys.readouterr().err
    assert main(["release-card", "verify", "--ci", "--card", str(b.card)]) == 2  # --junit-dir is required


def test_verify_ci_card_from_the_tag_message(repo: FakeGit, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """xewe-release.yml: the card part of the tag message, written outside the tree, is verified;
    nothing is read from releases/<v>/ (a testcard.md there does not count for it)."""
    from test_release import tag_step

    b = full_run(repo)
    assert b.render("--chips", "c3,s3", "--hardware", "s3") == 0
    card_text = b.card.read_text()
    junit = tmp_path / "ci-junit"
    junit.mkdir()
    shutil.copy(CARD_FIXTURES / "unit.xml", junit / "unit.xml")
    shutil.copy(CARD_FIXTURES / "board-c3.xml", junit / "board-c3.xml")
    no_board_s3 = (CARD_FIXTURES / "board-c3.xml").read_text().replace('value="c3"', 'value="s3"')
    (junit / "board-s3.xml").write_text(no_board_s3)
    proc, out = tag_step(tmp_path / "plan", "Faster boot.\n\n" + card_text)
    assert proc.returncode == 0, proc.stderr
    tag_card = out / "release_card.md"
    assert tag_card.read_text() == card_text and (out / "notes.txt").read_text() == "Faster boot.\n"
    (b.card.parent / "testcard.md").write_text("old")  # in the tree: blocks a tree card only
    args = ["release-card", "verify", "--ci", "--junit-dir", str(junit), "--card"]
    assert main([*args, str(tag_card)]) == 0, capsys.readouterr().err
    assert main([*args, str(b.card)]) == 1
    assert "testcard.md exists" in capsys.readouterr().err
    proc, out = tag_step(tmp_path / "edited", "Faster boot.\n\n" + card_text.replace("demo", "dem0", 1))
    assert main([*args, str(out / "release_card.md")]) == 1
    assert "was edited after render" in capsys.readouterr().err


def test_tests_filter_makes_a_module_card(repo: FakeGit, capsys: pytest.CaptureFixture[str]) -> None:
    b = full_run(repo)
    assert b.render("--chips", "c3,s3", "--hardware", "s3", "--tests", "modules/led/") == 0
    data = rc.read_card_data(b.card)
    assert {t[0] for t in data["tests"]} == {"modules/led/tests/board/test_led.py::test_render_priority"}
    assert "| Result | 0 unit passed; c3: 1 compiled, not run; s3: 1 passed on hardware" in b.card.read_text()
    assert b.render("--chips", "c3,s3", "--hardware", "s3", "--tests", "modules/wifi/") == 1  # no hardware row there
    assert "refused: s3: no PASS (hardware) row (--hardware s3)" in capsys.readouterr().err


def test_refusal_when_a_junit_copy_is_deleted(repo: FakeGit, capsys: pytest.CaptureFixture[str]) -> None:
    b = full_run(repo)
    (b.logs / "8-board-s3.xml").unlink()
    assert b.render("--chips", "c3,s3", "--hardware", "s3") == 1
    err = refused(capsys)
    assert "refused: 8-board-s3.xml of step 8-board-s3 is missing" in err
    assert "refused: s3: no PASS (hardware) row (--hardware s3)" in err
    assert b.draft.is_file()


# --------------------------------------------------------------------------- integration points (CI jobs)


def test_fingerprint_action(repo: FakeGit, capsys: pytest.CaptureFixture[str]) -> None:
    """`release-card fingerprint` prints what `init` records; `--card --check` is the card jobs' test."""
    b = full_run(repo)
    assert b.render("--chips", "c3,s3", "--hardware", "s3") == 0
    data = rc.read_card_data(b.card)
    capsys.readouterr()
    assert main(["release-card", "fingerprint"]) == 0
    assert capsys.readouterr().out.strip() == rc.tree_fingerprint(repo.root, rc.DEFAULT_EXCLUDES) == data["tree"]
    assert main(["release-card", "fingerprint", "--card", str(b.card)]) == 0
    assert capsys.readouterr().out.strip() == data["tree"]
    assert main(["release-card", "fingerprint", "--card", str(b.card), "--check"]) == 0
    assert "matches" in capsys.readouterr().out
    (repo.root / "src" / "app.cpp").write_text("int main() { return 2; }\n")
    assert main(["release-card", "fingerprint", "--card", str(b.card), "--check"]) == 1
    assert "is not the card's" in capsys.readouterr().out
    assert main(["release-card", "fingerprint", "--check"]) == 2  # --check needs --card


def test_fingerprint_action_uses_the_cards_excludes(repo: FakeGit, capsys: pytest.CaptureFixture[str]) -> None:
    (repo.root / "modules" / "led" / "releases" / "3.1.0").mkdir(parents=True)
    (repo.root / "modules" / "led" / "releases" / "3.1.0" / "release_card.md").write_text("x")
    excludes = ["releases/", "modules/*/releases"]
    card = repo.root / "card.md"
    card.write_text(
        rc.data_comment({"schema": 1, "tree": rc.tree_fingerprint(repo.root, excludes), "exclude": excludes})
    )
    assert main(["release-card", "fingerprint", "--card", str(card), "--check"]) == 1  # card.md itself is tracked
    card.unlink()
    card = repo.root / "releases" / "card.md"
    card.write_text(
        rc.data_comment({"schema": 1, "tree": rc.tree_fingerprint(repo.root, excludes), "exclude": excludes})
    )
    assert main(["release-card", "fingerprint", "--card", str(card), "--check"]) == 0
    (repo.root / "modules" / "led" / "releases" / "3.1.0" / "release_card.md").write_text("changed")
    assert main(["release-card", "fingerprint", "--card", str(card), "--check"]) == 0  # excluded by the card


UNIT_ONLY_CI = [ci("tests/unit/test_logic.py::test_logic", rc.PASS, kind="unit")]


def test_ci_unit_only_compares_the_unit_rows() -> None:
    """Core and modules CI run no board session: `--unit-only` leaves the card's board rows out."""
    assert rc.compare(card_data(), "sha256:aa", UNIT_ONLY_CI, None).blocks != []  # the full rule blocks
    cmp = rc.compare(card_data(), "sha256:aa", UNIT_ONLY_CI, None, unit_only=True)
    assert cmp.blocks == [] and cmp.allowed == ["3 board tests on the card not compared (unit-only CI)"]
    extra = [*UNIT_ONLY_CI, ci("tests/unit/test_logic.py::test_new", rc.PASS, kind="unit")]
    (block,) = rc.compare(card_data(), "sha256:aa", extra, None, unit_only=True).blocks
    assert block == "collected tests differ: 1 in CI only (tests/unit/test_logic.py::test_new)"
    assert rc.compare(card_data(), "sha256:aa", [], None, unit_only=True).blocks == [
        "collected tests differ: 1 on the card only (tests/unit/test_logic.py::test_logic)"
    ]
    failed = [ci("tests/unit/test_logic.py::test_logic", rc.FAIL, "boom", kind="unit")]
    assert rc.compare(card_data(), "sha256:aa", failed, None, unit_only=True).blocks == [
        "CI FAIL tests/unit/test_logic.py::test_logic (PASS on the card): boom"
    ]


def test_ci_module_card_compares_its_own_tests() -> None:
    """A module card (`render --tests`) keeps "only" in its data block; CI rows outside it are not compared."""
    data = card_data(tests=[["modules/led/tests/unit/test_led.py::test_a", "unit", "-", "PASS"]], only=["modules/led/"])
    rows = [
        ci("modules/led/tests/unit/test_led.py::test_a", rc.PASS, kind="unit"),
        ci("modules/wifi/tests/unit/test_wifi.py::test_b", rc.PASS, kind="unit"),
    ]
    assert rc.compare(data, "sha256:aa", rows, None, unit_only=True).blocks == []
    del data["only"]
    assert rc.compare(data, "sha256:aa", rows, None, unit_only=True).blocks != []


def test_module_card_records_its_filter(repo: FakeGit) -> None:
    b = full_run(repo)
    assert b.render("--chips", "c3,s3", "--hardware", "s3", "--tests", "modules/led/") == 0
    assert rc.read_card_data(b.card)["only"] == ["modules/led/"]
    assert b.render("--chips", "c3,s3", "--hardware", "s3") == 0
    assert "only" not in rc.read_card_data(b.card)


def test_verify_ci_unit_only_flag(repo: FakeGit, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    b = full_run(repo)
    assert b.render("--chips", "c3,s3", "--hardware", "s3") == 0
    junit = tmp_path / "ci-junit"
    junit.mkdir()
    shutil.copy(CARD_FIXTURES / "unit.xml", junit / "tests-unit.xml")
    args = ["release-card", "verify", "--ci", "--card", str(b.card), "--junit-dir", str(junit)]
    assert main(args) == 1  # unit results only: the full comparison misses the board tests
    capsys.readouterr()
    assert main([*args, "--unit-only"]) == 0
    assert "board tests on the card not compared (unit-only CI)" in capsys.readouterr().out


# --------------------------------------------------------------------------- init: what it may empty


def test_init_refuses_a_folder_that_is_not_a_run_folder(
    repo: FakeGit, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """init deletes only the files of a previous run folder of the same repo and version."""
    notes = repo.root / "releases" / "3.1.0" / "notes.txt"
    before = notes.read_text()

    def init(d: str, version: str = "3.1.0") -> int:
        return main(["release-card", "init", "--dir", d, "--repo", "xewe-labs/demo", "--version", version])

    assert init("releases/3.1.0") == 2  # the release folder: notes.txt is tracked
    assert "holds tracked files" in capsys.readouterr().err
    tracked = [p for p in repo.tracked() if not p.endswith("notes.txt")]
    monkeypatch.setattr(repo, "tracked", lambda: tracked)  # notes.txt just written by --prepare, not committed
    assert init("releases/3.1.0") == 2  # --dir releases/<v> without /logs: notes.txt is not a run file
    assert notes.read_text() == before
    assert "not a run folder (it holds notes.txt)" in capsys.readouterr().err
    assert init(".") == 2 and init(str(repo.root.parent)) == 2  # the repo root, or above it
    assert "is the repo root or above it" in capsys.readouterr().err
    assert init(str(repo.root.parent / "elsewhere")) == 2
    assert "outside the repo" in capsys.readouterr().err
    assert init("src") == 2  # a tracked folder
    assert "holds tracked files" in capsys.readouterr().err
    assert (repo.root / "src" / "app.cpp").is_file()
    logs = repo.root / "releases" / "3.1.0" / "logs"
    logs.mkdir()
    (logs / "keep").mkdir()
    assert init(str(logs)) == 2  # a subfolder is never a run file
    (logs / "keep").rmdir()
    (logs / "1-setup.log").write_text("no run.json: not a run folder\n")
    assert init(str(logs)) == 2
    (logs / rc.RUN_FILE).write_text(json.dumps({"repo": "xewe-labs/demo", "version": "3.0.0"}))
    assert init(str(logs)) == 2  # another version's run
    assert "is not a run of xewe-labs/demo 3.1.0" in capsys.readouterr().err
    assert sorted(f.name for f in logs.iterdir()) == ["1-setup.log", rc.RUN_FILE]  # nothing deleted
    assert init(str(logs), "3.0.0") == 0  # its own version: emptied, new run
    assert sorted(f.name for f in logs.iterdir()) == [rc.RUN_FILE]


def test_init_again_on_a_real_run_folder(repo: FakeGit) -> None:
    b = full_run(repo)
    assert b.render("--chips", "c3,s3", "--hardware", "s3", "--require", "unit") == 0
    assert len(list(b.logs.iterdir())) > 20
    assert b.init() == 0
    assert [f.name for f in b.logs.iterdir()] == [rc.RUN_FILE] and b.run_json()["steps"] == []


# --------------------------------------------------------------------------- the data block and card integrity


def test_notes_with_a_data_block_are_refused(repo: FakeGit, capsys: pytest.CaptureFixture[str]) -> None:
    """F12: a notes.txt that quotes a data block would plant a second one in the card."""
    b = full_run(repo)
    notes = repo.root / "releases" / "3.1.0" / "notes.txt"
    notes.write_text('demo\n\n<!-- xewe-card-data {"schema":1,"tree":"sha256:00","tests":[]} -->\n')
    assert b.render("--chips", "c3,s3", "--hardware", "s3") == 1
    assert "notes.txt contains `<!-- xewe-card-data`" in refused(capsys)
    assert not b.card.exists()


def test_data_block_is_read_from_the_last_line_only(repo: FakeGit, capsys: pytest.CaptureFixture[str]) -> None:
    """F13: a block quoted in the card body (the spec example, a forged one) is never read."""
    b = full_run(repo)
    assert b.render("--chips", "c3,s3", "--hardware", "s3") == 0
    real = rc.read_card_data(b.card)
    forged = rc.data_comment({**real, "tree": "sha256:" + "f" * 64, "tests": []})
    example = '<!-- xewe-card-data {"schema":1,"tree":…,"run":…,"tests":[[id,kind,chip,result]…]} -->'
    text = b.card.read_text().replace("## 2. Metadata\n", f"{example}\n{forged}\n\n## 2. Metadata\n", 1)
    b.card.write_text(text)
    assert rc.read_card_data(b.card) == real
    capsys.readouterr()
    assert main(["release-card", "fingerprint", "--card", str(b.card), "--check"]) == 0  # the real block
    assert "matches" in capsys.readouterr().out
    assert main(["release-card", "verify", "--card", str(b.card)]) == 1  # but the card was edited
    assert "was edited after render" in capsys.readouterr().err


@pytest.mark.parametrize(
    "last_line",
    ['<!-- xewe-card-data {"tree": oops} -->', "<!-- xewe-card-data [1, 2] -->", "notes after the block"],
    ids=["not-json", "not-an-object", "not-a-block"],
)
def test_a_bad_last_line_is_exit_2(repo: FakeGit, last_line: str, capsys: pytest.CaptureFixture[str]) -> None:
    """A card whose last line is no valid data block is a usage error (exit 2), never "another tree"
    (exit 1, which the CI card jobs skip)."""
    b = full_run(repo)
    assert b.render("--chips", "c3,s3", "--hardware", "s3") == 0
    b.card.write_text(b.card.read_text() + last_line + "\n")
    for args in (["fingerprint", "--card", str(b.card), "--check"], ["verify", "--card", str(b.card)]):
        assert main(["release-card", *args]) == 2
    assert "xewe-card-data" in capsys.readouterr().err


def test_verify_fails_on_an_edited_card(repo: FakeGit, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """F5 (the body) and F6 (the data block) edited after render: verify and verify --ci fail."""
    b = full_run(repo)
    assert b.render("--chips", "c3,s3", "--hardware", "s3") == 0
    rendered = b.card.read_text()
    junit = tmp_path / "ci-junit"
    junit.mkdir()
    for name in ("unit.xml", "board-c3.xml"):
        shutil.copy(CARD_FIXTURES / name, junit / name)
    (junit / "board-s3.xml").write_text(
        (CARD_FIXTURES / "board-c3.xml").read_text().replace('value="c3"', 'value="s3"')
    )
    ci_args = ["release-card", "verify", "--ci", "--card", str(b.card), "--junit-dir", str(junit)]
    assert main(["release-card", "verify", "--card", str(b.card)]) == 0
    assert main(ci_args) == 0
    body = rendered.replace("| Result | 2 unit passed;", "| Result | 3 unit passed;")
    data_edit = rendered.replace(
        '["tests/board/test_fw.py::test_status","board","c3","SKIP (compiled, not run)"]',
        '["tests/board/test_fw.py::test_status","board","c3","PASS (hardware)"]',
    )
    for edited in (body, data_edit):
        assert edited != rendered
        b.card.write_text(edited)
        capsys.readouterr()
        assert main(["release-card", "verify", "--card", str(b.card)]) == 1
        assert "was edited after render (card_sha256 differs)" in capsys.readouterr().err
        assert main(ci_args) == 1
        assert "block: " in capsys.readouterr().err
    b.card.write_text(rendered)
    assert main(["release-card", "verify", "--card", str(b.card)]) == 0


def test_card_digest_covers_body_and_data(repo: FakeGit) -> None:
    b = full_run(repo)
    assert b.render("--chips", "c3,s3", "--hardware", "s3") == 0
    body, data = rc.read_card(b.card)
    assert data["card_sha256"] == rc.card_digest(body, data)
    assert rc.card_digest(body + " ", data) != data["card_sha256"]
    assert rc.card_digest(body, {**data, "run": "other"}) != data["card_sha256"]


# --------------------------------------------------------------------------- redaction of structured copies

PASSWORD = 'p&ss"w<rd 42'


def test_redactor_escaped_and_encoded_forms() -> None:
    r = rc.Redactor({"XEWE_WIFI_PSW": PASSWORD, "XEWE_WIFI_SSID": "test"}, "/home/user")
    for form in ("p&amp;ss&quot;w&lt;rd 42", 'p&ss\\"w<rd 42', "p%26ss%22w%3Crd%2042", "p%26ss%22w%3Crd+42"):
        assert r(f"[{form}]") == "[«redacted:XEWE_WIFI_PSW»]", form
    assert r("/home/user/x /home/username2 /home/user") == "~/x /home/username2 ~"  # whole path parts only
    assert r.value("tests/unit/test_x.py test") == "tests/unit/test_x.py «redacted:XEWE_WIFI_SSID»"


def test_junit_and_meta_copies_are_redacted_value_by_value(repo: FakeGit, monkeypatch: pytest.MonkeyPatch) -> None:
    """M2: the password in a JUnit skip message (XML-escaped on disk), a JSON value and a URL never
    survives in any form; an SSID that is a common word never rewrites XML markup or test ids."""
    monkeypatch.setattr(
        rc.Redactor,
        "from_environment",
        classmethod(lambda cls, root: cls({"XEWE_WIFI_PSW": PASSWORD, "XEWE_WIFI_SSID": "test"}, "/home/bench")),
    )
    b = Bench(repo.root)
    assert b.init() == 0
    xml = (
        '<?xml version="1.0" encoding="utf-8"?><testsuites><testsuite name="pytest">'
        '<testcase classname="tests.unit.test_x" name="test_join" file="tests/unit/test_x.py" line="1" time="0.1">'
        '<properties><property name="xewe_kind" value="unit" />'
        '<property name="xewe_nodeid" value="tests/unit/test_x.py::test_join" /></properties>'
        '<skipped type="pytest.skip" message="cannot join test with p&amp;ss&quot;w&lt;rd 42">'
        "url http://x/?pw=p%26ss%22w%3Crd+42</skipped></testcase></testsuite></testsuites>"
    )
    b.output("unit", "1 skipped\n", xml)
    assert b.record("unit", "unit", junit=True) == 0
    write_image(repo.root, "s3", 1000)
    meta_path = repo.root / "build" / "builds" / "s3" / "out" / "meta.json"
    meta = json.loads(meta_path.read_text())
    meta["config"] = {"XEWE_WIFI_PSW": PASSWORD, "NOTE": "test value"}
    meta_path.write_text(json.dumps(meta))
    b.output("build-s3", "ok\n")
    assert b.record("build", "build-s3", "--chip", "s3") == 0
    texts = [(b.logs / name).read_text() for name in ("1-unit.xml", "2-build-s3.meta.json")]
    for text in texts:
        for form in ("p&ss", "p&amp;ss", 'p&ss\\"w', "p%26ss", "w<rd", "w&lt;rd"):
            assert form not in text, (form, text)
    (row,) = rc.parse_junit(b.logs / "1-unit.xml")
    assert row.id == "tests/unit/test_x.py::test_join"  # ids and markup untouched by the SSID "test"
    assert row.detail == "cannot join «redacted:XEWE_WIFI_SSID» with «redacted:XEWE_WIFI_PSW»"
    copied = json.loads(texts[1])
    assert copied["config"] == {
        "XEWE_WIFI_PSW": "«redacted:XEWE_WIFI_PSW»",
        "NOTE": "«redacted:XEWE_WIFI_SSID» value",  # a whole word: redacted; keys are never touched
    }
    assert copied["sketch_size"] == 1000 and copied["type"] == "s3"


# --------------------------------------------------------------------------- refusal rules added after review


def test_refusal_banner_unknown(repo: FakeGit, capsys: pytest.CaptureFixture[str]) -> None:
    """A hardware row whose boot banner was never seen (fw_match unknown) is refused."""
    b = full_run(repo, s3_match="unknown")
    assert b.render("--chips", "c3,s3", "--hardware", "s3") == 1
    assert "refused: s3: boot banner Build Timestamp does not match meta timestamp (fw_match unknown)" in refused(
        capsys
    )


def test_refusal_board_record_of_another_chip(repo: FakeGit, capsys: pytest.CaptureFixture[str]) -> None:
    """F9: an s3 hardware row whose board record says c3."""
    b = full_run(repo, s3_board_chip="c3")
    assert b.render("--chips", "c3,s3", "--hardware", "s3") == 1
    assert "refused: s3: board record is c3 (tests/board/test_fw.py::test_status)" in refused(capsys)


def test_hardware_pass_without_a_board_record_is_an_error(tmp_path: Path) -> None:
    xml = tmp_path / "x.xml"
    xml.write_text(
        '<testsuites><testsuite name="pytest"><testcase classname="tests.board.test_fw" name="test_status" '
        'file="tests/board/test_fw.py" time="1"><properties><property name="xewe_kind" value="board" />'
        '<property name="xewe_needs" value="hardware" /><property name="xewe_chip" value="s3" /></properties>'
        "</testcase></testsuite></testsuites>"
    )
    (row,) = rc.parse_junit(xml)
    assert (row.result, row.detail) == (rc.ERROR, "hardware test passed without a board record")


def test_test_steps_need_results_and_rows(repo: FakeGit, capsys: pytest.CaptureFixture[str]) -> None:
    """L2: a unit/board step without --junit/--format is a usage error; one with zero rows refuses."""
    b = Bench(repo.root)
    assert b.init() == 0
    assert main(["release-card", "record", "--dir", str(b.logs), "--kind", "unit", "--", "build/step", "u"]) == 2
    assert main(["release-card", "record", "--dir", str(b.logs), "--kind", "board", "--chip", "s3", "--", "true"]) == 2
    b.output("setup", "ok\n")
    assert b.record("setup", "setup") == 0
    b.output("unit", "no tests ran\n", '<testsuites><testsuite name="pytest" tests="0" /></testsuites>')
    assert b.record("unit", "unit", junit=True) == 0
    assert b.render() == 1
    assert "refused: step 2-unit produced no test rows" in refused(capsys)


def test_record_resolves_the_root_from_a_subfolder(repo: FakeGit) -> None:
    """L1: without --root, record uses the git work tree (as init/render/verify), not the cwd."""
    b = Bench(repo.root)
    assert b.init() == 0
    write_image(repo.root, "s3", 1000)
    os.chdir(repo.root / "src")
    args = ["release-card", "record", "--dir", str(b.logs), "--kind", "build", "--chip", "s3"]
    assert main([*args, "--", sys.executable, "-c", "pass"]) == 0
    step = b.run_json()["steps"][0]
    assert step["meta"] == "1-build-s3.meta.json" and step["compile_log"] == "1-build-s3.compile.log"


# --------------------------------------------------------------------------- fingerprint, excludes, dirty paths


def test_fingerprint_symlinks_and_submodules(repo: FakeGit) -> None:
    """L3: a symlink (to a folder too) is hashed as its target text; a submodule by its index commit."""
    (repo.root / "lib").mkdir()
    (repo.root / "lib" / "a.h").write_text("#pragma once\n")
    (repo.root / "lib-link").symlink_to("lib")
    first = rc.tree_fingerprint(repo.root, rc.DEFAULT_EXCLUDES)
    assert rc.tree_fingerprint(repo.root, rc.DEFAULT_EXCLUDES) == first
    assert ("hash-object", "--stdin-paths") in repo.calls  # regular files only (no symlink, no folder)
    (repo.root / "lib-link").unlink()
    (repo.root / "lib-link").symlink_to("src")
    second = rc.tree_fingerprint(repo.root, rc.DEFAULT_EXCLUDES)
    assert second != first
    repo.gitlinks["vendor/core"] = "a" * 40
    with_module = rc.tree_fingerprint(repo.root, rc.DEFAULT_EXCLUDES)
    repo.gitlinks["vendor/core"] = "b" * 40
    assert rc.tree_fingerprint(repo.root, rc.DEFAULT_EXCLUDES) not in (with_module, second)


def test_exclude_star_matches_one_part() -> None:
    assert rc.excluded("modules/led/releases/3.1.0/x", ["modules/*/releases"])
    assert not rc.excluded("modules/a/b/releases/x", ["modules/*/releases"])
    assert rc.excluded("releases/3.1.0/logs/run.json", ["releases/3.1.0/logs/"])
    assert not rc.excluded("releasesX/a", ["releases/"])


def test_dirty_paths_reads_renames(repo: FakeGit) -> None:
    repo.status = "R  new.cpp\0old.cpp\0 M src/app.cpp\0?? releases/3.1.0/x\0"
    assert rc.dirty_paths(repo.root, ["releases/"]) == ["new.cpp", "old.cpp", "src/app.cpp"]
    repo.status = "R  releases/3.1.0/moved\0src/gone.cpp\0"
    assert rc.dirty_paths(repo.root, ["releases/"]) == ["src/gone.cpp"]


# --------------------------------------------------------------------------- CI comparison inputs and scope


@pytest.mark.parametrize(
    ("reason", "kind"),
    [
        ("flaky on this_runner", "warning"),
        ("needs a broker on some_host", "warning"),
        ("XEWE_TEST_X not set", "allowed"),
        ("needs XEWE_TEST_HA_MQTT_HOST", "allowed"),
        ("value Not Set here", "allowed"),
        ("no golden dir", "allowed"),
    ],
)
def test_env_gate_is_upper_snake_case_sensitive(reason: str, kind: str) -> None:
    rows = [*CI_OK[:3], ci("tests/board/test_fw.py::test_wifi", rc.SKIP, reason)]
    cmp = rc.compare(card_data(), "sha256:aa", rows, None)
    hits = [x for x in (cmp.warnings if kind == "warning" else cmp.allowed) if "test_wifi" in x]
    assert len(hits) == 1, (cmp.warnings, cmp.allowed)


def test_ci_unit_only_ignores_ci_board_rows() -> None:
    """--unit-only: a CI board row whose id is not on the card is not compared (kills M10)."""
    rows = [*UNIT_ONLY_CI, ci("tests/board/test_fw.py::test_ci_only", rc.SKIP_NOT_RUN, "no board attached")]
    assert rc.compare(card_data(), "sha256:aa", rows, None, unit_only=True).blocks == []
    assert rc.compare(card_data(), "sha256:aa", rows, None).blocks != []


def test_verify_ci_input_checks(repo: FakeGit, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """L5: a --meta-dir that does not exist and a negative --size-tolerance are usage errors; L6: the
    card's chips, hardware and require are printed."""
    b = full_run(repo)
    assert b.render("--chips", "c3,s3", "--hardware", "s3") == 0
    junit = tmp_path / "ci-junit"
    junit.mkdir()
    shutil.copy(CARD_FIXTURES / "unit.xml", junit / "tests-unit.xml")
    args = ["release-card", "verify", "--ci", "--unit-only", "--card", str(b.card), "--junit-dir", str(junit)]
    assert main([*args, "--meta-dir", str(tmp_path / "no-such-dir")]) == 2
    assert main([*args, "--size-tolerance", "-1"]) == 2
    capsys.readouterr()
    assert main(args) == 0
    out = capsys.readouterr().out
    assert "allowed: card rendered with chips c3,s3, hardware s3, require setup,validate,unit,build,board" in out
    data = rc.read_card_data(b.card)
    assert (data["chips"], data["hardware"]) == (["c3", "s3"], ["s3"])
    assert b.render("--chips", "c3,s3", "--hardware", "s3", "--require", "unit") == 0
    assert rc.read_card_data(b.card)["require"] == ["unit"]  # F7: visible in the data block
