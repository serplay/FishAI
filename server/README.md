# Billy Bass AI — Backend Relay Server

Real-time AI conversation server for the ESP32-based Billy Bass animatronic fish.

## Architecture

```
ESP32 Mic → [WebSocket] → Wake Word → Gemini Live API → Text Chunker → ElevenLabs TTS → [WebSocket] → ESP32 Speaker
```

## Quick Start

### 1. Prerequisites

- Python 3.11+
- A [Picovoice](https://console.picovoice.ai/) access key (free tier available)
- A [Google AI Studio](https://aistudio.google.com/apikey) API key
- An [ElevenLabs](https://elevenlabs.io/) API key

### 2. Setup

```bash
cd server

# Create virtual environment
python -m venv venv
source venv/bin/activate  # Linux/Mac
# or: venv\Scripts\activate  # Windows

# Install dependencies
pip install -r requirements.txt

# Configure API keys
cp .env.example .env
nano .env  # Fill in your API keys
```

### 3. Run

```bash
python main.py
```

The server will start on `ws://0.0.0.0:8765/ws` by default.

### 4. Configure ESP32

On the Billy Bass setup portal:
- Set **Operating Mode** to "AI Agent Mode (WiFi)"
- Set **AI Server Host** to your server's IP address
- Set **AI Server Port** to `8765`
- Save & reboot

## How It Works

1. **Idle State:** ESP32 streams mic audio to the server. The server runs Porcupine wake word detection locally.

2. **Wake Detected:** When the wake word ("Jarvis" by default) is detected or the button is pressed:
   - Server sends `{"type":"wake"}` → ESP32 raises the fish's head
   - Mic audio is redirected from Porcupine to Gemini

3. **AI Conversation:** Three concurrent streams run simultaneously:
   - **ESP32 → Gemini:** Raw PCM audio streamed to Gemini's Multimodal Live API
   - **Gemini → ElevenLabs:** Text tokens chunked at sentence boundaries, sent to ElevenLabs
   - **ElevenLabs → ESP32:** PCM audio relayed back to the ESP32 for playback

4. **Turn Complete:** When Gemini finishes responding:
   - Server sends `{"type":"done"}` → ESP32 lowers the head
   - Server returns to wake word listening

## Configuration

All settings are in `.env` (see `.env.example` for the full list):

| Variable | Default | Description |
|---|---|---|
| `GEMINI_API_KEY` | *required* | Google AI Studio API key |
| `ELEVENLABS_API_KEY` | *required* | ElevenLabs API key |
| `ELEVENLABS_VOICE_ID` | *required* | Voice to use for TTS |
| `PICOVOICE_ACCESS_KEY` | *required* | Picovoice access key |
| `PORCUPINE_BUILTIN_KEYWORD` | `jarvis` | Built-in wake word |
| `SERVER_PORT` | `8765` | WebSocket server port |
| `LOG_LEVEL` | `INFO` | Logging verbosity |

## File Structure

```
server/
├── main.py               # WebSocket server + session manager
├── pipeline.py            # Conversation cascade orchestrator
├── wake_word.py           # Porcupine wake word wrapper
├── gemini_client.py       # Gemini Multimodal Live API client
├── elevenlabs_client.py   # ElevenLabs streaming TTS client
├── text_chunker.py        # Sentence-boundary text chunker
├── config.py              # Environment variables & constants
├── requirements.txt       # Python dependencies
├── .env.example           # API key template
└── README.md              # This file
```

## Troubleshooting

- **"Failed to start wake word engine"** — Check your `PICOVOICE_ACCESS_KEY` in `.env`
- **No audio playback** — Verify `ELEVENLABS_VOICE_ID` is valid and your API key has credits
- **ESP32 won't connect** — Ensure the server IP and port match what's configured on the fish
- **High latency** — Use `eleven_flash_v2_5` model (default) and `LOG_LEVEL=WARNING` to reduce overhead
