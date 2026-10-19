# xewe.board

Everything that touches a board over USB: the serial console, finding and selecting boards, and
answering the first-boot prompts. The contract is [doc/spec.md](../../../doc/spec.md) §5 (board
selection and `build/config/boards.toml`) and §8 (flash and serial).

- `serialio.py`: the serial console on top of pyserial (listen with timestamps, send, expect).
- `boards.py`: board detection, selection and `build/config/boards.toml`.
- `provision.py`: `xewe provision`, the first-boot prompts of a freshly flashed board.

## Serial and resets

The tools open the port without resetting the board; `--reset` or flashing resets it. The chip
resets on the control-line state DTR=0 & RTS=1 (native USB, ESP32-S3/C3/C6 USB-Serial/JTAG
`303a:1001`: `rst:0x15 (USB_UART_CHIP_RESET)`; bridge boards: EN pulled low). The kernel raises
DTR and RTS on open and pyserial applies DTR before RTS, so `Console.open` configures DTR high and
RTS low for the open (1/1 -> 1/0) and drops DTR afterwards (-> 0/0), never passing through 0/1.
A reset re-enumerates a native USB port and a slow passthrough can lose it, which is why opening
must not reset.

A board on the native USB-Serial/JTAG (VID:PID `303a:1001`) is flashed and probed with esptool
`--before usb-reset`, so esptool does not have to recognise the port from its name; every other
board gets `--before default-reset` (`before_reset`). This applies to `flash`, `run` and the
`boards` probe.

On Linux a port's USB ids are read from sysfs by device number (`/sys/dev/char/<major>:<minor>`,
`sysfs_board`), whatever the node is called. `listed_boards` takes pyserial's list and then scans
`/dev/xewe-usb/` too, so a node a host mirrors into a container under another name is found and
shown by `xewe boards`. Without sysfs (macOS, Windows) only pyserial's list is used.

After a reset, `xewe serial` shows the ROM lines (`ESP-ROM:...`, `rst:0x...`) and the boot log.
With `--send`, the command is sent right away when nothing boot-like arrives within 2 s of opening
(the normal case), or, if a boot is in progress, after `System Setup Complete` (up to
`--boot-timeout`, default 90 s: Wi-Fi + NTP can take ~30 s). An unprovisioned board (`Name your
device`) exits 1 without sending: run `xewe provision`. A `boot: ...` line records which case
happened.

The board's USB console queues at most 1024 received bytes and has no flow control, so the tools
split any write longer than 900 bytes into pieces 50 ms apart. During `xewe test`, a board test
that fails on a timeout makes the next test reopen the port once and send `$system uid`; a board
that still does not answer ends the session (`board on <port> is silent after a restart …;
power-cycle it`, exit 1) instead of failing every later test; a port that cannot be reopened ends
it with `board on <port> disappeared from USB …; power-cycle it`. A native-USB board can drop off
USB at the reset after a flash; `xewe test --no-flash` skips the write when the board already runs
the image (the board reports no build id, so the tools cannot check that for you).

## Provision

Flashing keeps NVS: a board flashed with `xewe flash` (no `--erase`) keeps its name, Wi-Fi and
module choices and asks nothing it has stored. `xewe run` (what `./run.sh` calls) erases the flash
first by default, so its board starts with a first boot (`--keep-nvs` keeps NVS). A board with
erased NVS stops at `Name your device`; then, in the sketch's declaration order, each module that
can be disabled asks `Would you like to enable <Name> module?` and an enabled module runs its own
setup (Wi-Fi: the network list and password; Time: whether the detected time is right; a template
module may ask its own values); then `Initial Setup Complete` and a restart. Only selected modules
are in the image: with `selected = []` there is no Wi-Fi or Time prompt at all (setup, build and
run print a `warning:` line). `xewe provision` resets the board and answers those prompts:

```sh
build/tools/.venv/bin/python -m xewe provision                # settings from the dotenv file
build/tools/.venv/bin/python -m xewe provision --port /dev/ttyACM0
```

Values come from flags, then the environment (`XEWE_DEVICE_NAME`, `XEWE_WIFI_SSID`,
`XEWE_WIFI_PASSWORD`, `XEWE_TIMEZONE`, `XEWE_PROVISION_MODULES`), then the dotenv file
([env/](../env/README.md)), then defaults (name from `xewe.toml`, all modules, accept the
detected timezone). The password has no flag and is printed as `********`. A prompt the tool has
no value for gets the prompt's default instead of failing, and each such auto-answer is logged:
the "enable?" question of a module not in `XEWE_PROVISION_MODULES`/`--modules` is answered `n`,
any other unknown prompt `n` at `(y/n) > ` and Enter at `> `.

A board whose earlier provisioning stopped part-way keeps what it stored and starts at a later
prompt after the reset, such as the Wi-Fi network list; the tool answers whatever prompt comes
first. The SSID is picked by its number in the list; an SSID not in the scan gets one rescan
(`-2`), then is entered as a custom SSID (`-3`). `! Invalid number` is answered again. One empty
"nudge" line is sent when no prompt appears within 10 s of the reset, never after the first
prompt; `--timeout` (default 60 s) bounds the wait from the reset to the first prompt. Exit 0 when
provisioned or already provisioned, 2 for missing or bad settings (before the port is touched), 1
when the board stops answering as expected, 4 without a board.

Known limitation: `xewe provision` answers a `(y/n)` prompt it does not know with `n`. A firmware
whose first boot asks its own questions, for example an LED setup wizard, must be answered by hand
once (with `xewe serial`); then `xewe provision --no-reset` can finish the Wi-Fi and module
prompts.

## Without a board

Every hardware command still compiles, then prints

```
compiled, not run: no board attached (<chip>, build/builds/<chip>/out/<image>.bin)
```

and exits 0. `xewe test` runs unit tests and reports board tests as "compiled, not run".
`--require-board` (or `XEWE_REQUIRE_BOARD=1`) turns this into exit 4 (`xewe test` too).

`--no-board` (or `XEWE_NO_BOARD=1`) is the hard switch for machines that must never touch a
board: no port is listed, probed or opened. `flash`/`run` build and then exit 4, `serial`,
`provision` and `boards` exit 4, all with `board access disabled (--no-board / XEWE_NO_BOARD)`;
`xewe test` reports board tests as "compiled, not run" and exits 0. Agents in compile-only mode
must set `XEWE_NO_BOARD=1`.
