"""Versions and URLs shipped with this release of the tools."""

ARDUINO_CLI_VERSION = "1.5.1"
ESP32_CORE_VERSION = "3.3.12"
ESP32_CORE_FALLBACK_VERSION = "3.3.7"  # SPEC O1: use if a gate compile fails on 3.3.12

ESP32_INDEX_URL = "https://espressif.github.io/arduino-esp32/package_esp32_index.json"
ARDUINO_CLI_URL = (
    "https://github.com/arduino/arduino-cli/releases/download/v{version}/"
    "arduino-cli_{version}_{platform}.{ext}"
)
ARDUINO_CLI_CHECKSUMS_URL = (
    "https://github.com/arduino/arduino-cli/releases/download/v{version}/{version}-checksums.txt"
)

DEFAULT_CORE_REPO = "https://github.com/xewe-labs/xewe-os-core"
DEFAULT_MODULES_REPO = "https://github.com/xewe-labs/xewe-os-modules"
DEFAULT_TOOLS_REPO = "https://github.com/xewe-labs/xewe-os-tools"
DEFAULT_CORE_REF = "1.0.0"
DEFAULT_MODULES_REF = "v1.0.0"
DEFAULT_TOOLS_REF = "v0.1.0"

MIN_FREE_DISK_BYTES = 6 * 1024**3
