# demo 3.1.0 release card

| Field | Value |
|---|---|
| Repository | xewe-labs/demo |
| Release | 3.1.0 (tag `3.1.0`) |
| Card generated | 2026-10-13T12:00:00Z, run `20261013T120000Z-abcd`, xewe-os-tools 3.1.0 (`c0ffeec0ffeec0ffeec0ffeec0ffeec0ffeec0ff`) |
| Tested commit | `1234567890abcdef1234567890abcdef12345678` |
| Tested tree | `sha256:229ee7bf123baa64bbba1a5e2b26d0b78cfd261c678a67e7b71a9780282b40a7` |
| Result | 2 unit passed; c3: 1 compile only, 3 compiled, not run; s3: 2 passed on hardware (board sn=A0:F2:62:F1:5E:DC), 1 compile only; 2 skipped; 1 xfail; 0 failed |

## 1. Release notes

```text
demo 3.1.0

What changed: everything.
```

## 2. Metadata

### Pins

| Component | Ref | Commit | Source |
|---|---|---|---|
| xewe-os-core | 3.1.0 | 207c2562023649c5fb53200365c9d58e9b86dbe3 | https://github.com/xewe-labs/xewe-os-core |
| xewe-os-modules | 3.1.0 | 3d5e319aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa | https://github.com/xewe-labs/xewe-os-modules |
| xewe-os-tools | 3.1.0 | - | https://github.com/xewe-labs/xewe-os-tools |
| FastLED | 3.10.3 | fa57ed | https://github.com/FastLED/FastLED |

### Toolchain and host

| xewe-os-tools | arduino-cli | esp32 core | Python | Host |
|---|---|---|---|---|
| 3.1.0 | 1.5.1 | 3.3.12 | 3.12.3 | Linux aarch64 |

### Images

| Chip | FQBN | Partition scheme | App partition (B) | Flash used (B) | % | Static RAM (B) | RAM max (B) | Warnings | Log |
|---|---|---|---|---|---|---|---|---|---|
| c3 | `esp32:esp32:esp32c3:CDCOnBoot=cdc,PartitionScheme=huge_app` | huge_app | 3,145,728 | 1,123,456 | 35.7 | 45,678 | 327,680 | 0 | 4-build-c3.compile.log |
| s3 | `esp32:esp32:esp32s3:CDCOnBoot=cdc,PartitionScheme=huge_app` | huge_app | 3,145,728 | 1,234,567 | 39.2 | 45,678 | 327,680 | 0 | 5-build-s3.compile.log |

## 3. Boards

| Chip | Board id | Port | Defines | Firmware sha256 | Meta timestamp | Banner match | Flash step | Flash time |
|---|---|---|---|---|---|---|---|---|
| s3 | A0:F2:62:F1:5E:DC | /dev/ttyACM0 | XEWE_MODULE_LED_PIN_DATA=48 | `4f40ffef0eef23b0bedf683f83fa2b054d1b422d9197d7fe6a48352c2368eb01` | 2026-10-13T11:58:00Z | yes | 7-flash-s3 | 1.5 s |

## 4. Validator and checks

| Step | Command | Exit | Time | Summary line |
|---|---|---|---|---|
| 1-setup | `build/step setup` | 0 | 1.5 s | setup complete |
| 2-validate | `build/step validate` | 0 | 1.5 s | 8 modules, 0 errors, 0 warnings |
| 10-check-format | `build/step format` | 0 | 1.5 s | format: 12 C++ and 3 Python files, 0 would reformat |
| 11-check-brand-lint | `build/step brand-lint` | 0 | 1.5 s | brand-lint: clean |

#### 1-setup (last 20 lines of 1-setup.log)

```text
installing into build/
Wi-Fi password «redacted:XEWE_WIFI_PASSWORD» stays in the dotenv file
setup complete
```

#### 2-validate (last 20 lines of 2-validate.log)

```text
8 modules, 0 errors, 0 warnings
```

#### 10-check-format (last 20 lines of 10-check-format.log)

```text
format: 12 C++ and 3 Python files, 0 would reformat
```

#### 11-check-brand-lint (last 20 lines of 11-check-brand-lint.log)

