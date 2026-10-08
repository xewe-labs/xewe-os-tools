"""Shared fixtures: a fake project, fake arduino-cli/esptool, no serial ports, a fake serial port."""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from serial.tools import list_ports

from xewe import config
from xewe.modules import Registry
from xewe.project import Paths

HERE = Path(__file__).parent
FAKES = HERE / "fakes"
FIXTURES = HERE / "fixtures"
PROPERTIES = FIXTURES / "module_properties"


def make_modules_checkout(dest: Path, layout: str = "modules") -> Path:
    """A modules checkout built from the six real module.properties (+ stub headers)."""
    for props in sorted(PROPERTIES.glob("*.properties")):
        slug = props.stem
        d = dest / ("modules" if layout == "modules" else "") / (slug if layout == "modules" else f"xewe-os-module-{slug}")
        d.mkdir(parents=True)
        shutil.copy(props, d / "module.properties")
        text = props.read_text()
        folder = next(line.split("=", 1)[1] for line in text.splitlines() if line.startswith("folder="))
        (d / "src" / folder).mkdir(parents=True)
        (d / "src" / folder / f"{folder}.h").write_text(f"#pragma once\nclass {folder} {{}};\n")
    return dest


LOCK = """\
# xewe.lock: pinned inputs of this firmware. Edit by hand or with `xewe lock update`.
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
    (root / "xewe.lock").write_text(LOCK.format(selected=selected))
    (root / f"{ino or root.name}.ino").write_text('#include "Config.h"\nvoid setup() {}\nvoid loop() {}\n')
    (root / "Config.h").write_text("#pragma once\n#include <XeWeBuildInfo.h>\n")
    return Paths(root)


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for var in ("XEWE_PORT", "XEWE_CHIP", "XEWE_REQUIRE_BOARD", "XEWE_ARDUINO_CLI", "XEWE_ESPTOOL",
                "XEWE_ARDUINO_DATA", "XEWE_CORE_SOURCE", "XEWE_MODULES_SOURCE", "XEWE_TOOLS_SOURCE"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("XEWE_CACHE", str(tmp_path / "xdg-cache"))


@pytest.fixture
def no_ports(monkeypatch: pytest.MonkeyPatch) -> None:
    """No serial ports on the system."""
    monkeypatch.setattr(list_ports, "comports", lambda: [])


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
    from xewe import modules

    p = write_project(tmp_path / "xewe-os")
    make_modules_checkout(p.modules_checkout)
    registry = Registry.load(p.modules_checkout)
    modules.generate(p, registry, ["wifi", "web-interface"], "https://github.com/xewe-labs/xewe-os-modules", "v1.0.0", "-")
    p.libraries.mkdir(parents=True)
    cfg = config.BuildConfig(
        setup_completed="2026-10-08T12:00:00Z",
        paths={"arduino_cli": "bin/arduino-cli", "arduino_data": "arduino15", "libraries": "libraries",
               "modules": "xewe-os-modules"},
        installed={"arduino_cli": "1.5.1", "esp32": "3.3.12",
                   "core": {"ref": "1.0.0", "commit": "abc", "source": "https://github.com/xewe-labs/xewe-os-core"},
                   "modules": {"ref": "v1.0.0", "commit": "def", "source": "https://github.com/xewe-labs/xewe-os-modules"},
                   "tools": {"ref": "v0.1.1", "commit": "-", "source": "https://github.com/xewe-labs/xewe-os-tools"},
                   "libraries": {}},
    )
    config.save(p, cfg)
    monkeypatch.chdir(p.root)
    return p


class FakeSerial:
    """In-memory stand-in for ``serial.Serial`` (unopened on construction, like pyserial)."""

    instances: list[FakeSerial] = []

    def __init__(self, script: Callable[[bytes], bytes] | None = None, incoming: bytes = b"") -> None:
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
        self.rts_history: list[bool] = []
        FakeSerial.instances.append(self)

    def __setattr__(self, name: str, value: Any) -> None:
        if name == "rts" and "rts_history" in self.__dict__:
            self.rts_history.append(value)
        object.__setattr__(self, name, value)

    def open(self) -> None:
        self.dtr_at_open, self.rts_at_open = self.dtr, self.rts
        self.is_open = True

    def close(self) -> None:
        self.is_open = False

    @property
    def in_waiting(self) -> int:
        return len(self.rx)

    def read(self, n: int = 1) -> bytes:
        if self.fail_reads:
            self.fail_reads -= 1
            import serial

            raise serial.SerialException("device disconnected")
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
