"""Shared fixtures and helpers for every area (see tests/README.md): a scratch project, fake
arduino-cli/esptool, fake serial ports and a fake serial port, a fake clock."""

from __future__ import annotations

import json
import os
import shutil
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import serial as pyserial
from serial.tools import list_ports

from xewe.board import serialio
from xewe.board.boards import Board
from xewe.env import config, dotenv
from xewe.env.project import Paths
from xewe.modules.registry import Registry

HERE = Path(__file__).parent
REPO = HERE.parent
"""The tools checkout (``scripts/``, ``.env.example``)."""
FAKES = HERE / "fakes"
FIXTURES = HERE / "fixtures"
PROPERTIES = FIXTURES / "module_properties"

BIN = "build/builds/c3/out/2.0.15-c3-xewe-os.bin"
"""The c3 image ``xewe build`` makes for the ``project`` fixture (relative to its root)."""
PORT = "/dev/ttyFAKE"
DROP = object()
"""``FakeSerial`` chunk marker: the port vanishes for one read (native-USB reboot)."""


def make_modules_checkout(dest: Path, layout: str = "modules", board_tests: bool = False) -> Path:
    """A modules checkout built from the six real module.properties (+ stub headers; with
    ``board_tests`` also an empty ``tests/board/test_<slug>.py`` each)."""
    for props in sorted(PROPERTIES.glob("*.properties")):
        slug = props.stem
        d = dest / ("modules" if layout == "modules" else "") / (slug if layout == "modules" else f"xewe-os-module-{slug}")
        d.mkdir(parents=True)
        shutil.copy(props, d / "module.properties")
        text = props.read_text()
        folder = next(line.split("=", 1)[1] for line in text.splitlines() if line.startswith("folder="))
        (d / "src" / folder).mkdir(parents=True)
        (d / "src" / folder / f"{folder}.h").write_text(f"#pragma once\nclass {folder} {{}};\n")
        if board_tests:
            (d / "tests" / "board").mkdir(parents=True)
            (d / "tests" / "board" / f"test_{slug}.py").write_text("")
    return dest


UNIT_TESTS = '''
import pytest


@pytest.mark.unit
def test_logic():
    assert 1 + 1 == 2
'''
BOARD_TESTS = '''
def test_status(serial):
    serial.command("$system status", expect="Uptime", timeout=1)
'''


def write_tests(root: Path, board: str = BOARD_TESTS, unit: str | None = UNIT_TESTS) -> None:
    """``tests/board/test_fw.py`` (``board``; one board test by default) and, unless ``unit`` is None,
    ``tests/unit/test_logic.py`` (one unit test)."""
    if unit is not None:
        (root / "tests" / "unit").mkdir(parents=True, exist_ok=True)
        (root / "tests" / "unit" / "test_logic.py").write_text(unit)
    (root / "tests" / "board").mkdir(parents=True, exist_ok=True)
    (root / "tests" / "board" / "test_fw.py").write_text(board)


LOCK = """\
# xewe.toml: this firmware's manifest (pinned inputs). Edit by hand or with `xewe manifest update`.
# ./setup.sh installs exactly these refs into build/.
schema = 1

[project]
version = "2.0.15"
chip = "c3"

[core]
repo = "https://github.com/xewe-labs/xewe-os-core"
ref = "1.0.0"

[modules]
repo = "https://github.com/xewe-labs/xewe-os-modules"
ref = "v1.0.0"
selected = {selected}

[tools]
repo = "https://github.com/xewe-labs/xewe-os-tools"
ref = "v0.1.1"

[libraries]
"""


