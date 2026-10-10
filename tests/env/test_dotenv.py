"""xewe.env.dotenv: parsing, real-environment precedence, which file is used."""

import os
from pathlib import Path

import pytest

from conftest import write_project
from xewe.env import dotenv
from xewe.env.dotenv import apply, find, load, load_settings, package_checkout, parse
from xewe.report import XeWeError

SECRET = "s3cr3t-value"


def test_parse_comments_quotes_export() -> None:
    text = (
        "# comment\n"
        "\n"
        "XEWE_WIFI_SSID=My Net\n"
        "  export XEWE_DEVICE_NAME = 'Kitchen Lights'  \n"
        'XEWE_WIFI_PASSWORD="pa ss#word=x"\n'
        "XEWE_TIMEZONE=\n"
        "XEWE_CHIP='s3\n"  # unbalanced quote: kept as is
        "XEWE_PORT=$HOME/dev\n"  # no interpolation
    )
    assert parse(text) == {
        "XEWE_WIFI_SSID": "My Net",
        "XEWE_DEVICE_NAME": "Kitchen Lights",
        "XEWE_WIFI_PASSWORD": "pa ss#word=x",
        "XEWE_TIMEZONE": "",
        "XEWE_CHIP": "'s3",
        "XEWE_PORT": "$HOME/dev",
    }


@pytest.mark.parametrize("text", [f"XEWE_WIFI_PASSWORD {SECRET}\n", f"=={SECRET}\n", f"BAD KEY={SECRET}\n"])
def test_parse_error_names_line_not_value(text: str) -> None:
    with pytest.raises(XeWeError) as exc:
        parse("# ok\n" + text, "/x/.env")
    assert exc.value.code == 2 and "/x/.env:2" in str(exc.value) and SECRET not in str(exc.value)


def test_load_missing_file_is_not_an_error_and_earlier_wins(tmp_path: Path) -> None:
    a, b = tmp_path / "a", tmp_path / "b"
    a.write_text("XEWE_CHIP=s3\n")
    b.write_text("XEWE_CHIP=c3\nXEWE_PORT=/dev/x\n")
    assert load([tmp_path / "missing", a, b]) == {"XEWE_CHIP": "s3", "XEWE_PORT": "/dev/x"}
    assert load([tmp_path / "missing"]) == {}


def test_apply_real_environment_wins() -> None:
    env = {"XEWE_CHIP": "c6"}
    assert apply({"XEWE_CHIP": "s3", "XEWE_PORT": "/dev/x"}, env) == ["XEWE_PORT"]
    assert env == {"XEWE_CHIP": "c6", "XEWE_PORT": "/dev/x"}


def test_apply_defaults_to_os_environ(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("XEWE_TEST_PINS_ADC_PIN", raising=False)
    apply({"XEWE_TEST_PINS_ADC_PIN": "4"})
    assert os.environ["XEWE_TEST_PINS_ADC_PIN"] == "4"


@pytest.fixture
def layout(tmp_path: Path) -> tuple[Path, Path, Path]:
    """A project whose build_config.toml records a local tools checkout, plus a third file."""
    project = write_project(tmp_path / "harness").root
    tools = tmp_path / "xewe-os-tools"
    tools.mkdir()
    (project / "build" / "config").mkdir(parents=True)
    (project / "build" / "config" / "build_config.toml").write_text(
        f'[installed.tools]\nref = "v0.1.1"\ncommit = "-"\nsource = "local:{tools}"\n')
    other = tmp_path / "elsewhere.env"
    for d, tag in ((project, "project"), (tools, "tools")):
        (d / ".env").write_text(f"XEWE_DEVICE_NAME={tag}\n")
    other.write_text("XEWE_DEVICE_NAME=other\n")
    return project, tools, other


def test_resolution_order(layout: tuple[Path, Path, Path]) -> None:
    project, tools, other = layout
    assert find(project, other, {}) == other  # --env FILE
    assert find(project, None, {"XEWE_ENV": str(other)}) == other  # XEWE_ENV
    assert find(project, other, {"XEWE_ENV": "/nope"}) == other  # the flag wins over XEWE_ENV
    assert find(project, None, {}) == project / ".env"  # the project (harness) directory
    (project / ".env").unlink()
    assert find(project, None, {}) == tools / ".env"  # the tools checkout setup recorded
    (tools / ".env").unlink()
    assert find(project, None, {}) is None  # no file: not an error


def test_explicit_missing_file_exit_2(tmp_path: Path) -> None:
    for explicit, env in ((tmp_path / "nope", {}), (None, {"XEWE_ENV": str(tmp_path / "nope")})):
        with pytest.raises(XeWeError) as exc:
            find(tmp_path, explicit, env)
        assert exc.value.code == 2 and "no such file" in str(exc.value)


def test_tools_checkout_falls_back_to_package_checkout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    checkout = tmp_path / "tools"
    (checkout / "src" / "xewe" / "env").mkdir(parents=True)
    (checkout / "pyproject.toml").write_text("[project]\n")
    module = checkout / "src" / "xewe" / "env" / "dotenv.py"
    assert package_checkout(module) == checkout
    assert package_checkout(tmp_path / "site-packages" / "xewe" / "env" / "dotenv.py") is None  # not a checkout
    (checkout / ".env").write_text("XEWE_CHIP=s3\n")
    monkeypatch.setattr(dotenv, "package_checkout", lambda module_file=None: checkout)
    project = write_project(tmp_path / "harness").root  # no build_config.toml, no .env
    assert find(project, None, {}) == checkout / ".env"


def test_load_settings_logs_count_never_values(layout: tuple[Path, Path, Path],
                                               caplog: pytest.LogCaptureFixture) -> None:
    project, _, _ = layout
    (project / ".env").write_text(
        f"XEWE_WIFI_PASSWORD={SECRET}\nXEWE_WIFI_SSID=Net\nXEWE_ENV=/ignored\nOTHER_SECRET={SECRET}\n"
        "XEWE_WIFI_PASWORD=typo\n")
    env = {"XEWE_WIFI_SSID": "Real"}
    caplog.set_level("DEBUG", logger="xewe")
    assert load_settings(project, None, env) == project / ".env"
    assert env == {"XEWE_WIFI_SSID": "Real", "XEWE_WIFI_PASSWORD": SECRET, "XEWE_WIFI_PASWORD": "typo"}
    assert f"loaded 3 keys from {project / '.env'}" in caplog.text
    assert "unknown key XEWE_WIFI_PASWORD" in caplog.text
    assert SECRET not in caplog.text and "typo" not in caplog.text


def test_skeleton_written_when_absent(tmp_path: Path) -> None:
    assert dotenv.write_skeleton(tmp_path)
    path = tmp_path / dotenv.FILENAME
    text = path.read_text()
    assert path.stat().st_mode & 0o777 == 0o600
    assert parse(text) == {key: "" for key in dotenv.KEYS}
    lines = text.splitlines()
    for key, help_ in dotenv.KEYS.items():
        i = lines.index(f"{key}=")
        assert lines[i - 1] == f"# {help_}" and "first provision" in help_


def test_skeleton_never_overwrites(tmp_path: Path) -> None:
    path = tmp_path / dotenv.FILENAME
    path.write_text("XEWE_PORT=/dev/ttyX\n")
    assert not dotenv.write_skeleton(tmp_path)
    assert path.read_text() == "XEWE_PORT=/dev/ttyX\n"


def test_skeleton_loads_as_no_settings(tmp_path: Path) -> None:
    dotenv.write_skeleton(tmp_path)
    env: dict[str, str] = {}
    load_settings(tmp_path, None, env)
    assert env == {}