```text
brand-lint: clean
```

## 5. Tests

### Runs

| Run | Command | Chip | Board | Unit passed | Hardware passed | Compile-only passed | Compiled, not run | Skipped (other) | XFAIL | Failed | Exit |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 3-unit | `build/step unit` | - | - | 2 | 0 | 0 | 0 | 1 | 1 | 0 | 0 |
| 8-board-s3 | `build/step board-s3` | s3 | sn=A0:F2:62:F1:5E:DC | 0 | 2 | 1 | 0 | 1 | 0 | 0 | 0 |
| 9-board-c3 | `build/step board-c3` | c3 | - | 0 | 0 | 1 | 3 | 0 | 0 | 0 | 0 |

### Every test

| Test | Kind | Chip | Result | Detail |
|---|---|---|---|---|
| `modules/led/tests/board/test_led.py::test_render_priority` | board | c3 | SKIP (compiled, not run) | no board attached (board access disabled (--no-board / XEWE_NO_BOARD)) |
| `modules/led/tests/board/test_led.py::test_render_priority` | board | s3 | PASS (hardware) | board s3 sn=A0:F2:62:F1:5E:DC fw 4f40ffef0eef XEWE_MODULE_LED_PIN_DATA=48 |
| `modules/wifi/tests/unit/test_wifi.py::test_module_id` | unit | - | PASS | 0.00 s |
| `tests/board/test_fw.py::test_compiles` | board | c3 | PASS (compile only) | built for c3, no board needed |
| `tests/board/test_fw.py::test_compiles` | board | s3 | PASS (compile only) | built for s3, no board needed |
| `tests/board/test_fw.py::test_status` | board | c3 | SKIP (compiled, not run) | no board attached (board access disabled (--no-board / XEWE_NO_BOARD)) |
| `tests/board/test_fw.py::test_status` | board | s3 | PASS (hardware) | board s3 sn=A0:F2:62:F1:5E:DC fw 4f40ffef0eef XEWE_MODULE_LED_PIN_DATA=48 |
| `tests/board/test_ha.py::test_mqtt_round_trip` | board | c3 | SKIP (compiled, not run) | no board attached (board access disabled (--no-board / XEWE_NO_BOARD)) |
| `tests/board/test_ha.py::test_mqtt_round_trip` | board | s3 | SKIP | needs an MQTT broker (XEWE_TEST_HA_MQTT_HOST) |
| `tests/unit/test_logic.py::test_golden_tree` | unit | - | SKIP | XEWE_LED_OS_REPO not set |
| `tests/unit/test_logic.py::test_known_gap` | unit | - | XFAIL | hex parser rejects #RGB (F9) |
| `tests/unit/test_logic.py::test_logic` | unit | - | PASS | 0.00 s |

## 6. Local logs

The record of this run in `releases/3.1.0/logs` (not committed, not published).

