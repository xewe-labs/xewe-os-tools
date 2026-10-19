# tests/fixtures

Input files and golden outputs of the self-tests, read through `FIXTURES` in `tests/conftest.py`.
What each one is for: [tests/README.md](../README.md).

- `esp32-core/`: the `PartitionScheme` menu of the esp32 3.3.12 `boards.txt` and six of its
  partition CSVs.
- `module_properties/`: the real `module.properties` of six modules.
- `releasecard/`: JUnit and core host inputs and `golden_card.md` for the release card tests.
- `webui/`: the Flask example `fan-dashboard/` and its golden export `fan-dashboard_export/`.
- `manifest-2.0.0-c3.json`: the golden ESP Web Tools manifest of a c3 build.
- `release_matrix_43.csv`: a 43-row release matrix (c3 7, c6 14, s3 22 rows).
- `setup.sh`: a copy of the template's `setup.sh`, for the bootstrap string checks.

Do not add files inside `webui/fan-dashboard_export/`: the webui tests compare that whole tree with
a fresh export, so any extra file fails them.
