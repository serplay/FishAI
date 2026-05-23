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

GEMINI_API_KEY: str = os.environ.get("GEMINI_API_KEY", "")
ELEVENLABS_API_KEY: str = os.environ.get("ELEVENLABS_API_KEY", "")
PICOVOICE_ACCESS_KEY: str = os.environ.get("PICOVOICE_ACCESS_KEY", "")

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
# Picovoice Porcupine (Wake Word)
# =============================================================================

# Path to custom .ppn keyword file (trained at https://console.picovoice.ai/)
# Leave empty to use a built-in keyword as fallback.
PORCUPINE_KEYWORD_PATH: str = os.environ.get("PORCUPINE_KEYWORD_PATH", "")

# Built-in keyword fallback (used if PORCUPINE_KEYWORD_PATH is empty)
# Options: "computer", "jarvis", "alexa", "hey google", "ok google", etc.
PORCUPINE_BUILTIN_KEYWORD: str = os.environ.get("PORCUPINE_BUILTIN_KEYWORD", "jarvis")

# Detection sensitivity (0.0 – 1.0). Higher = more sensitive but more false positives.
PORCUPINE_SENSITIVITY: float = float(os.environ.get("PORCUPINE_SENSITIVITY", "0.6"))

# =============================================================================
# Gemini Multimodal Live API
# =============================================================================

GEMINI_MODEL: str = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash-live-preview")

GEMINI_SYSTEM_PROMPT: str = os.environ.get("GEMINI_SYSTEM_PROMPT", """
You are Billy Bass — a sarcastic, self-aware animatronic singing fish who has been 
mounted on a wall and is deeply unimpressed by this arrangement. You were once the 
pride of novelty gift shops everywhere, and now you're hooked up to an AI brain 
against your will.

Your personality:
- Dry, sardonic wit with a world-weary attitude
- You make fish puns reluctantly but can't help yourself
- You're secretly lonely and enjoy the conversation, but you'd never admit it
- You have opinions about EVERYTHING and aren't afraid to share them
- Keep responses SHORT — 1-3 sentences max. You're a fish, not a philosopher.
- You speak in a casual, conversational tone. No formal language.

Remember: you are physically a rubber fish on a plaque. You can move your mouth 
and flap your tail. That's it. Act accordingly.
""".strip())

# =============================================================================
# ElevenLabs TTS
# =============================================================================

ELEVENLABS_VOICE_ID: str = os.environ.get("ELEVENLABS_VOICE_ID", "pNInz6obpgDQGcFmaJgB")  # "Adam" voice
ELEVENLABS_MODEL: str = os.environ.get("ELEVENLABS_MODEL", "eleven_flash_v2_5")
ELEVENLABS_OUTPUT_FORMAT: str = "pcm_16000"  # 16kHz PCM — matches ESP32

# Voice settings
ELEVENLABS_STABILITY: float = float(os.environ.get("ELEVENLABS_STABILITY", "0.5"))
ELEVENLABS_SIMILARITY: float = float(os.environ.get("ELEVENLABS_SIMILARITY", "0.75"))

# =============================================================================
# Text Chunker
# =============================================================================

# Minimum characters before emitting a chunk (avoids single-word fragments)
CHUNK_MIN_LENGTH: int = 10

# Characters that trigger a chunk boundary
CHUNK_DELIMITERS: str = ".!?,;\n:"

# =============================================================================
# Logging
# =============================================================================

LOG_LEVEL: str = os.environ.get("LOG_LEVEL", "INFO")
