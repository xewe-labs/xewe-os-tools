#include <WiFi.h>
#include <WebServer.h>

WebServer server(80);

// --- Auto-Generated Web Files ---
#include "templates/index_html.h"
#include "static/app_js.h"
#include "static/fan_api_js.h"
#include "static/style_css.h"
// --------------------------------

void setupRoutes() {
  // --- Auto-Generated Routes ---
  server.on("/", HTTP_GET, []() {
    server.sendHeader("Access-Control-Allow-Origin", "*");
    server.send_P(200, "text/html", INDEX_HTML);
  });
  server.on("/static/app.js", HTTP_GET, []() {
    server.sendHeader("Access-Control-Allow-Origin", "*");
    server.send_P(200, "application/javascript", APP_JS);
  });
  server.on("/static/fan-api.js", HTTP_GET, []() {
    server.sendHeader("Access-Control-Allow-Origin", "*");
    server.send_P(200, "application/javascript", FAN_API_JS);
  });
  server.on("/static/style.css", HTTP_GET, []() {
    server.sendHeader("Access-Control-Allow-Origin", "*");
    server.send_P(200, "text/css", STYLE_CSS);
  });
}

void setup() {
  Serial.begin(115200);
  // TODO: Setup WiFi here

  setupRoutes();
  server.begin();
}

void loop() {
  server.handleClient();
}
