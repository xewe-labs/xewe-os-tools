import tomllib
from pathlib import Path
from typing import Any

import pytest

from conftest import make_modules_checkout, write_project
from xewe import arduino, config, fetch, lockfile, setup
from xewe.cli import main
from xewe.project import Paths
from xewe.setup import SetupOptions


@pytest.fixture
def fresh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_cli: Any, no_ports: None) -> Paths:
    """A project straight from the template: no build/ yet; local core + legacy modules sources."""
    p = write_project(tmp_path / "xewe-os", selected="[]")
    core = tmp_path / "core-src"
    (core / "src").mkdir(parents=True)
    (core / "library.properties").write_text("name=XeWeCore\n")
    make_modules_checkout(tmp_path / "modules-src", "legacy")
    (tmp_path / "modules-src" / "unrelated").mkdir()
    monkeypatch.setenv("XEWE_CORE_SOURCE", str(core))
    monkeypatch.setenv("XEWE_MODULES_SOURCE", str(tmp_path / "modules-src"))
    monkeypatch.setattr(setup.pins, "MIN_FREE_DISK_BYTES", 0)
    monkeypatch.chdir(p.root)
    return p


def _installs(calls: list[dict[str, Any]]) -> list[list[str]]:
    return [c["argv"] for c in calls if c["argv"][:2] == ["core", "install"]]


def test_full_setup_with_local_sources(fresh: Paths, fake_cli, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["setup", "--modules", "scheduler"]) == 0
    assert "setup complete" in capsys.readouterr().out
    argvs = [c["argv"][:2] for c in fake_cli()]
    assert argvs[0] == ["version"]
    assert argvs.index(["core", "update-index"]) < argvs.index(["core", "install"])
    assert _installs(fake_cli()) == [["core", "install", "esp32:esp32@3.3.12"]]
    cfg = tomllib.loads(fresh.build_config.read_text())
    assert cfg["paths"]["arduino_data"] == "arduino15"
    assert cfg["paths"]["esptool"] == "arduino15/packages/esp32/tools/esptool_py/5.3.1/esptool"
    assert cfg["installed"]["esp32"] == "3.3.12" and cfg["installed"]["arduino_cli"] == "1.5.1"
    assert cfg["installed"]["core"]["source"].startswith("local:")
    assert cfg["installed"]["modules"]["source"].startswith("local:")
    assert (fresh.libraries / "XeWeCore" / "library.properties").is_file()
    assert sorted(d.name for d in fresh.modules_checkout.iterdir())[0] == "xewe-os-module-buttons"
    assert not (fresh.modules_checkout / "unrelated").exists()
    assert lockfile.load(fresh.lock).selected == ["scheduler"]
    assert "Scheduler scheduler(os, time_module);" in (fresh.src_modules / "Modules.h").read_text()
    assert (fresh.build / ".gitignore").read_text() == "*\n"
    assert (fresh.arduino_user).is_dir()
    for call in fake_cli():  # every call is isolated from ~/.arduino15 and ~/Arduino
        assert call["env"]["ARDUINO_DIRECTORIES_DATA"] == str(fresh.build / "arduino15")
        assert call["env"]["ARDUINO_DIRECTORIES_USER"] == str(fresh.build / "arduino-user")


def test_rerun_is_a_noop(fresh: Paths, fake_cli) -> None:
    assert main(["setup", "--modules", "wifi"]) == 0
    before = len(_installs(fake_cli()))
    assert main(["setup"]) == 0
    assert len(_installs(fake_cli())) == before
    assert lockfile.load(fresh.lock).selected == ["wifi"]


def test_interrupted_setup_leaves_no_build_config(fresh: Paths, monkeypatch: pytest.MonkeyPatch) -> None:
    assert main(["setup", "--modules", "wifi"]) == 0
    (fresh.build / "arduino15" / "fake-core.json").unlink()
    monkeypatch.setenv("FAKE_ARDUINO_INSTALL_FAILS", "99")
    delays: list[float] = []
    with pytest.raises(Exception):
        setup.run_setup(fresh, SetupOptions(), sleep=delays.append)
    assert delays == [10, 20, 40, 80]
    assert not fresh.build_config.exists()
    assert main(["build"]) == 3


