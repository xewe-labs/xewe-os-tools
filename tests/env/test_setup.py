import tomllib
from pathlib import Path
from typing import Any

import pytest

from conftest import REPO, make_modules_checkout, write_project
from xewe.cli import main
from xewe.env import arduino, config, fetch, setup
from xewe.env.project import Paths
from xewe.env.setup import SetupOptions
from xewe.modules import lockfile


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


def test_full_setup_with_local_sources(fresh: Paths, fake_cli, capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    assert main(["setup", "--modules", "scheduler"]) == 0
    assert "setup complete" in capsys.readouterr().out
    argvs = [c["argv"][:2] for c in fake_cli()]
    assert argvs[0] == ["version"]
    assert argvs.index(["core", "update-index"]) < argvs.index(["core", "install"])
    assert _installs(fake_cli()) == [["core", "install", "esp32:esp32@3.3.12"]]
    cfg = tomllib.loads(fresh.build_config.read_text())
    tools = tmp_path / "xewe-home" / "build-tools"
    assert fresh.build_config == fresh.root / "build" / "config" / "build_config.toml"
    assert cfg["paths"]["arduino_data"] == str(tools / "arduino15")
    assert cfg["paths"]["arduino_user"] == str(tools / "arduino-user")
    assert cfg["paths"]["esptool"] == str(tools / "arduino15/packages/esp32/tools/esptool_py/5.3.1/esptool")
    assert cfg["paths"]["libraries"] == "libraries"
    assert cfg["paths"]["modules"] == str(tmp_path / "modules-src")  # XEWE_MODULES_SOURCE is used where it is
    assert cfg["installed"]["esp32"] == "3.3.12" and cfg["installed"]["arduino_cli"] == "1.5.1"
    assert cfg["installed"]["core"]["source"].startswith("local:")
    assert cfg["installed"]["modules"]["source"].startswith("local:")
    assert (fresh.libraries / "XeWeCore" / "library.properties").is_file()
    assert lockfile.load(fresh.lock).selected == ["scheduler"]
    assert "Scheduler scheduler(os, time_module);" in fresh.src_modules_h.read_text()
    assert (fresh.modules / "src" / "Scheduler").is_dir() and fresh.modules_lock.is_file()
    assert fresh.modules_lock == fresh.build / "modules" / "modules.lock"
    assert (fresh.build / ".gitignore").read_text() == "*\n"
    assert sorted(d.name for d in tools.iterdir()) == [".lock", "arduino-user", "arduino15", "bin", "downloads"]
    # no build/tmp after setup (created on demand by build and test)
    assert sorted(d.name for d in fresh.build.iterdir()) == [".gitignore", "config", "libraries", "modules"]
    for call in fake_cli():  # every call is isolated from ~/.arduino15 and ~/Arduino
        assert call["env"]["ARDUINO_DIRECTORIES_DATA"] == str(tools / "arduino15")
        assert call["env"]["ARDUINO_DIRECTORIES_USER"] == str(tools / "arduino-user")
        assert call["env"]["ARDUINO_DIRECTORIES_DOWNLOADS"] == str(tools / "downloads")


def test_rerun_is_a_noop(fresh: Paths, fake_cli) -> None:
    assert main(["setup", "--modules", "wifi"]) == 0
    before = len(_installs(fake_cli()))
    assert main(["setup"]) == 0
    assert len(_installs(fake_cli())) == before
    assert lockfile.load(fresh.lock).selected == ["wifi"]


def test_interrupted_setup_leaves_no_build_config(fresh: Paths, monkeypatch: pytest.MonkeyPatch) -> None:
    assert main(["setup", "--modules", "wifi"]) == 0
    (fresh.default_arduino_data / "fake-core.json").unlink()
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
    assert fetched == [(url, tmp_path / "xewe-home" / "build-tools" / "downloads" / "packages" / "big.tar.xz")]
    assert delays == []


def test_reuse_arduino_data(fresh: Paths, tmp_path: Path, fake_cli, monkeypatch: pytest.MonkeyPatch) -> None:
    shared = tmp_path / "shared-arduino15"
    shared.mkdir()
    (shared / "fake-core.json").write_text('"3.3.12"')
    assert main(["setup", "--modules", "wifi", "--arduino-data", str(shared)]) == 0
    assert _installs(fake_cli()) == []
    cfg = config.load(fresh)
    assert cfg is not None and cfg.paths["arduino_data"] == str(shared)
    assert not (fresh.default_arduino_data / "fake-core.json").exists()  # nothing installed into the shared one
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
    assert "(os" not in fresh.src_modules_h.read_text()
    assert main(["build", "--chip", "c3"]) == 0


def test_tty_empty_answer_means_zero_modules(fresh: Paths, monkeypatch: pytest.MonkeyPatch,
                                             caplog: pytest.LogCaptureFixture) -> None:
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda *a: "")
    assert main(["setup"]) == 0
    assert "no modules selected" in caplog.text
    assert lockfile.load(fresh.lock).selected == []
    assert fresh.src_modules_h.is_file() and (fresh.modules / "library.properties").is_file()


