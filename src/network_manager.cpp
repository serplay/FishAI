// =============================================================================
// network_manager.cpp — WiFi, Captive Portal, WebSocket Client
// =============================================================================

#include "network_manager.h"
#include <ArduinoJson.h>
#include <DNSServer.h>

NetworkManager Network;

static DNSServer* dnsServer = nullptr;
static TaskHandle_t dnsTaskHandle = nullptr;

// =============================================================================
// Captive Portal HTML — stored in PROGMEM to save heap
// =============================================================================

static const char PORTAL_HTML[] PROGMEM = R"rawliteral(
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Billy Bass Setup</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:system-ui,-apple-system,sans-serif;background:#0f172a;color:#e2e8f0;min-height:100vh;display:flex;align-items:center;justify-content:center;padding:1rem}
.card{background:rgba(30,41,59,.85);backdrop-filter:blur(12px);border:1px solid rgba(148,163,184,.15);border-radius:1rem;padding:2rem;max-width:420px;width:100%}
h1{font-size:1.5rem;text-align:center;margin-bottom:.25rem;background:linear-gradient(135deg,#38bdf8,#818cf8);-webkit-background-clip:text;-webkit-text-fill-color:transparent}
.sub{text-align:center;color:#94a3b8;font-size:.85rem;margin-bottom:1.5rem}
label{display:block;font-size:.8rem;color:#94a3b8;margin-bottom:.25rem;margin-top:.75rem}
input,select{width:100%;padding:.6rem .75rem;background:#1e293b;border:1px solid #334155;border-radius:.5rem;color:#e2e8f0;font-size:.9rem}
input:focus,select:focus{outline:none;border-color:#38bdf8}
button{width:100%;padding:.7rem;margin-top:1.25rem;background:linear-gradient(135deg,#38bdf8,#818cf8);color:#fff;border:none;border-radius:.5rem;font-size:.95rem;font-weight:600;cursor:pointer;transition:opacity .2s}
button:hover{opacity:.85}
.status{margin-top:1rem;padding:.75rem;background:#1e293b;border-radius:.5rem;font-size:.8rem;color:#94a3b8}
.status b{color:#38bdf8}
#networks{max-height:150px;overflow-y:auto}
</style>
</head>
<body>
<div class="card">
<h1>&#127911; Billy Bass Setup</h1>
<p class="sub">Configure your animatronic fish</p>
<form id="wf" action="/save" method="POST">
<label>WiFi Network</label>
<select name="ssid" id="networks"><option>Scanning...</option></select>
<label>WiFi Password</label>
<input type="password" name="pass" placeholder="Enter password">
<label>Operating Mode</label>
<select name="mode">
<option value="0">AI Agent Mode (WiFi)</option>
<option value="1">Bluetooth Speaker Mode</option>
</select>
<label>AI Server Host</label>
<input type="text" name="wshost" placeholder="192.168.1.100">
<label>AI Server Port</label>
<input type="number" name="wsport" value="8765">
<button type="submit">Save &amp; Reboot</button>
</form>
<div class="status">
Status: <b id="st">Initializing...</b>
</div>
</div>
<script>
fetch('/scan').then(r=>r.json()).then(d=>{
  let s=document.getElementById('networks');
  s.innerHTML='';
  d.forEach(n=>{let o=document.createElement('option');o.value=n.ssid;o.textContent=n.ssid+' ('+n.rssi+'dBm)';s.appendChild(o)});
}).catch(()=>{document.getElementById('networks').innerHTML='<option>Scan failed</option>'});
fetch('/status').then(r=>r.json()).then(d=>{document.getElementById('st').textContent=d.status});
</script>
</body>
</html>
)rawliteral";

// =============================================================================
// DNS Task — redirects all DNS queries to the AP IP (captive portal)
// =============================================================================

static void dnsTask(void* param) {
    DNSServer* dns = (DNSServer*)param;
    while (true) {
        dns->processNextRequest();
        vTaskDelay(pdMS_TO_TICKS(10));
    }
}

// =============================================================================
// Initialization
// =============================================================================

void NetworkManager::begin() {
    _prefs.begin(NVS_NAMESPACE, false);
}

// =============================================================================
// Captive Portal
// =============================================================================

void NetworkManager::startCaptivePortal() {
    if (_portalActive) return;

    WiFi.mode(WIFI_AP_STA);  // AP + optional STA for scanning
    WiFi.softAP(AP_SSID, AP_PASSWORD);
    Serial.printf("[NET] AP started: %s  IP: %s\n", AP_SSID, 
                  WiFi.softAPIP().toString().c_str());

    // DNS server — redirect all domains to our IP
    dnsServer = new DNSServer();
    dnsServer->start(53, "*", WiFi.softAPIP());

    // Start DNS processing task on Core 0
    xTaskCreatePinnedToCore(dnsTask, "dns", 2048, dnsServer, 1, 
                            &dnsTaskHandle, 0);

    // Async web server
    _server = new AsyncWebServer(80);
    _setupRoutes();
    _server->begin();
    _portalActive = true;
    Serial.println("[NET] Captive portal active");
}

void NetworkManager::stopCaptivePortal() {
    if (!_portalActive) return;

    if (_server) { _server->end(); delete _server; _server = nullptr; }
    if (dnsTaskHandle) { vTaskDelete(dnsTaskHandle); dnsTaskHandle = nullptr; }
    if (dnsServer) { dnsServer->stop(); delete dnsServer; dnsServer = nullptr; }

    WiFi.softAPdisconnect(true);
    _portalActive = false;
    Serial.println("[NET] Captive portal stopped");
}

// =============================================================================
// Web Server Routes
// =============================================================================

void NetworkManager::_setupRoutes() {
    // Serve the main portal page
    _server->on("/", HTTP_GET, [](AsyncWebServerRequest* req) {
        req->send(200, "text/html", PORTAL_HTML);
    });

    // Captive portal detection endpoints (iOS/Android/Windows)
    _server->on("/hotspot-detect.html", HTTP_GET, [](AsyncWebServerRequest* req) {
        req->send(200, "text/html", PORTAL_HTML);
    });
    _server->on("/generate_204", HTTP_GET, [](AsyncWebServerRequest* req) {
        req->send(200, "text/html", PORTAL_HTML);
    });
    _server->on("/connecttest.txt", HTTP_GET, [](AsyncWebServerRequest* req) {
        req->send(200, "text/html", PORTAL_HTML);
    });

    // WiFi scan endpoint
    _server->on("/scan", HTTP_GET, [](AsyncWebServerRequest* req) {
        int n = WiFi.scanComplete();
        if (n == WIFI_SCAN_FAILED) {
            WiFi.scanNetworks(true);  // Async scan
            req->send(200, "application/json", "[]");
            return;
        }
        JsonDocument doc;
        JsonArray arr = doc.to<JsonArray>();
        for (int i = 0; i < n && i < 15; i++) {
            JsonObject net = arr.add<JsonObject>();
            net["ssid"] = WiFi.SSID(i);
            net["rssi"] = WiFi.RSSI(i);
        }
        WiFi.scanDelete();
        WiFi.scanNetworks(true);  // Start next scan
        String out;
        serializeJson(doc, out);
        req->send(200, "application/json", out);
    });

    // Status endpoint
    _server->on("/status", HTTP_GET, [this](AsyncWebServerRequest* req) {
        JsonDocument doc;
        doc["status"] = isWiFiConnected() ? "Connected to WiFi" : "AP Mode";
        doc["ip"] = getLocalIP();
        doc["mode"] = (uint8_t)loadMode();
        doc["heap"] = ESP.getFreeHeap();
        String out;
        serializeJson(doc, out);
        req->send(200, "application/json", out);
    });

    // Save configuration
    _server->on("/save", HTTP_POST, [this](AsyncWebServerRequest* req) {
        if (req->hasParam("ssid", true) && req->hasParam("pass", true)) {
            saveWiFiCredentials(req->getParam("ssid", true)->value(),
                                req->getParam("pass", true)->value());
        }
        if (req->hasParam("mode", true)) {
            uint8_t m = req->getParam("mode", true)->value().toInt();
            saveMode((SystemMode)m);
        }
        if (req->hasParam("wshost", true)) {
            String host = req->getParam("wshost", true)->value();
            uint16_t port = req->hasParam("wsport", true) 
                            ? req->getParam("wsport", true)->value().toInt() 
                            : WS_SERVER_PORT;
            saveServerConfig(host, port);
        }
        req->send(200, "text/html", 
            "<html><body style='background:#0f172a;color:#e2e8f0;display:flex;"
            "align-items:center;justify-content:center;height:100vh;font-family:"
            "system-ui'><h2>Saved! Rebooting...</h2></body></html>");
        
        // Delayed reboot to let the response flush
        vTaskDelay(pdMS_TO_TICKS(1500));
        ESP.restart();
    });

    // Catch-all for captive portal redirect
    _server->onNotFound([](AsyncWebServerRequest* req) {
        req->redirect("/");
    });
}

// =============================================================================
// WiFi Station
// =============================================================================

bool NetworkManager::connectWiFi(uint32_t timeoutMs) {
    String ssid, pass;
    if (!loadWiFiCredentials(ssid, pass)) {
        Serial.println("[NET] No WiFi credentials stored");
        return false;
    }

    WiFi.mode(WIFI_STA);
    WiFi.begin(ssid.c_str(), pass.c_str());
    Serial.printf("[NET] Connecting to '%s'...\n", ssid.c_str());

    uint32_t start = millis();
    while (WiFi.status() != WL_CONNECTED && (millis() - start) < timeoutMs) {
        vTaskDelay(pdMS_TO_TICKS(250));
        Serial.print(".");
    }
    Serial.println();

    if (WiFi.status() == WL_CONNECTED) {
        Serial.printf("[NET] Connected! IP: %s\n", WiFi.localIP().toString().c_str());
        return true;
    }
    Serial.println("[NET] WiFi connection failed");
    return false;
}

void NetworkManager::disconnectWiFi() {
    WiFi.disconnect(true);
    WiFi.mode(WIFI_OFF);
    Serial.println("[NET] WiFi disconnected & radio off");
}

bool NetworkManager::isWiFiConnected() const {
    return WiFi.status() == WL_CONNECTED;
}

String NetworkManager::getLocalIP() const {
    if (WiFi.status() == WL_CONNECTED) return WiFi.localIP().toString();
    return WiFi.softAPIP().toString();
}

// =============================================================================
// WebSocket Client (AI Agent Mode)
// =============================================================================

// Static thunk for WebSocketsClient callback
static NetworkManager* _netInstance = nullptr;

void NetworkManager::_wsEventStatic(WStype_t type, uint8_t* payload, size_t length) {
    if (_netInstance) _netInstance->_wsEvent(type, payload, length);
}

void NetworkManager::connectWebSocket() {
    String host;
    uint16_t port;
    loadServerConfig(host, port);

    _netInstance = this;
    _wsClient.begin(host.c_str(), port, "/ws");
    _wsClient.onEvent(_wsEventStatic);
    _wsClient.setReconnectInterval(3000);
    Serial.printf("[NET] WebSocket connecting to %s:%d\n", host.c_str(), port);
}

void NetworkManager::disconnectWebSocket() {
    _wsClient.disconnect();
    _wsConnected = false;
}

void NetworkManager::sendAudioChunk(const uint8_t* data, size_t len) {
    if (_wsConnected) {
        _wsClient.sendBIN(data, len);
    }
}

void NetworkManager::sendCommand(const char* jsonPayload) {
    if (_wsConnected) {
        _wsClient.sendTXT(jsonPayload);
    }
}

void NetworkManager::loopWebSocket() {
    _wsClient.loop();
}

void NetworkManager::_wsEvent(WStype_t type, uint8_t* payload, size_t length) {
    switch (type) {
        case WStype_CONNECTED:
            _wsConnected = true;
            Serial.println("[NET] WebSocket connected");
            sendCommand("{\"type\":\"hello\",\"device\":\"billy_bass\"}");
            break;

        case WStype_DISCONNECTED:
            _wsConnected = false;
            Serial.println("[NET] WebSocket disconnected");
            break;

        case WStype_TEXT: {
            // Parse JSON commands from server
            JsonDocument doc;
            if (deserializeJson(doc, payload, length) == DeserializationError::Ok) {
                const char* msgType = doc["type"] | "";
                if (strcmp(msgType, "wake") == 0 && _wakeCb) {
                    _wakeCb();
                } else if (strcmp(msgType, "done") == 0 && _doneCb) {
                    _doneCb();
                }
            }
            break;
        }

        case WStype_BIN:
            // Binary data = audio PCM from server
            if (_audioCb && length > 0) {
                _audioCb(payload, length);
            }
            break;

        default:
            break;
    }
}

// =============================================================================
// NVS Persistence
// =============================================================================

bool NetworkManager::loadWiFiCredentials(String& ssid, String& pass) {
    ssid = _prefs.getString(NVS_KEY_WIFI_SSID, "");
    pass = _prefs.getString(NVS_KEY_WIFI_PASS, "");
    return ssid.length() > 0;
}

void NetworkManager::saveWiFiCredentials(const String& ssid, const String& pass) {
    _prefs.putString(NVS_KEY_WIFI_SSID, ssid);
    _prefs.putString(NVS_KEY_WIFI_PASS, pass);
    Serial.printf("[NVS] WiFi credentials saved: '%s'\n", ssid.c_str());
}

SystemMode NetworkManager::loadMode() {
    uint8_t m = _prefs.getUChar(NVS_KEY_MODE, (uint8_t)SystemMode::SETUP_PORTAL);
    return (SystemMode)m;
}

void NetworkManager::saveMode(SystemMode mode) {
    _prefs.putUChar(NVS_KEY_MODE, (uint8_t)mode);
    Serial.printf("[NVS] Mode saved: %d\n", (int)mode);
}

void NetworkManager::loadServerConfig(String& host, uint16_t& port) {
    host = _prefs.getString(NVS_KEY_WS_HOST, WS_DEFAULT_HOST);
    port = _prefs.getUShort(NVS_KEY_WS_PORT, WS_SERVER_PORT);
}

void NetworkManager::saveServerConfig(const String& host, uint16_t port) {
    _prefs.putString(NVS_KEY_WS_HOST, host);
    _prefs.putUShort(NVS_KEY_WS_PORT, port);
    Serial.printf("[NVS] Server config saved: %s:%d\n", host.c_str(), port);
}
