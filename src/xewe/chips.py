"""Supported chips (D1): FQBN board, chip family for the web-flasher manifest, esptool id."""

from __future__ import annotations

from dataclasses import dataclass

from xewe.report import EXIT_USAGE, XeweError

BOARD_OPTIONS = (
    "CDCOnBoot=cdc,CPUFreq=160,DebugLevel=none,EraseFlash=all,FlashMode=qio,"
    "FlashSize=4M,PartitionScheme=no_ota,UploadSpeed=921600"
)


@dataclass(frozen=True)
class Chip:
    """One supported chip."""

    name: str
    board: str
    family: str
    esptool_id: str

    @property
    def fqbn(self) -> str:
        """Full FQBN with the fixed board options."""
        return f"esp32:esp32:{self.board}:{BOARD_OPTIONS}"


CHIPS: dict[str, Chip] = {
    "c3": Chip("c3", "esp32c3", "ESP32-C3", "esp32c3"),
    "c6": Chip("c6", "esp32c6", "ESP32-C6", "esp32c6"),
    "s3": Chip("s3", "esp32s3", "ESP32-S3", "esp32s3"),
}

ALL_CHIPS: tuple[str, ...] = ("c3", "c6", "s3")
DEFAULT_CHIP = "c3"


def get(name: str) -> Chip:
    """Return the chip called ``name`` or raise a usage error."""
    try:
        return CHIPS[name]
    except KeyError:
        raise XeweError(f"unknown chip '{name}' (expected c3, c6 or s3)", EXIT_USAGE) from None
