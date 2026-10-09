"""``xewe clean``: what each level removes and what it always keeps."""

from xewe.cli import main
from xewe.env.project import Paths


def test_clean(project: Paths) -> None:
    assert main(["build", "--chip", "c3"]) == 0
    assert main(["clean"]) == 0
    assert not project.builds.exists() and not project.tmp.exists()
    assert project.build_config.exists() and project.modules.is_dir() and project.src_modules_h.is_file()
    assert main(["build", "--chip", "c3"]) == 0
    assert main(["clean", "--modules"]) == 0
    assert not project.modules.exists() and not project.src_modules_h.exists() and not project.builds.exists()
    assert project.build_config.exists() and project.libraries.is_dir()
    assert main(["build"]) == 3


def test_clean_all_keeps_tools_and_shared_toolchain(project: Paths) -> None:
    assert main(["build", "--chip", "c3"]) == 0
    project.venv.mkdir(parents=True)
    (project.tools_checkout / "pyproject.toml").write_text("")
    for d in (project.default_arduino_data, project.bin, project.arduino_user, project.downloads):
        d.mkdir(parents=True, exist_ok=True)
    (project.bin / "arduino-cli-1.5.1").write_text("")
    (project.modules_checkout("v1.0.0") / "modules").mkdir(parents=True)  # a shared modules checkout
    before = sorted(str(f.relative_to(project.home)) for f in project.home.rglob("*"))
    assert main(["clean", "--all"]) == 0
    assert sorted(c.name for c in project.build.iterdir()) == ["tools"]
    assert project.venv.is_dir() and (project.tools_checkout / "pyproject.toml").is_file()
    assert project.src_modules_h.is_file()  # --all without --modules keeps src/Modules.h
    assert sorted(str(f.relative_to(project.home)) for f in project.home.rglob("*")) == before
    assert main(["clean", "--all", "--modules"]) == 0
    assert not project.src_modules_h.exists()
    assert main(["build"]) == 3
