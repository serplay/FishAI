// =============================================================================
// main.cpp — Billy Bass AI Animatronic Fish — Main Firmware
// ESP32-WROOM-32D (No PSRAM) | Arduino Framework | FreeRTOS
// =============================================================================
//
// ARCHITECTURE OVERVIEW:
// ┌─────────────────────────────────────────────────────────────────────┐
// │                         STATE MACHINE                               │
// │  BOOT → [NVS Read] → SETUP_PORTAL / BT_INIT / WIFI_CONNECTING     │
// │                                                                     │
// │  SETUP_PORTAL ──(save & reboot)──→ BOOT                            │
// │  BT_INIT ──→ BT_STREAMING ──(long press)──→ BOOT (mode=PORTAL)    │
// │  WIFI_CONNECTING ──→ AI_IDLE ──→ AI_LISTENING ──→ AI_RESPONDING    │
// │                                    ↑               │                │
// │                                    └───────────────┘                │
// └─────────────────────────────────────────────────────────────────────┘
//
// CORE ASSIGNMENT:
//   Core 0: WiFi/BT stack, Network task, DNS task
//   Core 1: Audio I2S processing, Motor control, Button polling
//
// MEMORY BUDGET (520KB SRAM):
//   ~80KB  — FreeRTOS kernel + WiFi/BT stack
//   ~8KB   — I2S DMA buffers (2 ports × 4 bufs × 512B)
//   ~16KB  — FreeRTOS task stacks (4 tasks)
//   ~4KB   — Audio processing buffers (main.cpp)
//   ~12KB  — WebSocket + AsyncWebServer
//   ~400KB — Remaining for heap, strings, JSON, etc.
// =============================================================================

#include <Arduino.h>
#include <esp_bt.h>
#include <BluetoothA2DPSink.h>

#include "config.h"
#include "audio_manager.h"
#include "motor_control.h"
#include "network_manager.h"
#include "button_handler.h"

#include <math.h>

// =============================================================================
// GLOBALS
// =============================================================================

static volatile SystemState  g_state = SystemState::BOOT;
static volatile SystemMode   g_mode  = SystemMode::SETUP_PORTAL;

// FreeRTOS task handles
static TaskHandle_t audioTaskHandle   = nullptr;
static TaskHandle_t networkTaskHandle = nullptr;
static TaskHandle_t motorTaskHandle   = nullptr;
static TaskHandle_t buttonTaskHandle  = nullptr;

// Inter-task communication: ring buffer for server→amplifier audio
// Using a FreeRTOS queue of fixed-size chunks to avoid heap fragmentation
#define PLAYBACK_CHUNK_SIZE   512   // bytes per queue item
#define PLAYBACK_QUEUE_DEPTH  8     // 8 × 512 = 4KB total
static QueueHandle_t playbackQueue = nullptr;

// Lip-sync sample buffer shared between audio and motor tasks
// Protected by a critical section (copy-on-write pattern)
static int16_t g_lipSyncBuf[I2S_DMA_BUF_LEN];
static volatile size_t g_lipSyncCount = 0;
static portMUX_TYPE g_lipSyncMux = portMUX_INITIALIZER_UNLOCKED;

// A2DP Sink instance (Bluetooth mode)
static BluetoothA2DPSink* a2dpSink = nullptr;

// Forward declarations — defined after task functions
static void handleShortPress();
static void handleLongPress();

// =============================================================================
// A2DP CALLBACK — Bluetooth audio data received
// =============================================================================
// Called by the A2DP library from its internal task (Core 0).
// We extract samples for lip-sync and forward to I2S amp.

static void a2dp_data_callback(const uint8_t* data, uint32_t len) {
    // Data is 16-bit stereo PCM (44.1kHz from most BT sources)
    // The A2DP library + arduino-audio-tools handles I2S output internally.
    // We just need to extract amplitude for lip-sync.
    
    const int16_t* samples = (const int16_t*)data;
    size_t sampleCount = len / sizeof(int16_t);
    
    // Downsample: take every Nth sample for RMS (we don't need full fidelity)
    // Stereo interleaved: L,R,L,R... — just use left channel
    size_t monoCount = 0;
    int16_t monoBuf[128];  // Small stack buffer
    for (size_t i = 0; i < sampleCount && monoCount < 128; i += 2) {
        monoBuf[monoCount++] = samples[i];
    }
    
    // Copy to shared lip-sync buffer
    portENTER_CRITICAL(&g_lipSyncMux);
    memcpy(g_lipSyncBuf, monoBuf, monoCount * sizeof(int16_t));
    g_lipSyncCount = monoCount;
    portEXIT_CRITICAL(&g_lipSyncMux);
}