def test_core_install_retries(fresh: Paths, monkeypatch: pytest.MonkeyPatch, fake_cli) -> None:
    monkeypatch.setenv("FAKE_ARDUINO_INSTALL_FAILS", "2")
    delays: list[float] = []
    assert setup.run_setup(fresh, SetupOptions(modules="wifi"), sleep=delays.append) == 0
    assert delays == [10, 20] and len(_installs(fake_cli())) == 3


def test_head_rescue(fresh: Paths, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    url = "https://github.com/espressif/esp-rv32/releases/download/2601/big.tar.xz"
    monkeypatch.setenv("FAKE_ARDUINO_HEAD_URL", url)
    fetched: list[tuple[str, Path]] = []
    monkeypatch.setattr(arduino.fetch, "download", lambda u, d, *a, **k: fetched.append((u, d)))
    delays: list[float] = []
    assert setup.run_setup(fresh, SetupOptions(modules="wifi"), sleep=delays.append) == 0
    assert fetched == [(url, tmp_path / "xdg-cache" / "arduino-staging" / "packages" / "big.tar.xz")]
    assert delays == []


def test_reuse_arduino_data(fresh: Paths, tmp_path: Path, fake_cli, monkeypatch: pytest.MonkeyPatch) -> None:
    shared = tmp_path / "shared-arduino15"
    shared.mkdir()
    (shared / "fake-core.json").write_text('"3.3.12"')
    assert main(["setup", "--modules", "wifi", "--arduino-data", str(shared)]) == 0
    assert _installs(fake_cli()) == []
    cfg = config.load(fresh)
    assert cfg is not None and cfg.paths["arduino_data"] == str(shared)
    assert not (fresh.build / "arduino15").exists()
    assert main(["build", "--chip", "c3"]) == 0
    assert fake_cli()[-1]["env"]["ARDUINO_DIRECTORIES_DATA"] == str(shared)
    # the choice sticks on a re-run without the flag; the env var works too
    assert main(["setup"]) == 0
    assert config.load(fresh).paths["arduino_data"] == str(shared)
    other = tmp_path / "other"
    monkeypatch.setenv("XEWE_ARDUINO_DATA", str(other))
    assert main(["setup"]) == 0
    assert config.load(fresh).paths["arduino_data"] == str(other)
    assert _installs(fake_cli()) == [["core", "install", "esp32:esp32@3.3.12"]]


def test_no_selection_without_tty_means_zero_modules(fresh: Paths, monkeypatch: pytest.MonkeyPatch,
                                                     caplog: pytest.LogCaptureFixture) -> None:
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr("builtins.input", lambda *a: pytest.fail("prompted without a TTY"))
    assert main(["setup"]) == 0
    assert "no modules selected" in caplog.text
    assert lockfile.load(fresh.lock).selected == []
    assert "#include" not in (fresh.src_modules / "Modules.h").read_text()
    assert main(["build", "--chip", "c3"]) == 0


def test_tty_empty_answer_means_zero_modules(fresh: Paths, monkeypatch: pytest.MonkeyPatch,
                                             caplog: pytest.LogCaptureFixture) -> None:
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda *a: "")
    assert main(["setup"]) == 0
    assert "no modules selected" in caplog.text
    assert lockfile.load(fresh.lock).selected == []
    assert (fresh.src_modules / "Modules.h").is_file()


@pytest.mark.parametrize("value", ["", "none"])
def test_modules_flag_none(fresh: Paths, value: str) -> None:
    assert main(["setup", "--modules", "wifi"]) == 0
    assert main(["setup", "--modules", value]) == 0
    assert lockfile.load(fresh.lock).selected == []
    assert "#include" not in (fresh.src_modules / "Modules.h").read_text()


