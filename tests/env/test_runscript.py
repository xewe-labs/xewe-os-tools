import os
import subprocess
from pathlib import Path

from xewe import __version__
from xewe.env import runscript
from xewe.env.project import Paths


def test_render_fills_version_chip_and_keep_nvs() -> None:
    text = runscript.render("s3")
    lines = text.splitlines()
    assert lines[0] == "#!/usr/bin/env bash"
    assert lines[1].startswith(runscript.GENERATED_MARK) and f"xewe-os-tools {__version__}" in lines[1]
    assert "chip: s3 unless --chip C" in text and "--keep-nvs" in text
    assert 'PY="${ROOT}/build/tools/.venv/bin/python"' in text
    assert lines[-1] == 'exec "${PY}" -m xewe --project "${ROOT}" run "$@"'


def test_render_check_calls_xewe_check() -> None:
    text = runscript.render_check()
    lines = text.splitlines()
    assert lines[1].startswith(runscript.GENERATED_MARK) and f"xewe-os-tools {__version__}" in lines[1]
    assert "--all-chips" in text and "tests/unit/run.sh" in text
    assert lines[-1] == 'exec "${PY}" -m xewe --project "${ROOT}" check "$@"'


def test_both_forms_are_valid_bash(tmp_path: Path) -> None:
    for text in (runscript.render("c3"), runscript.render_check()):
        script = tmp_path / "run.sh"
        script.write_text(text)
        assert subprocess.run(["bash", "-n", str(script)], check=False).returncode == 0


def test_write_new_and_regenerate(tmp_path: Path) -> None:
    p = Paths(tmp_path)
    runscript.write(p, runscript.render("c3"))
    run = tmp_path / "run.sh"
    assert run.read_text() == runscript.render("c3") and os.access(run, os.X_OK)
    run.write_text(runscript.render("c3", version="0.0.1"))
    runscript.write(p, runscript.render("c6"))
    assert run.read_text() == runscript.render("c6")


def test_any_existing_run_sh_is_replaced(tmp_path: Path) -> None:
    (tmp_path / "run.sh").write_text("#!/usr/bin/env bash\n# my own run script\nexec make flash\n")
    runscript.write(Paths(tmp_path), runscript.render("c3"))
    assert (tmp_path / "run.sh").read_text() == runscript.render("c3")
    other = tmp_path / "elsewhere.sh"
    other.write_text("keep me\n")
    (tmp_path / "run.sh").unlink()
    (tmp_path / "run.sh").symlink_to(other)
    runscript.write(Paths(tmp_path), runscript.render_check())
    assert not (tmp_path / "run.sh").is_symlink() and other.read_text() == "keep me\n"