// =============================================================================
// NETWORK CALLBACKS — AI server audio & events
// =============================================================================

static void onServerAudio(const uint8_t* data, size_t len) {
    // Enqueue audio chunks for the audio task to play
    // If queue is full, drop oldest — we can't block the WS callback
    size_t offset = 0;
    uint8_t chunk[PLAYBACK_CHUNK_SIZE];
    while (offset < len) {
        size_t take = min((size_t)PLAYBACK_CHUNK_SIZE, len - offset);
        memcpy(chunk, data + offset, take);
        // Pad remainder with silence if partial chunk
        if (take < PLAYBACK_CHUNK_SIZE) {
            memset(chunk + take, 0, PLAYBACK_CHUNK_SIZE - take);
        }
        // Non-blocking send — drop if full
        xQueueSend(playbackQueue, chunk, 0);
        offset += take;
    }
}

static void onServerWake() {
    Serial.println("[AI] Server detected wake word!");
    g_state = SystemState::AI_LISTENING;
    Motors.raiseHead();
}

static void onServerDone() {
    Serial.println("[AI] Server response complete");
    g_state = SystemState::AI_IDLE;
    Motors.lowerHead();
    Motors.closeMouth();
}

// =============================================================================
// FREERTOS TASK: Audio Processing (Core 1)
// =============================================================================
// This task handles:
//   - AI Mode: Reading mic → sending to network, playing server audio
//   - BT Mode: Nothing (A2DP library handles I2S internally)

static void audioTask(void* param) {
    // Buffers allocated once — no heap fragmentation
    // Mic reads 32-bit samples from INMP441, we convert to 16-bit
    int32_t micRaw[MIC_CHUNK_SAMPLES];
    int16_t micConverted[MIC_CHUNK_SAMPLES];
    uint8_t playChunk[PLAYBACK_CHUNK_SIZE];

    Serial.println("[TASK] Audio task started on Core 1");

    while (true) {
        SystemState state = g_state;
        
        if (state == SystemState::AI_IDLE || 
            state == SystemState::AI_LISTENING) {
            // --- Read microphone and stream to server ---
            size_t bytesRead = 0;
            esp_err_t err = Audio.readFromMic(micRaw, sizeof(micRaw), 
                                              &bytesRead, pdMS_TO_TICKS(100));
            if (err == ESP_OK && bytesRead > 0) {
                size_t sampleCount = bytesRead / sizeof(int32_t);
                
                // Convert 32-bit INMP441 → 16-bit PCM
                // INMP441 data is MSB-aligned in 32-bit frame:
                // Useful data is in bits [31:16], lower bits are noise
                for (size_t i = 0; i < sampleCount; i++) {
                    micConverted[i] = (int16_t)(micRaw[i] >> 16);
                }
                
                // Send to server via WebSocket
                Network.sendAudioChunk((uint8_t*)micConverted, 
                                       sampleCount * sizeof(int16_t));
                
                // Also feed lip-sync (in case we want to show mic activity)
                // Usually we only lip-sync during playback, but this is here
                // for potential "echo" visualization
            }
        }
        
        if (state == SystemState::AI_RESPONDING) {
            // --- Play audio from server ---
            if (xQueueReceive(playbackQueue, playChunk, pdMS_TO_TICKS(50)) == pdTRUE) {
                size_t written = 0;
                Audio.writeToAmp(playChunk, PLAYBACK_CHUNK_SIZE, &written);
                
                // Feed lip-sync from playback data
                size_t sampleCount = PLAYBACK_CHUNK_SIZE / sizeof(int16_t);
                portENTER_CRITICAL(&g_lipSyncMux);
                memcpy(g_lipSyncBuf, playChunk, PLAYBACK_CHUNK_SIZE);
                g_lipSyncCount = sampleCount;
                portEXIT_CRITICAL(&g_lipSyncMux);
            }
        }
        
        // Yield to prevent watchdog — if nothing to do, sleep longer
        if (state != SystemState::AI_IDLE && 
            state != SystemState::AI_LISTENING && 
            state != SystemState::AI_RESPONDING) {
            vTaskDelay(pdMS_TO_TICKS(50));
        }
    }
}

