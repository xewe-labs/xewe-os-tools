"""``xewe provision``: answer the first-boot prompts of a freshly flashed board (SPEC §8).

Settings come from flags, then ``XEWE_*`` environment variables, then the dotenv file
(:mod:`xewe.env.dotenv`: ``--env``/``--from FILE``, ``XEWE_ENV``, ``.env`` in the project, ``.env`` in the
tools checkout), then defaults. The Wi-Fi password has no flag, so it never appears in argv; the
console masks every line that contains it.

The firmware prompts (xewe-os-core ``XeWeOs.cpp``/``Module.cpp``/``Serial.cpp``, modules
``Wifi.cpp``/``Time.cpp``) wait forever and drop input typed before the prompt is printed
(``get_core`` calls ``clear_input`` first), so each answer is sent only after its input line
(``> `` or ``(y/n) > ``) has been seen.

A prompt this tool has no answer for gets the prompt's default instead of failing: a module's
"enable?" question for a module left out of ``XEWE_PROVISION_MODULES``/``--modules`` is answered
``n``, and any other input line nothing was queued for (e.g. the template's ``Starting level
(0-100)?``) is answered ``n`` at ``(y/n) > `` and an empty line (Enter) at ``> ``. A bounded
firmware prompt takes an empty line as a wrong answer and, once its attempts are used, returns its
default. Each such auto-answer is logged.
"""

from __future__ import annotations

import os
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import serial

from xewe.report import EXIT_FAIL, EXIT_NO_BOARD, EXIT_OK, EXIT_USAGE, NO_BOARD, XeWeError, log, result
from xewe.board.serialio import (
    BOOT_READY,
    BOOT_UNPROVISIONED,
    TAIL_LINES,
    Console,
    ExpectTimeout,
    wait_for_banner,
)

START_TIMEOUT = 60.0
"""Default ``--timeout``: reset to the first prompt (or to the banner of a provisioned board)."""
PROMPT_TIMEOUT = 30.0
"""Longest wait for the next prompt (or progress line) after an answer; covers the Wi-Fi scan."""
NUDGE_SECONDS = 10.0
"""No prompt after this long: send one empty line so a board waiting at the name prompt prints it
again (an empty name is answered ``n`` at its confirmation; anywhere else an empty line is
ignored or re-prompts)."""
REBOOT_TIMEOUT = 90.0
"""``Rebooting`` to ``System Setup Complete``: reboot, Wi-Fi join, NTP."""
NAME_MAX = 254

TIMEZONE_RE = re.compile(r"^GMT([+-])(\d\d):(\d\d)$")