| File | Bytes | sha256 |
|---|---|---|
| 1-setup.build_config.toml | 622 | `6ccc85f02e2acbd1b2dda0b1e9d4846b38c187a6d61f1b0c88f6aaf3a852ac9f` |
| 1-setup.log | 110 | `3170c5127264df2e8da4beaa29759d77d2d7f88e06c91edd9488d428480232d1` |
| 10-check-format.log | 52 | `09f3e4155658c963e349d88483950ef589a43b4bb2ee36175c8194ac09dc5845` |
| 11-check-brand-lint.log | 18 | `bda010d633eeae56194d6295464ea2c0e0977f69156569e7282df247d0373ae7` |
| 2-validate.log | 32 | `390f25d997a1ca5b0e0fd5ae8cd9f460880e61a8481eb0c1dc46d13c9d5df7c8` |
| 3-unit.log | 9 | `518b6d28ff982ea8d41d40c2c75a35dc1359b47c27c5969dc01e8ec9419402fd` |
| 3-unit.xml | 1,927 | `2adb48ebe7b653cfc3bb2371c6fd8fb147878b8405cf0983ef361d52e29c682e` |
| 4-build-c3.compile.log | 209 | `6c93c55061a8d5a9ff49aa6aa3e44a63fc414845d7ac1e6d4323de4b5ecbbc11` |
| 4-build-c3.log | 61 | `6a8a7ae6e56f9293b453dc64ff2c0d33c44f78f49360793201540d94d4d9ef77` |
| 4-build-c3.meta.json | 291 | `b731cf023859983a1687dbf08f0e3c214817ec20d875f10620ee0212cc2b23bc` |
| 5-build-s3.compile.log | 209 | `a9625b31680e956ec664d170b03ec497b9d1a7fc1f13ef0e7764172b0530e0e8` |
| 5-build-s3.log | 61 | `fbfbc8e4b45aedb9cbda512e19bc2f8f400665a9ccc30dcc9c6c63011e014224` |
| 5-build-s3.meta.json | 291 | `12e4a0ef9ad24e4938e159733b11bd322779c7fbb7efdc1817c80610cadc15e3` |
| 6-flash-s3.log | 38 | `f953dbe9b2b69832e19a89c147f2406721d347ba07020f8306dce4cf62cbc777` |
| 6-flash-s3.meta.json | 329 | `a13ba8ca11a6eea93140080416ec536bf1e6ab0e7b3d17710f94aa4aa7d4d815` |
| 7-flash-s3.log | 35 | `4b5583488b54f9adcf20f52c3e119bd4ea4428bd2bf24e97c3e2abd984ffb77b` |
| 7-flash-s3.meta.json | 329 | `a13ba8ca11a6eea93140080416ec536bf1e6ab0e7b3d17710f94aa4aa7d4d815` |
| 8-board-s3.log | 20 | `97659866cf3da7125c6e6065e76d6ed0eac51b5555c7a21f8e867c291c62674f` |
| 8-board-s3.xml | 2,619 | `c0d8e7412b95a46e92c1dff334e90a1ef009ee9dc430fcd31dc1cafc4eaeab87` |
| 9-board-c3.log | 20 | `96363075e5abcb5427f7c70f96fe8190303431ebce91ea5d2e7729e89bb8f28f` |
| 9-board-c3.xml | 2,577 | `c0af96f0159b0792e9ae4a9196922b9b99d3a6464dcc3a248cfedd7c555fd645` |
| run.json | 9,559 | `394b84ef6937c81ec51c0f8575bee68198763f0d9530e31f6b23a4111a7c685f` |

## 7. Not covered

- Chips without a hardware run: c3.
- Skipped, by reason:
  - XEWE_LED_OS_REPO not set (1): `tests/unit/test_logic.py::test_golden_tree`
  - needs an MQTT broker (XEWE_TEST_HA_MQTT_HOST) (1): `tests/board/test_ha.py::test_mqtt_round_trip [s3]`
  - compiled, not run: no board attached (board access disabled (--no-board / XEWE_NO_BOARD)) (3): `modules/led/tests/board/test_led.py::test_render_priority [c3]`, `tests/board/test_fw.py::test_status [c3]`, `tests/board/test_ha.py::test_mqtt_round_trip [c3]`
- Manual checks: tests/MANUAL.md and the BOARD-TEST-PLAN ids are not part of this card; the release notes name what was checked by hand.

## 8. Retries

| Earlier attempt | Attempt | Exit | Time | Log |
|---|---|---|---|---|
| 6-flash-s3 | 1 | 2 | 1.5 s | 6-flash-s3.log |

