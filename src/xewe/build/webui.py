"""``xewe webui export|prompt``: a Flask web UI prototype as PROGMEM headers and a sketch (doc/webui.md).

``export`` reads ``templates/`` and ``static/`` of a Flask project and writes each ``.html``,
``.css`` and ``.js`` file as ``<name>_<ext>.h`` (one ``PROGMEM`` raw-literal array, the folder
layout kept) into ``<out>/<project>_<YYYYmmdd_HHMMSS>/``, with Jinja
``{{ url_for('static', filename='x') }}`` rewritten to ``/static/x``. ``templates/index.html`` is
served at ``/``, every other file at its own path. It copies ``app.py`` and generates
``<project>_app.ino``: the includes, one ``server.on`` GET route per file with its MIME type,
``setup()`` and ``loop()``; Wi-Fi and the API are left for the porting step, which ``prompt``
prints (``PROMPT_FILE``, package data). Other files are skipped; Flask is not needed.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from xewe.env.project import write_atomic
from xewe.report import EXIT_OK, EXIT_USAGE, XeWeError, log, result

MIME_TYPES = {".html": "text/html", ".css": "text/css", ".js": "application/javascript"}
"""The exported suffixes and the Content-Type each route sends."""
SOURCE_DIRS = ("templates", "static")
URL_FOR_RE = re.compile(r"\{\{\s*url_for\(['\"]static['\"]\s*,\s*filename=['\"](.+?)['\"]\)\s*\}\}")
PROMPT_FILE = Path(__file__).with_name("webui-prompt.txt")
"""The LLM prompt that ports ``app.py`` into the generated sketch (package data)."""


@dataclass(frozen=True)
class WebFile:
    """One exported file: its header (relative to the sketch), C array, URL and MIME type."""

    include: str
    var: str
    route: str
    mime: str


def header(var: str, content: str) -> str:
    """The ``.h`` text holding ``content`` as one PROGMEM raw string literal."""
    if not content.endswith("\n"):
        content += "\n"
    return f'#pragma once\n#include <pgmspace.h>\nstatic const char {var}[] PROGMEM = R"rawliteral(\n{content})rawliteral";\n'


def sketch(files: list[WebFile]) -> str:
    """The generated ``<project>_app.ino``: includes, one GET route per file, setup and loop."""
    lines = ["#include <WiFi.h>", "#include <WebServer.h>", "", "WebServer server(80);", ""]
    lines.append("// --- Auto-Generated Web Files ---")
    lines += [f'#include "{f.include}"' for f in files]
    lines += ["// --------------------------------", "", "void setupRoutes() {", "  // --- Auto-Generated Routes ---"]
    for f in files:
        lines += [
            f'  server.on("{f.route}", HTTP_GET, []() {{',
            '    server.sendHeader("Access-Control-Allow-Origin", "*");',
            f'    server.send_P(200, "{f.mime}", {f.var});',
            "  });",
        ]
    lines += ["}", "", "void setup() {", "  Serial.begin(115200);", "  // TODO: Setup WiFi here", ""]
    lines += ["  setupRoutes();", "  server.begin();", "}", "", "void loop() {", "  server.handleClient();", "}"]
    return "\n".join(lines) + "\n"


def export(project: Path, out: Path, stamp: str | None = None) -> Path:
    """Write the headers, ``app.py`` and the sketch into ``out/<project>_<stamp>/``; return that folder."""
    project = project.resolve()
    if not project.is_dir():
        raise XeWeError(f"{project} is not a directory", EXIT_USAGE)
    stamp = stamp or datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = out.resolve() / f"{project.name}_{stamp}"
    dest.mkdir(parents=True, exist_ok=True)
    files: list[WebFile] = []
    seen: dict[str, Path] = {}
    for top in SOURCE_DIRS:
        for src in sorted((project / top).rglob("*")):
            mime = MIME_TYPES.get(src.suffix)
            if mime is None or not src.is_file():
                continue
            rel = src.relative_to(project)
            try:
                content = src.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                log.warning("skipped (not UTF-8): %s", rel)
                continue
            content = URL_FOR_RE.sub(r"/static/\1", content)
            ext = src.suffix[1:]
            safe = src.stem.replace("-", "_").replace(".", "_")
            var = f"{safe.upper()}_{ext.upper()}"
            if var in seen:
                raise XeWeError(f"{rel} and {seen[var]} both become {var}; rename one")
            seen[var] = rel
            h = rel.with_name(f"{safe}_{ext}.h")
            write_atomic(dest / h, header(var, content))
            route = "/" if rel.as_posix() == "templates/index.html" else f"/{rel.as_posix()}"
            files.append(WebFile(h.as_posix(), var, route, mime))
            log.debug("%s -> %s (%s %s)", rel, h.as_posix(), route, mime)
    app = project / "app.py"
    if app.is_file():
        shutil.copy2(app, dest / "app.py")
    ino = dest / f"{project.name}_app.ino"
    write_atomic(ino, sketch(files))
    result(f"webui: {len(files)} headers and {ino.name}{' + app.py' if app.is_file() else ''} in {dest}")
    return dest


def webui_main(action: str, project: Path | None = None, out: Path | None = None) -> int:
    """``xewe webui export PROJECT OUT`` or ``xewe webui prompt``."""
    if action == "prompt":
        result(PROMPT_FILE.read_text(encoding="utf-8").rstrip("\n"))
        return EXIT_OK
    assert project is not None and out is not None
    export(project, out)
    log.info("next: port app.py into the sketch with the prompt of `xewe webui prompt` (doc/webui.md)")
    return EXIT_OK