def _no_module_warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records
            if r.levelname == "WARNING" and r.getMessage().startswith("no modules selected")]


def test_no_modules_warning_names_both_fixes(fresh: Paths, monkeypatch: pytest.MonkeyPatch,
                                            caplog: pytest.LogCaptureFixture,
                                            capsys: pytest.CaptureFixture[str]) -> None:
    """Enter at the menu keeps "none", but setup, build and run each print one warning line."""
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda *a: "")
    assert main(["setup"]) == 0
    (msg,) = _no_module_warnings(caplog)
    assert "./setup.sh --modules LIST" in msg and "xewe modules select" in msg and "\n" not in msg
    err = capsys.readouterr().err
    assert sum(1 for line in err.splitlines() if line.startswith("warning: no modules selected")) == 1
    caplog.clear()
    assert main(["build", "--chip", "c3"]) == 0
    assert len(_no_module_warnings(caplog)) == 1
    caplog.clear()
    main(["run", "--no-board"])  # exit 4 (board access disabled) after building
    assert len(_no_module_warnings(caplog)) == 1


def test_no_warning_with_modules_selected(fresh: Paths, caplog: pytest.LogCaptureFixture) -> None:
    assert main(["setup", "--modules", "wifi"]) == 0
    assert main(["build", "--chip", "c3"]) == 0
    assert _no_module_warnings(caplog) == []


@pytest.mark.parametrize("value", ["", "none"])
def test_modules_flag_none(fresh: Paths, value: str) -> None:
    assert main(["setup", "--modules", "wifi"]) == 0
    assert main(["setup", "--modules", value]) == 0
    assert lockfile.load(fresh.lock).selected == []
    assert "(os" not in fresh.src_modules_h.read_text()


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
    monkeypatch.setattr(fetch, "head_commit", lambda path: "c0ffee" if path.is_dir() else "-")
    monkeypatch.setattr(fetch, "git", lambda *a, **k: tags if a[0] == "ls-remote" else "")
    return clones


def test_git_sources_and_skip_when_recorded(fresh: Paths, git_sources: list[tuple[str, str]], tmp_path: Path) -> None:
    assert main(["setup", "--modules", "wifi"]) == 0
    assert git_sources == [("https://github.com/xewe-labs/xewe-os-core", "1.0.0"),
                           ("https://github.com/xewe-labs/xewe-os-modules", "v1.0.0")]
    cfg = config.load(fresh)
    assert cfg is not None and cfg.installed["core"] == {
        "ref": "1.0.0", "commit": "c0ffee", "source": "https://github.com/xewe-labs/xewe-os-core"}
    # the modules repo is a shared checkout per ref outside the project
    shared = tmp_path / "xewe-home" / "build-tools" / "sources" / "xewe-os-modules" / "v1.0.0"
    assert fresh.modules_checkout("v1.0.0") == shared and (shared / "modules" / "wifi").is_dir()
    assert cfg.paths["modules"] == str(shared)
    assert cfg.installed["modules"] == {
        "ref": "v1.0.0", "commit": "c0ffee", "source": "https://github.com/xewe-labs/xewe-os-modules"}
    assert sorted(d.name for d in fresh.build.iterdir()) == [".gitignore", "config", "libraries", "modules"]
    git_sources.clear()
    assert main(["setup"]) == 0
    assert git_sources == []
    # a second project on the same machine and ref reuses the checkout
    other = write_project(tmp_path / "other", selected='["wifi"]')
    assert main(["--project", str(other.root), "setup"]) == 0
    assert git_sources == [("https://github.com/xewe-labs/xewe-os-core", "1.0.0")]
    assert (other.modules / "src" / "Wifi").is_dir()
    git_sources.clear()
    assert main(["setup", "--force"]) == 0
    assert len(git_sources) == 2


