import tomllib

from xewe.modules import tomlw


def test_round_trip_all_value_types() -> None:
    data = {
        "schema": 1,
        "flag": True,
        "ratio": 0.5,
        "name": 'quote " backslash \\ tab \t newline \n',
        "list": ["a", "b"],
        "table": {"key": "v", "inline": {"ref": "1.0.0", "nested": {"x": 1}}, "empty": {}},
        "with space": {"weird key": "x"},
        "board": [{"port": "/dev/ttyACM0", "chip": "c3"}, {"port": "COM3"}],
    }
    text = tomlw.dumps(data, header="# header line")
    assert text.startswith("# header line\n")
    assert tomllib.loads(text) == data


def test_header_comments() -> None:
    assert tomlw.header_comments("# a\n# b\nschema = 1\n# c\n") == "# a\n# b"