# Event patterns, tested in this order against every line the board prints. Inside a Wi-Fi network
# list (after ``Scanning WiFi networks...``, up to ``Selection:``) a ``N. <ssid>`` line is always a
# network: an SSID may contain spaces, punctuation, a leading ``!`` or text that looks like a prompt.
EVENTS: list[tuple[str, re.Pattern[str]]] = [
    ("initial", re.compile(r"Initial Setup Complete")),
    ("ready", re.compile(BOOT_READY)),
    ("name", re.compile(BOOT_UNPROVISIONED)),
    ("confirm", re.compile(r'^Confirm "(.*)"\?\s*$')),
    ("module", re.compile(r"Would you like to enable (.+?) module\?")),
    ("scan", re.compile(r"Scanning WiFi networks")),
    ("selection", re.compile(r"^Selection:\s*$")),
    ("custom", re.compile(r"Enter custom SSID")),
    ("password", re.compile(r"^Password:")),
    ("join_failed", re.compile(r"Unable to join|Invalid choice|Terminated WiFi setup")),
    ("time", re.compile(r"Is your time: .*\(([^)]*)\)\?")),
    ("tz", re.compile(r"Enter your timezone offset")),
    ("input", re.compile(r"^(?:\(y/n\) )?> $")),
    ("invalid", re.compile(r"^! Invalid number")),
    ("error", re.compile(r"^! ")),
    ("progress", re.compile(r"Joined |Detecting Timezone|Timezone set|\w Setup\b")),
]
MODULE_RE = dict(EVENTS)["module"]
BOX_RULE_RE = re.compile(r"^[\s+\-=_*#\u2500-\u257f]*$")
"""A box border line (``+----+`` or box-drawing characters): no text of its own."""
BOX_EDGE = " \t|\u2502\u2503\u2551"
"""Stripped from both ends of a boxed line (``|  text  |``)."""
WRAP_LOOKBACK = 40
"""At most this many lines before an unexpected ``(y/n) > `` are joined to look for a wrapped
module question (``Driver._wrapped_module``)."""
NETWORK_RE = re.compile(r"^(\d+)\. (.*)$")
"""One entry of ``Wifi::scan``'s list (``0. My Network``); only looked for inside a list."""
ANY_EVENT = "|".join(f"(?:{rx.pattern})" for rx in [*(rx for _, rx in EVENTS), NETWORK_RE])
START_KINDS = frozenset(k for k, _ in EVENTS) - {"initial", "progress"}
START_EVENT = "|".join(f"(?:{rx.pattern})" for k, rx in EVENTS if k in START_KINDS)
"""What ends the wait after the reset: any prompt or input line, a rejected answer, or the banner of
a provisioned board. A board whose earlier provisioning stopped part-way skips what it has stored
(name, module choices) and starts at a later prompt, e.g. the Wi-Fi network list."""
RESCANS = 1
"""SSID not in the scan: rescan (``-2``) this many times, then enter it as a custom SSID (``-3``)."""
MAX_SELECTIONS = 4
JOIN_RETRIES = 1
"""``Unable to join``: the firmware prints the network list again; answer it again (same SSID, same
password) this many times before giving up. A single failed join is common right after a reset."""
MAX_INVALID = 3
MAX_AUTO = 16
"""More auto-answers than this in one run: the board is stuck in a prompt; give up."""
AUTO = "auto"
"""Prompt kind of an auto-answer (the prompt's default: ``n`` or Enter)."""

# --------------------------------------------------------------------------- settings


@dataclass
class Settings:
    """What to answer. ``modules`` None means every module."""

    name: str
    modules: frozenset[str] | None = None
    ssid: str | None = None
    password: str | None = field(default=None, repr=False)
    timezone: str | None = None

    def enables(self, slug: str) -> bool:
        return self.modules is None or slug in self.modules

    @property
    def wifi(self) -> bool:
        return self.enables("wifi")


def slug(name: str) -> str:
    """Module display name or slug → slug (``Web Interface`` → ``web-interface``)."""
    return re.sub(r"[\s_]+", "-", name.strip().lower())


def parse_modules(text: str) -> frozenset[str] | None:
    """``all`` → None; ``none`` or empty → no modules; else a comma-separated list of slugs or names."""
    t = text.strip().lower()
    if t == "all":
        return None
    if t in ("", "none"):
        return frozenset()
    return frozenset(slug(x) for x in t.split(",") if x.strip())


def check_timezone(tz: str) -> str:
    """Validate ``GMT[+-]HH:MM`` (the range the firmware accepts); exit 2 otherwise."""
    m = TIMEZONE_RE.match(tz)
    if not m or int(m[2]) > 14 or int(m[3]) >= 60 or (int(m[2]) == 14 and int(m[3]) > 0):
        raise XeWeError(f"timezone {tz!r} is not GMT+HH:MM or GMT-HH:MM (e.g. GMT-08:00, GMT+05:30)", EXIT_USAGE)
    return tz


