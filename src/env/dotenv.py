"""Settings and credentials from a dotenv file (SPEC §15). Stdlib only.

Which file, first found wins:

1. ``--env FILE`` (``xewe provision --from FILE`` is an alias), else the ``XEWE_ENV`` variable;
   a file named this way must exist (exit 2 otherwise);
2. ``.env`` in the project directory (the harness: the nearest ancestor with ``xewe.toml``);
3. ``.env`` in the xewe-os-tools source checkout: the ``local:<path>`` source that setup recorded
   for tools in ``build/config/build_config.toml``, else the checkout this package runs from when it is
   installed from a path (``src/env/dotenv.py``, two levels below a ``pyproject.toml``).

No file is not an error. Lines are ``KEY=VALUE``; ``#`` comments, blank lines, an ``export ``
prefix and single or double quotes around the value are allowed; nothing is interpolated. Only
``XEWE_*`` keys with a non-empty value are applied (an empty value counts as unset), and only
when the variable is not already set: the real environment
wins over the file, flags win over both. Values are never logged; errors name the file, line and
key only.
"""

from __future__ import annotations

import os
import tomllib
from collections.abc import Iterable, Mapping, MutableMapping
from pathlib import Path

from xewe.env.project import Paths
from xewe.report import EXIT_USAGE, XeweError, log

FILENAME = ".env"
ENV_VAR = "XEWE_ENV"
PREFIX = "XEWE_"
KEYS = (
    "XEWE_DEVICE_NAME", "XEWE_WIFI_SSID", "XEWE_WIFI_PASSWORD", "XEWE_TIMEZONE", "XEWE_PROVISION_MODULES",
    "XEWE_TEST_BUTTONS_PIN", "XEWE_TEST_PINS_ADC_PIN", "XEWE_PORT", "XEWE_CHIP",
)
"""The keys of ``.env.example``."""
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
            raise XeweError(f"{source}:{n}: expected KEY=VALUE", EXIT_USAGE)
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
            raise XeweError(f"{path}: cannot read ({exc.strerror})", EXIT_USAGE) from None
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
    """The source checkout this package runs from (``<checkout>/src/env/dotenv.py``), if any."""
    root = Path(module_file or __file__).resolve().parents[2]
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
            raise XeweError(f"{how} {path}: no such file", EXIT_USAGE)
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
