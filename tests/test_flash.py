from pathlib import Path

import pytest
from serial.tools import list_ports

from xewe import boards, flash, serialio
from xewe.cli import main
from xewe.project import Paths

BIN = "build/out/c3/2.0.15-c3-xewe-os.bin"


@pytest.fixture
def one_board(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> str:
    """One Espressif board at a port that exists as a file."""
    port = tmp_path / "ttyACM0"
    port.write_text("")

    class Info:
        device, vid, pid, serial_number, description = str(port), 0x303A, 0x1001, "SN", "USB JTAG"

    monkeypatch.setattr(list_ports, "comports", lambda: [Info()])
    return str(port)


def test_no_board_compiles_and_exits_0(project: Paths, capsys: pytest.CaptureFixture[str], fake_cli) -> None:
    assert main(["flash"]) == 0
    out = capsys.readouterr().out
    assert f"compiled, not run: no board attached (c3, {BIN})" in out
    assert (project.root / BIN).is_file()
    assert main(["flash"]) == 0
    assert f"build  c3  up to date ({BIN})" in capsys.readouterr().out


def test_no_board_require_board_exits_4(project: Paths, capsys: pytest.CaptureFixture[str], monkeypatch) -> None:
    assert main(["flash", "--require-board"]) == 4
    captured = capsys.readouterr()
    assert "compiled, not run: no board attached" in captured.out
    assert "error: --require-board: no board attached" in captured.err
    monkeypatch.setenv("XEWE_REQUIRE_BOARD", "1")
    assert main(["flash"]) == 4


def test_esptool_argv(project: Paths, one_board: str, fake_esptool, capsys: pytest.CaptureFixture[str]) -> None:
    project.boards_toml.write_text('schema = 1\n[[board]]\nport = "x"\nserial_number = "SN"\nchip = "c3"\n')
    assert main(["flash"]) == 0
    calls = fake_esptool()
    assert calls[-1] == ["--chip", "esp32c3", "--port", one_board, "--baud", "921600", "--before", "default-reset",
                         "--after", "hard-reset", "write-flash", "0x0", str(project.root / BIN)]
    assert "flashed  c3" in capsys.readouterr().out


def test_erase_and_probe(project: Paths, one_board: str, fake_esptool, monkeypatch: pytest.MonkeyPatch,
                         tmp_path: Path) -> None:
    real_wait = flash.wait_for_port

    def wait(port: str, exists, **kw) -> None:  # log into the esptool call log to see the order
        with (tmp_path / "esptool.jsonl").open("a") as f:
            f.write(f'["WAIT", "{port}"]\n')
        real_wait(port, exists, **kw)

    monkeypatch.setattr(flash, "wait_for_port", wait)
    assert main(["flash", "--erase"]) == 0
    calls = fake_esptool()
    assert "chip-id" in calls[0]
    assert calls[1] == ["--chip", "esp32c3", "--port", one_board, "erase-flash"]
    assert calls[2] == ["WAIT", one_board]  # port back and stable before write-flash
    assert "write-flash" in calls[3]
    assert calls[4] == ["WAIT", one_board]
    assert len(calls) == 5


def test_port_not_back_after_flash_exit_4(project: Paths, one_board: str, fake_esptool,
                                          monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    real = serialio.wait_for_port
    monkeypatch.setattr(flash, "wait_for_port", lambda port, exists: real(port, lambda p: False, timeout=0.2))
    assert main(["flash"]) == 4
    assert f"{one_board} did not come back" in capsys.readouterr().err


def test_baud_fallback(project: Paths, one_board: str, fake_esptool, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAKE_ESPTOOL_FAIL_BAUD", "921600")
    assert main(["flash", "--chip", "c3"]) == 0
    bauds = [c[c.index("--baud") + 1] for c in fake_esptool() if "write-flash" in c]
    assert bauds == ["921600", "460800"]
    monkeypatch.setenv("FAKE_ESPTOOL_FAIL_BAUD", "460800")
    assert main(["flash", "--chip", "c3", "--baud", "460800"]) == 1


def test_chip_mismatch_exit_2(project: Paths, one_board: str, fake_esptool, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAKE_ESPTOOL_CHIP", "ESP32-S3")
    assert main(["flash", "--chip", "c3"]) == 2
    assert not any("write-flash" in c for c in fake_esptool())


def test_board_chip_selects_build(project: Paths, one_board: str, fake_esptool, monkeypatch) -> None:
    monkeypatch.setenv("FAKE_ESPTOOL_CHIP", "ESP32-C6")
    assert main(["flash"]) == 0
    assert (project.out / "c6/2.0.15-c6-xewe-os.bin").is_file()


def test_run_no_board(project: Paths, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["run", "--define", "LED_PIN=8"]) == 0
    assert "compiled, not run: no board attached (c3," in capsys.readouterr().out
    assert "LED_PIN 8" in (project.gen / "c3/XeWeBuildInfo/src/XeWeBuildInfo.h").read_text()


def test_define_change_makes_build_stale(project: Paths, capsys: pytest.CaptureFixture[str], fake_cli) -> None:
    assert main(["build", "--chip", "c3", "--define", "PROJECT_URL=https://example.test"]) == 0
    compiles = len([c for c in fake_cli() if c["argv"][0] == "compile"])
    capsys.readouterr()
    assert main(["flash"]) == 0  # built with a define, flashed without: stale
    out = capsys.readouterr().out
    assert "up to date" not in out
    assert len([c for c in fake_cli() if c["argv"][0] == "compile"]) == compiles + 1
    assert "PROJECT_URL" not in (project.gen / "c3/XeWeBuildInfo/src/XeWeBuildInfo.h").read_text()
    assert main(["flash"]) == 0
    assert "up to date" in capsys.readouterr().out
    assert main(["run", "--define", "PROJECT_URL=https://example.test"]) == 0
    assert "up to date" not in capsys.readouterr().out
    assert main(["run", "--define", "PROJECT_URL=https://example.test"]) == 0
    assert "up to date" in capsys.readouterr().out
    assert len([c for c in fake_cli() if c["argv"][0] == "compile"]) == compiles + 2


def test_port_exists_uses_device_nodes(tmp_path: Path, no_ports: None) -> None:
    node = tmp_path / "ttyX"
    node.write_text("")
    assert boards.port_exists(str(node)) and not boards.port_exists(str(tmp_path / "nope"))


def _merged_image() -> bytes:
    """A 4 MB merged image laid out like the real S3 one: bootloader, partition table at 0x8000,
    blank nvs, otadata with content, app, blank spiffs and coredump."""
    import struct

    img = bytearray(b"\xff" * 0x400000)
    img[0:4] = b"\xe9BOOT"[:4]
    table = [(1, 2, 0x9000, 0x5000, b"nvs"), (1, 0, 0xE000, 0x2000, b"otadata"),
             (0, 16, 0x10000, 0x200000, b"app0"), (1, 130, 0x210000, 0x1E0000, b"spiffs"),
             (1, 3, 0x3F0000, 0x10000, b"coredump")]
    for i, (ptype, sub, off, size, name) in enumerate(table):
        img[0x8000 + 32 * i:0x8000 + 32 * (i + 1)] = struct.pack("<2sBBII16sI", b"\xaa\x50", ptype, sub, off, size, name, 0)
    img[0xE000:0xE004] = b"\x00\x00\x00\x00"
    img[0x10000:0x10004] = b"\xe9APP"
    return bytes(img)


def test_flash_segments_skip_blank_data_partitions() -> None:
    image = _merged_image()
    pieces = flash.flash_segments(image)
    assert [(off, len(data)) for off, data in pieces] == [(0x0, 0x9000), (0xE000, 0x202000)]
    assert b"".join(data for _, data in pieces) == image[:0x9000] + image[0xE000:0x210000]
    assert flash.flash_segments(b"\xe9tiny") == [(0, b"\xe9tiny")]  # no partition table: whole image


def test_write_image_keeps_nvs(tmp_path: Path, fake_esptool, monkeypatch: pytest.MonkeyPatch) -> None:
    """Flashing without --erase must not write over NVS (it holds the provisioning)."""
    binary = tmp_path / "2.0.0-s3-xewe-os.bin"
    binary.write_bytes(_merged_image())
    monkeypatch.setattr(flash, "wait_for_port", lambda port, exists: None)
    from xewe import esptool
    flash.write_image(esptool.command(Paths(tmp_path)), boards.Board(port="/dev/x"), "s3", binary)
    (call,) = [c for c in fake_esptool() if "write-flash" in c]
    files = call[call.index("write-flash") + 1:]
    assert files[::2] == ["0x0", "0xe000"]
    assert "0x9000" not in files and "0x210000" not in files