def write_project(root: Path, selected: str = '["wifi", "web-interface"]', ino: str | None = None) -> Paths:
    """Project files only (no build/)."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "xewe.toml").write_text(LOCK.format(selected=selected))
    (root / f"{ino or root.name}.ino").write_text('#include "Config.h"\nvoid setup() {}\nvoid loop() {}\n')
    (root / "Config.h").write_text("#pragma once\n#include <XeWeBuildInfo.h>\n")
    return Paths(root)


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    for var in ("XEWE_PORT", "XEWE_CHIP", "XEWE_REQUIRE_BOARD", "XEWE_ARDUINO_CLI", "XEWE_ESPTOOL",
                "XEWE_NO_BOARD", "XEWE_ARDUINO_DATA", "XEWE_CORE_SOURCE", "XEWE_MODULES_SOURCE", "XEWE_TOOLS_SOURCE",
                "XEWE_CACHE", *dotenv.KEYS, dotenv.ENV_VAR):
        monkeypatch.delenv(var, raising=False)
    # Never touch the real ~/.xewe-os: the shared toolchain of every test lives in its tmp dir.
    monkeypatch.setenv("XEWE_HOME", str(tmp_path / "xewe-home"))
    # Never read the dotenv file of this checkout (the developer's credentials): no tools checkout
    # is found unless a test sets one up.
    monkeypatch.setattr(dotenv, "package_checkout", lambda module_file=None: None)
    saved = dict(os.environ)  # dotenv.apply writes os.environ directly; undo it after each test
    yield
    os.environ.clear()
    os.environ.update(saved)


@pytest.fixture
def no_ports(monkeypatch: pytest.MonkeyPatch) -> None:
    """No serial ports on the system."""
    monkeypatch.setattr(list_ports, "comports", lambda: [])


@dataclass
class Port:
    """What ``list_ports.comports()`` yields."""

    device: str
    vid: int | None = None
    pid: int | None = None
    serial_number: str | None = None
    description: str = ""


@pytest.fixture
def ports(monkeypatch: pytest.MonkeyPatch) -> list[Port]:
    """The system's serial ports: append ``Port`` entries to the returned list."""
    found: list[Port] = []
    monkeypatch.setattr(list_ports, "comports", lambda: list(found))
    return found


@pytest.fixture
def port_node(tmp_path: Path) -> str:
    """A port path that exists as a file (``boards.port_exists`` is true for it)."""
    node = tmp_path / "ttyACM0"
    node.write_text("")
    return str(node)


def select_board(board: Board | None) -> Callable[[str | None], Board | None]:
    """A ``select`` callable for ``serial_main``/``provision_main`` that always returns ``board``."""
    return lambda port: board


