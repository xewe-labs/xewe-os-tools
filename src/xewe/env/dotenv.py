"""Settings and credentials from a dotenv file (doc/spec.md §14). Stdlib only.

Which file, first found wins:

1. ``--env FILE`` (``xewe provision --from FILE`` is an alias), else the ``XEWE_ENV`` variable;
   a file named this way must exist (exit 2 otherwise);
2. ``.env`` in the project directory (the harness: the nearest ancestor with ``xewe.toml``);
3. ``.env`` in the xewe-os-tools source checkout: the ``local:<path>`` source that setup recorded
   for tools in ``build/config/build_config.toml``, else the checkout this package runs from when it is
   installed from a path (``src/xewe/env/dotenv.py``, three levels below a ``pyproject.toml``).

No file is not an error. Lines are ``KEY=VALUE``; ``#`` comments, blank lines, an ``export ``
prefix and single or double quotes around the value are allowed; nothing is interpolated. Only
``XEWE_*`` keys with a non-empty value are applied (an empty value counts as unset), and only
when the variable is not already set: the real environment
wins over the file, flags win over both. Values are never logged; errors name the file, line and
key only.

``xewe setup`` writes a commented skeleton (``skeleton()``) into a project that has no file yet.
"""

from __future__ import annotations

import os
import tomllib
from collections.abc import Iterable, Mapping, MutableMapping
from pathlib import Path

from xewe.env.project import Paths
from xewe.report import EXIT_USAGE, XeWeError, log

FILENAME = ".env"
ENV_VAR = "XEWE_ENV"
PREFIX = "XEWE_"
KEYS: dict[str, str] = {
    "XEWE_DEVICE_NAME": "device name the first boot asks for; first provision: optional "
                        "(default: [project] name in xewe.toml, else the folder name)",
    "XEWE_WIFI_SSID": "Wi-Fi network name; first provision: needed when the wifi module is enabled",
    "XEWE_WIFI_PASSWORD": "Wi-Fi password, masked in all output; first provision: needed when the wifi "
                          "module is enabled",
    "XEWE_TIMEZONE": "GMT+HH:MM, set instead of the detected timezone; first provision: optional "
                     "(empty accepts the detected one)",
    "XEWE_PROVISION_MODULES": "modules to enable at their prompt: all, none or a comma list; first "
                              "provision: optional (default all)",
    "XEWE_TEST_BUTTONS_PIN": "free GPIO for the buttons module's board test (skipped when empty); "
                             "first provision: not used",
    "XEWE_TEST_PINS_ADC_PIN": "ADC GPIO the pins module's board test reads (default 1); first "
                              "provision: not used",
    "XEWE_PORT": "serial port of the board (default: detected); first provision: optional",
    "XEWE_CHIP": "chip of the board, c3, c6 or s3 (default: detected); first provision: optional",
}
"""The keys the skeleton lists, each with its comment line. Run ./setup.sh and fill in the dotenv file."""
OTHER_KEYS = (
    "XEWE_REQUIRE_BOARD", "XEWE_ARDUINO_CLI", "XEWE_ESPTOOL", "XEWE_ARDUINO_DATA", "XEWE_CACHE",
    "XEWE_CORE_SOURCE", "XEWE_MODULES_SOURCE", "XEWE_TOOLS_SOURCE",
)
"""Other variables the tools read; allowed in the file too."""


def parse(text: str, source: str = "<text>") -> dict[str, str]:
    """``KEY=VALUE`` lines → dict (later lines win). Errors never contain a value."""
    out: dict[str, str] = {}
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, sep, value = line.partition("=")
        key = key.strip()
        if not sep or not key or not key.replace("_", "").isalnum():
            raise XeWeError(f"{source}:{n}: expected KEY=VALUE", EXIT_USAGE)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        out[key] = value
    return out


