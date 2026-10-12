"""A minimal device web UI prototype for `xewe webui export` (doc/webui.md): one fan and one LED
strip as in-memory state, served with fake data. Run it with `pip install flask` and
`python app.py` (http://127.0.0.1:5000)."""

from copy import deepcopy

from flask import Flask, jsonify, render_template, request

app = Flask(__name__)

STATE = {
    "fan": {"speed": 40, "rpm": 1180},
    "led": {"on": False, "color": "#00c2ff"},
}


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/state", methods=["GET"])
def api_state():
    return jsonify(deepcopy(STATE))


@app.route("/api/fan", methods=["POST"])
def api_fan():
    payload = request.get_json(silent=True) or {}
    try:
        STATE["fan"]["speed"] = max(0, min(100, int(payload["speed"])))
    except (KeyError, TypeError, ValueError):
        return jsonify({"ok": False}), 400
    return jsonify({"ok": True, "fan": deepcopy(STATE["fan"])})


@app.route("/api/led", methods=["POST"])
def api_led():
    payload = request.get_json(silent=True) or {}
    if isinstance(payload.get("on"), bool):
        STATE["led"]["on"] = payload["on"]
    if isinstance(payload.get("color"), str):
        STATE["led"]["color"] = payload["color"]
    return jsonify({"ok": True, "led": deepcopy(STATE["led"])})


if __name__ == "__main__":
    app.run(debug=True)