def test_modules_branch_follows_remote_head(fresh: Paths, git_sources: list[tuple[str, str]],
                                            monkeypatch: pytest.MonkeyPatch) -> None:
    fresh.lock.write_text(fresh.lock.read_text().replace('ref = "v1.0.0"', 'ref = "feature/x"'))
    followed: list[tuple[Path, str]] = []
    monkeypatch.setattr(fetch, "current_branch", lambda path: "feature/x")
    monkeypatch.setattr(fetch, "follow_branch", lambda path, ref: followed.append((path, ref)) or "beef")
    assert main(["setup", "--modules", "wifi"]) == 0
    assert ("https://github.com/xewe-labs/xewe-os-modules", "feature/x") in git_sources and followed == []
    assert fresh.modules_checkout("feature/x").name == "feature_x"
    git_sources.clear()
    assert main(["setup"]) == 0
    assert git_sources == [] and followed == [(fresh.modules_checkout("feature/x"), "feature/x")]
    assert config.load(fresh).installed["modules"]["commit"] == "beef"


def test_modules_checkout_of_another_repo_is_recloned(fresh: Paths, git_sources: list[tuple[str, str]],
                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    assert main(["setup", "--modules", "wifi"]) == 0
    git_sources.clear()
    monkeypatch.setattr(fetch, "remote_url", lambda path: "https://github.com/example/fork")
    assert main(["setup"]) == 0
    assert git_sources == [("https://github.com/xewe-labs/xewe-os-modules", "v1.0.0")]


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
    assert config.load(fresh).paths["modules"] == str(fresh.root.parent / "modules-src")  # catalogue read there
    assert (fresh.libraries / "FastLED").is_dir()
    rec = config.load(fresh).installed["libraries"]["FastLED"]
    assert rec["ref"] == "3.10.3" and rec["source"] == FASTLED and rec["origin"] == "modules catalogue"
    assert lockfile.load(fresh.lock).libraries == {}  # the catalogue never edits the lock
    capsys.readouterr()
    assert main(["manifest", "show"]) == 0
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
    assert "library FastLED: from xewe.toml (3.9.0)" in caplog.text
    assert "from modules catalogue" not in caplog.text
    assert config.load(fresh).installed["libraries"]["FastLED"]["origin"] == "xewe.toml"
    capsys.readouterr()
    assert main(["manifest", "show", "--json"]) == 0
    import json

    row = next(r for r in json.loads(capsys.readouterr().out) if r["name"] == "libraries.FastLED")
    assert row["manifest"] == "3.9.0" and row["origin"] == "xewe.toml" and not row["drift"]


def test_unselected_module_libraries_are_not_installed(fresh: Paths, led_source: list[tuple[str, str]]) -> None:
    assert main(["setup", "--modules", "wifi"]) == 0
    assert led_source == []
    assert config.load(fresh).installed["libraries"] == {}


def test_library_missing_from_catalogue_warns(fresh: Paths, led_source: list[tuple[str, str]], tmp_path: Path,
                                             caplog: pytest.LogCaptureFixture) -> None:
    (tmp_path / "modules-src" / "libraries.toml").unlink()
    assert main(["setup", "--modules", "led-strip"]) == 0
    assert led_source == []
    assert "library FastLED (needed by module led-strip) is neither in xewe.toml [libraries] nor in the " \
           "modules libraries.toml; not installed" in caplog.text


def test_bad_catalogue_fails(fresh: Paths, led_source: list[tuple[str, str]], tmp_path: Path) -> None:
    (tmp_path / "modules-src" / "libraries.toml").write_text('[FastLED]\nrepo = "x"\n')
    assert main(["setup", "--modules", "led-strip"]) == 1


# --- the shared toolchain (~/.xewe-os/build-tools; XEWE_HOME in the tests)


@pytest.fixture
def cli_release(fresh: Paths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, Path]]:
    """No XEWE_ARDUINO_CLI: setup downloads arduino-cli; the "release" is a tar.gz of the fake cli.
    Returns the list of downloads."""
    import hashlib
    import shutil
    import tarfile

    from conftest import FAKES

    monkeypatch.delenv("XEWE_ARDUINO_CLI")
    archive = tmp_path / "release" / "arduino-cli_1.5.1_Linux_64bit.tar.gz"
    archive.parent.mkdir()
    with tarfile.open(archive, "w:gz") as tf:
        tf.add(FAKES / "arduino-cli", arcname="arduino-cli")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    downloads: list[tuple[str, Path]] = []

    def download(url: str, dest: Path, sha256: str | None = None, timeout: float = 60) -> Path:
        assert sha256 == digest
        downloads.append((url, dest))
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(archive, dest)
        return dest

    monkeypatch.setattr(fetch, "arduino_cli_platform", lambda: ("Linux_64bit", "tar.gz"))
    monkeypatch.setattr(fetch, "fetch_text", lambda url, timeout=60: f"{digest}  {archive.name}\n")
    monkeypatch.setattr(fetch, "download", download)
    return downloads


