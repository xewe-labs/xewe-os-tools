"""xewe webui: the export of the fan-dashboard example (golden), url_for, MIME types, skipped files, prompt."""

from pathlib import Path

import pytest
from conftest import FIXTURES

from xewe.build import webui
from xewe.cli import main

EXAMPLE = FIXTURES / "webui" / "fan-dashboard"
GOLDEN = FIXTURES / "webui" / "fan-dashboard_export"


def _tree(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def test_example_matches_golden(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    dest = webui.export(EXAMPLE, tmp_path, stamp="export")
    assert dest == tmp_path / "fan-dashboard_export"
    assert _tree(dest) == _tree(GOLDEN)
    assert capsys.readouterr().out == f"webui: 4 headers and fan-dashboard_app.ino + app.py in {dest}\n"


def test_cli_export_writes_a_timestamped_folder(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["webui", "export", str(EXAMPLE), str(tmp_path / "out")]) == 0
    (dest,) = (tmp_path / "out").iterdir()
    assert dest.name.startswith("fan-dashboard_") and len(dest.name) == len("fan-dashboard_YYYYmmdd_HHMMSS")
    assert _tree(dest) == _tree(GOLDEN)
    assert "xewe webui prompt" in capsys.readouterr().err


def test_url_for_rewrite() -> None:
    html = (GOLDEN / "templates" / "index_html.h").read_text()
    assert "url_for" not in html
    assert 'href="/static/style.css"' in html and 'src="/static/fan-api.js"' in html
    assert 'src="/static/app.js"' in html  # double quotes, no spaces inside the braces


def test_routes_names_and_mime(tmp_path: Path) -> None:
    for rel in ("templates/index.html", "templates/about.html", "static/a-b.c.css", "static/js/x.js"):
        (tmp_path / "p" / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / "p" / rel).write_text("x")
    ino = (webui.export(tmp_path / "p", tmp_path, stamp="t") / "p_app.ino").read_text()
    assert '#include "static/a_b_c_css.h"' in ino and '#include "static/js/x_js.h"' in ino
    assert 'server.on("/", HTTP_GET' in ino and 'send_P(200, "text/html", INDEX_HTML)' in ino
    assert 'server.on("/templates/about.html", HTTP_GET' in ino and "ABOUT_HTML" in ino
    assert 'server.on("/static/a-b.c.css", HTTP_GET' in ino and 'send_P(200, "text/css", A_B_C_CSS)' in ino
    assert 'server.on("/static/js/x.js", HTTP_GET' in ino and 'send_P(200, "application/javascript", X_JS)' in ino
    assert (tmp_path / "p_t" / "static" / "a_b_c_css.h").read_text() == (
        '#pragma once\n#include <pgmspace.h>\nstatic const char A_B_C_CSS[] PROGMEM = R"rawliteral(\nx\n)rawliteral";\n'
    )


def test_only_html_css_js_are_exported(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    p = tmp_path / "p"
    (p / "static").mkdir(parents=True)
    (p / "other").mkdir()
    (p / "static" / "logo.png").write_bytes(b"\x89PNG\0")
    (p / "static" / "notes.txt").write_text("x")
    (p / "static" / "bad.js").write_bytes(b"\xff\xfe")
    (p / "other" / "x.js").write_text("x")
    dest = webui.export(p, tmp_path, stamp="t")
    assert sorted(_tree(dest)) == ["p_app.ino"]  # no app.py either
    assert "skipped (not UTF-8): static/bad.js" in caplog.text
    assert "#include" not in (dest / "p_app.ino").read_text().split("#include <WebServer.h>")[1]


def test_same_variable_twice_fails(tmp_path: Path) -> None:
    for rel in ("static/a/x.js", "static/b/x.js"):
        (tmp_path / "p" / rel).parent.mkdir(parents=True)
        (tmp_path / "p" / rel).write_text("x")
    assert main(["webui", "export", str(tmp_path / "p"), str(tmp_path)]) == 1


def test_missing_project_exit_2(tmp_path: Path) -> None:
    assert main(["webui", "export", str(tmp_path / "nope"), str(tmp_path)]) == 2


def test_prompt_is_printed_without_credentials(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["webui", "prompt"]) == 0
    out = capsys.readouterr().out
    assert "[INSERT ARDUINO APP TEMPLATE HERE]" in out and "[INSERT APP.PY REFERENCE HERE]" in out
    assert 'ssid = "YOUR_SSID"' in out and 'password = "YOUR_PASSWORD"' in out
