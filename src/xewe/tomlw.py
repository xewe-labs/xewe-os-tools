"""Minimal TOML writer for the files xewe generates (``tomllib`` reads; the stdlib cannot write).

Layout rules: top-level scalars first, then one ``[table]`` per top-level dict and one
``[[table]]`` per element of a top-level list of dicts. Dicts nested inside a table are written
as inline tables. Supported values: str, bool, int, float, list of those, dict.
"""

from __future__ import annotations

import re
from typing import Any

_BARE_KEY = re.compile(r"^[A-Za-z0-9_-]+$")


def _key(key: str) -> str:
    return key if _BARE_KEY.match(key) else _string(key)


def _string(value: str) -> str:
    out = ['"']
    for ch in value:
        if ch == '"':
            out.append('\\"')
        elif ch == "\\":
            out.append("\\\\")
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\t":
            out.append("\\t")
        elif ord(ch) < 0x20 or ord(ch) == 0x7F:
            out.append(f"\\u{ord(ch):04x}")
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def value(v: Any) -> str:
    """Render one TOML value (inline form)."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return repr(v)
    if isinstance(v, str):
        return _string(v)
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(value(x) for x in v) + "]"
    if isinstance(v, dict):
        if not v:
            return "{}"
        return "{ " + ", ".join(f"{_key(k)} = {value(x)}" for k, x in v.items()) + " }"
    raise TypeError(f"cannot write {type(v).__name__} to TOML")


def _is_table_array(v: Any) -> bool:
    return isinstance(v, list) and bool(v) and all(isinstance(x, dict) for x in v)


def dumps(data: dict[str, Any], header: str = "") -> str:
    """Serialise ``data``; ``header`` (comment lines, without trailing newline) goes first."""
    lines: list[str] = []
    if header:
        lines.extend(header.rstrip("\n").splitlines())
    for k, v in data.items():
        if v is None or isinstance(v, dict) or _is_table_array(v):
            continue
        lines.append(f"{_key(k)} = {value(v)}")
    for k, v in data.items():
        if isinstance(v, dict):
            lines.append("")
            lines.append(f"[{_key(k)}]")
            for sk, sv in v.items():
                if sv is not None:
                    lines.append(f"{_key(sk)} = {value(sv)}")
        elif _is_table_array(v):
            for item in v:
                lines.append("")
                lines.append(f"[[{_key(k)}]]")
                for sk, sv in item.items():
                    if sv is not None:
                        lines.append(f"{_key(sk)} = {value(sv)}")
    return "\n".join(lines) + "\n"


def header_comments(text: str) -> str:
    """Return the leading block of ``#`` comment lines of an existing TOML file."""
    out: list[str] = []
    for line in text.splitlines():
        if line.startswith("#"):
            out.append(line)
        else:
            break
    return "\n".join(out)
