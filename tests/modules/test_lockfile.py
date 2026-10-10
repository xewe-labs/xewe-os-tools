from pathlib import Path

import pytest

from conftest import LOCK
from xewe.env import pins
from xewe.modules import lockfile
from xewe.report import XeWeError


def test_parse_defaults() -> None:
    lock = lockfile.parse("schema = 1\n")
    assert lock.version == "0.0.0" and lock.chip == "c3"
    assert lock.core.repo == pins.DEFAULT_CORE_REPO
    assert lock.esp32_version == "3.3.12" and lock.arduino_cli_version == "1.5.1"


def test_round_trip_keeps_header_and_values() -> None:
    text = LOCK.format(selected='["wifi"]') + 'ArduinoJson = { repo = "https://github.com/bblanchon/ArduinoJson", ref = "v7.4.2" }\n'
    lock = lockfile.parse(text)
    assert lock.selected == ["wifi"]
    assert lock.libraries["ArduinoJson"].ref == "v7.4.2"
    out = lockfile.dumps(lock)
    assert out.startswith("# xewe.toml: this firmware's manifest")
    again = lockfile.parse(out)
    assert again == lock


def test_toolchain_override() -> None:
    lock = lockfile.parse('[toolchain]\nesp32 = "3.3.7"\n')
    assert lock.esp32_version == "3.3.7"
    assert "[toolchain]" in lockfile.dumps(lock)


@pytest.mark.parametrize("text", [
    "schmea = 1\n",
    "[core]\nreff = \"1\"\n",
    "schema = 2\n",
    "[project]\nchip = \"esp32\"\n",
    "[project]\nversion = \"2.0\"\n",
    "[libraries]\nX = { repo = \"r\" }\n",
    "[modules]\nselected = \"wifi\"\n",
    "not toml [",
])
def test_invalid_lock_is_usage_error(text: str) -> None:
    with pytest.raises(XeWeError) as exc:
        lockfile.parse(text)
    assert exc.value.code == 2


def test_save_is_atomic(tmp_path: Path) -> None:
    path = tmp_path / "xewe.toml"
    lockfile.save(lockfile.Lock(), path)
    assert lockfile.load(path).version == "0.0.0"
    assert not (tmp_path / "xewe.toml.tmp").exists()


# --- the ref `latest` (the default branch head)


def test_latest_ref_is_accepted_everywhere() -> None:
    text = (LOCK.format(selected="[]").replace('ref = "1.0.0"', 'ref = "latest"')
            .replace('ref = "v1.0.0"', 'ref = "latest"').replace('ref = "v0.1.1"', 'ref = "latest"')
            + 'FastLED = { repo = "https://github.com/FastLED/FastLED", ref = "latest" }\n')
    lock = lockfile.parse(text)
    assert [lock.source(n).ref for n in ("core", "modules", "tools")] == ["latest"] * 3
    assert lock.core.tracks_latest and lock.libraries["FastLED"].tracks_latest
    assert not lockfile.parse(LOCK.format(selected="[]")).core.tracks_latest
    assert lockfile.parse(lockfile.dumps(lock)) == lock
