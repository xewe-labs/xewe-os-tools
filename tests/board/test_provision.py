"""``xewe provision`` against a scripted first-boot transcript (prompt texts from the firmware source)."""

import re
from pathlib import Path
from typing import Any

import pytest
import serial as pyserial

from conftest import DROP, PORT, FakeClock, FakeSerial
from xewe.board import boards, provision, serialio
from xewe.board.boards import Board
from xewe.board.provision import Settings, provision_main, resolve
from xewe.cli import main
from xewe.env.project import Paths

IN = object()
"""Transcript marker: the board waits here for one input line (and echoes it)."""
PASSWORD = "pl4ceholder-pw"


class Pause:
    """Transcript marker: the board prints nothing for ``seconds`` (fake time, see ``clock``)."""

    def __init__(self, seconds: float) -> None:
        self.seconds = seconds


# The S3's Wi-Fi prompt as printed on the real board (2026-10-08), verbatim.
REAL_NETWORKS = ("!user", "fuck villa torino", "wifihigh5", "VillaTorinoSucks", "XeWe Laptop Chiller",
                 "Emerald Sky", "XFSETUP-7148", "NoWiFi", "The Return of Pings-2.4", "NETGEAR",
                 "smitty werbenyagermanjensen", "notyomamaswifi", "Benidorm", "ATTuhhDXCb")
INVALID = "! Invalid number. Please enter a base-10 integer."
SEP = "+------------------------------------------------+"


def header(*lines: str) -> list[str]:
    return [SEP, *(f"|{line:^48}|" for line in lines), SEP]


def name_block(name: str) -> list[Any]:
    return [*header("XeWe OS", "Version 2.0.0"), *header("System Setup"),
            "Name your device (ex: Kitchen Lights):", "> ", IN,
            f'Confirm "{name}"?', "(y/n) > ", IN]


def module_block(name: str) -> list[Any]:
    return [*header(f"{name} Setup"), *header(f"Would you like to enable {name} module?", "", "description"),
            "(y/n) > ", IN]


def scan_block(networks: tuple[str, ...]) -> list[Any]:
    """``Wifi::scan`` + ``prompt_credentials`` up to the board waiting for the selection."""
    out: list[Any] = ["Scanning WiFi networks..."]
    out += [f"{i}. {ssid}" for i, ssid in enumerate(networks)]
    return out + ["", "Select network by number; or enter", "-1 to exit", "-2 to rescan",
                  "-3 to enter custom SSID", "Selection: ", "> ", IN]


def join_block(ssid: str = "HomeNet") -> list[Any]:
    return [f"Selected: '{ssid}'", "Password: ", "> ", IN, f"Joining {ssid}.....", "", f"Joined {ssid}",
            "Local ip: 192.168.1.20", "Mac: AA:BB:CC:DD:EE:FF"]


def wifi_block(networks: tuple[str, ...], custom: bool = False, ssid: str = "HomeNet") -> list[Any]:
    out: list[Any] = ["Stored WiFi credentials not found", *scan_block(networks)]
    if custom:
        out += [*scan_block(networks), "Enter custom SSID: ", "> ", IN]  # -2 rescans once, then -3
    return out + join_block(ssid)


def time_block(detected: str | None = "GMT+02:00", asks_offset: bool = False) -> list[Any]:
    out: list[Any] = [*header("Time Setup"), "Detecting Timezone......"]
    if detected is None:
        out += ["Unable to reach timezone server.", "Check your internet connection."]
    else:
        out += [f"Is your time: 2026-10-08 12:00:00 ({detected})?", "(y/n) > ", IN]
    if asks_offset or detected is None:
        out += ["Enter your timezone offset (e.g. GMT-08:00)", "For support visit:",
                "https://webbrowsertools.com/timezone/", "> ", IN, "Timezone set to GMT-08:00"]
    else:
        out += ["Timezone set"]
    return out


def finish_block(drop: bool = False) -> list[Any]:
    out: list[Any] = [*header("Scheduler Setup"), *header("Web Interface Setup"),
                      *header("Initial Setup Complete"), *header("Rebooting")]
    if drop:
        out += [DROP, DROP]
    return out + ["ESP-ROM:esp32s3-20210327", *header("System Setup Complete")]


