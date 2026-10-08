from pathlib import Path

import pytest

from xewe.cli import main
from xewe.project import Paths

TESTS = '''
import pytest


@pytest.mark.host
def test_logic():
    assert 1 + 1 == 2


def test_status(serial):
    serial.command("$system status", expect="Uptime", timeout=1)
'''


@pytest.fixture
def proj(project: Paths) -> Paths:
    (project.root / "tests").mkdir()
    (project.root / "tests" / "test_fw.py").write_text(TESTS)
    return project


def _run(pytester: pytest.Pytester, p: Paths, *args: str) -> pytest.RunResult:
    return pytester.runpytest("--xewe-project", str(p.root), "--xewe-chip", "c3", "-p", "no:cacheprovider",
                              str(p.root / "tests"), *args)


def test_no_board_reports_compiled_not_run(pytester: pytest.Pytester, proj: Paths) -> None:
    res = _run(pytester, proj)
    res.assert_outcomes(passed=1, skipped=1)
    assert res.ret == 0
    res.stdout.fnmatch_lines([
        "*= compiled, not run (no board attached, chip c3) =*",
        "*test_fw.py::test_status",
        "xewe test: 1 host passed, 1 compiled, not run, 0 failed",
    ])
    assert (proj.out / "c3/2.0.15-c3-xewe-os.bin").is_file()  # hardware tests still compile


def test_require_board_fails(pytester: pytest.Pytester, proj: Paths) -> None:
    res = _run(pytester, proj, "--xewe-require-board")
    res.assert_outcomes(passed=1, errors=1)
    assert res.ret == 1


def test_host_only_deselects_hardware(pytester: pytest.Pytester, proj: Paths) -> None:
    res = _run(pytester, proj, "-m", "host")
    res.assert_outcomes(passed=1, deselected=1)
    assert not (proj.out / "c3").exists()


def test_compile_failure_fails_hardware_tests(pytester: pytest.Pytester, proj: Paths,
                                              monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAKE_ARDUINO_COMPILE_FAIL", "all")
    res = _run(pytester, proj)
    res.assert_outcomes(passed=1, errors=1)
    assert res.ret == 1
    res.stdout.fnmatch_lines(["*build failed for c3*"])


def test_no_tests_collected_is_success(pytester: pytest.Pytester, project: Paths) -> None:
    (project.root / "tests").mkdir()
    res = _run(pytester, project)
    assert res.ret == 0


def test_xewe_test_cli_runs_project_and_module_tests(proj: Paths, capsys: pytest.CaptureFixture[str]) -> None:
    mod_tests = proj.modules_checkout / "modules" / "wifi" / "tests"
    mod_tests.mkdir()
    (mod_tests / "test_fw.py").write_text(TESTS)  # same basename as the project test: importlib mode
    assert main(["test"]) == 0
    out = capsys.readouterr().out
    assert "xewe test: 2 host passed, 2 compiled, not run, 0 failed" in out
    assert main(["test", "--require-board"]) == 1
    assert main(["test", "--module", "pins"]) == 2
    assert main(["test", "--module", "wifi", "--host-only"]) == 0


def test_xewe_test_all_chips(proj: Paths, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["test", "--all-chips", "--", "-q"]) == 0
    out = capsys.readouterr().out
    for chip in ("c3", "c6", "s3"):
        assert f"no board attached, chip {chip}" in out
        assert (proj.out / chip).is_dir()


def test_xewe_test_without_tests(project: Paths, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["test"]) == 0
    assert "no tests found" in capsys.readouterr().out


def test_hardware_marker_is_automatic(pytester: pytest.Pytester, proj: Paths) -> None:
    res = _run(pytester, proj, "-m", "hardware", "--collect-only", "-q")
    res.stdout.fnmatch_lines(["*test_fw.py::test_status"])
    assert "test_logic" not in res.stdout.str()


def test_extra_args_only_for_test(project: Path) -> None:
    assert main(["build", "--", "-q"]) == 2
