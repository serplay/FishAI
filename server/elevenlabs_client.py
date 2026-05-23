# =============================================================================
# elevenlabs_client.py — ElevenLabs Streaming TTS Client
# =============================================================================
#
# Opens a WebSocket connection to the ElevenLabs Input Text Streaming API.
# Sends text chunks and receives raw PCM audio bytes in real-time.
# Uses the low-latency `eleven_flash_v2_5` model with `pcm_16000` output
# to match the ESP32's I2S amplifier configuration.
# =============================================================================

import asyncio
import base64
import json
import logging
from typing import AsyncGenerator

import websockets

from config import (
    ELEVENLABS_API_KEY,
    ELEVENLABS_MODEL,
    ELEVENLABS_OUTPUT_FORMAT,
    ELEVENLABS_SIMILARITY,
    ELEVENLABS_STABILITY,
    ELEVENLABS_VOICE_ID,
)

logger = logging.getLogger(__name__)

# ElevenLabs WebSocket endpoint for input text streaming
_EL_WS_URL = (
    f"wss://api.elevenlabs.io/v1/text-to-speech"
    f"/{ELEVENLABS_VOICE_ID}/stream-input"
    f"?model_id={ELEVENLABS_MODEL}"
    f"&output_format={ELEVENLABS_OUTPUT_FORMAT}"
)


class ElevenLabsStreamer:
    """
    Real-time text-to-speech streaming via ElevenLabs WebSocket API.

    Usage:
        async with ElevenLabsStreamer() as tts:
            # Send text chunks as they arrive from the LLM
            await tts.send_text("Hello, I'm a fish.")
            await tts.send_text("A very sarcastic fish.")
            await tts.finish()  # Signal end of input

            # Receive PCM audio chunks
            async for pcm_bytes in tts.receive_audio():
                send_to_esp32(pcm_bytes)
    """

    def __init__(self):
        self._ws: websockets.WebSocketClientProtocol | None = None
        self._connected: bool = False

    async def connect(self):
        """Open the WebSocket connection and send the initial auth frame."""
        logger.info("Connecting to ElevenLabs TTS...")

        self._ws = await websockets.connect(
            _EL_WS_URL,
            additional_headers={"xi-api-key": ELEVENLABS_API_KEY},
        )

        # Send the initial configuration / auth message
        # A space character with voice settings initializes the stream
        init_msg = {
            "text": " ",
            "voice_settings": {
                "stability": ELEVENLABS_STABILITY,
                "similarity_boost": ELEVENLABS_SIMILARITY,
            },
            "xi_api_key": ELEVENLABS_API_KEY,
        }
        await self._ws.send(json.dumps(init_msg))
        self._connected = True
        logger.info("ElevenLabs TTS connected")

    async def send_text(self, text: str, flush: bool = True):
        """
        Send a text chunk to ElevenLabs for synthesis.

        Args:
            text: The text to synthesize.
            flush: If True, forces immediate synthesis of buffered text.
                   Set True at sentence boundaries for lower latency.
        """
        if not self._connected or self._ws is None:
            return

        msg = {
            "text": text,
            "try_trigger_generation": True,
        }
        if flush:
            msg["flush"] = True

        try:
            await self._ws.send(json.dumps(msg))
        except websockets.exceptions.ConnectionClosed:
            logger.warning("ElevenLabs connection closed during send")
            self._connected = False

    async def finish(self):
        """
        Signal end of text input. ElevenLabs will synthesize any remaining
        buffered text and close the generation.
        """
        if not self._connected or self._ws is None:
            return

        try:
            await self._ws.send(json.dumps({"text": ""}))
        except websockets.exceptions.ConnectionClosed:
            pass

    async def receive_audio(self) -> AsyncGenerator[bytes, None]:
        """
        Async generator that yields raw PCM audio bytes as they arrive
        from ElevenLabs. Terminates when ElevenLabs signals completion.
        """
        if not self._connected or self._ws is None:
            return

        try:
            async for message in self._ws:
                data = json.loads(message)

                # Audio data arrives as base64-encoded PCM
                audio_b64 = data.get("audio")
                if audio_b64:
                    pcm_bytes = base64.b64decode(audio_b64)
                    if len(pcm_bytes) > 0:
                        yield pcm_bytes

                # Check for final message
                if data.get("isFinal"):
                    logger.info("ElevenLabs: generation complete")
                    return

        except websockets.exceptions.ConnectionClosed:
            logger.info("ElevenLabs WebSocket closed")
        except asyncio.CancelledError:
            logger.info("ElevenLabs receive cancelled")
            raise

    async def close(self):
        """Close the WebSocket connection."""
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
            finally:
                self._ws = None
                self._connected = False
                logger.info("ElevenLabs connection closed")

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()
