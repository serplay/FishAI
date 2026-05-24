// =============================================================================
// config.h — Hardware Pin Definitions & System Constants
// Billy Bass AI Animatronic Fish — ESP32-WROOM-32D (No PSRAM)
// =============================================================================
#pragma once

#include <Arduino.h>

// =============================================================================
// PIN DEFINITIONS
// =============================================================================

// --- I2S Microphone (INMP441) ---
// Used in AI Agent Mode for voice capture & streaming
#define MIC_I2S_PORT      I2S_NUM_1
#define MIC_SCK_PIN       14   // Serial Clock
#define MIC_WS_PIN        15   // Word Select (L/R Clock)
#define MIC_SD_PIN        32   // Serial Data

// --- I2S Amplifier (MAX98357A) ---
// Used in both BT Speaker Mode and AI Agent Mode for audio output
#define AMP_I2S_PORT      I2S_NUM_0
#define AMP_BCLK_PIN      26   // Bit Clock
#define AMP_LRC_PIN       25   // Left/Right Clock (Word Select)
#define AMP_DIN_PIN       22   // Data In

// --- Motor Driver (L298N) — Mouth ---
// PWM-controlled for proportional lip-sync
#define MOUTH_IN1_PIN     18
#define MOUTH_IN2_PIN     19

// --- Motor Driver (L298N) — Head/Body ---
// Simple on/off directional control
#define HEAD_IN3_PIN      21
#define HEAD_IN4_PIN      23

// --- Wake / Mode Button ---
#define BUTTON_PIN        33   // INPUT_PULLUP, active LOW

// =============================================================================
// I2S CONFIGURATION
// =============================================================================

// Audio sample rate — 16kHz is optimal for speech on ESP32 w/o PSRAM
#define AUDIO_SAMPLE_RATE     16000

// Bits per sample — INMP441 outputs 32-bit frames, we'll read as 32 and
// truncate to 16 for network/processing efficiency
#define MIC_BITS_PER_SAMPLE   32
#define AMP_BITS_PER_SAMPLE   16

// DMA buffer configuration — tuned for 520KB SRAM constraint
// Each DMA buffer: 256 samples × 2 bytes = 512 bytes
// 4 buffers × 512 bytes = 2KB per I2S port — very conservative
#define I2S_DMA_BUF_COUNT     4
#define I2S_DMA_BUF_LEN       256

// =============================================================================
// MOTOR / LIP-SYNC CONFIGURATION
// =============================================================================

// PWM configuration for mouth motor
#define MOUTH_PWM_FREQ        1000     // 1kHz PWM frequency
#define MOUTH_PWM_RESOLUTION  8        // 8-bit = 0–255 duty cycle
#define MOUTH_PWM_CHANNEL_A   0        // LEDC channel for IN1
#define MOUTH_PWM_CHANNEL_B   1        // LEDC channel for IN2

// RMS → Motor mapping thresholds
// Absolute silence gate: below this RMS, mouth always stays closed
// (catches true silence / noise floor only)
#define RMS_SILENCE_THRESHOLD 80

// Maximum RMS expected from a loud signal (for mapping to 0–255 PWM)
#define RMS_MAX_EXPECTED      8000

// Minimum PWM duty to actually move the motor (overcome stiction)
#define MOUTH_PWM_MIN         60
#define MOUTH_PWM_MAX         220

// --- Adaptive lip-sync envelope tracking ---
// The mouth opens when instantaneous RMS exceeds a slow-moving baseline
// by this ratio. 1.6 = must be 60% above baseline to open.
// Higher = more selective, only opens on vocal attacks / drum hits.
#define LIPSYNC_PEAK_RATIO    1.6f

// Smoothing for the slow-moving baseline (energy envelope of the song).
// 0.04 tracks over ~0.5s at 60Hz — fast enough to follow verse/chorus shifts.
#define LIPSYNC_BASELINE_ALPHA  0.04f

// Asymmetric smoothing for mouth position (fast attack, fast release)
// Attack — mouth snaps open on syllable onsets
#define LIPSYNC_ATTACK_ALPHA    0.7f
// Release — mouth closes quickly between syllables (~80ms to fully close)
#define LIPSYNC_RELEASE_ALPHA   0.45f

