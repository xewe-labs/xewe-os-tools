"""Command line: ``xewe <command>`` (SPEC §3). Exit codes are in :mod:`xewe.report`."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from xewe import __version__
from xewe.board import boards, provision, serialio
from xewe.build import chips, flash, release
from xewe.build import compile as build
from xewe.env import clean, config, doctor, dotenv, setup
from xewe.env.project import Paths, find_root
from xewe.modules import lockfile, manifest
from xewe.modules import registry as modules
from xewe.modules.lockfile import Lock
from xewe.report import (
    EXIT_FAIL,
    EXIT_NO_BOARD,
    EXIT_OK,
    EXIT_USAGE,
    NO_BOARD,
    XeWeError,
    board_disabled,
    board_disabled_exit,
    disable_board,
    log,
    result,
    setup_logging,
)
from xewe.testing.runner import run_tests


def _chip_args(sp: argparse.ArgumentParser, all_chips: bool = True) -> None:
    group = sp.add_mutually_exclusive_group()
    group.add_argument("--chip", choices=chips.ALL_CHIPS, help="target chip")
    if all_chips:
        group.add_argument("--all-chips", action="store_true", help="c3, c6, s3 in turn")


def _require_board(sp: argparse.ArgumentParser) -> None:
    sp.add_argument("--require-board", action="store_true",
                    help="exit 4 (or fail tests) when no board is attached; also XEWE_REQUIRE_BOARD=1")


ENV_HELP = ("dotenv file with XEWE_* settings; default: XEWE_ENV, else .env in the project, else .env in "
            "the xewe-os-tools source checkout (first found wins; the real environment wins over the file)")
ENV_COMMANDS = frozenset({"flash", "serial", "provision", "test", "run", "boards"})
"""Commands that read XEWE_* settings (port, chip, provisioning, test pins) from the dotenv file."""


def _env_arg(sp: argparse.ArgumentParser, *aliases: str) -> None:
    sp.add_argument("--env", *aliases, dest="env_file", type=Path, metavar="FILE", help=ENV_HELP)


NO_BOARD_COMMANDS = frozenset({"flash", "serial", "provision", "test", "run", "boards"})
"""Commands that take ``--no-board`` (and honour XEWE_NO_BOARD)."""


def _no_board_arg(sp: argparse.ArgumentParser) -> None:
    sp.add_argument("--no-board", action="store_true",
                    help="never look for or open a board (also XEWE_NO_BOARD=1); a command that needs one exits 4")


VERBOSE_HELP = "print every subprocess command and its output"


def _verbose_everywhere(ap: argparse.ArgumentParser) -> None:
    """Accept ``-v/--verbose`` after any (sub)command too, not only before it (``xewe test -v``)."""
    for action in ap._actions:
        if isinstance(action, argparse._SubParsersAction):
            for sp in action.choices.values():
                # SUPPRESS: when absent here, the global flag's value is kept
                sp.add_argument("-v", "--verbose", action="store_true", default=argparse.SUPPRESS, help=VERBOSE_HELP)
                _verbose_everywhere(sp)


def parser() -> argparse.ArgumentParser:
    """The full argument parser."""
    ap = argparse.ArgumentParser(prog="xewe", description="XeWe OS firmware tools: setup, build, flash, serial, provision, test.")
    ap.add_argument("--project", metavar="DIR", help="project root (default: nearest ancestor with xewe.toml)")
    ap.add_argument("-v", "--verbose", action="store_true", help=VERBOSE_HELP + " (also accepted after the command)")
    ap.add_argument("--version", action="version", version=f"xewe-os-tools {__version__}")
    sub = ap.add_subparsers(dest="command", required=True, metavar="COMMAND")

    sp = sub.add_parser("setup", help="install the shared toolchain (~/.xewe-os/build-tools) and the core, libraries and modules into build/")
    sp.add_argument("--latest", action="store_true", help="install the newest tags (xewe.toml is not changed)")
    sp.add_argument("--modules", metavar="LIST|all|none", help="select modules (written to xewe.toml; \"\" or none = no modules)")
    sp.add_argument("--force", action="store_true", help="redo every step")
    sp.add_argument("--core-source", metavar="DIR", type=Path, help="local xewe-os-core checkout (also XEWE_CORE_SOURCE)")
    sp.add_argument("--modules-source", metavar="DIR", type=Path, help="local modules checkout (also XEWE_MODULES_SOURCE)")
    sp.add_argument("--arduino-data", metavar="DIR", type=Path,
                    help="reuse this arduino-cli data dir (has packages/esp32) instead of ~/.xewe-os/build-tools/arduino15; also XEWE_ARDUINO_DATA")

    sp = sub.add_parser("build", help="compile into build/builds/<chip>/out/")
    _chip_args(sp)
    sp.add_argument("--define", action="append", default=[], metavar="KEY=VALUE", help="extra #define in XeWeBuildInfo.h")
    sp.add_argument("--clean", action="store_true", help="drop the compile cache first")
    sp.add_argument("--dry-run", action="store_true", help="print the arduino-cli command; do not run it")

    sp = sub.add_parser("flash", help="build if needed, then write the image to the board")
    _chip_args(sp, all_chips=False)
    sp.add_argument("--port", help="serial port")
    sp.add_argument("--baud", type=int, default=flash.DEFAULT_BAUD)
    sp.add_argument("--erase", action="store_true",
                    help="erase the whole flash (NVS too) first, so the board starts with a first boot; "
                         "default: NVS (name, Wi-Fi, module choices) is kept")
    sp.add_argument("--no-build", action="store_true", help="flash the existing image")
    _require_board(sp)
    _env_arg(sp)

    sp = sub.add_parser(
        "serial", help="listen to the board, or send a command and wait for a reply",
        description="Listen to the board, or send one command and wait for a reply. The tools open the port "
                    "without resetting the board; --reset or flashing resets it (then the boot log is shown). "
                    "With --send, the command is sent right away if no boot output appears within 2 s, or once "
                    "a booting board printed 'System Setup Complete' (up to --boot-timeout); an "
                    "unprovisioned board ('Name your device') exits 1 without sending. Without --send on a terminal "
                    "the console is interactive: each line typed is sent to the board; Ctrl-C or Ctrl-D exits "
                    "(--no-input, or stdin not a terminal: listen only). The interactive console prints the "
                    "board's lines as they are (no timestamp, no '> cmd' echo; --timestamps adds the time); "
                    "--log and listen-only output are always timestamped.")
    sp.add_argument("--port", help="serial port")
    sp.add_argument("--baud", type=int, default=115200)
    sp.add_argument("--reset", action="store_true", help="pulse EN (RTS) before listening")
    sp.add_argument("--send", metavar="CMD", help="send one command")
    sp.add_argument("--expect", metavar="RE", help="with --send: wait for a line matching RE")
    sp.add_argument("--timeout", type=float, default=10.0, help="seconds to wait for --expect")
    sp.add_argument("--boot-timeout", type=float, default=serialio.BOOT_TIMEOUT_SECONDS, metavar="S",
                    help="with --send: seconds to wait for the boot to finish before sending (default %(default)g)")
    sp.add_argument("--duration", type=float, help="listen for this many seconds")
    sp.add_argument("--no-input", action="store_true",
                    help="listen only; do not send lines typed on the terminal")
    sp.add_argument("--timestamps", action="store_true",
                    help="interactive console: prefix each line with HH:MM:SS.mmm (default: raw lines)")
    sp.add_argument("--log", type=Path, metavar="FILE", help="append timestamped lines to FILE")
    _require_board(sp)
    _env_arg(sp)

    sp = sub.add_parser(
        "provision", help="answer the first-boot prompts (name, modules, Wi-Fi, timezone) of a flashed board",
        description="Answer the first-boot prompts of a freshly flashed board. Values: flags, then the environment "
                    "(XEWE_DEVICE_NAME, XEWE_WIFI_SSID, XEWE_WIFI_PASSWORD, XEWE_TIMEZONE, XEWE_PROVISION_MODULES), "
                    "then the dotenv file, then defaults. Dotenv file, first found wins: --env/--from FILE, else "
                    "XEWE_ENV, else .env in the project directory, else .env in the xewe-os-tools source checkout "
                    "(see .env.example). With the file in place no flags are needed. The Wi-Fi password has no "
                    "flag and is masked in the output and log.")
    sp.add_argument("--port", help="serial port")
    sp.add_argument("--name", help="device name (default: [project] name in xewe.toml, else the folder name)")
    sp.add_argument("--modules", metavar="all|none|LIST", help="modules to enable at their prompt (default: all)")
    sp.add_argument("--timezone", metavar="GMT+HH:MM", help="answer n to the detected time and set this offset "
                    "(default: accept the detected timezone)")
    _env_arg(sp, "--from")
    sp.add_argument("--timeout", type=float, default=provision.START_TIMEOUT,
                    help="seconds from reset to the first prompt (default %(default)g)")
    sp.add_argument("--no-reset", action="store_true", help="do not reset the board first")
    sp.add_argument("--log", type=Path, metavar="FILE", help="append timestamped lines to FILE (password masked)")

    sp = sub.add_parser("test", help="run project and module tests with pytest (args after -- go to pytest)")
    _chip_args(sp)
    sp.add_argument("--port", help="serial port")
    sp.add_argument("--module", action="append", default=[], metavar="SLUG", help="only this module's tests")
    sp.add_argument("--unit-only", action="store_true", help="only tests marked unit (developer machine, no board)")
    _require_board(sp)
    _env_arg(sp)

    sp = sub.add_parser(
        "run", help="build, erase, flash, then listen (what run.sh calls)",
        description="Build when stale, erase the whole flash (NVS too: name, Wi-Fi, module choices), flash, then "
                    "open the console. Every run is a true first boot (the board asks for its name); --keep-nvs "
                    "skips the erase. The 'flashed' line ends with 'flash erased: first boot' or 'nvs kept'.")
    _chip_args(sp, all_chips=False)
    sp.add_argument("--port", help="serial port")
    sp.add_argument("--define", action="append", default=[], metavar="KEY=VALUE")
    sp.add_argument("--keep-nvs", action="store_true",
                    help="do not erase the flash first; the board keeps its name, Wi-Fi and module choices")
    sp.add_argument("--no-serial", action="store_true", help="stop after flashing")
    sp.add_argument("--no-input", action="store_true",
                    help="listen only; do not send lines typed on the terminal")
    sp.add_argument("--timestamps", action="store_true",
                    help="interactive console: prefix each line with HH:MM:SS.mmm (default: raw lines)")
    _env_arg(sp)

    sp = sub.add_parser("boards", help="list attached boards; set a port/chip override")
    sp.add_argument("--no-probe", action="store_true", help="do not reset boards to read their chip")
    sp.add_argument("--json", action="store_true")
    sp.add_argument("--set-port", metavar="P", help="write [override] port to build/config/boards.toml")
    sp.add_argument("--set-chip", choices=chips.ALL_CHIPS, help="with --set-port: [override] chip")
    sp.add_argument("--clear", action="store_true", help="remove the [override]")
    _require_board(sp)
    _env_arg(sp)

    sp = sub.add_parser("modules", help="list, select, validate and generate modules")
    msub = sp.add_subparsers(dest="modules_command", required=True, metavar="ACTION")
    m = msub.add_parser("list", help="modules in the modules repo checkout (* = selected)")
    m.add_argument("--json", action="store_true")
    m = msub.add_parser("select", help="write [modules] selected, then generate")
    m.add_argument("selection", metavar="LIST|all|none")
    m.add_argument("--no-generate", action="store_true")
    m = msub.add_parser("validate", help="check every module.properties rule and the tests/ layout")
    m.add_argument("path", nargs="?", type=Path,
                   help="modules repo checkout (default: the one setup uses, XEWE_MODULES_SOURCE or "
                        "~/.xewe-os/build-tools/sources/xewe-os-modules/<ref>)")
    msub.add_parser("generate", help="rebuild build/modules/ and src/Modules.h from the manifest selection")

    sp = sub.add_parser("manifest", help="show or update the refs in xewe.toml")
    lsub = sp.add_subparsers(dest="manifest_command", required=True, metavar="ACTION")
    m = lsub.add_parser("show", help="manifest refs vs installed refs (! = drift)")
    m.add_argument("--json", action="store_true")
    m = lsub.add_parser("update", help="move refs to the newest tag (or --to REF)")
    m.add_argument("names", nargs="*", metavar="core|modules|tools")
    m.add_argument("--to", metavar="REF")

    sp = sub.add_parser("clean", help="delete build/builds and build/tmp")
    sp.add_argument("--all", action="store_true", help="delete all of build/ except build/tools (never ~/.xewe-os/build-tools)")
    sp.add_argument("--modules", action="store_true", help="also delete build/modules/ and src/Modules.h")

    sub.add_parser("doctor", help="check the environment")

    sp = sub.add_parser("release", help="build the release matrix into static/firmware/releases/<version>/")
    sp.add_argument("--version", required=True, dest="release_version", metavar="X.Y.Z")
    sp.add_argument("--matrix", type=Path, metavar="FILE")
    sp.add_argument("--notes", type=Path, metavar="FILE")

    for name, choice in sub.choices.items():
        if name in NO_BOARD_COMMANDS:
            _no_board_arg(choice)
    _verbose_everywhere(ap)
    return ap


def _require_board_flag(args: argparse.Namespace) -> bool:
    return bool(getattr(args, "require_board", False)) or os.environ.get("XEWE_REQUIRE_BOARD") == "1"


def _chips(args: argparse.Namespace, p: Paths, lock: Lock) -> list[str]:
    """Chip rule for build/test: --chip, --all-chips, a known attached board (no probe), lock, c3."""
    if getattr(args, "all_chips", False):
        return list(chips.ALL_CHIPS)
    if args.chip:
        return [args.chip]
    board = boards.select(p, port=getattr(args, "port", None), probe=False)
    return [boards.resolve_chip(None, board, lock.chip)]


def _modules_command(args: argparse.Namespace, p: Paths) -> int:
    lock = lockfile.load(p.lock)
    cfg = config.load(p)
    checkout = modules.checkout(p, lock.modules.ref, cfg.path(p, "modules") if cfg else None)
    if args.modules_command == "validate":
        root = args.path or checkout
        registry = modules.Registry.load(root)
        problems = modules.validate(registry, lock.core.ref) + modules.check_tests_layout(registry)
        for problem in problems:
            result(str(problem))
        errors = sum(1 for x in problems if x.error)
        result(f"{len(registry.all)} modules, {errors} errors, {len(problems) - errors} warnings")
        return EXIT_FAIL if errors else EXIT_OK
    registry = modules.Registry.load(checkout)
    if args.modules_command == "list":
        rows: list[dict[str, Any]] = [
            {"slug": m.slug, "id": m.props.get("id", ""), "version": m.props.get("version", ""),
             "depends": m.deps, "selected": m.slug in lock.selected}
            for m in registry.by_slug.values()
        ]
        if args.json:
            result(json.dumps(rows, indent=2))
        else:
            for r in rows:
                mark = "*" if r["selected"] else " "
                result(f"{mark} {r['slug']:<16} {r['id']:<16} {r['version']:<8} {','.join(r['depends'])}")
        return EXIT_OK
    if args.modules_command == "select":
        lock.selected = modules.expand_selection(registry, args.selection)
        lockfile.save(lock, p.lock)
        result(f"selected: {', '.join(lock.selected) or 'none'}")
        if args.no_generate:
            return EXIT_OK
    cfg = config.require(p)
    rec = cfg.installed.get("modules", {})
    order = modules.generate(p, registry, lock.selected, rec.get("source", lock.modules.repo),
                             rec.get("ref", lock.modules.ref), rec.get("commit", "-"))
    result(f"modules: {', '.join(m.slug for m in order) or 'none'}  (build/modules, src/Modules.h)")
    return EXIT_OK


def _boards_command(args: argparse.Namespace, p: Paths) -> int:
    if args.set_port or args.clear or args.set_chip:
        if args.set_chip and not args.set_port:
            raise XeWeError("--set-chip needs --set-port", EXIT_USAGE)
        override = boards.set_override(p, args.set_port, args.set_chip, args.clear)
        result(f"override: {override or 'none'}")
        return EXIT_OK
    found = boards.scan(p, probe=not args.no_probe, esptool_cmd=lambda: flash.esptool_cmd(p))
    if args.json:
        result(json.dumps([vars(b) for b in found], indent=2))
    for b in found if not args.json else []:
        result(f"{b.port:<16} {b.chip or '?':<6} {b.vid}:{b.pid}  {b.serial_number or '-':<20} {b.detected_by or '-':<8} {b.description}")
    if not found:
        if not args.json:
            result(NO_BOARD)
        if _require_board_flag(args):
            log.error("--require-board: %s", NO_BOARD)
            return EXIT_NO_BOARD
    return EXIT_OK


def dispatch(args: argparse.Namespace, extra: list[str]) -> int:
    """Run the parsed command."""
    cmd = args.command
    if getattr(args, "no_board", False):
        disable_board()
    p = Paths(find_root(args.project))
    if cmd in ENV_COMMANDS:
        dotenv.load_settings(p.root, getattr(args, "env_file", None))
    if cmd == "setup":
        opts = setup.SetupOptions(
            latest=args.latest,
            modules=args.modules,
            force=args.force,
            core_source=args.core_source.resolve() if args.core_source else None,
            modules_source=args.modules_source.resolve() if args.modules_source else None,
            arduino_data=args.arduino_data.expanduser().resolve() if args.arduino_data else None,
        )
        return setup.run_setup(p, opts)
    if cmd == "modules":
        return _modules_command(args, p)
    if cmd == "boards":
        if board_disabled() and not (args.set_port or args.clear or args.set_chip):
            return board_disabled_exit()
        return _boards_command(args, p)
    if cmd == "clean":
        return clean.clean(p, args.all, args.modules)
    lock = lockfile.load(p.lock)
    if cmd in ("build", "run"):
        setup.warn_no_modules(lock.selected)
    if cmd == "build":
        return build.build(p, lock, _chips(args, p, lock), build.parse_defines(args.define), args.clean, args.dry_run)
    if cmd == "flash":
        code, _ = flash.flash_with_board(
            p, lock, args.chip, args.port, args.baud, args.erase, args.no_build, _require_board_flag(args)
        )
        return code
    if cmd in ("serial", "provision") and board_disabled():
        return board_disabled_exit()
    if cmd == "serial":
        return serialio.serial_main(
            args.port,
            lambda port: boards.select(p, port=port, probe=False),
            args.baud, args.reset, args.send, args.expect, args.timeout, args.boot_timeout, args.duration, args.log,
            _require_board_flag(args), no_input=args.no_input, timestamps=args.timestamps,
        )
    if cmd == "provision":
        settings = provision.resolve(lock.name or p.root.name, args.name, args.modules, args.timezone)
        return provision.provision_main(args.port, lambda port: boards.select(p, port=port, probe=False),
                                        settings, args.timeout, not args.no_reset, args.log)
    if cmd == "test":
        return run_tests(p, lock, _chips(args, p, lock), args.port, args.module, args.unit_only,
                         _require_board_flag(args), extra)
    if cmd == "run":
        return flash.run(p, lock, args.chip, args.port, build.parse_defines(args.define), args.no_serial,
                         no_input=args.no_input, timestamps=args.timestamps, erase=not args.keep_nvs)
    if cmd == "manifest":
        if args.manifest_command == "show":
            return manifest.show(p, lock, args.json)
        return manifest.update(p, lock, args.names, args.to)
    if cmd == "doctor":
        return doctor.doctor(p, lock)
    if cmd == "release":
        return release.release(p, lock, args.release_version, args.matrix, args.notes)
    raise XeWeError(f"unknown command {cmd}", EXIT_USAGE)


def main(argv: list[str] | None = None) -> int:
    """Console-script entry point; returns the exit code."""
    argv = list(sys.argv[1:] if argv is None else argv)
    extra: list[str] = []
    if "--" in argv:
        i = argv.index("--")
        argv, extra = argv[:i], argv[i + 1:]
    args = parser().parse_args(argv)
    setup_logging(args.verbose)
    if extra and args.command != "test":
        log.error("arguments after -- are only accepted by `xewe test`")
        return EXIT_USAGE
    try:
        return dispatch(args, extra)
    except XeWeError as exc:
        log.error("%s", exc)
        return exc.code
    except KeyboardInterrupt:
        log.error("interrupted")
        return 130
