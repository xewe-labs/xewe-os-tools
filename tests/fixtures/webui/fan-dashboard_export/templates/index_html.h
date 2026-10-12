#pragma once
#include <pgmspace.h>
static const char INDEX_HTML[] PROGMEM = R"rawliteral(
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Fan dashboard</title>
  <link rel="icon" href="/static/icon.svg" />
  <link rel="stylesheet" href="/static/style.css" />
</head>
<body>
  <main class="page">
    <h1>Fan dashboard</h1>
    <section class="card">
      <h2>Fan</h2>
      <p>Speed <output id="fanSpeedValue">-</output> % · <span id="fanRpm">-</span> rpm</p>
      <input id="fanSpeed" type="range" min="0" max="100" step="5" />
    </section>
    <section class="card">
      <h2>LED strip</h2>
      <label><input id="ledOn" type="checkbox" /> On</label>
      <input id="ledColor" type="color" />
    </section>
  </main>
  <script src="/static/fan-api.js"></script>
  <script src="/static/app.js"></script>
</body>
</html>
)rawliteral";