def test_second_project_reuses_shared_toolchain(fresh: Paths, tmp_path: Path, fake_cli,
                                                cli_release: list[tuple[str, Path]]) -> None:
    tools = tmp_path / "xewe-home" / "build-tools"
    assert main(["setup", "--modules", "wifi"]) == 0
    cli = tools / "bin" / "arduino-cli-1.5.1"
    assert cli.is_file() and cli_release == [
        ("https://github.com/arduino/arduino-cli/releases/download/v1.5.1/arduino-cli_1.5.1_Linux_64bit.tar.gz",
         tools / "downloads" / "arduino-cli" / "arduino-cli_1.5.1_Linux_64bit.tar.gz")]
    assert len(_installs(fake_cli())) == 1
    assert config.load(fresh).paths["arduino_cli"] == str(cli)

    second = write_project(tmp_path / "second", selected="[]")
    assert main(["--project", str(second.root), "setup", "--modules", "scheduler"]) == 0
    assert len(cli_release) == 1  # no second cli download
    assert len(_installs(fake_cli())) == 1  # no second core install
    cfg = config.load(second)
    assert cfg is not None and cfg.paths["arduino_cli"] == str(cli)
    assert cfg.paths["arduino_data"] == str(tools / "arduino15")
    assert not (second.build / "arduino15").exists() and not (second.build / "bin").exists()
    assert main(["--project", str(second.root), "build", "--chip", "c3"]) == 0
    compile_call = fake_cli()[-1]
    assert compile_call["argv"][0] == "compile" and compile_call["env"]["ARDUINO_DIRECTORIES_DATA"] == str(tools / "arduino15")
    assert (second.out_dir("c3") / "2.0.15-c3-second.bin").is_file()


def test_xewe_home_override(fresh: Paths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_cli,
                            cli_release: list[tuple[str, Path]]) -> None:
    home = tmp_path / "ci-home"
    monkeypatch.setenv("XEWE_HOME", str(home))
    assert fresh.build_tools == home / "build-tools"
    assert main(["setup", "--modules", "wifi"]) == 0
    assert (home / "build-tools" / "bin" / "arduino-cli-1.5.1").is_file()
    assert (home / "build-tools" / "arduino15" / "fake-core.json").is_file()
    assert not (tmp_path / "xewe-home").exists()
    assert config.load(fresh).paths["arduino_data"] == str(home / "build-tools" / "arduino15")
    # a recorded shared default follows XEWE_HOME on the next setup (an explicit --arduino-data sticks)
    other = tmp_path / "other-home"
    monkeypatch.setenv("XEWE_HOME", str(other))
    assert main(["setup"]) == 0
    assert config.load(fresh).paths["arduino_data"] == str(other / "build-tools" / "arduino15")
    assert len(cli_release) == 2 and len(_installs(fake_cli())) == 2


def test_home_defaults_to_dot_xewe_os(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("XEWE_HOME")
    monkeypatch.setenv("HOME", str(tmp_path / "user"))
    p = Paths(tmp_path / "proj")
    assert p.build_tools == tmp_path / "user" / ".xewe-os" / "build-tools"
    assert p.bin == p.build_tools / "bin" and p.downloads == p.build_tools / "downloads"
    assert arduino.default_cli(p).name == f"arduino-cli-{setup.pins.ARDUINO_CLI_VERSION}"
    assert not (tmp_path / "user").exists()  # resolving paths creates nothing


def test_toolchain_lock_is_exclusive(tmp_path: Path) -> None:
    import fcntl

    p = Paths(tmp_path / "proj")
    with arduino.toolchain_lock(p):
        assert p.toolchain_lock.is_file()
        with p.toolchain_lock.open("a") as other, pytest.raises(BlockingIOError):
            fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
    with p.toolchain_lock.open("a") as other:
        fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)  # released