def resolve(
    default_name: str,
    name: str | None = None,
    modules: str | None = None,
    timezone: str | None = None,
    env: Mapping[str, str] | None = None,
) -> Settings:
    """Merge flags, environment (the dotenv file is already applied to it) and defaults; exit 2 on a
    bad combination."""
    env = os.environ if env is None else env

    def pick(flag: str | None, key: str) -> str | None:
        for value in (flag, env.get(key)):
            if value is not None and value != "":
                return value
        return None

    s = Settings(
        name=(pick(name, "XEWE_DEVICE_NAME") or default_name).strip(),
        ssid=pick(None, "XEWE_WIFI_SSID"),
        password=pick(None, "XEWE_WIFI_PASSWORD"),
    )
    mods = modules if modules is not None else env.get("XEWE_PROVISION_MODULES")
    s.modules = parse_modules(mods) if mods is not None else None
    tz = pick(timezone, "XEWE_TIMEZONE")
    s.timezone = check_timezone(tz.strip().upper()) if tz else None

    if not s.name or len(s.name) > NAME_MAX or "\n" in s.name:
        raise XeWeError(f"device name must be 1..{NAME_MAX} characters on one line", EXIT_USAGE)
    if s.wifi:
        missing = [k for k, v in (("XEWE_WIFI_SSID", s.ssid), ("XEWE_WIFI_PASSWORD", s.password)) if not v]
        if missing:
            raise XeWeError(
                f"wifi is enabled but {' and '.join(missing)} {'is' if len(missing) == 1 else 'are'} not set "
                "(put them in .env, see .env.example; or the environment, or --env FILE); "
                "set them, or leave wifi out of --modules", EXIT_USAGE)
    else:
        for dep in ("time", "web-interface", "scheduler"):
            if s.modules and dep in s.modules:
                log.warning("%s needs wifi; the firmware will leave it disabled", dep)
    return s


# --------------------------------------------------------------------------- driver


class ProvisionFailed(Exception):
    """The board did something unexpected or went quiet; ends the command with exit 1."""


@dataclass
class Outcome:
    """What was answered (for the summary line)."""

    enabled: list[str] = field(default_factory=list)
    declined: list[str] = field(default_factory=list)
    ssid: str | None = None
    ssid_how: str = ""
    timezone: str = "not asked"
    name_asked: bool = False
    join_failures: int = 0
    auto: list[str] = field(default_factory=list)
    """Prompts answered with their default (see ``Driver._auto``)."""