// =============================================================================
// BT PERFORMANCE MODE (head / dance behavior during Bluetooth playback)
// =============================================================================

// After this many ms of silence (no audio data), the next audio onset
// triggers a "performance" — head lifts and eventually dances.
#define BT_SILENCE_TIMEOUT_MS     7000

// How long the head stays raised after the performance starts (ms)
#define BT_HEAD_LIFT_DURATION_MS  4000

// After this many ms of continuous audio playback, start dancing
#define BT_DANCE_START_MS         30000

// Interval between dance moves (head toggle / tail flap) in ms
#define BT_DANCE_INTERVAL_MS      1500

// Dance burst duration — how long a dance burst lasts (ms)
#define BT_DANCE_BURST_MS         10000

// Dance rest duration — how long to rest between bursts (ms)
#define BT_DANCE_REST_MS          15000


// =============================================================================
// BUTTON TIMING
// =============================================================================

#define BUTTON_DEBOUNCE_MS       50
#define BUTTON_LONG_PRESS_MS     4000   // 4 seconds for mode override
#define BUTTON_SHORT_PRESS_MAX   500    // Max duration for "short press"

// =============================================================================
// NETWORKING
// =============================================================================

// Captive Portal AP credentials
#define AP_SSID             "Billy_Setup"
#define AP_PASSWORD         ""          // Open network for easy setup

// WebSocket server for AI Agent Mode
#define WS_SERVER_PORT      8765
// Default server address — configurable via web panel
#define WS_DEFAULT_HOST     "192.168.1.100"

// Mic stream chunk size (bytes) sent per WebSocket frame
// 512 samples × 2 bytes = 1024 bytes per chunk
#define MIC_CHUNK_SAMPLES   512
#define MIC_CHUNK_BYTES     (MIC_CHUNK_SAMPLES * 2)

// =============================================================================
// FREERTOS TASK CONFIGURATION
// =============================================================================

// Stack sizes — carefully tuned for no-PSRAM ESP32
#define TASK_STACK_AUDIO        8192
#define TASK_STACK_NETWORK      8192   // WiFi/WS needs more stack
#define TASK_STACK_MOTOR        2048
#define TASK_STACK_BUTTON       2048

// Task priorities (higher = more urgent, max 24 on ESP32)
#define TASK_PRIORITY_AUDIO     5      // Highest — audio must not glitch
#define TASK_PRIORITY_MOTOR     4      // Lip-sync must track audio closely
#define TASK_PRIORITY_NETWORK   3      // Network can tolerate jitter
#define TASK_PRIORITY_BUTTON    2      // UI polling is least critical

// =============================================================================
// NVS KEYS
// =============================================================================

#define NVS_NAMESPACE       "billy"
#define NVS_KEY_MODE        "opmode"   // 0 = AI Agent, 1 = BT Speaker
#define NVS_KEY_WIFI_SSID   "wifissid"
#define NVS_KEY_WIFI_PASS   "wifipass"
#define NVS_KEY_WS_HOST     "wshost"
#define NVS_KEY_WS_PORT     "wsport"

// =============================================================================
// SYSTEM MODES
// =============================================================================

enum class SystemMode : uint8_t {
    AI_AGENT    = 0,
    BT_SPEAKER  = 1,
    SETUP_PORTAL = 2   // Forced by long-press or first boot
};

// =============================================================================
// STATE MACHINE STATES
// =============================================================================

enum class SystemState : uint8_t {
    BOOT,               // Initial hardware setup
    SETUP_PORTAL,       // Captive portal AP + web server
    BT_INIT,            // Initializing Bluetooth A2DP
    BT_STREAMING,       // Bluetooth audio active
    WIFI_CONNECTING,    // Connecting to saved WiFi
    AI_IDLE,            // WiFi connected, mic streaming, waiting for wake
    AI_LISTENING,       // Wake word detected or button pressed
    AI_RESPONDING,      // Playing back server audio response
    ERROR_STATE         // Unrecoverable error — display via LED/serial
};