def test_tools_branch_ref_is_recorded(fresh: Paths, monkeypatch: pytest.MonkeyPatch) -> None:
    """[tools] ref may be a branch (setup.sh follows its remote head); setup records the ref and
    the commit of build/tools. setup.sh itself has no shell test in this suite."""
    fresh.lock.write_text(fresh.lock.read_text().replace('ref = "v0.1.1"', 'ref = "main"'))
    fresh.tools_checkout.mkdir(parents=True)
    commits: list[Path] = []
    real = fetch.head_commit
    monkeypatch.setattr(fetch, "head_commit", lambda path: commits.append(path) or ("b4a2c" if path == fresh.tools_checkout else real(path)))
    assert main(["setup", "--modules", "wifi"]) == 0
    cfg = config.load(fresh)
    assert cfg is not None and cfg.installed["tools"] == {
        "ref": "main", "commit": "b4a2c", "source": "https://github.com/xewe-labs/xewe-os-tools"}
    assert fresh.tools_checkout in commits and fresh.venv == fresh.root / "build" / "tools" / ".venv"


def test_bootstrap_scripts_use_new_layout() -> None:
    import subprocess

    scripts = REPO / "scripts"
    for name in ("setup.sh", "run.sh"):
        assert subprocess.run(["bash", "-n", str(scripts / name)]).returncode == 0
    boot = (scripts / "setup.sh").read_text()
    assert 'VENV="${BUILD}/tools/.venv"' in boot and 'SRC="${BUILD}/tools"' in boot
    assert 'reset --quiet --hard "origin/${TRACK}"' in boot  # branch refs follow the remote head
    # `latest`: the default branch from ls-remote --symref (else main), then followed like a branch
    assert '[[ "${TOOLS_REF}" == "latest" ]]' in boot and 'git ls-remote --symref "${TOOLS_REPO}" HEAD' in boot
    assert 'TRACK="${TRACK:-main}"' in boot and '--branch "${TRACK}"' in boot
    assert 'echo "tools: latest -> ${TRACK}@' in boot
    assert "build/tools/.venv/bin/python" in (scripts / "run.sh").read_text()


# --- the ref `latest`: the default branch head, followed on every setup

HEAD_SHA = "f15739255f78b21d7fb5b25f378a3f072ed61794"
MOVED_SHA = "0123456789abcdef0123456789abcdef01234567"


