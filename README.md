# 🐟 Billy Bass AI

An AI-powered animatronic Big Mouth Billy Bass that listens, thinks, and talks back — with synchronized lip movements, head gestures, and a sarcastic personality.

Built on an **ESP32** microcontroller with a Python relay server that chains **Google Gemini** → **ElevenLabs** into a real-time conversational pipeline. Also works as a standalone **Bluetooth speaker** with automatic dance choreography.

## Features

- **AI Conversations** — Say "Hey Vega" (or press the button) and Billy responds with context-aware sass, powered by Gemini's multimodal understanding and ElevenLabs voice synthesis
- **Bluetooth Speaker** — Pairs as a standard A2DP receiver with real-time lip-sync to any music you stream
- **Adaptive Lip-Sync** — RMS-based envelope tracking with fast attack / smooth release for natural mouth movement on both speech and music
- **Dance Mode** — Detects sustained music playback and triggers head-bobbing dance bursts (10s on, 15s rest) — only after 30s of actual audio energy, not silence
- **Web Setup Portal** — Captive portal (`Billy_Setup` AP) for WiFi config, mode selection, and server settings from any phone
- **Button Override** — Short press = wake/interrupt; long press (4s) = factory reset back to setup portal

## Architecture

```
┌──────────────────────── ESP32 ────────────────────────┐
│                                                        │
│  Mic (INMP441) ──I2S──► Audio Task ──WS──► Server     │
│                                                        │
│  Server ──WS──► Audio Task ──I2S──► Amp (MAX98357A)   │
│                                                        │
│  Motor Task (60Hz) ──► Mouth PWM (lip-sync)           │
│                    ──► Head GPIO (dance/gestures)      │
│                                                        │
│  State: PORTAL │ BT_STREAMING │ AI_IDLE/LISTEN/REPLY  │
└────────────────────────────────────────────────────────┘
          │ WebSocket (16kHz PCM + JSON)
          ▼
┌──────────────── Ubuntu Relay Server ──────────────────┐
│                                                        │
│  Porcupine Wake Word ──► Gemini Live (text only)      │
│                              │                         │
│                              ▼                         │
│                     Text Chunker (. , ? !)             │
│                              │                         │
│                              ▼                         │
│                     ElevenLabs TTS ──► PCM to ESP32   │
└────────────────────────────────────────────────────────┘
```

## Hardware

| Component | Part | Notes |
|-----------|------|-------|
| MCU | ESP32-WROOM-32D | No PSRAM — 520KB SRAM, carefully budgeted |
| Microphone | INMP441 | I2S input, 3.3V |
| Speaker Amp | MAX98357A | I2S output, 5V |
| Motor Driver | L298N H-Bridge | Mouth (PWM lip-sync) + Head (directional) |
| Power | 3.7V Li-Po → TP4056 → MT3608 | Boosted to 5V; star grounding + decoupling caps |
| Button | Tactile switch on GPIO 33 | INPUT_PULLUP, debounced |

<details>
<summary><strong>Pin Map</strong></summary>

```
Microphone (INMP441)           Amplifier (MAX98357A)
  SCK  = GPIO 14                 BCLK = GPIO 26
  WS   = GPIO 15                 LRC  = GPIO 25
  SD   = GPIO 32                 DIN  = GPIO 22

Motor Driver (L298N)           Button
  Mouth IN1 = GPIO 18 (PWM)      GPIO 33
  Mouth IN2 = GPIO 19 (PWM)
  Head  IN3 = GPIO 21
  Head  IN4 = GPIO 23
```

</details>

## Getting Started

### Prerequisites