def test_modules_source_without_modules_warns(fresh: Paths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                              caplog: pytest.LogCaptureFixture) -> None:
    registry_only = tmp_path / "old-registry"
    registry_only.mkdir()
    (registry_only / "repositories.txt").write_text("xewe-os-module-wifi\n")
    monkeypatch.setenv("XEWE_MODULES_SOURCE", str(registry_only))
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    assert main(["setup"]) == 0
    warning = next(r.getMessage() for r in caplog.records if r.levelname == "WARNING" and "no modules found" in r.getMessage())
    assert str(registry_only) in warning
    assert "modules/<slug>/module.properties" in warning and "xewe-os-module-<slug>" in warning
    assert main(["setup", "--modules", "wifi"]) == 2


def test_missing_config_h(fresh: Paths) -> None:
    (fresh.root / "Config.h").unlink()
    assert main(["setup", "--modules", "wifi"]) == 1
    assert not fresh.build_config.exists()


def test_legacy_version_state_hint(fresh: Paths, caplog: pytest.LogCaptureFixture) -> None:
    fresh.build.mkdir()
    (fresh.build / "version_state").write_text("MAJOR=2\nMINOR=0\nPATCH=15\nBUILD_ID=0\n")
    assert main(["setup", "--modules", "wifi"]) == 0
    assert 'version = "2.0.15"' in caplog.text
    assert (fresh.build / "version_state").exists()


