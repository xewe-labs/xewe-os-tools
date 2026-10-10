"""``xewe check``: the checks of an Arduino library such as xewe-os-core (doc/spec.md §13).

Two parts, both by default:

- unit: ``tests/unit/run.sh`` (the library's host tests), with ``ARDUINOJSON_SRC`` pointing at
  ``build/libraries/ArduinoJson/src`` when setup installed it and the variable is not set;
- examples: every sketch under ``examples/`` (``<name>/<name>.ino``, nested folders too) compiled
  per chip with the same FQBN board options as ``xewe build``, the library itself as ``--library``
  and ``build/libraries`` (what ``xewe setup`` installed from ``depends=``) as ``--libraries``.

Exit 0 when every part passed, 1 when one failed, 3 when the examples need ``./setup.sh`` first.
"""

from __future__ import annotations

import os
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from xewe.build import chips
from xewe.build.compile import _compile, cli_path, parse_size
from xewe.env import arduino, config
from xewe.env.project import Paths
from xewe.report import EXIT_FAIL, EXIT_OK, EXIT_USAGE, XeWeError, log, result

UNIT_RUNNER = Path("tests") / "unit" / "run.sh"
ARDUINOJSON_ENV = "ARDUINOJSON_SRC"


@dataclass
class ExampleResult:
    """One example compiled for one chip."""

    chip: str
    example: str
    ok: bool
    seconds: int = 0
    size: int | None = None
    warnings: int = 0


def find_examples(root: Path) -> list[Path]:
    """Sketch folders under ``examples/``: a folder ``<name>`` holding ``<name>.ino``, sorted."""
    base = root / "examples"
    if not base.is_dir():
        return []
    return sorted({ino.parent for ino in base.rglob("*.ino") if ino.stem == ino.parent.name})


def example_build_path(p: Paths, chip: str, example: Path) -> Path:
    """``build/builds/<chip>/examples/<path under examples/>``: the arduino-cli build path of one example."""
    return p.builds / chip / "examples" / example.relative_to(p.root / "examples")


def example_argv(p: Paths, cli: str, chip: chips.Chip, example: Path) -> list[str]:
    """The ``arduino-cli compile`` argv of one example (paths relative to the library root)."""
    return [
        cli,
        "compile",
        "--fqbn",
        chip.fqbn,
        "--build-path",
        p.rel(example_build_path(p, chip.name, example)),
        "--libraries",
        p.rel(p.libraries),
        "--library",
        ".",
        "--warnings",
        "default",
        "--jobs",
        "0",
        p.rel(example),
    ]


def run_unit(p: Paths) -> bool | None:
    """Run ``tests/unit/run.sh``; True when it passed, None when the library has none."""
    runner = p.root / UNIT_RUNNER
    if not runner.is_file():
        result(f"unit   no {UNIT_RUNNER}; skipped")
        return None
    env = dict(os.environ)
    bundled = p.libraries / "ArduinoJson" / "src"
    if not env.get(ARDUINOJSON_ENV) and (bundled / "ArduinoJson.h").is_file():
        env[ARDUINOJSON_ENV] = str(bundled)
    result(f"unit   {UNIT_RUNNER}")
    argv = ["bash", str(runner)]
    log.debug("$ %s", " ".join(argv))
    code = subprocess.run(argv, cwd=p.root, env=env, check=False).returncode
    result(f"{'ok  ' if code == 0 else 'FAIL'}   unit  {UNIT_RUNNER} exit {code}")
    return code == 0


def compile_example(p: Paths, cfg: config.BuildConfig, chip: chips.Chip, example: Path) -> ExampleResult:
    """Compile one example; the arduino-cli output goes to ``compile.log`` in its build path."""
    name = example.relative_to(p.root / "examples").as_posix()
    build_path = example_build_path(p, chip.name, example)
    build_path.mkdir(parents=True, exist_ok=True)
    log_path = build_path / "compile.log"
    argv = example_argv(p, p.rel(cli_path(p, cfg)), chip, example)
    start = time.monotonic()
    code, output = _compile(argv, arduino.env(p, cfg.arduino_data(p)), p.root, log_path)
    seconds = int(time.monotonic() - start)
    if code != 0:
        for ln in [ln for ln in output.splitlines() if re.search(r"\berror\b", ln, re.IGNORECASE)][-15:]:
            log.error("%s", ln)
        result(f"FAIL   {chip.name}  {name}  {seconds} s  see {p.rel(log_path)}")
        return ExampleResult(chip.name, name, False, seconds)
    size, percent = parse_size(output)
    warnings = sum(1 for ln in output.splitlines() if "warning:" in ln)
    size_text = f"Sketch uses {size} bytes" if size is not None else "size unknown"
    if size is not None and percent is not None:
        size_text += f" ({percent}%)"
    result(f"ok     {chip.name}  {name}  {seconds} s  {size_text}  {warnings} warnings")
    return ExampleResult(chip.name, name, True, seconds, size, warnings)


def check(p: Paths, unit: bool, examples: bool, chip_names: list[str]) -> int:
    """``xewe check``: ``unit`` and/or ``examples`` (both when neither is set) for ``chip_names``."""
    if not p.is_library:
        raise XeWeError(f"xewe check runs in an Arduino library (library.properties, no xewe.toml); "
                        f"{p.root} is a project: use xewe build and xewe test", EXIT_USAGE)
    if not unit and not examples:
        unit = examples = True
    ok = True
    summary: list[str] = []
    if unit:
        passed = run_unit(p)
        ok = ok and passed is not False
        summary.append("unit " + {True: "ok", False: "FAILED", None: "none"}[passed])
    if examples:
        cfg = config.require(p)
        sketches = find_examples(p.root)
        if not sketches:
            result("examples: none under examples/")
        results: list[ExampleResult] = []
        for chip_name in chip_names:
            chip = chips.get(chip_name)
            result(f"check  {chip.name}  {chip.fqbn}")
            results += [compile_example(p, cfg, chip, ex) for ex in sketches]
        failed = sum(1 for r in results if not r.ok)
        warnings = sum(r.warnings for r in results)
        ok = ok and failed == 0
        summary.append(f"examples {len(results) - failed}/{len(results)} compiled ({', '.join(chip_names)}), "
                       f"{failed} failed, {warnings} warnings")
    result(f"check: {'; '.join(summary)}")
    return EXIT_OK if ok else EXIT_FAIL