<!-- xewe-card-data {"schema":1,"tree":"sha256:229ee7bf123baa64bbba1a5e2b26d0b78cfd261c678a67e7b71a9780282b40a7","run":"20261013T120000Z-abcd","tests":[["modules/led/tests/board/test_led.py::test_render_priority","board","c3","SKIP (compiled, not run)"],["modules/led/tests/board/test_led.py::test_render_priority","board","s3","PASS (hardware)"],["modules/wifi/tests/unit/test_wifi.py::test_module_id","unit","-","PASS"],["tests/board/test_fw.py::test_compiles","board","c3","PASS (compile only)"],["tests/board/test_fw.py::test_compiles","board","s3","PASS (compile only)"],["tests/board/test_fw.py::test_status","board","c3","SKIP (compiled, not run)"],["tests/board/test_fw.py::test_status","board","s3","PASS (hardware)"],["tests/board/test_ha.py::test_mqtt_round_trip","board","c3","SKIP (compiled, not run)"],["tests/board/test_ha.py::test_mqtt_round_trip","board","s3","SKIP"],["tests/unit/test_logic.py::test_golden_tree","unit","-","SKIP"],["tests/unit/test_logic.py::test_known_gap","unit","-","XFAIL"],["tests/unit/test_logic.py::test_logic","unit","-","PASS"]],"images":{"c3":{"sketch_size":1123456,"static_ram":45678,"config":{}},"s3":{"sketch_size":1234567,"static_ram":45678,"config":{}}},"exclude":["releases/","releases/3.1.0/logs/"],"logs":{"1-setup.build_config.toml":"6ccc85f02e2acbd1b2dda0b1e9d4846b38c187a6d61f1b0c88f6aaf3a852ac9f","1-setup.log":"3170c5127264df2e8da4beaa29759d77d2d7f88e06c91edd9488d428480232d1","10-check-format.log":"09f3e4155658c963e349d88483950ef589a43b4bb2ee36175c8194ac09dc5845","11-check-brand-lint.log":"bda010d633eeae56194d6295464ea2c0e0977f69156569e7282df247d0373ae7","2-validate.log":"390f25d997a1ca5b0e0fd5ae8cd9f460880e61a8481eb0c1dc46d13c9d5df7c8","3-unit.log":"518b6d28ff982ea8d41d40c2c75a35dc1359b47c27c5969dc01e8ec9419402fd","3-unit.xml":"2adb48ebe7b653cfc3bb2371c6fd8fb147878b8405cf0983ef361d52e29c682e","4-build-c3.compile.log":"6c93c55061a8d5a9ff49aa6aa3e44a63fc414845d7ac1e6d4323de4b5ecbbc11","4-build-c3.log":"6a8a7ae6e56f9293b453dc64ff2c0d33c44f78f49360793201540d94d4d9ef77","4-build-c3.meta.json":"b731cf023859983a1687dbf08f0e3c214817ec20d875f10620ee0212cc2b23bc","5-build-s3.compile.log":"a9625b31680e956ec664d170b03ec497b9d1a7fc1f13ef0e7764172b0530e0e8","5-build-s3.log":"fbfbc8e4b45aedb9cbda512e19bc2f8f400665a9ccc30dcc9c6c63011e014224","5-build-s3.meta.json":"12e4a0ef9ad24e4938e159733b11bd322779c7fbb7efdc1817c80610cadc15e3","6-flash-s3.log":"f953dbe9b2b69832e19a89c147f2406721d347ba07020f8306dce4cf62cbc777","6-flash-s3.meta.json":"a13ba8ca11a6eea93140080416ec536bf1e6ab0e7b3d17710f94aa4aa7d4d815","7-flash-s3.log":"4b5583488b54f9adcf20f52c3e119bd4ea4428bd2bf24e97c3e2abd984ffb77b","7-flash-s3.meta.json":"a13ba8ca11a6eea93140080416ec536bf1e6ab0e7b3d17710f94aa4aa7d4d815","8-board-s3.log":"97659866cf3da7125c6e6065e76d6ed0eac51b5555c7a21f8e867c291c62674f","8-board-s3.xml":"c0d8e7412b95a46e92c1dff334e90a1ef009ee9dc430fcd31dc1cafc4eaeab87","9-board-c3.log":"96363075e5abcb5427f7c70f96fe8190303431ebce91ea5d2e7729e89bb8f28f","9-board-c3.xml":"c0af96f0159b0792e9ae4a9196922b9b99d3a6464dcc3a248cfedd7c555fd645","run.json":"394b84ef6937c81ec51c0f8575bee68198763f0d9530e31f6b23a4111a7c685f"},"repo":"xewe-labs/demo","version":"3.1.0","commit":"1234567890abcdef1234567890abcdef12345678","chips":["c3","s3"],"hardware":["s3"],"require":["setup","validate","unit","build","board"],"card_sha256":"ce5d141140ce631c00a9ebc9e465508a769e5aa5288cf578454106b65d201080"} -->