def full(name: str = "Kitchen Lights", networks: tuple[str, ...] = ("Neighbour", "HomeNet"), custom: bool = False,
         tz_offset: bool = False, drop: bool = False, wifi: bool = True) -> list[Any]:
    out = [*name_block(name), *module_block("Buttons"), *module_block("Pins"), *module_block("Wifi")]
    if wifi:
        out += [*wifi_block(networks, custom), *module_block("Time"), *time_block(asks_offset=tz_offset)]
    return out + finish_block(drop)


class FakeBoard(FakeSerial):
    """Plays a transcript: lines up to each ``IN`` marker, then waits for one written line."""

    def __init__(self, transcript: list[Any], clock: FakeClock | None = None) -> None:
        super().__init__()
        self.clock = clock
        self.quiet_until = 0.0
        self.segments: list[list[Any]] = [[]]
        for item in transcript:
            if item is IN:
                self.segments.append([])
            else:
                self.segments[-1].append(item)
        self.queue: list[Any] = []
        self.answers: list[str] = []
        self._next_segment()

    def _next_segment(self) -> None:
        if not self.segments:
            return
        for item in self.segments.pop(0):
            self.queue.append(item if item is DROP or isinstance(item, Pause) else (item + "\r\n").encode())

    def read(self, n: int = 1) -> bytes:
        if self.clock is not None and self.clock.now < self.quiet_until:
            return b""
        if not self.rx and self.queue:
            item = self.queue.pop(0)
            if isinstance(item, Pause):
                assert self.clock is not None, "Pause needs the fake clock"
                self.quiet_until = self.clock.now + item.seconds
                return b""
            if item is DROP:
                raise pyserial.SerialException("device reports readiness to read but returned no data")
            self.rx += item
            n = max(n, len(self.rx))
        return super().read(n)

    def write(self, data: bytes) -> int:
        self.written += data
        for line in data.decode().split("\n")[:-1]:
            self.answers.append(line)
            self.queue.append((line + "\r\n").encode())  # the firmware echoes what is typed
            self._next_segment()
        return len(data)


def settings(**kw: Any) -> Settings:
    base: dict[str, Any] = {"name": "Kitchen Lights", "ssid": "HomeNet", "password": PASSWORD}
    base.update(kw)
    return Settings(**base)


def run(fake: FakeBoard, s: Settings, **kw: Any) -> int:
    return provision_main(None, lambda port: Board(PORT, "s3"), s, factory=lambda: fake, **kw)


