"""Supported chips: FQBN board, chip family for the web-flasher manifest, esptool id."""

from __future__ import annotations

from dataclasses import dataclass

from xewe.report import EXIT_USAGE, XeWeError

DEFAULT_PARTITION_SCHEME = "no_ota"
"""``PartitionScheme`` when ``xewe.toml`` has no ``[build] partition_scheme``."""
FLASH_SIZE = 0x400000
"""Bytes of flash the fixed ``FlashSize=4M`` option builds for; a partition scheme must fit in it."""


def board_options(scheme: str = DEFAULT_PARTITION_SCHEME) -> str:
    """The fixed board options with ``PartitionScheme=<scheme>``."""
    return (
        "CDCOnBoot=cdc,CPUFreq=160,DebugLevel=none,EraseFlash=all,FlashMode=qio,"
        f"FlashSize=4M,PartitionScheme={scheme},UploadSpeed=921600"
    )


BOARD_OPTIONS = board_options()


@dataclass(frozen=True)
class Chip:
    """One supported chip."""

    name: str
    board: str
    family: str
    esptool_id: str

    @property
    def fqbn(self) -> str:
        """Full FQBN with the fixed board options and the default partition scheme."""
        return self.fqbn_for(DEFAULT_PARTITION_SCHEME)

    def fqbn_for(self, scheme: str) -> str:
        """Full FQBN with the fixed board options and ``PartitionScheme=<scheme>``."""
        return f"esp32:esp32:{self.board}:{board_options(scheme)}"


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
        raise XeWeError(f"unknown chip '{name}' (expected c3, c6 or s3)", EXIT_USAGE) from None
