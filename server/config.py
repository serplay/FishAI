# =============================================================================
# config.py — Environment & Constants for Billy Bass AI Server
# =============================================================================

import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from the server directory
_env_path = Path(__file__).parent / ".env"
load_dotenv(_env_path)

# =============================================================================
# API Keys
# =============================================================================

ELEVENLABS_API_KEY: str = os.environ.get("ELEVENLABS_API_KEY", "")

# =============================================================================
# ElevenLabs Conversational AI Agent
# =============================================================================

# Agent ID — create one at https://elevenlabs.io/app/conversational-ai
# The agent's personality, voice, LLM, and system prompt are all
# configured in the ElevenLabs dashboard, not here.
ELEVENLABS_AGENT_ID: str = os.environ.get("ELEVENLABS_AGENT_ID", "")

# ConvAI WebSocket base URL (no need to change unless self-hosting)
ELEVENLABS_CONVAI_URL: str = os.environ.get(
    "ELEVENLABS_CONVAI_URL",
    "wss://api.elevenlabs.io/v1/convai/conversation",
)

# =============================================================================
# Server
# =============================================================================

SERVER_HOST: str = os.environ.get("SERVER_HOST", "0.0.0.0")
SERVER_PORT: int = int(os.environ.get("SERVER_PORT", "8765"))
WS_PATH: str = "/ws"

# =============================================================================
# Audio Format (must match ESP32 I2S config)
# =============================================================================

SAMPLE_RATE: int = 16000       # Hz
SAMPLE_WIDTH: int = 2          # bytes (16-bit)
CHANNELS: int = 1              # mono
AUDIO_MIME: str = "audio/pcm;rate=16000"

# =============================================================================
# openWakeWord (Wake Word)
# =============================================================================

# Comma-separated list of pre-trained model names to load.
# Available models: "hey_jarvis_v0.1", "alexa_v0.1", "hey_mycroft_v0.1", etc.
# See: https://github.com/dscripka/openWakeWord#pre-trained-models
# Leave empty to load ALL available models (not recommended for performance).
OWW_MODEL_NAMES: str = os.environ.get("OWW_MODEL_NAMES", "hey_jarvis_v0.1")

# Detection threshold (0.0 – 1.0). Higher = fewer false positives.
# 0.5 is a good starting point; increase if you get too many false triggers.
OWW_THRESHOLD: float = float(os.environ.get("OWW_THRESHOLD", "0.5"))

# =============================================================================
# Logging
# =============================================================================

LOG_LEVEL: str = os.environ.get("LOG_LEVEL", "INFO")