// =============================================================================
// FREERTOS TASK: Motor Control / Lip-Sync (Core 1)
// =============================================================================
// Runs at ~60Hz, reads the shared lip-sync buffer and drives the mouth motor.

static void motorTask(void* param) {
    Serial.println("[TASK] Motor task started on Core 1");
    
    int16_t localBuf[I2S_DMA_BUF_LEN];
    
    // --- BT Performance Mode state ---
    // Tracks silence/activity to trigger head lifts and dancing
    uint32_t btLastAudioMs     = 0;     // Timestamp of last audio data received
    uint32_t btAudioStartMs    = 0;     // When continuous audio playback began
    bool     btWasSilent       = true;  // True if silence exceeded threshold
    bool     btHeadUp          = false; // Head is currently raised
    uint32_t btHeadUpSinceMs   = 0;     // When head was raised
    bool     btDancing         = false; // Dance mode active (within a burst)
    uint32_t btLastDanceMs     = 0;     // Last dance move timestamp
    bool     btDanceToggle     = false; // Alternates dance direction
    bool     btDanceUnlocked   = false; // True once 30s threshold is passed
    uint32_t btDanceBurstStartMs = 0;   // When current dance burst began
    bool     btDanceResting    = false; // True during rest period between bursts
    uint32_t btDanceRestStartMs  = 0;   // When current rest period began
    
    while (true) {
        SystemState state = g_state;
        uint32_t now = millis();
        
        if (state == SystemState::BT_STREAMING || 
            state == SystemState::AI_RESPONDING) {
            // Copy lip-sync data under critical section
            size_t count = 0;
            portENTER_CRITICAL(&g_lipSyncMux);
            count = g_lipSyncCount;
            if (count > 0) {
                memcpy(localBuf, g_lipSyncBuf, count * sizeof(int16_t));
                g_lipSyncCount = 0;  // Consumed
            }
            portEXIT_CRITICAL(&g_lipSyncMux);
            
            if (count > 0) {
                // Always do lip-sync (mouth moves regardless of performance state)
                Motors.updateLipSync(localBuf, count);
                
                // --- Compute RMS to distinguish real audio from silent frames ---
                // A2DP sends PCM data even during silence (zero-valued frames),
                // so we must check actual energy, not just data presence.
                int64_t sumSq = 0;
                for (size_t i = 0; i < count; i++) {
                    int32_t s = localBuf[i];
                    sumSq += s * s;
                }
                float frameRMS = sqrtf((float)(sumSq / (int64_t)count));
                bool hasRealAudio = (frameRMS > RMS_SILENCE_THRESHOLD);
                
                // --- BT Performance Mode logic (only in BT_STREAMING) ---
                if (state == SystemState::BT_STREAMING) {
                    // Only treat as "audio active" if there's actual energy
                    if (hasRealAudio) {
                        btLastAudioMs = now;
                    }
                    
                    // Detect audio resuming after silence (only on real audio)
                    if (btWasSilent && hasRealAudio) {
                        btWasSilent = false;
                        btAudioStartMs = now;
                        btDancing = false;
                        btDanceUnlocked = false;
                        btDanceResting = false;
                        
                        // Raise head when audio starts after a silence gap
                        Motors.raiseHead();
                        btHeadUp = true;
                        btHeadUpSinceMs = now;
                        Serial.println("[BT-PERF] Audio resumed after silence — head up!");
                    }
                    
                    // Check if 30s of continuous audio unlocks dancing
                    if (!btDanceUnlocked && (now - btAudioStartMs) >= BT_DANCE_START_MS) {
                        btDanceUnlocked = true;
                        // Start the first burst immediately
                        btDancing = true;
                        btDanceResting = false;
                        btDanceBurstStartMs = now;
                        btLastDanceMs = now;
                        if (!btHeadUp) {
                            Motors.setHeadRaw(true);
                            btHeadUp = true;
                        }
                        Serial.println("[BT-PERF] Dance mode unlocked! Starting first burst.");
                    }
                    
                    // --- Dance burst/rest cycle ---
                    if (btDanceUnlocked) {
                        if (btDanceResting) {
                            // Currently resting — check if rest period is over
                            if ((now - btDanceRestStartMs) >= BT_DANCE_REST_MS) {
                                btDanceResting = false;
                                btDancing = true;
                                btDanceBurstStartMs = now;
                                btLastDanceMs = now;
                                if (!btHeadUp) {
                                    Motors.setHeadRaw(true);
                                    btHeadUp = true;
                                }
                                Serial.println("[BT-PERF] Rest over — new dance burst!");
                            }
                        } else if (btDancing) {
                            // Currently in a dance burst — check if burst expired
                            if ((now - btDanceBurstStartMs) >= BT_DANCE_BURST_MS) {
                                btDancing = false;
                                btDanceResting = true;
                                btDanceRestStartMs = now;
                                // Lower head during rest
                                Motors.setHeadRaw(false);
                                btHeadUp = false;
                                Serial.println("[BT-PERF] Dance burst done — resting.");
                            } else {
                                // Execute dance moves within the burst
                                if ((now - btLastDanceMs) >= BT_DANCE_INTERVAL_MS) {
                                    btLastDanceMs = now;
                                    btDanceToggle = !btDanceToggle;
                                    Motors.setHeadRaw(btDanceToggle);
                                    btHeadUp = btDanceToggle;
                                }
                            }
                        }
                    }
                    
                    // Lower head after lift duration (only if NOT dancing and not in burst cycle)
                    if (btHeadUp && !btDancing && !btDanceResting &&
                        (now - btHeadUpSinceMs) >= BT_HEAD_LIFT_DURATION_MS) {
                        Motors.setHeadRaw(false);  // Non-blocking lower
                        btHeadUp = false;
                        Serial.println("[BT-PERF] Head lowered (lift duration elapsed)");
                    }
                }
            } else {
                // No new audio data this frame
                if (state == SystemState::BT_STREAMING) {
                    // Check if silence threshold exceeded
                    if (!btWasSilent && btLastAudioMs > 0 && 
                        (now - btLastAudioMs) >= BT_SILENCE_TIMEOUT_MS) {
                        btWasSilent = true;
                        btDancing = false;
                        btDanceUnlocked = false;
                        btDanceResting = false;
                        
                        // Ensure head is down during silence
                        if (btHeadUp) {
                            Motors.setHeadRaw(false);
                            btHeadUp = false;
                        }
                        Motors.coastHead();
                        Serial.println("[BT-PERF] Silence detected — ready for next performance");
                    }
                }
            }
        } else {
            // Not in an audio-active state — ensure mouth is closed
            Motors.closeMouth();
            
            // Reset BT performance state
            btWasSilent = true;
            btDancing = false;
            btDanceUnlocked = false;
            btDanceResting = false;
            if (btHeadUp) {
                Motors.coastHead();
                btHeadUp = false;
            }
        }
        
        // ~60Hz update rate for smooth lip movement
        vTaskDelay(pdMS_TO_TICKS(16));
    }
}