def test_happy_path_wifi_and_time(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    fake = FakeBoard(full())
    log = tmp_path / "provision.log"
    assert run(fake, settings(), log_path=log) == 0
    assert fake.answers == ["Kitchen Lights", "y", "y", "y", "y", "1", PASSWORD, "y", "y"]
    assert fake.rts_history[-2:] == [True, False]  # reset first
    out = capsys.readouterr().out
    assert re.search(r"provisioned in \d+ s: ", out)
    assert ("name \"Kitchen Lights\"; modules buttons, pins, wifi, time; "
            "wifi \"HomeNet\" (network 1 of 2); timezone y, board detected GMT+02:00") in out
    assert PASSWORD not in out and PASSWORD not in log.read_text()
    assert out.count("********") == 2  # the typed line and the firmware's echo
    assert "> 1" in out and "Joined HomeNet" in out


def test_ssid_not_in_scan_uses_custom(capsys: pytest.CaptureFixture[str]) -> None:
    fake = FakeBoard(full(networks=("Neighbour", "Other"), custom=True))
    assert run(fake, settings()) == 0
    assert fake.answers[5:9] == ["-2", "-3", "HomeNet", PASSWORD]
    assert 'wifi "HomeNet" (custom SSID, not in the scan)' in capsys.readouterr().out


def test_timezone_configured_answers_n_and_offset(capsys: pytest.CaptureFixture[str]) -> None:
    fake = FakeBoard(full(tz_offset=True))
    assert run(fake, settings(timezone="GMT-08:00")) == 0
    assert fake.answers[-3:] == ["y", "n", "GMT-08:00"]  # time module enabled, detected time declined
    assert "timezone n, set GMT-08:00 (board detected GMT+02:00)" in capsys.readouterr().out


def test_timezone_not_detected_and_not_configured_fails(caplog: pytest.LogCaptureFixture) -> None:
    transcript = [*name_block("Kitchen Lights"), *module_block("Buttons"), *module_block("Pins"),
                  *module_block("Wifi"), *wifi_block(("HomeNet",)), *module_block("Time"),
                  *time_block(detected=None), *finish_block()]
    fake = FakeBoard(transcript)
    assert run(fake, settings()) == 1
    assert "could not detect the timezone" in caplog.text


def test_timezone_not_detected_uses_configured(capsys: pytest.CaptureFixture[str]) -> None:
    transcript = [*name_block("Kitchen Lights"), *module_block("Buttons"), *module_block("Pins"),
                  *module_block("Wifi"), *wifi_block(("HomeNet",)), *module_block("Time"),
                  *time_block(detected=None), *finish_block()]
    fake = FakeBoard(transcript)
    assert run(fake, settings(timezone="GMT-08:00")) == 0
    assert fake.answers[-1] == "GMT-08:00"


def test_modules_without_wifi() -> None:
    fake = FakeBoard(full(wifi=False))
    assert run(fake, settings(modules=frozenset({"pins"}), ssid=None, password=None)) == 0
    assert fake.answers == ["Kitchen Lights", "y", "n", "y", "n"]


def test_already_provisioned_sends_nothing(capsys: pytest.CaptureFixture[str]) -> None:
    fake = FakeBoard(["ESP-ROM:esp32s3-20210327", "Stored WiFi credentials found", *header("System Setup Complete")])
    assert run(fake, settings()) == 0
    assert bytes(fake.written) == b""
    assert "already provisioned" in capsys.readouterr().out


def test_prompt_timeout_exit_1_with_masked_tail(monkeypatch: pytest.MonkeyPatch,
                                                caplog: pytest.LogCaptureFixture) -> None:
    monkeypatch.setattr(provision, "PROMPT_TIMEOUT", 0.3)
    transcript = [*name_block("Kitchen Lights"), *module_block("Buttons"), *module_block("Pins"),
                  *module_block("Wifi"), *wifi_block(("HomeNet",))[:-5], "Joining HomeNet....."]
    fake = FakeBoard(transcript)
    assert run(fake, settings()) == 1
    assert "no prompt from the board within 0.3 s" in caplog.text
    assert "  ********" in caplog.text and "  Joining HomeNet....." in caplog.text
    assert PASSWORD not in caplog.text


def test_start_timeout_exit_1(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    monkeypatch.setattr(provision, "NUDGE_SECONDS", 0.1)
    fake = FakeBoard(["ESP-ROM:esp32s3-20210327"])
    assert run(fake, settings(), timeout=0.3) == 1
    assert "printed no first-boot prompt (e.g. 'Name your device') and no 'System Setup Complete' within 0.3 s" in caplog.text
    assert bytes(fake.written) == b"\n"  # the one nudge


def test_nudge_reprints_the_name_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prompt printed before the port opened (--no-reset): an empty line, 'n', then the real answers."""
    monkeypatch.setattr(provision, "NUDGE_SECONDS", 0.2)
    fake = FakeBoard([IN, 'Confirm ""?', "(y/n) > ", IN, "Name your device (ex: Kitchen Lights):", "> ", IN,
                      'Confirm "Kitchen Lights"?', "(y/n) > ", IN, *full()[len(name_block("x")):]])
    assert run(fake, settings(), reset=False) == 0
    assert fake.answers[:4] == ["", "n", "Kitchen Lights", "y"]
    assert True not in fake.rts_history  # --no-reset: RTS only ever set low (at open)


def test_port_drop_at_reboot() -> None:
    fake = FakeBoard(full(drop=True))
    assert run(fake, settings()) == 0
    assert fake.opens == 3  # dropped twice after "Rebooting", reopened each time


def failed_join(ssid: str = "HomeNet") -> list[Any]:
    """``Wifi::join`` timing out once with user credentials; ``connect`` then scans again."""
    return [f"Selected: '{ssid}'", "Password: ", "> ", IN, f"Joining {ssid}.....", "", f"Unable to join {ssid}",
            "Check the password", "try moving closer to router", "and restarting the router", "Retrying"]


def test_wrong_password_fails_after_one_retry(caplog: pytest.LogCaptureFixture) -> None:
    transcript = [*name_block("Kitchen Lights"), *module_block("Buttons"), *module_block("Pins"),
                  *module_block("Wifi"), "Stored WiFi credentials not found",
                  *scan_block(("HomeNet",)), *failed_join(), *scan_block(("HomeNet",)), *failed_join(),
                  *scan_block(("HomeNet",))]
    fake = FakeBoard(transcript)
    assert run(fake, settings()) == 1
    assert fake.answers[-4:] == ["0", PASSWORD, "0", PASSWORD]  # answered twice, then gave up
    assert "Wi-Fi setup failed (Unable to join)" in caplog.text and PASSWORD not in caplog.text


def test_failed_join_is_retried_once(capsys: pytest.CaptureFixture[str]) -> None:
    networks = ("Neighbour", "HomeNet")
    transcript = [*name_block("Kitchen Lights"), *module_block("Buttons"), *module_block("Pins"),
                  *module_block("Wifi"), "Stored WiFi credentials not found", *scan_block(networks),
                  *failed_join(), *scan_block(("HomeNet", "Neighbour")), *join_block(),
                  *module_block("Time"), *time_block(), *finish_block()]
    fake = FakeBoard(transcript)
    assert run(fake, settings()) == 0
    assert fake.answers == ["Kitchen Lights", "y", "y", "y", "y", "1", PASSWORD, "0", PASSWORD, "y", "y"]
    out = capsys.readouterr().out
    assert 'wifi "HomeNet" (network 0 of 2, joined after 1 failed join(s))' in out
    assert PASSWORD not in out


def test_summary_name_kept_from_board(capsys: pytest.CaptureFixture[str]) -> None:
    transcript = [*resumed(), *join_block("XeWe Laptop Chiller"), *module_block("Time"), *time_block(),
                  *finish_block()]
    assert run(FakeBoard(transcript), settings(ssid="XeWe Laptop Chiller")) == 0
    out = capsys.readouterr().out
    assert "name: kept from board; modules time;" in out and 'name "Kitchen Lights"' not in out


def level_block() -> list[Any]:
    """The template's YourModuleFull ``begin_routines_init``: ``get_uint16`` with two attempts; an
    empty line is an invalid number, the second one returns the default."""
    return ["Starting level (0-100)?", "> ", IN, INVALID, "> ", IN, "No answer: level stays 50"]


def template_first_boot(full_enabled: bool) -> list[Any]:
    """Template first boot with ``selected = []``: name, Your Module, Your Module Full (+ level prompt)."""
    out = [*name_block("Kitchen Lights"), *module_block("Your Module"), *module_block("Your Module Full")]
    if full_enabled:
        out += level_block()
    return out + [*header("Initial Setup Complete"), *header("Rebooting"), "ESP-ROM:esp32s3-20210327",
                  *header("System Setup Complete")]


def test_unlisted_module_gets_n_and_is_logged(caplog: pytest.LogCaptureFixture,
                                              capsys: pytest.CaptureFixture[str]) -> None:
    fake = FakeBoard(template_first_boot(full_enabled=False))
    s = settings(modules=frozenset({"your-module"}), ssid=None, password=None)
    with caplog.at_level("INFO", logger="xewe"):
        assert run(fake, s) == 0
    assert fake.answers == ["Kitchen Lights", "y", "y", "n"]
    assert "auto-answer n (the default) to 'Would you like to enable Your Module Full module?'" in caplog.text
    assert "declined your-module-full" in capsys.readouterr().out


def test_unknown_value_prompt_gets_enter_until_default(caplog: pytest.LogCaptureFixture,
                                                       capsys: pytest.CaptureFixture[str]) -> None:
    """XEWE_PROVISION_MODULES unset (all): Your Module Full is enabled, its level prompt gets Enter."""
    fake = FakeBoard(template_first_boot(full_enabled=True))
    with caplog.at_level("INFO", logger="xewe"):
        assert run(fake, settings(modules=None)) == 0
    assert fake.answers == ["Kitchen Lights", "y", "y", "y", "", ""]
    autos = [r.getMessage() for r in caplog.records if r.getMessage().startswith("auto-answer Enter")]
    assert len(autos) == 2 and "'Starting level (0-100)?'" in autos[0] and "Your Module Full" in autos[0]
    assert "2 prompt(s) answered with their default" in capsys.readouterr().out


def test_unknown_yn_prompt_gets_n() -> None:
    transcript = [*name_block("Kitchen Lights"), "Reset the counter?", "(y/n) > ", IN, *finish_block()]
    fake = FakeBoard(transcript)
    assert run(fake, settings(modules=frozenset(), ssid=None, password=None)) == 0
    assert fake.answers == ["Kitchen Lights", "y", "n"]


def test_unknown_prompt_that_never_ends_fails(caplog: pytest.LogCaptureFixture) -> None:
    transcript = [*name_block("Kitchen Lights"), "Pick a colour:", *(["> ", IN] * (provision.MAX_AUTO + 1))]
    assert run(FakeBoard(transcript), settings()) == 1
    assert "keeps asking for input this tool does not know" in caplog.text


# --------------------------------------------------------------------------- the real S3 Wi-Fi prompt


def test_real_network_list_selects_by_number(capsys: pytest.CaptureFixture[str]) -> None:
    transcript = [*name_block("Kitchen Lights"), *module_block("Buttons"), *module_block("Pins"),
                  *module_block("Wifi"), *wifi_block(REAL_NETWORKS, ssid="XeWe Laptop Chiller"),
                  *module_block("Time"), *time_block(), *finish_block()]
    fake = FakeBoard(transcript)
    assert run(fake, settings(ssid="XeWe Laptop Chiller")) == 0
    assert fake.answers == ["Kitchen Lights", "y", "y", "y", "y", "4", PASSWORD, "y", "y"]
    assert 'wifi "XeWe Laptop Chiller" (network 4 of 14)' in capsys.readouterr().out


def resumed(networks: tuple[str, ...] = REAL_NETWORKS) -> list[Any]:
    """A board whose earlier provisioning stopped at Wi-Fi: after the reset it skips the name and
    module prompts (stored) and goes straight to the network list (the stall seen on the S3)."""
    return ["ESP-ROM:esp32s3-20210327", *header("XeWe OS", "Version 2.0.0"), *header("System Setup"),
            *header("Buttons Setup"), *header("Pins Setup"), *header("Wifi Setup"),
            "Stored WiFi credentials not found", *scan_block(networks)]


def test_resumed_board_at_network_list_is_answered_without_nudge(clock: FakeClock) -> None:
    transcript = [*resumed(), *join_block("XeWe Laptop Chiller"), *module_block("Time"), *time_block(),
                  *finish_block()]
    fake = FakeBoard(transcript, clock)
    assert run(fake, settings(ssid="XeWe Laptop Chiller")) == 0
    assert fake.answers == ["4", PASSWORD, "y", "y"]  # no empty nudge line


def test_unlisted_ssid_rescans_once_then_custom(capsys: pytest.CaptureFixture[str]) -> None:
    transcript = [*resumed(), *scan_block(REAL_NETWORKS[:5]), "Enter custom SSID: ", "> ", IN,
                  *join_block("Hidden Net"), *module_block("Time"), *time_block(), *finish_block()]
    fake = FakeBoard(transcript)
    assert run(fake, settings(ssid="Hidden Net")) == 0
    assert fake.answers[:4] == ["-2", "-3", "Hidden Net", PASSWORD]
    assert 'wifi "Hidden Net" (custom SSID, not in the scan)' in capsys.readouterr().out


def test_ssid_found_after_rescan() -> None:
    transcript = [*resumed(REAL_NETWORKS[:3]), *scan_block(REAL_NETWORKS), *join_block("XeWe Laptop Chiller"),
                  *module_block("Time"), *time_block(), *finish_block()]
    fake = FakeBoard(transcript)
    assert run(fake, settings(ssid="XeWe Laptop Chiller")) == 0
    assert fake.answers[:3] == ["-2", "4", PASSWORD]


def test_invalid_number_answers_again() -> None:
    transcript = [*resumed(), INVALID, "> ", IN, *join_block("XeWe Laptop Chiller"), *module_block("Time"),
                  *time_block(), *finish_block()]
    fake = FakeBoard(transcript)
    assert run(fake, settings(ssid="XeWe Laptop Chiller")) == 0
    assert fake.answers[:3] == ["4", "4", PASSWORD]


def test_nudge_into_unseen_network_list_rescans(monkeypatch: pytest.MonkeyPatch) -> None:
    """--no-reset with the list printed before the port opened: the nudge's empty line gets
    '! Invalid number'; the tool cannot know the numbers, so it rescans (-2)."""
    monkeypatch.setattr(provision, "NUDGE_SECONDS", 0.2)
    transcript = [IN, INVALID, "> ", IN, *scan_block(REAL_NETWORKS), *join_block("XeWe Laptop Chiller"),
                  *module_block("Time"), *time_block(), *finish_block()]
    fake = FakeBoard(transcript)
    assert run(fake, settings(ssid="XeWe Laptop Chiller"), reset=False) == 0
    assert fake.answers[:4] == ["", "-2", "4", PASSWORD]


def test_nudge_never_fires_after_the_first_prompt(clock: FakeClock) -> None:
    """A quiet board after the first answered prompt (a slow Wi-Fi scan here) gets no empty line."""
    transcript = [*name_block("Kitchen Lights"), *module_block("Buttons"), *module_block("Pins"),
                  *module_block("Wifi"), "Stored WiFi credentials not found", Pause(provision.NUDGE_SECONDS + 5),
                  *scan_block(REAL_NETWORKS), *join_block("XeWe Laptop Chiller"), *module_block("Time"),
                  *time_block(), *finish_block()]
    fake = FakeBoard(transcript, clock)
    assert run(fake, settings(ssid="XeWe Laptop Chiller")) == 0
    assert "" not in fake.answers
    assert fake.answers == ["Kitchen Lights", "y", "y", "y", "y", "4", PASSWORD, "y", "y"]


def test_resumed_board_quiet_before_first_prompt_gets_no_nudge_after_it(clock: FakeClock) -> None:
    """The first prompt after the reset is the network list; a later silence never nudges."""
    transcript = [*resumed(), Pause(provision.NUDGE_SECONDS + 5), *join_block("XeWe Laptop Chiller"),
                  *module_block("Time"), *time_block(), *finish_block()]
    fake = FakeBoard(transcript, clock)
    assert run(fake, settings(ssid="XeWe Laptop Chiller")) == 0
    assert fake.answers == ["4", PASSWORD, "y", "y"]


# --------------------------------------------------------------------------- settings and CLI


def test_resolve_precedence() -> None:
    env = {"XEWE_DEVICE_NAME": "From Env", "XEWE_WIFI_SSID": "EnvNet", "XEWE_WIFI_PASSWORD": "env pw",
           "XEWE_TIMEZONE": "GMT-03:00", "XEWE_PROVISION_MODULES": "wifi,time"}
    s = resolve("default", env=env)
    assert (s.name, s.ssid, s.password, s.timezone, s.modules) == (
        "From Env", "EnvNet", "env pw", "GMT-03:00", frozenset({"wifi", "time"}))
    s = resolve("default", name="From Flag", modules="buttons, Web Interface", timezone="gmt+05:30", env=env)
    assert (s.name, s.timezone, s.modules) == ("From Flag", "GMT+05:30", frozenset({"buttons", "web-interface"}))
    s = resolve("default", modules="none", env={})
    assert (s.name, s.modules, s.timezone) == ("default", frozenset(), None)


@pytest.mark.parametrize("tz", ["GMT+8", "UTC+01:00", "GMT+15:00", "GMT+14:30", "GMT-08:60"])
def test_bad_timezone_exit_2(tz: str) -> None:
    with pytest.raises(provision.XeWeError) as exc:
        resolve("d", modules="none", timezone=tz, env={})
    assert exc.value.code == 2


def test_cli_missing_password_exit_2_before_port(project: Paths, monkeypatch: pytest.MonkeyPatch,
                                                 caplog: pytest.LogCaptureFixture) -> None:
    def no_select(*a: Any, **k: Any) -> None:
        raise AssertionError("board selected despite bad configuration")

    monkeypatch.setattr(boards, "select", no_select)
    monkeypatch.setenv("XEWE_WIFI_SSID", "HomeNet")
    assert main(["provision"]) == 2
    assert "XEWE_WIFI_PASSWORD is not set" in caplog.text and ".env.example" in caplog.text
    assert main(["provision", "--modules", "pins", "--timezone", "GMT+8"]) == 2


def test_cli_no_board_exit_4(project: Paths) -> None:
    assert main(["provision", "--modules", "none"]) == 4


def _fake_board_cli(monkeypatch: pytest.MonkeyPatch, fake: FakeBoard) -> list[str | None]:
    ports: list[str | None] = []

    def select(p: Paths, port: str | None = None, **k: Any) -> Board:
        ports.append(port)
        return Board(port or "/dev/ttyAUTO", "s3")

    monkeypatch.setattr(boards, "select", select)
    monkeypatch.setattr(serialio.serial, "Serial", lambda: fake)
    return ports


def test_cli_settings_from_project_dotenv_without_flags(project: Paths, monkeypatch: pytest.MonkeyPatch,
                                                         capsys: pytest.CaptureFixture[str],
                                                         caplog: pytest.LogCaptureFixture) -> None:
    """Credentials only in <project>/.env: `xewe provision` with no flags (board auto-selected)."""
    (project.root / ".env").write_text(
        f"XEWE_DEVICE_NAME='Kitchen Lights'\nXEWE_WIFI_SSID=\"XeWe Laptop Chiller\"\n"
        f"export XEWE_WIFI_PASSWORD={PASSWORD}\nXEWE_PROVISION_MODULES=all\n")
    fake = FakeBoard([*name_block("Kitchen Lights"), *module_block("Buttons"), *module_block("Pins"),
                      *module_block("Wifi"), *wifi_block(REAL_NETWORKS, ssid="XeWe Laptop Chiller"),
                      *module_block("Time"), *time_block(), *finish_block()])
    ports = _fake_board_cli(monkeypatch, fake)
    caplog.set_level("INFO", logger="xewe")
    assert main(["provision"]) == 0
    assert ports == [None]
    assert fake.answers == ["Kitchen Lights", "y", "y", "y", "y", "4", PASSWORD, "y", "y"]
    out = capsys.readouterr().out
    assert PASSWORD not in out and PASSWORD not in caplog.text
    assert f"loaded 4 keys from {project.root / '.env'}" in caplog.text


def test_cli_port_flag_with_dotenv(project: Paths, monkeypatch: pytest.MonkeyPatch) -> None:
    (project.root / ".env").write_text(f"XEWE_WIFI_SSID=HomeNet\nXEWE_WIFI_PASSWORD={PASSWORD}\n")
    fake = FakeBoard(full(name="xewe-os"))
    ports = _fake_board_cli(monkeypatch, fake)
    assert main(["provision", "--port", "/dev/ttyACM9"]) == 0
    assert ports == ["/dev/ttyACM9"] and fake.answers[0] == "xewe-os"


@pytest.mark.parametrize("flag", ["--env", "--from"])
def test_cli_env_flag_wins_over_project_dotenv(project: Paths, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
                                               flag: str) -> None:
    (project.root / ".env").write_text("XEWE_WIFI_SSID=Wrong\nXEWE_WIFI_PASSWORD=wrong\n")
    f = tmp_path / "outside-repo.env"
    f.write_text(f"XEWE_WIFI_SSID=HomeNet\nXEWE_WIFI_PASSWORD={PASSWORD}\nXEWE_DEVICE_NAME=File Name\n")
    fake = FakeBoard(full(name="Flag Name"))
    _fake_board_cli(monkeypatch, fake)
    assert main(["provision", flag, str(f), "--name", "Flag Name"]) == 0
    assert fake.answers[0] == "Flag Name" and PASSWORD in fake.answers


def test_cli_real_environment_wins_over_dotenv(project: Paths, monkeypatch: pytest.MonkeyPatch) -> None:
    (project.root / ".env").write_text(f"XEWE_WIFI_SSID=Wrong\nXEWE_WIFI_PASSWORD={PASSWORD}\n")
    monkeypatch.setenv("XEWE_WIFI_SSID", "HomeNet")
    fake = FakeBoard(full(name="xewe-os"))
    _fake_board_cli(monkeypatch, fake)
    assert main(["provision"]) == 0
    assert fake.answers[5] == "1"  # HomeNet is network 1 in full()


def test_cli_missing_env_file_exit_2(project: Paths) -> None:
    assert main(["provision", "--env", str(project.root / "nope.env")]) == 2


def test_default_name_is_project_name(project: Paths, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[Settings] = []
    monkeypatch.setattr(provision, "provision_main", lambda port, select, s, *a: seen.append(s) or 0)
    assert main(["provision", "--modules", "none"]) == 0
    assert seen[0].name == "xewe-os"  # no [project] name in the fixture lock: the folder name