def load(paths: Iterable[Path]) -> dict[str, str]:
    """Merge the files that exist (an earlier file wins on a key); a missing file is skipped."""
    out: dict[str, str] = {}
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise XeWeError(f"{path}: cannot read ({exc.strerror})", EXIT_USAGE) from None
        for key, value in parse(text, str(path)).items():
            out.setdefault(key, value)
    return out


def apply(values: Mapping[str, str], environ: MutableMapping[str, str] | None = None) -> list[str]:
    """Set each key that is not already set (the real environment wins); return the keys set."""
    env = os.environ if environ is None else environ
    applied = []
    for key, value in values.items():
        if key not in env:
            env[key] = value
            applied.append(key)
    return applied


def package_checkout(module_file: Path | None = None) -> Path | None:
    """The source checkout this package runs from (``<checkout>/src/xewe/env/dotenv.py``), if any."""
    root = Path(module_file or __file__).resolve().parents[3]
    return root if (root / "pyproject.toml").is_file() else None


def tools_checkout(project_root: Path | None) -> Path | None:
    """The tools source checkout: setup's ``local:<path>`` record, else ``package_checkout()``."""
    if project_root is not None:
        try:
            data = tomllib.loads(Paths(project_root).build_config.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError):
            data = {}
        source = str(data.get("installed", {}).get("tools", {}).get("source", ""))
        if source.startswith("local:"):
            return Path(source[len("local:"):])
    return package_checkout()


def find(project_root: Path | None, explicit: str | Path | None = None,
         environ: Mapping[str, str] | None = None) -> Path | None:
    """The dotenv file to use (see the module docstring), or None."""
    env = os.environ if environ is None else environ
    given = explicit or env.get(ENV_VAR)
    if given:
        path = Path(given).expanduser()
        if not path.is_file():
            how = "--env" if explicit else ENV_VAR
            raise XeWeError(f"{how} {path}: no such file", EXIT_USAGE)
        return path
    candidates = [project_root / FILENAME] if project_root is not None else []
    tools = tools_checkout(project_root)
    if tools is not None:
        candidates.append(tools / FILENAME)
    return next((c for c in candidates if c.is_file()), None)


def load_settings(project_root: Path | None, explicit: str | Path | None = None,
                  environ: MutableMapping[str, str] | None = None) -> Path | None:
    """Find the file, apply its ``XEWE_*`` keys; log the path and key count only. Returns the path."""
    env = os.environ if environ is None else environ
    path = find(project_root, explicit, env)
    if path is None:
        log.debug("no %s file (project, tools checkout) and no --env/%s", FILENAME, ENV_VAR)
        return None
    values = load([path])
    known = set(KEYS) | set(OTHER_KEYS)
    for key in sorted(values):
        if key.startswith(PREFIX) and key not in known:
            log.warning("%s: unknown key %s (known: %s)", path, key, ", ".join(KEYS))
    ours = {k: v for k, v in values.items() if k.startswith(PREFIX) and k != ENV_VAR and v != ""}
    apply(ours, env)
    log.info("loaded %d keys from %s", len(ours), path)
    return path


SKELETON_HEADER = """\
# Settings and credentials for xewe: provisioning, board tests, board selection.
# Never commit this file. ./setup.sh writes it when it is missing and never overwrites it.
# An empty value counts as unset; the real environment wins over this file.
"""


def skeleton() -> str:
    """The commented dotenv file ``xewe setup`` writes: one comment line and one empty key per ``KEYS`` entry."""
    return SKELETON_HEADER + "".join(f"\n# {help_}\n{key}=\n" for key, help_ in KEYS.items())


def write_skeleton(project_root: Path) -> bool:
    """Write ``skeleton()`` to the project's dotenv file (mode 600) unless one exists; True when written."""
    path = project_root / FILENAME
    if path.exists() or path.is_symlink():
        return False
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(skeleton())
    log.info("wrote %s: fill in the values the first provision needs", FILENAME)
    return True