// =============================================================================
// FREERTOS TASK: Network (Core 0)
// =============================================================================

static void networkTask(void* param) {
    Serial.println("[TASK] Network task started on Core 0");
    
    while (true) {
        SystemState state = g_state;
        
        if (state == SystemState::AI_IDLE || 
            state == SystemState::AI_LISTENING || 
            state == SystemState::AI_RESPONDING) {
            // Process WebSocket events
            Network.loopWebSocket();
        }
        
        // WebSocket library needs frequent polling (5ms recommended)
        vTaskDelay(pdMS_TO_TICKS(5));
    }
}

// =============================================================================
// FREERTOS TASK: Button Handler (Core 1)
// =============================================================================

static void buttonTask(void* param) {
    Serial.println("[TASK] Button task started on Core 1");
    
    while (true) {
        ButtonEvent evt = Button.poll();
        
        switch (evt) {
            case ButtonEvent::SHORT_PRESS:
                Serial.println("[BTN] Short press detected");
                handleShortPress();
                break;
                
            case ButtonEvent::LONG_PRESS:
                Serial.println("[BTN] LONG PRESS — Mode override!");
                handleLongPress();
                break;
                
            case ButtonEvent::NONE:
            default:
                break;
        }
        
        vTaskDelay(pdMS_TO_TICKS(50));  // 20Hz polling
    }
}

