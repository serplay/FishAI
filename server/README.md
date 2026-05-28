# Billy Bass AI — Backend Relay Server

Real-time AI conversation server for the ESP32-based Billy Bass animatronic fish.

## Architecture

```text
ESP32 Mic → WebSocket → Wake Word → ElevenLabs ConvAI → PCM relay → ESP32 Speaker
```

## Quick Start

### 1. Prerequisites

- Python 3.11+
- An [ElevenLabs](https://elevenlabs.io/) API key
- An ElevenLabs Conversational AI agent ID
- No wake-word API key is required; the server uses [openWakeWord](https://github.com/dscripka/openWakeWord) locally

### 2. Setup

```bash
cd server

# Create virtual environment
python -m venv venv
source venv/bin/activate  # Linux/Mac
# or: venv\Scripts\activate  # Windows

# Install dependencies
pip install -r requirements.txt

copy .env.example .env
```

Fill in the values in `.env`, especially:

- `ELEVENLABS_API_KEY`
- `ELEVENLABS_AGENT_ID`
- `SERVER_PORT` if you want a non-default port

### 3. Run

```bash
python main.py
```

The server listens on `ws://0.0.0.0:8765/ws` by default.

### 4. Configure the ESP32

On the fish setup portal:

- Set **Operating Mode** to `AI Agent Mode (WiFi)`
- Set **AI Server Host** to your server's `IP address`
- Set **AI Server Port** to `8765`
- Save & reboot

## How It Works

1. **Idle state:** the ESP32 streams mic audio to this server while the wake word engine listens locally.
2. **Wake event:** either the wake word or the button starts the conversation and the server sends `{"type":"wake"}` to the fish.
3. **Conversation:** the ESP32 mic is forwarded to ElevenLabs ConvAI, and TTS audio is chunked and relayed back to the ESP32 speaker.
4. **Turn end:** when the agent finishes, the server sends `{"type":"done"}` and the fish returns to idle.

## Configuration

All settings are in `.env` (see `.env.example` for the full list):

| Variable | Default | Description |
|---|---|---|
| `ELEVENLABS_API_KEY` | required | ElevenLabs API key |
| `ELEVENLABS_AGENT_ID` | required | Conversational AI agent ID |
| `OWW_MODEL_NAMES` | `hey_jarvis_v0.1` | Wake word model(s) |
| `OWW_THRESHOLD` | `0.5` | Wake word detection threshold |
| `SERVER_HOST` | `0.0.0.0` | Bind address |
| `SERVER_PORT` | `8765` | WebSocket server port |
| `LOG_LEVEL` | `INFO` | Logging verbosity |

## File Structure

```text
server/
├── main.py             # WebSocket server and session manager
├── pipeline.py         # ElevenLabs audio bridge
├── wake_word.py        # Wake word engine wrapper
├── config.py           # Environment variables and constants
├── requirements.txt    # Python dependencies
├── .env.example        # Local configuration template
└── README.md           # This file
```

## Troubleshooting

- **Wake word engine fails to start**: verify that `openwakeword` is installed and that model downloads are allowed on first run.
- **ESP32 disconnects on wake**: confirm the fish is on the same WiFi network and the server IP/port are correct.
- **No playback**: verify the ElevenLabs agent exists and your API key has credits.
- **Crackling or dropouts**: lower server load and keep the ESP32 playback queue settings as shipped in firmware.