- [PlatformIO](https://platformio.org/) (VS Code extension or CLI)
- Python 3.10+
- API keys for [Gemini](https://aistudio.google.com/apikey), [ElevenLabs](https://elevenlabs.io/app/settings/api-keys), and [Picovoice](https://console.picovoice.ai/)

### 1. Flash the Firmware

```bash
git clone https://github.com/serplay/FishAI.git
cd FishAI

pio run --target upload    # Build and flash
pio device monitor         # Serial output (115200 baud)
```

### 2. Configure the Fish

On first boot the ESP32 creates an open WiFi AP called **Billy_Setup**.

1. Connect to `Billy_Setup` from your phone — a captive portal appears
2. Pick your WiFi network and enter the password
3. Choose mode: **AI Agent** or **Bluetooth Speaker**
4. For AI mode, enter your server's local IP (default port `8765`)
5. Save & Reboot

### 3. Start the Server (AI Agent Mode)

```bash
cd server
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Fill in your API keys in .env

python main.py
```

The server listens on `ws://0.0.0.0:8765/ws` by default. The ESP32 connects automatically after reboot.

### 4. Bluetooth Speaker Mode

No server needed — just pair your phone to **"Billy Bass"** and play music. The fish lip-syncs automatically and starts dancing after 30 seconds of continuous playback.

## Project Structure

```
FishAI/
├── include/                   # ESP32 headers
│   ├── config.h               #   Pin map, timing, enums, constants
│   ├── audio_manager.h        #   I2S mic/amp driver
│   ├── motor_control.h        #   Lip-sync + head motor controller
│   ├── network_manager.h      #   WiFi, portal, WebSocket client
│   └── button_handler.h       #   Debounced button (short/long press)
├── src/                       # ESP32 sources
│   ├── main.cpp               #   State machine, FreeRTOS tasks, A2DP
│   ├── audio_manager.cpp      #   I2S init, read/write
│   ├── motor_control.cpp      #   RMS engine, PWM control, dance logic
│   ├── network_manager.cpp    #   Web server routes, WS events, NVS
│   └── button_handler.cpp     #   ISR-based polling
├── server/                    # Python relay server
│   ├── main.py                #   WS server, session manager
│   ├── pipeline.py            #   Gemini → Chunker → ElevenLabs
│   ├── gemini_client.py       #   Gemini Multimodal Live client
│   ├── elevenlabs_client.py   #   ElevenLabs streaming TTS
│   ├── text_chunker.py        #   Punctuation-based text splitter
│   ├── wake_word.py           #   Porcupine wake word engine
│   ├── config.py              #   Env vars, models, system prompt
│   ├── requirements.txt       #   websockets, google-genai, pvporcupine
│   └── .env.example           #   API key template
└── platformio.ini             # Build config, libs, partition table
```

## Configuration

### Firmware Constants

Key tuning parameters in [`include/config.h`](include/config.h):

| Constant | Default | What it does |
|----------|---------|--------------|
| `RMS_SILENCE_THRESHOLD` | `80` | Audio energy floor — below this, mouth stays closed |
| `LIPSYNC_PEAK_RATIO` | `1.6` | Signal must exceed baseline by 60% to open |
| `LIPSYNC_ATTACK_ALPHA` | `0.7` | Mouth open speed (higher = snappier) |
| `LIPSYNC_RELEASE_ALPHA` | `0.45` | Mouth close speed |
| `BT_DANCE_START_MS` | `30000` | Continuous audio before dance unlocks |
| `BT_DANCE_BURST_MS` | `10000` | Dance duration per burst |
| `BT_DANCE_REST_MS` | `15000` | Rest between bursts |

### Server Environment

See [`server/.env.example`](server/.env.example) for all options. Key variables:

| Variable | Required | Description |
|----------|----------|-------------|
| `GEMINI_API_KEY` | ✅ | Google Gemini API key |
| `ELEVENLABS_API_KEY` | ✅ | ElevenLabs API key |
| `ELEVENLABS_VOICE_ID` | ✅ | Voice to use for TTS |
| `PICOVOICE_ACCESS_KEY` | ✅ | Picovoice key for wake word |
| `GEMINI_MODEL` | | Default: `gemini-2.0-flash-live-preview` |
| `ELEVENLABS_MODEL` | | Default: `eleven_flash_v2_5` |
| `SERVER_PORT` | | Default: `8765` |

## Firmware Dependencies

Managed automatically by PlatformIO:

| Library | Version | Purpose |
|---------|---------|---------|
| [ESP32-A2DP](https://github.com/pschatzmann/ESP32-A2DP) | v1.8.3 | Bluetooth A2DP Sink |
| [arduino-audio-tools](https://github.com/pschatzmann/arduino-audio-tools) | v1.0.2 | I2S / A2DP integration |
| [ESPAsyncWebServer](https://github.com/me-no-dev/ESPAsyncWebServer) | latest | Captive portal web server |
| [WebSockets](https://github.com/Links2004/arduinoWebSockets) | ≥2.6.1 | WebSocket client (AI mode) |
| [ArduinoJson](https://github.com/bblanchon/ArduinoJson) | ≥7.3.0 | JSON parsing |

## Constraints & Design Decisions

- **No PSRAM** — all buffers are statically sized or stack-allocated. No `malloc` in hot paths.
- **WiFi ⊕ Bluetooth** — mutually exclusive due to RAM. Mode is stored in NVS and requires reboot to switch.
- **~400KB usable heap** after FreeRTOS + radio stack. The firmware uses `huge_app.csv` partitions (~3MB app, no OTA).
- **Text-only Gemini** — the server requests text from Gemini (not audio) so the voice character is controlled by ElevenLabs, keeping latency low and personality consistent.
- **Star grounding + decoupling caps** — prevents motor-induced brownouts from crashing the ESP32 on battery power.