// =============================================================================
// BUTTON ACTION HANDLERS
// =============================================================================

static void handleShortPress() {
    SystemState state = g_state;
    
    if (state == SystemState::AI_IDLE) {
        // Force wake — tell server to start listening, raise head
        Serial.println("[AI] Button wake override — forcing listen mode");
        g_state = SystemState::AI_LISTENING;
        Motors.raiseHead();
        Network.sendCommand("{\"type\":\"button_wake\"}");
    }
    else if (state == SystemState::AI_LISTENING) {
        // Cancel listening — return to idle
        g_state = SystemState::AI_IDLE;
        Motors.lowerHead();
        Network.sendCommand("{\"type\":\"cancel\"}");
    }
    else if (state == SystemState::BT_STREAMING) {
        // In BT mode, short press does nothing meaningful for now
        // Could toggle play/pause via AVRCP in future
        Serial.println("[BT] Short press — no action in BT mode");
    }
}

static void handleLongPress() {
    // SYSTEM OVERRIDE: Switch to portal mode and reboot
    // This is the escape hatch from Bluetooth mode where WiFi is disabled
    Serial.println("[SYS] Writing SETUP_PORTAL to NVS and rebooting...");
    
    // Stop all motors immediately
    Motors.stopAll();
    
    // Save portal mode to NVS
    Network.saveMode(SystemMode::SETUP_PORTAL);
    
    // Brief delay for NVS write to complete
    vTaskDelay(pdMS_TO_TICKS(200));
    
    // Hard restart
    ESP.restart();
}

// =============================================================================
// MODE INITIALIZATION FUNCTIONS
// =============================================================================

static void initBluetoothMode() {
    Serial.println("\n========== BLUETOOTH SPEAKER MODE ==========");
    
    // Ensure WiFi is fully off to reclaim ~80KB of heap
    Network.disconnectWiFi();
    
    // NOTE: Do NOT call Audio.beginAmplifier() here.
    // The A2DP library manages its own I2S driver on I2S_NUM_0.
    // Pre-initializing it would conflict with A2DP's I2S setup.
    
    // Create and configure A2DP Sink
    a2dpSink = new BluetoothA2DPSink();
    
    // Register raw data callback for lip-sync.
    // Second param = true → library outputs audio to I2S AND calls our callback.
    // (false would disable I2S output entirely, causing silence/beep)
    a2dpSink->set_stream_reader(a2dp_data_callback, true);
    
    // Set I2S pins for the A2DP library's internal I2S driver
    i2s_pin_config_t pins = {};
    pins.bck_io_num = AMP_BCLK_PIN;
    pins.ws_io_num = AMP_LRC_PIN;
    pins.data_out_num = AMP_DIN_PIN;
    pins.data_in_num = I2S_PIN_NO_CHANGE;
    a2dpSink->set_pin_config(pins);
    
    // Start A2DP with device name
    a2dpSink->start("Billy Bass");
    
    g_state = SystemState::BT_STREAMING;
    Serial.printf("[BT] A2DP Sink active — Free heap: %d bytes\n", 
                  ESP.getFreeHeap());
}

static void initAIAgentMode() {
    Serial.println("\n========== AI AGENT MODE ==========");
    
    // Release Bluetooth memory if it was previously initialized
    // This is a one-way operation — BT cannot be restarted without reboot
    esp_bt_controller_disable();
    esp_bt_controller_deinit();
    esp_bt_mem_release(ESP_BT_MODE_CLASSIC_BT);
    
    // Connect to WiFi
    g_state = SystemState::WIFI_CONNECTING;
    if (!Network.connectWiFi(15000)) {
        Serial.println("[AI] WiFi failed — falling back to captive portal");
        Network.startCaptivePortal();
        g_state = SystemState::SETUP_PORTAL;
        return;
    }
    
    // Also start the web panel (accessible via local IP)
    Network.startCaptivePortal();
    
    // Initialize both I2S ports
    Audio.beginAmplifier();
    Audio.beginMicrophone();
    
    // Connect WebSocket to AI server
    Network.onAudioData(onServerAudio);
    Network.onWakeWord(onServerWake);
    Network.onResponseDone(onServerDone);
    Network.connectWebSocket();
    
    g_state = SystemState::AI_IDLE;
    Serial.printf("[AI] Agent mode active — Free heap: %d bytes\n", 
                  ESP.getFreeHeap());
}

