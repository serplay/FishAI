// =============================================================================
// network_manager.h — WiFi, Captive Portal, and WebSocket Management
// =============================================================================
#pragma once

#include <Arduino.h>
#include <WiFi.h>
#include <ESPAsyncWebServer.h>
#include <WebSocketsClient.h>
#include <Preferences.h>
#include "config.h"

// =============================================================================
// Callback types for decoupling network events from audio/motor logic
// =============================================================================

/// Called when audio data is received from the AI server via WebSocket
using AudioDataCallback = void (*)(const uint8_t* data, size_t len);

/// Called when the server signals wake word detection
using WakeWordCallback = void (*)();

/// Called when the server signals end-of-response
using ResponseDoneCallback = void (*)();

/// Called when the server signals an interrupt (user barge-in)
using InterruptCallback = void (*)();

// =============================================================================
// NetworkManager — WiFi, AP, Web Server, and WebSocket Client
// =============================================================================

class NetworkManager {
public:
    /// Initialize — call once during setup
    void begin();

    /// Start the captive portal AP + async web server
    void startCaptivePortal();

    /// Stop the captive portal and shut down AP
    void stopCaptivePortal();

    /// Connect to saved WiFi credentials from NVS.
    /// @param timeoutMs  Maximum time to wait for connection
    /// @return true if connected successfully
    bool connectWiFi(uint32_t timeoutMs = 15000);

    /// Disconnect WiFi and free associated memory
    void disconnectWiFi();

    /// Check WiFi connection status
    bool isWiFiConnected() const;

    /// Start WebSocket connection to AI server
    void connectWebSocket();

    /// Disconnect WebSocket
    void disconnectWebSocket();

    /// Send binary audio data to the server
    /// @param data   PCM audio data
    /// @param len    Length in bytes
    void sendAudioChunk(const uint8_t* data, size_t len);

    /// Send a JSON command to the server (e.g., wake trigger, status)
    void sendCommand(const char* jsonPayload);

    /// Must be called periodically from the network task to process WS events
    void loopWebSocket();

    /// Register callbacks for incoming server events
    void onAudioData(AudioDataCallback cb)       { _audioCb = cb; }
    void onWakeWord(WakeWordCallback cb)          { _wakeCb = cb; }
    void onResponseDone(ResponseDoneCallback cb)  { _doneCb = cb; }
    void onInterrupt(InterruptCallback cb)        { _interruptCb = cb; }

    /// Read/write WiFi credentials from NVS
    bool loadWiFiCredentials(String& ssid, String& pass);
    void saveWiFiCredentials(const String& ssid, const String& pass);

    /// Read/write operational mode from NVS
    SystemMode loadMode();
    void saveMode(SystemMode mode);

    /// Read/write WebSocket server address from NVS
    void loadServerConfig(String& host, uint16_t& port);
    void saveServerConfig(const String& host, uint16_t port);

    /// Get local IP address (for display on serial / web panel)
    String getLocalIP() const;

private:
    AsyncWebServer* _server = nullptr;
    AsyncWebSocket* _ws = nullptr;
    WebSocketsClient _wsClient;
    Preferences _prefs;
    // network_manager.h
    SemaphoreHandle_t _wsMutex = nullptr;

    bool _portalActive = false;
    bool _wsConnected = false;

    // Callbacks
    AudioDataCallback   _audioCb = nullptr;
    WakeWordCallback    _wakeCb  = nullptr;
    ResponseDoneCallback _doneCb = nullptr;
    InterruptCallback _interruptCb = nullptr;

    /// Build and register all web server routes
    void _setupRoutes();

    /// WebSocket event handler (static thunk + instance dispatch)
    static void _wsEventStatic(WStype_t type, uint8_t* payload, size_t length);
    void _wsEvent(WStype_t type, uint8_t* payload, size_t length);
};

extern NetworkManager Network;
