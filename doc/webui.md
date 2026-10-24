# Web UI prototypes: `xewe webui`

A device's web page is easier to build as a small Flask app on a laptop than in C++ on the board:
HTML, CSS and JavaScript reload in a second against fake data. `xewe webui export` then turns the
page into C++ that an ESP32 sketch serves at the same URLs, and `xewe webui prompt` prints a
prompt that asks an AI model to port the Flask API to the sketch. The code is
`src/xewe/build/webui.py`; it came from the retired `xewe-labs/xewe-os-frontend-builder`
prototype (`export/export.py`).

## Workflow

1. **Vibe-code the UI in Flask.** A folder with `app.py` (the API on in-memory data),
   `templates/` and `static/`. Reference assets with `{{ url_for('static', filename='x.css') }}`
   or plain `/static/...` paths, and keep the API in JSON routes. The example is
   [`tests/fixtures/webui/fan-dashboard/`](../tests/fixtures/webui/fan-dashboard/) (one fan, one
   LED strip):

   ```sh
   pip install flask                      # only for running the prototype; xewe does not need it
   python tests/fixtures/webui/fan-dashboard/app.py    # http://127.0.0.1:5000
   ```

2. **Export.** `xewe webui export FLASK_DIR OUT_DIR` (needs no project) writes
   `OUT_DIR/<name>_<YYYYmmdd_HHMMSS>/`:
   - every `.html`, `.css` and `.js` under `templates/` and `static/` as `<stem>_<ext>.h` in the
     same folder layout, holding `static const char <STEM>_<EXT>[] PROGMEM = R"rawliteral(...)rawliteral";`
     (`-` and `.` in the stem become `_`: `static/fan-api.js` is `static/fan_api_js.h`,
     `FAN_API_JS`); `url_for('static', ...)` is rewritten to `/static/...`. Other files (images,
     fonts) are skipped: inline them, or serve them yourself;
   - a copy of `app.py`;
   - `<name>_app.ino`: the includes and one `server.on(<url>, HTTP_GET, ...)` per file with its
     MIME type (`text/html`, `text/css`, `application/javascript`) and
     `Access-Control-Allow-Origin: *`. `templates/index.html` is served at `/`, every other file
     at its own path (`/static/fan-api.js`). Wi-Fi is a `TODO`.

   Two files that map to the same array name (`static/a/x.js`, `static/b/x.js`) fail with exit 1.
   The golden output of the example is
   [`tests/fixtures/webui/fan-dashboard_export/`](../tests/fixtures/webui/fan-dashboard_export/).

3. **Port the API.** `xewe webui prompt` prints the prompt; paste the generated sketch and
   `app.py` where it says. It asks for one complete `.ino`: the Flask state as
   `std::map<String, String>`, ArduinoJson GET/POST/DELETE handlers, a CORS helper with
   `OPTIONS` preflight answered `204` in `onNotFound`, and `Serial` debug lines. Its Wi-Fi lines
   are the placeholders `YOUR_SSID` and `YOUR_PASSWORD`; put your network in the result only, and
   never commit it.

4. **Use it.** Build the sketch with `arduino-cli` (`WiFi`, `WebServer`, ArduinoJson), or move
   the headers and handlers into a XeWe OS project or module (in a XeWe OS build, Wi-Fi comes
   from the wifi module and provisioning, not from the sketch).