@pytest.fixture
def git_sources(fresh: Paths, monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """Replace git with fakes: clone writes a checkout, ls-remote answers with tags."""
    monkeypatch.delenv("XEWE_CORE_SOURCE")
    monkeypatch.delenv("XEWE_MODULES_SOURCE")
    clones: list[tuple[str, str]] = []

    def clone(repo: str, ref: str, dest: Path) -> str:
        clones.append((repo, ref))
        if dest.exists():
            import shutil

            shutil.rmtree(dest)
        if "modules" in repo:
            make_modules_checkout(dest, "modules")
        else:
            dest.mkdir(parents=True)
        return "c0ffee"

    tags = "\n".join(f"{'0' * 40}\trefs/tags/{t}" for t in ["v1.0.0", "v1.10.0", "v1.9.0", "1.2.0"])
    monkeypatch.setattr(fetch, "clone", clone)
    monkeypatch.setattr(fetch, "head_commit", lambda path: "c0ffee")
    monkeypatch.setattr(fetch, "git", lambda *a, **k: tags)
    return clones


def test_git_sources_and_skip_when_recorded(fresh: Paths, git_sources: list[tuple[str, str]]) -> None:
    assert main(["setup", "--modules", "wifi"]) == 0
    assert git_sources == [("https://github.com/xewe-labs/xewe-os-core", "1.0.0"),
                           ("https://github.com/xewe-labs/xewe-os-modules", "v1.0.0")]
    cfg = config.load(fresh)
    assert cfg is not None and cfg.installed["core"] == {
        "ref": "1.0.0", "commit": "c0ffee", "source": "https://github.com/xewe-labs/xewe-os-core"}
    git_sources.clear()
    assert main(["setup"]) == 0
    assert git_sources == []
    assert main(["setup", "--force"]) == 0
    assert len(git_sources) == 2


def test_latest_installs_newest_tag_without_editing_lock(fresh: Paths, git_sources: list[tuple[str, str]]) -> None:
    before = fresh.lock.read_text()
    assert main(["setup", "--latest", "--modules", "wifi"]) == 0
    assert ("https://github.com/xewe-labs/xewe-os-core", "v1.10.0") in git_sources
    cfg = config.load(fresh)
    assert cfg is not None and cfg.installed["core"]["ref"] == "v1.10.0"
    assert lockfile.load(fresh.lock).core.ref == "1.0.0"
    assert fresh.lock.read_text() == before.replace("selected = []", 'selected = ["wifi"]')


# --- library dependencies of modules (depends_libraries + the modules catalogue libraries.toml)

FASTLED = "https://github.com/FastLED/FastLED"


@pytest.fixture
def led_source(fresh: Paths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """The fake modules source gains a led-strip module (depends_libraries=FastLED) and a catalogue;
    library clones are faked and recorded."""
    src = tmp_path / "modules-src"
    d = src / "xewe-os-module-led-strip"
    (d / "src" / "Led").mkdir(parents=True)
    (d / "src" / "Led" / "Led.h").write_text("#pragma once\nclass Led {};\n")
    props = (src / "xewe-os-module-pins" / "module.properties").read_text()
    props = (props.replace("slug=pins", "slug=led-strip").replace("id=pins", "id=led").replace("Pins", "Led")
             .replace("depends_libraries=XeWeOS (>=0.1.0)", "depends_libraries=FastLED, XeWeOS (>=0.1.0)"))
    (d / "module.properties").write_text(props)
    (src / "libraries.toml").write_text(f'[FastLED]\nrepo = "{FASTLED}"\nref = "3.10.3"\n')
    clones: list[tuple[str, str]] = []

    def clone(repo: str, ref: str, dest: Path) -> str:
        clones.append((repo, ref))
        dest.mkdir(parents=True, exist_ok=True)
        return "f00d"

    monkeypatch.setattr(fetch, "clone", clone)
    monkeypatch.setattr(fetch, "head_commit", lambda path: "f00d")
    return clones


def test_module_library_from_catalogue(fresh: Paths, led_source: list[tuple[str, str]],
                                       caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["setup", "--modules", "led-strip"]) == 0
    assert led_source == [(FASTLED, "3.10.3")]
    assert "library FastLED: from modules catalogue (3.10.3)" in caplog.text
    assert "XeWeOS" not in caplog.text  # the core library is never looked up
    assert (fresh.modules_checkout / "libraries.toml").is_file()  # copied with the legacy layout too
    assert (fresh.libraries / "FastLED").is_dir()
    rec = config.load(fresh).installed["libraries"]["FastLED"]
    assert rec["ref"] == "3.10.3" and rec["source"] == FASTLED and rec["origin"] == "modules catalogue"
    assert lockfile.load(fresh.lock).libraries == {}  # the catalogue never edits the lock
    capsys.readouterr()
    assert main(["lock", "show"]) == 0
    line = next(x for x in capsys.readouterr().out.splitlines() if "libraries.FastLED" in x)
    assert line.startswith(" ") and "3.10.3" in line and line.endswith("(modules catalogue)")
    # a re-run does not clone again
    led_source.clear()
    assert main(["setup"]) == 0
    assert led_source == []


def test_lock_pin_wins_over_catalogue(fresh: Paths, led_source: list[tuple[str, str]],
                                      caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str]) -> None:
    fork = "https://github.com/example/FastLED"
    fresh.lock.write_text(fresh.lock.read_text() + f'FastLED = {{ repo = "{fork}", ref = "3.9.0" }}\n')
    assert main(["setup", "--modules", "led-strip"]) == 0
    assert led_source == [(fork, "3.9.0")]
    assert "library FastLED: from xewe.lock (3.9.0)" in caplog.text
    assert "from modules catalogue" not in caplog.text
    assert config.load(fresh).installed["libraries"]["FastLED"]["origin"] == "xewe.lock"
    capsys.readouterr()
    assert main(["lock", "show", "--json"]) == 0
    import json

    row = next(r for r in json.loads(capsys.readouterr().out) if r["name"] == "libraries.FastLED")
    assert row["lock"] == "3.9.0" and row["origin"] == "xewe.lock" and not row["drift"]


def test_unselected_module_libraries_are_not_installed(fresh: Paths, led_source: list[tuple[str, str]]) -> None:
    assert main(["setup", "--modules", "wifi"]) == 0
    assert led_source == []
    assert config.load(fresh).installed["libraries"] == {}


def test_library_missing_from_catalogue_warns(fresh: Paths, led_source: list[tuple[str, str]], tmp_path: Path,
                                             caplog: pytest.LogCaptureFixture) -> None:
    (tmp_path / "modules-src" / "libraries.toml").unlink()
    assert main(["setup", "--modules", "led-strip"]) == 0
    assert led_source == []
    assert "library FastLED (needed by module led-strip) is neither in xewe.lock [libraries] nor in the " \
           "modules libraries.toml; not installed" in caplog.text


def test_bad_catalogue_fails(fresh: Paths, led_source: list[tuple[str, str]], tmp_path: Path) -> None:
    (tmp_path / "modules-src" / "libraries.toml").write_text('[FastLED]\nrepo = "x"\n')
    assert main(["setup", "--modules", "led-strip"]) == 1
