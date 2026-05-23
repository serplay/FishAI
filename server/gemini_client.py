# =============================================================================
# gemini_client.py — Gemini Multimodal Live API Client
# =============================================================================
#
# Manages a bidirectional WebSocket session with Google's Gemini Live API.
# Sends real-time PCM audio and receives streamed text tokens.
# Uses the official google-genai SDK for the async Live connection.
# =============================================================================

import asyncio
import logging
from typing import AsyncGenerator

from google import genai
from google.genai import types

from config import GEMINI_API_KEY, GEMINI_MODEL, GEMINI_SYSTEM_PROMPT, AUDIO_MIME

logger = logging.getLogger(__name__)


class GeminiLiveSession:
    """
    Manages a single conversation turn with Gemini's Multimodal Live API.

    Usage:
        async with GeminiLiveSession() as session:
            # Send audio from ESP32
            await session.send_audio(pcm_bytes)

            # Receive text tokens as they stream
            async for token in session.receive_text():
                print(token, end="")
    """

    def __init__(self):
        self._client = genai.Client(
            api_key=GEMINI_API_KEY,
            http_options=types.HttpOptions(api_version="v1beta1"),
        )
        self._session = None
        self._connected = False

    async def connect(self):
        """Open the Live API bidirectional WebSocket session."""
        config = types.LiveConnectConfig(
            response_modalities=[types.Modality.TEXT],
            system_instruction=types.Content(
                parts=[types.Part(text=GEMINI_SYSTEM_PROMPT)]
            ),
        )

        logger.info(f"Connecting to Gemini Live API ({GEMINI_MODEL})...")

        self._session = self._client.aio.live.connect(
            model=GEMINI_MODEL,
            config=config,
        )
        # Enter the async context manager
        self._live = await self._session.__aenter__()
        self._connected = True
        logger.info("Gemini Live session established")

    async def send_audio(self, pcm_bytes: bytes):
        """
        Stream raw PCM audio bytes to Gemini.
        Call this continuously with mic data from the ESP32.
        """
        if not self._connected or self._live is None:
            return

        try:
            await self._live.send_realtime_input(
                audio=types.Blob(data=pcm_bytes, mime_type=AUDIO_MIME)
            )
        except Exception as e:
            logger.error(f"Error sending audio to Gemini: {e}")
            self._connected = False
            raise

    async def receive_text(self) -> AsyncGenerator[str, None]:
        """
        Async generator that yields text tokens as they arrive from Gemini.
        Terminates when Gemini signals end-of-turn.
        """
        if not self._connected or self._live is None:
            return

        try:
            async for response in self._live.receive():
                # Check for text content
                if response.text is not None:
                    yield response.text

                # Check for turn completion
                server_content = getattr(response, "server_content", None)
                if server_content and getattr(server_content, "turn_complete", False):
                    logger.info("Gemini: turn complete")
                    return

        except asyncio.CancelledError:
            logger.info("Gemini receive cancelled")
            raise
        except Exception as e:
            logger.error(f"Error receiving from Gemini: {e}")
            raise

    async def close(self):
        """Close the Gemini Live session gracefully."""
        if self._session is not None:
            try:
                await self._session.__aexit__(None, None, None)
            except Exception as e:
                logger.debug(f"Error closing Gemini session: {e}")
            finally:
                self._session = None
                self._live = None
                self._connected = False
                logger.info("Gemini Live session closed")

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()