@pytest.fixture
def latest_remote(fresh: Paths, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """xewe.toml tracks `latest` for core, modules, tools and a FastLED library; git is faked: ls-remote
    answers main@<state['sha']>, clone/follow_branch move the fake checkout to it and are recorded."""
    monkeypatch.delenv("XEWE_CORE_SOURCE")
    monkeypatch.delenv("XEWE_MODULES_SOURCE")
    fresh.lock.write_text(
        fresh.lock.read_text().replace('ref = "1.0.0"', 'ref = "latest"').replace('ref = "v1.0.0"', 'ref = "latest"')
        .replace('ref = "v0.1.1"', 'ref = "latest"')
        + 'FastLED = { repo = "https://github.com/FastLED/FastLED", ref = "latest" }\n'
    )
    state: dict[str, Any] = {"sha": HEAD_SHA, "clones": [], "follows": [], "ls": [], "heads": {}}

    def git(*a: str, **k: Any) -> str:
        if a[:2] == ("ls-remote", "--symref"):
            state["ls"].append(a[2])
            return f"ref: refs/heads/main\tHEAD\n{state['sha']}\tHEAD\n"
        return ""

    def clone(repo: str, ref: str, dest: Path) -> str:
        state["clones"].append((repo, ref))
        if dest.exists():
            import shutil

            shutil.rmtree(dest)
        if "modules" in repo:
            make_modules_checkout(dest, "modules")
        else:
            dest.mkdir(parents=True)
        state["heads"][dest] = state["sha"]
        return state["sha"]

    def follow(path: Path, branch: str) -> str:
        state["follows"].append((path.name, branch))
        state["heads"][path] = state["sha"]
        return state["sha"]

    monkeypatch.setattr(fetch, "git", git)
    monkeypatch.setattr(fetch, "clone", clone)
    monkeypatch.setattr(fetch, "follow_branch", follow)
    monkeypatch.setattr(fetch, "head_commit", lambda path: state["heads"].get(path, "-"))
    monkeypatch.setattr(fetch, "current_branch", lambda path: "main" if path in state["heads"] else None)
    return state


def test_latest_refs_follow_the_default_branch(fresh: Paths, latest_remote: dict[str, Any],
                                               caplog: pytest.LogCaptureFixture,
                                               capsys: pytest.CaptureFixture[str]) -> None:
    fresh.tools_checkout.mkdir(parents=True)
    latest_remote["heads"][fresh.tools_checkout] = HEAD_SHA  # setup.sh checked out the tools head
    caplog.set_level("INFO", logger="xewe")
    assert main(["setup", "--modules", "wifi"]) == 0
    core = "https://github.com/xewe-labs/xewe-os-core"
    mods = "https://github.com/xewe-labs/xewe-os-modules"
    assert latest_remote["clones"] == [(core, "main"), (mods, "main"), (FASTLED, "main")]
    assert sorted(latest_remote["ls"]) == sorted([core, mods, FASTLED])  # tools: setup.sh resolved it
    cfg = config.load(fresh)
    assert cfg is not None
    for name, repo in (("core", core), ("modules", mods), ("tools", "https://github.com/xewe-labs/xewe-os-tools")):
        assert cfg.installed[name] == {"ref": "latest", "branch": "main", "commit": HEAD_SHA, "source": repo}
    assert cfg.installed["libraries"]["FastLED"]["ref"] == "latest"
    assert cfg.installed["libraries"]["FastLED"]["commit"] == HEAD_SHA
    assert cfg.paths["modules"] == str(fresh.modules_checkout("latest"))
    assert "core: latest -> main@f157392" in caplog.text and "tools: latest -> main@f157392" in caplog.text
    assert "core latest (main@f157392)" in capsys.readouterr().out
    assert lockfile.load(fresh.lock).core.ref == "latest"  # the manifest keeps `latest`
    # nothing moved: no clone, no fetch
    latest_remote["clones"].clear()
    assert main(["setup"]) == 0
    assert latest_remote["clones"] == [] and latest_remote["follows"] == []
    # the owner pushed: every latest checkout is fetched and reset to the new head
    latest_remote["sha"] = MOVED_SHA
    assert main(["setup"]) == 0
    assert latest_remote["clones"] == []
    assert sorted(latest_remote["follows"]) == [("FastLED", "main"), ("XeWeCore", "main"), ("latest", "main")]
    cfg = config.load(fresh)
    assert cfg is not None and cfg.installed["core"]["commit"] == MOVED_SHA
    assert cfg.installed["modules"]["commit"] == MOVED_SHA


def test_latest_local_source_wins(fresh: Paths, latest_remote: dict[str, Any], tmp_path: Path,
                                  monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XEWE_CORE_SOURCE", str(tmp_path / "core-src"))
    monkeypatch.setenv("XEWE_MODULES_SOURCE", str(tmp_path / "modules-src"))
    assert main(["setup", "--modules", "wifi"]) == 0
    assert latest_remote["clones"] == [] or all("FastLED" in r for r, _ in latest_remote["clones"])
    assert "https://github.com/xewe-labs/xewe-os-core" not in latest_remote["ls"]
    cfg = config.load(fresh)
    assert cfg is not None and cfg.installed["core"]["source"].startswith("local:")
    assert cfg.installed["core"]["ref"] == "latest" and cfg.installed["modules"]["source"].startswith("local:")


def test_latest_offline_keeps_installed_checkout(fresh: Paths, latest_remote: dict[str, Any],
                                                 monkeypatch: pytest.MonkeyPatch,
                                                 caplog: pytest.LogCaptureFixture) -> None:
    assert main(["setup", "--modules", "wifi"]) == 0
    latest_remote["clones"].clear()
    offline_git = fetch.git

    def git(*a: str, **k: Any) -> str:
        if a[0] == "ls-remote":
            raise fetch.XeWeError("git ls-remote failed: offline")
        return offline_git(*a, **k)

    monkeypatch.setattr(fetch, "git", git)
    assert main(["setup"]) == 0
    assert latest_remote["clones"] == [] and latest_remote["follows"] == []
    assert "cannot resolve latest" in caplog.text
    assert config.load(fresh).installed["core"]["commit"] == HEAD_SHA