@pytest.fixture
def fake_cli(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Callable[[], list[dict[str, Any]]]:
    """Use tests/fakes/arduino-cli; returns a reader of its call log."""
    log = tmp_path / "arduino-cli.jsonl"
    monkeypatch.setenv("XEWE_ARDUINO_CLI", str(FAKES / "arduino-cli"))
    monkeypatch.setenv("FAKE_ARDUINO_LOG", str(log))

    def calls() -> list[dict[str, Any]]:
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    return calls


@pytest.fixture
def fake_esptool(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Callable[[], list[list[str]]]:
    """Use tests/fakes/esptool; returns a reader of its call log."""
    log = tmp_path / "esptool.jsonl"
    monkeypatch.setenv("XEWE_ESPTOOL", str(FAKES / "esptool"))
    monkeypatch.setenv("FAKE_ESPTOOL_LOG", str(log))

    def calls() -> list[list[str]]:
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    return calls


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_cli: Any, no_ports: None) -> Paths:
    """A set-up project (as if `xewe setup` ran): lock, sketch, modules, build_config.toml."""
    from xewe.modules import registry as modules

    p = write_project(tmp_path / "xewe-os")
    checkout = make_modules_checkout(tmp_path / "modules-src")  # as XEWE_MODULES_SOURCE
    registry = Registry.load(checkout)
    modules.generate(p, registry, ["wifi", "web-interface"], "https://github.com/xewe-labs/xewe-os-modules", "v1.0.0", "-")
    p.libraries.mkdir(parents=True)
    cfg = config.BuildConfig(
        setup_completed="2026-10-08T12:00:00Z",
        paths={"arduino_cli": str(p.bin / "arduino-cli-1.5.1"), "arduino_data": str(p.default_arduino_data),
               "arduino_user": str(p.arduino_user), "libraries": "libraries", "modules": str(checkout)},
        installed={"arduino_cli": "1.5.1", "esp32": "3.3.12",
                   "core": {"ref": "1.0.0", "commit": "abc", "source": "https://github.com/xewe-labs/xewe-os-core"},
                   "modules": {"ref": "v1.0.0", "commit": "def", "source": "https://github.com/xewe-labs/xewe-os-modules"},
                   "tools": {"ref": "v0.1.1", "commit": "-", "source": "https://github.com/xewe-labs/xewe-os-tools"},
                   "libraries": {}},
    )
    config.save(p, cfg)
    monkeypatch.chdir(p.root)
    return p


@pytest.fixture
def project_with_tests(project: Paths) -> Paths:
    """``project`` plus ``write_tests`` (one unit test, one board test)."""
    write_tests(project.root)
    return project


class FakeClock:
    """Fake ``time`` for serialio: ``sleep`` advances ``monotonic``; each ``monotonic`` call also
    advances ``tick`` seconds, so polling loops over a FakeSerial end in fake time."""

    def __init__(self, tick: float = 0.0) -> None:
        self.now = 0.0
        self.tick = tick

    def monotonic(self) -> float:
        self.now += self.tick
        return self.now

    def sleep(self, s: float) -> None:
        self.now += s


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> FakeClock:
    """serialio on a ``FakeClock`` ticking 1 ms per ``monotonic`` call."""
    c = FakeClock(tick=0.001)
    monkeypatch.setattr(serialio, "time", c)
    return c


class FakeSerial:
    """In-memory stand-in for ``serial.Serial`` (unopened on construction, like pyserial).

    ``incoming`` is readable at once; ``chunks`` arrive one per ``read`` (``DROP`` raises like a
    vanished USB port); ``script`` maps each ``write`` to bytes the board answers."""

    instances: list[FakeSerial] = []

    def __init__(self, script: Callable[[bytes], bytes] | None = None, incoming: bytes = b"",
                 chunks: list[Any] | None = None) -> None:
        self.port: str | None = None
        self.baudrate = 9600
        self.timeout: float | None = None
        self.dsrdtr = True
        self.rtscts = True
        self.dtr = True
        self.rts = True
        self.is_open = False
        self.dtr_at_open: bool | None = None
        self.rts_at_open: bool | None = None
        self.rx = bytearray(incoming)
        self.written = bytearray()
        self.script = script
        self.fail_reads = 0
        self.chunks: list[Any] = list(chunks or [])
        self.opens = 0
        self.rts_history: list[bool] = []
        self.line_history: list[tuple[bool, bool]] = []
        """(dtr, rts) after each line change and at open, in order (open starts from cdc_acm's 1/1)."""
        FakeSerial.instances.append(self)

    def __setattr__(self, name: str, value: Any) -> None:
        if name == "rts" and "rts_history" in self.__dict__:
            self.rts_history.append(value)
        object.__setattr__(self, name, value)
        if name in ("dtr", "rts") and self.__dict__.get("is_open"):
            self.line_history.append((self.dtr, self.rts))

    def open(self) -> None:
        # Like cdc_acm + pyserial posix: the kernel raises both lines, then DTR, then RTS is applied.
        self.dtr_at_open, self.rts_at_open = self.dtr, self.rts
        self.line_history += [(True, True), (self.dtr, True), (self.dtr, self.rts)]
        self.is_open = True
        self.opens += 1

    def close(self) -> None:
        self.is_open = False

    @property
    def in_waiting(self) -> int:
        return len(self.rx)

    def read(self, n: int = 1) -> bytes:
        if self.fail_reads:
            self.fail_reads -= 1
            raise pyserial.SerialException("device disconnected")
        if self.chunks:
            chunk = self.chunks.pop(0)
            if chunk is DROP:
                raise pyserial.SerialException("device reports readiness to read but returned no data")
            self.rx += chunk
            n = max(n, len(self.rx))
        data = bytes(self.rx[:n])
        del self.rx[:n]
        return data

    def write(self, data: bytes) -> int:
        self.written += data
        if self.script is not None:
            self.rx += self.script(bytes(data))
        return len(data)

    def flush(self) -> None:
        pass