static void initSetupPortal() {
    Serial.println("\n========== SETUP PORTAL MODE ==========");
    
    // Release BT memory to maximize heap for web server
    esp_bt_controller_disable();
    esp_bt_controller_deinit();
    esp_bt_mem_release(ESP_BT_MODE_CLASSIC_BT);
    
    Network.startCaptivePortal();
    g_state = SystemState::SETUP_PORTAL;
    
    Serial.printf("[SETUP] Portal active — Free heap: %d bytes\n", 
                  ESP.getFreeHeap());
}

// =============================================================================
// SETUP — Runs once on Core 1
// =============================================================================

void setup() {
    Serial.begin(115200);
    while (!Serial) { delay(10); }
    
    Serial.println("\n╔══════════════════════════════════════╗");
    Serial.println("║    🐟 Billy Bass AI — Firmware v1.0  ║");
    Serial.println("║    ESP32-WROOM-32D | No PSRAM        ║");
    Serial.println("╚══════════════════════════════════════╝");
    Serial.printf("Free heap at boot: %d bytes\n", ESP.getFreeHeap());
    Serial.printf("SDK version: %s\n", ESP.getSdkVersion());
    
    // --- Initialize subsystems ---
    Network.begin();    // Opens NVS
    Motors.begin();     // PWM + GPIO
    Button.begin();     // ISR attach
    
    // --- Create playback queue ---
    playbackQueue = xQueueCreate(PLAYBACK_QUEUE_DEPTH, PLAYBACK_CHUNK_SIZE);
    if (!playbackQueue) {
        Serial.println("[FATAL] Failed to create playback queue!");
        g_state = SystemState::ERROR_STATE;
        return;
    }
    
    // --- Read preferred mode from NVS ---
    g_mode = Network.loadMode();
    Serial.printf("Stored mode: %d\n", (int)g_mode);
    
    // --- Create FreeRTOS tasks ---
    // Audio task on Core 1 (same as Arduino loop — but we don't use loop())
    xTaskCreatePinnedToCore(audioTask, "audio", TASK_STACK_AUDIO, 
                            NULL, TASK_PRIORITY_AUDIO, &audioTaskHandle, 1);
    
    // Motor task on Core 1 (needs tight timing with audio)
    xTaskCreatePinnedToCore(motorTask, "motor", TASK_STACK_MOTOR, 
                            NULL, TASK_PRIORITY_MOTOR, &motorTaskHandle, 1);
    
    // Network task on Core 0 (WiFi/BT stack lives here)
    xTaskCreatePinnedToCore(networkTask, "net", TASK_STACK_NETWORK, 
                            NULL, TASK_PRIORITY_NETWORK, &networkTaskHandle, 0);
    
    // Button task on Core 1
    xTaskCreatePinnedToCore(buttonTask, "btn", TASK_STACK_BUTTON, 
                            NULL, TASK_PRIORITY_BUTTON, &buttonTaskHandle, 1);
    
    // --- Enter the appropriate mode ---
    switch (g_mode) {
        case SystemMode::BT_SPEAKER:
            initBluetoothMode();
            break;
        case SystemMode::AI_AGENT:
            initAIAgentMode();
            break;
        case SystemMode::SETUP_PORTAL:
        default:
            initSetupPortal();
            break;
    }
    
    Serial.printf("\n[BOOT] Initialization complete — Free heap: %d bytes\n", 
                  ESP.getFreeHeap());
}

// =============================================================================
// LOOP — Not used (all work in FreeRTOS tasks)
// Arduino loop() still runs as the lowest-priority task on Core 1.
// We use it only for periodic diagnostics.
// =============================================================================

void loop() {
    // Periodic heap monitoring — helps catch memory leaks during development
    static uint32_t lastDiag = 0;
    if (millis() - lastDiag > 10000) {
        lastDiag = millis();
        Serial.printf("[DIAG] Heap: %d free, %d min-ever | State: %d\n",
                      ESP.getFreeHeap(), ESP.getMinFreeHeap(), (int)g_state);
    }
    
    vTaskDelay(pdMS_TO_TICKS(1000));
}