class Driver:
    """Answers the first-boot prompts as they appear (see EVENTS)."""

    def __init__(self, console: Console, settings: Settings) -> None:
        self.console = console
        self.s = settings
        self.out = Outcome()
        self.pending: tuple[str, str] | None = None
        """(prompt kind, answer) waiting for the board's input line."""
        self.last: tuple[str, str] | None = None
        """(prompt kind, answer) sent last; answered again after ``! Invalid number``."""
        self.listing = False
        self.networks: dict[int, str] = {}
        self.rescans = 0
        self.seen: dict[str, int] = {}
        self.mark = 0
        """``console.lines`` index just after the last answer sent: a wrapped question starts here."""
        self.module = ""
        """Display name of the module whose "enable?" question came last (for the auto-answer log)."""

    def classify(self, line: str) -> tuple[str, re.Match[str]] | None:
        """The event a line is (counted in ``seen``), or None for a line that is none."""
        if self.listing:
            nm = NETWORK_RE.match(line)
            if nm:
                return "network", nm
        for kind, rx in EVENTS:
            mm = rx.search(line)
            if mm:
                self.seen[kind] = self.seen.get(kind, 0) + 1
                return kind, mm
        return None

    def _next(self, timeout: float) -> tuple[str, re.Match[str]]:
        while True:
            m = self.console.expect(ANY_EVENT, timeout=timeout)
            event = self.classify(m.string)
            if event is not None:
                return event

    def _answer(self, kind: str, value: str) -> None:
        if self.pending is not None:
            raise ProvisionFailed("two prompts without an input line between them")
        self.pending = (kind, value)

    def run(self, kind: str, m: re.Match[str]) -> None:
        """From the first event after the reset (see START_EVENT) to ``Initial Setup Complete``.

        No nudge here: the board has printed a prompt, so silence means it is busy (Wi-Fi scan,
        join, NTP), and an empty line would only be taken as a wrong answer.
        """
        self._handle(kind, m)
        while True:
            try:
                kind, m = self._next(PROMPT_TIMEOUT)
            except ExpectTimeout:
                raise ProvisionFailed(f"no prompt from the board within {PROMPT_TIMEOUT:g} s") from None
            if kind == "initial":
                return
            self._handle(kind, m)

    def _select_network(self) -> str:
        s = self.s
        self.listing = False
        if self.seen["selection"] > MAX_SELECTIONS:
            raise ProvisionFailed(f"the board asked for the Wi-Fi network {MAX_SELECTIONS + 1} times")
        if not s.ssid:
            raise ProvisionFailed("the board asks for Wi-Fi but no XEWE_WIFI_SSID is set")
        listed = sorted(self.networks.items())
        number = next((n for n, name in listed if name == s.ssid), None)
        if number is None:
            number = next((n for n, name in listed if name.strip() == s.ssid.strip()), None)
        self.out.ssid = s.ssid
        if number is not None:
            self.out.ssid_how = f"network {number} of {len(self.networks)}"
            return str(number)
        if self.rescans < RESCANS:
            self.rescans += 1
            log.info("network %r is not in the scan (%d networks); rescanning", s.ssid, len(self.networks))
            return "-2"
        self.out.ssid_how = "custom SSID, not in the scan"
        return "-3"

    def _handle(self, kind: str, m: re.Match[str]) -> None:
        s = self.s
        if kind == "input":
            if self.pending is None:
                wrapped = self._wrapped_module()
                if wrapped is None:
                    self._auto(m[0])
                    return
                self._handle("module", wrapped)
                assert self.pending is not None
            self.last, self.pending = self.pending, None
            self.console.send(self.last[1])
            self.mark = len(self.console.lines)
        elif kind == "name":
            if self.seen["name"] > 2:
                raise ProvisionFailed("the board keeps asking for the device name")
            self.out.name_asked = True
            self._answer(kind, s.name)
        elif kind == "confirm":
            self._answer(kind, "y" if m[1] == s.name else "n")  # "n" re-asks the name (e.g. after a nudge)
        elif kind == "module":
            self.module = m[1]
            mod = slug(m[1])
            yes = s.enables(mod)
            (self.out.enabled if yes else self.out.declined).append(mod)
            if not yes:
                log.info("auto-answer n (the default) to 'Would you like to enable %s module?': "
                         "%s is not in XEWE_PROVISION_MODULES/--modules", m[1], mod)
            self._answer(kind, "y" if yes else "n")
        elif kind == "scan":
            self.networks = {}
            self.listing = True
        elif kind == "network":
            self.networks[int(m[1])] = m[2]
        elif kind == "selection":
            self._answer(kind, self._select_network())
        elif kind == "custom":
            self._answer(kind, s.ssid or "")
        elif kind == "password":
            if not s.password:
                raise ProvisionFailed("the board asks for the Wi-Fi password but XEWE_WIFI_PASSWORD is not set")
            self._answer(kind, s.password)
        elif kind == "join_failed":
            if m[0] == "Unable to join" and self.out.join_failures < JOIN_RETRIES:
                self.out.join_failures += 1
                log.warning("Wi-Fi join failed; answering the network list again (retry %d of %d)",
                            self.out.join_failures, JOIN_RETRIES)
                return
            raise ProvisionFailed(f"Wi-Fi setup failed ({m[0]}); check XEWE_WIFI_SSID and XEWE_WIFI_PASSWORD")
        elif kind == "time":
            if s.timezone:
                self.out.timezone = f"n, set {s.timezone} (board detected {m[1]})"
                self._answer(kind, "n")
            else:
                self.out.timezone = f"y, board detected {m[1]}"
                self._answer(kind, "y")
        elif kind == "tz":
            if not s.timezone:
                raise ProvisionFailed("the board could not detect the timezone and asks for it; "
                                      "set XEWE_TIMEZONE or --timezone GMT+HH:MM and run again")
            if self.seen["tz"] > 1:
                raise ProvisionFailed(f"the board rejected the timezone {s.timezone}")
            if self.out.timezone == "not asked":
                self.out.timezone = f"set {s.timezone} (board could not detect it)"
            self._answer(kind, s.timezone)
        elif kind == "invalid":
            # get_int re-prompts with "> " only. The one integer prompt of first boot is the Wi-Fi
            # selection: answer it again; after the nudge (list printed before the port was opened,
            # numbers unknown) rescan so the list is printed again.
            if self.seen["invalid"] > MAX_INVALID:
                raise ProvisionFailed(f"the board rejected an answer {MAX_INVALID + 1} times: {m.string.strip()}")
            if self.last is None:
                self._answer("selection", "-2")
            elif self.last[0] == AUTO:
                pass  # the firmware asks again with "> " (auto-answered again) or returns its default
            elif self.last[0] == "selection":
                self._answer(*self.last)
            else:
                raise ProvisionFailed(f"the board rejected an answer: {m.string.strip()}")
        elif kind == "error":
            if self.last is not None and self.last[0] == AUTO and self.seen["error"] <= MAX_INVALID:
                return  # e.g. "! Timeout." or "! Out of range" after an auto-answer: the prompt goes on
            if self.last is None:
                raise ProvisionFailed(f"the board was waiting at a prompt printed before the port was opened "
                                      f"({m.string.strip()}); run again without --no-reset")
            raise ProvisionFailed(f"the board rejected an answer: {m.string.strip()}")
        elif kind == "ready":
            raise ProvisionFailed("the board finished booting without 'Initial Setup Complete'")
        # "progress": nothing to answer; the prompt timer restarts

    def _auto(self, input_line: str) -> None:
        """Answer an input line nothing was queued for with the prompt's default and log it."""
        if len(self.out.auto) >= MAX_AUTO:
            raise ProvisionFailed("the board keeps asking for input this tool does not know")
        answer = "n" if input_line.startswith("(y/n)") else ""
        prompt = self._before().strip()
        where = f" (module {self.module})" if self.module else ""
        log.info("auto-answer %s (the prompt's default) to %r%s: not a prompt this tool knows",
                 "n" if answer else "Enter", prompt, where)
        self.out.auto.append(prompt)
        self.last = (AUTO, answer)
        self.console.send(answer)
        self.mark = len(self.console.lines)

    def _wrapped_module(self) -> re.Match[str] | None:
        """The module question of a boxed header wrapped over several lines, or None.

        ``Serial::print_header`` word-wraps at the box width, so a long question such as ``Would you
        like to enable Home Assistant module?`` arrives as ``|  …enable Home Assistant  |`` and
        ``|  module?  |``, and the per-line pattern never matches. Here the lines printed since the
        last answer are joined: border lines dropped, edges stripped,
        whitespace collapsed; then the same pattern is searched once more.
        """
        lines = self.console.lines
        start = max(min(self.mark, len(lines)), len(lines) - WRAP_LOOKBACK)
        parts = [ln.strip(BOX_EDGE) for ln in lines[start:] if not BOX_RULE_RE.match(ln)]
        text = " ".join(" ".join(parts).split())
        found = None
        for found in MODULE_RE.finditer(text):
            pass
        if found is not None:
            self.seen["module"] = self.seen.get("module", 0) + 1
            log.info("module question wrapped over several lines: %r", found[0])
        return found

    def _before(self) -> str:
        lines = self.console.lines
        return lines[-2] if len(lines) >= 2 else ""


