#pragma once
#include <pgmspace.h>
static const char FAN_API_JS[] PROGMEM = R"rawliteral(
// Calls to the device API; the same URLs work on Flask and on the exported sketch.
async function getState() {
  const res = await fetch("/api/state");
  return res.json();
}

async function postJson(url, body) {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return res.json();
}
)rawliteral";
