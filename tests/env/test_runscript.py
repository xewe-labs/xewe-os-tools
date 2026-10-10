import os
from pathlib import Path

import pytest

from conftest import REPO
from xewe import __version__
from xewe.env import runscript
from xewe.env.project import Paths


def test_render_fills_version_chip_and_keep_nvs() -> None:
    text = runscript.render("s3")
    lines = text.splitlines()
    assert lines[0] == "#!/usr/bin/env bash"
    assert lines[1].startswith(runscript.GENERATED_MARK) and f"xewe-os-tools {__version__}" in lines[1]
    assert "chip: s3 unless --chip C" in text and "--keep-nvs" in text
    assert lines[-1] == 'exec "${PY}" -m xewe --project "${ROOT}" run "$@"'
    assert runscript.is_generated(text)


def test_reference_copy_is_the_generated_form() -> None:
    """scripts/run.sh (and the template's committed run.sh) is exactly what setup writes for chip c3."""
    assert (REPO / "scripts" / "run.sh").read_text() == runscript.render("c3")


def test_write_new_and_regenerate(tmp_path: Path) -> None:
    p = Paths(tmp_path)
    assert runscript.write(p, "c3")
    run = tmp_path / "run.sh"
    assert run.read_text() == runscript.render("c3") and os.access(run, os.X_OK)
    run.write_text(runscript.render("c3", version="0.0.1"))  # an older generated copy
    assert runscript.write(p, "c6")
    assert run.read_text() == runscript.render("c6")


def test_legacy_reference_copy_is_replaced(tmp_path: Path) -> None:
    (tmp_path / "run.sh").write_text(runscript.LEGACY_RUN_SH)
    assert runscript.write(Paths(tmp_path), "c3")
    assert (tmp_path / "run.sh").read_text() == runscript.render("c3")


def test_hand_written_run_sh_is_kept(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    own = "#!/usr/bin/env bash\n# my own run script\nexec make flash\n"
    (tmp_path / "run.sh").write_text(own)
    assert not runscript.write(Paths(tmp_path), "c3")
    assert (tmp_path / "run.sh").read_text() == own
    assert "run.sh is hand-written" in capsys.readouterr().out