def _tail(console: Console) -> None:
    log.error("last %d lines:", TAIL_LINES)
    for line in console.lines[-TAIL_LINES:]:
        log.error("  %s", line)


def _wait_start(console: Console, timeout: float, reset: bool) -> re.Match[str]:
    """Reset (unless ``reset`` is False) and wait for the first prompt or the boot banner.

    The nudge is considered only here, before any prompt was seen after the reset: after
    ``NUDGE_SECONDS`` without one, one empty line is sent. A board already waiting at the name
    prompt (printed before the port was open) answers it with ``Confirm ""?``, which the driver
    declines so the name prompt is printed again; one waiting at the Wi-Fi list answers
    ``! Invalid number``, which the driver answers with a rescan.
    """
    first = min(NUDGE_SECONDS, timeout)
    try:
        return wait_for_banner(console, START_EVENT, first, reset=reset)
    except ExpectTimeout:
        if timeout <= first:
            raise
    log.info("no prompt yet after %g s; sending an empty line so the board prints its prompt again", first)
    console.send("")
    return wait_for_banner(console, START_EVENT, timeout - first, reset=False)


def _summary(s: Settings, out: Outcome, seconds: float) -> str:
    mods = ", ".join(out.enabled) or "none asked"
    parts = [f'name "{s.name}"' if out.name_asked else "name: kept from board", f"modules {mods}"]
    if out.declined:
        parts.append(f"declined {', '.join(out.declined)}")
    if out.ssid:
        retried = f", joined after {out.join_failures} failed join(s)" if out.join_failures else ""
        parts.append(f'wifi "{out.ssid}" ({out.ssid_how}{retried})')
    else:
        parts.append("wifi not configured")
    parts.append(f"timezone {out.timezone}")
    if out.auto:
        parts.append(f"{len(out.auto)} prompt(s) answered with their default")
    return f"provisioned in {seconds:.0f} s: " + "; ".join(parts)


def provision_main(
    port_flag: str | None,
    select_board: Callable[[str | None], Any],
    settings: Settings,
    timeout: float = START_TIMEOUT,
    reset: bool = True,
    log_path: Path | None = None,
    baud: int = 115200,
    factory: Callable[[], Any] | None = None,
) -> int:
    """``xewe provision``: settings are already resolved (bad ones exited 2 before this)."""
    board = select_board(port_flag)
    if board is None:
        log.error("%s; nothing to provision", NO_BOARD)
        return EXIT_NO_BOARD
    start = time.monotonic()
    secrets = [settings.password] if settings.password else []
    console = Console(board.port, baud, factory=factory, echo=True, log_path=log_path, secrets=secrets)
    try:
        console.open()
    except (serial.SerialException, OSError) as exc:
        console.close()
        raise XeWeError(f"cannot open {board.port}: {exc}", EXIT_FAIL) from None
    with console:
        try:
            m = _wait_start(console, timeout, reset)
            driver = Driver(console, settings)
            event = driver.classify(m.string)
            assert event is not None  # START_EVENT is made of EVENTS
            if event[0] == "ready":
                result(f"already provisioned: board on {board.port} printed 'System Setup Complete'; nothing sent")
                return EXIT_OK
            driver.run(*event)
            try:
                wait_for_banner(console, r"Rebooting", PROMPT_TIMEOUT, reset=False)
                m = wait_for_banner(console, f"{BOOT_READY}|{BOOT_UNPROVISIONED}", REBOOT_TIMEOUT, reset=False)
            except ExpectTimeout:
                raise ProvisionFailed("the board did not finish booting after 'Initial Setup Complete'") from None
            if m[0] == BOOT_UNPROVISIONED:
                raise ProvisionFailed("the board asked for its name again after the reboot (settings not saved?)")
        except ExpectTimeout:
            log.error("board on %s printed no first-boot prompt (e.g. %r) and no %r within %g s", board.port,
                      BOOT_UNPROVISIONED, BOOT_READY, timeout)
            _tail(console)
            return EXIT_FAIL
        except ProvisionFailed as exc:
            log.error("%s", exc)
            _tail(console)
            return EXIT_FAIL
        except XeWeError as exc:  # port gone for good
            log.error("%s", exc)
            _tail(console)
            return exc.code
        result(_summary(settings, driver.out, time.monotonic() - start))
    return EXIT_OK
