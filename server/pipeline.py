# =============================================================================
# pipeline.py — AI Conversation Cascade Orchestrator
# =============================================================================
#
# Manages the real-time streaming pipeline:
#   ESP32 Mic PCM → Gemini (audio→text) → Chunker → ElevenLabs (text→PCM) → ESP32 Amp
#
# All stages run as concurrent asyncio tasks within an asyncio.TaskGroup.
# Cancellation of any stage (ESP32 disconnect, user interrupt) propagates
# cleanly to all others.
# =============================================================================

import asyncio
import logging
from typing import Callable, Awaitable

from gemini_client import GeminiLiveSession
from elevenlabs_client import ElevenLabsStreamer
from text_chunker import TextChunker

logger = logging.getLogger(__name__)

# Type aliases
SendToESP32 = Callable[[bytes], Awaitable[None]]        # Binary PCM
SendCommandToESP32 = Callable[[str], Awaitable[None]]    # JSON text


class ConversationPipeline:
    """
    Orchestrates a single conversation turn through the cascading pipeline.

    The pipeline has 3 concurrent streaming tasks:
      1. audio_to_gemini:     ESP32 mic PCM → Gemini audio input
      2. gemini_to_elevenlabs: Gemini text → Chunker → ElevenLabs text input
      3. elevenlabs_to_esp32:  ElevenLabs PCM → ESP32 speaker

    Lifecycle:
      1. Called when wake word or button press is detected
      2. Runs until Gemini signals turn_complete or cancellation
      3. Sends {"type":"done"} to ESP32 on completion
      4. Returns control to the wake word listening state
    """

    def __init__(
        self,
        audio_in_queue: asyncio.Queue[bytes | None],
        send_audio: SendToESP32,
        send_command: SendCommandToESP32,
    ):
        self._audio_in = audio_in_queue
        self._send_audio = send_audio
        self._send_command = send_command
        self._cancelled = False

    async def run(self):
        """
        Execute the full conversation pipeline.
        Blocks until the conversation turn is complete.
        """
        logger.info("━━━ Conversation pipeline started ━━━")

        gemini = GeminiLiveSession()
        elevenlabs = ElevenLabsStreamer()
        chunker = TextChunker()

        # Queue to pass text chunks from Gemini→ElevenLabs
        text_queue: asyncio.Queue[str | None] = asyncio.Queue()

        try:
            # Open both API connections concurrently
            await asyncio.gather(
                gemini.connect(),
                elevenlabs.connect(),
            )

            # Run the 3 pipeline stages concurrently
            async with asyncio.TaskGroup() as tg:
                tg.create_task(
                    self._audio_to_gemini(gemini),
                    name="audio→gemini",
                )
                tg.create_task(
                    self._gemini_to_elevenlabs(gemini, elevenlabs, chunker, text_queue),
                    name="gemini→elevenlabs",
                )
                tg.create_task(
                    self._elevenlabs_to_esp32(elevenlabs),
                    name="elevenlabs→esp32",
                )

        except* asyncio.CancelledError:
            logger.info("Pipeline cancelled")
        except* Exception as eg:
            for exc in eg.exceptions:
                logger.error(f"Pipeline error: {exc}", exc_info=exc)
        finally:
            # Clean up API connections
            await self._cleanup(gemini, elevenlabs)

            # Signal ESP32 that the response is complete
            if not self._cancelled:
                try:
                    await self._send_command('{"type":"done"}')
                except Exception:
                    pass

            logger.info("━━━ Conversation pipeline ended ━━━")

    def cancel(self):
        """Signal cancellation (e.g., ESP32 disconnected or user pressed cancel)."""
        self._cancelled = True

    # =========================================================================
    # Stage 1: ESP32 Mic → Gemini
    # =========================================================================

    async def _audio_to_gemini(self, gemini: GeminiLiveSession):
        """
        Continuously read PCM audio from the ESP32 queue and stream it
        to Gemini for real-time speech understanding.
        """
        logger.debug("Stage 1: audio→gemini started")
        try:
            while not self._cancelled:
                # Wait for audio with a timeout to check cancellation
                try:
                    pcm_bytes = await asyncio.wait_for(
                        self._audio_in.get(), timeout=0.1
                    )
                except asyncio.TimeoutError:
                    continue

                if pcm_bytes is None:
                    # Sentinel: no more audio (ESP32 disconnected)
                    logger.info("Audio stream ended (sentinel)")
                    break

                await gemini.send_audio(pcm_bytes)

        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"audio→gemini error: {e}")
            raise

    # =========================================================================
    # Stage 2: Gemini Text → Chunker → ElevenLabs
    # =========================================================================

    async def _gemini_to_elevenlabs(
        self,
        gemini: GeminiLiveSession,
        elevenlabs: ElevenLabsStreamer,
        chunker: TextChunker,
        text_queue: asyncio.Queue[str | None],
    ):
        """
        Receive text tokens from Gemini, chunk them at sentence boundaries,
        and send chunks to ElevenLabs for synthesis.
        """
        logger.debug("Stage 2: gemini→elevenlabs started")
        full_response = ""

        try:
            async for token in gemini.receive_text():
                if self._cancelled:
                    break

                full_response += token
                logger.debug(f"Gemini token: {token!r}")

                # Feed token to chunker — may emit 0 or more chunks
                for chunk in chunker.feed(token):
                    logger.info(f"→ ElevenLabs chunk: {chunk!r}")
                    await elevenlabs.send_text(chunk, flush=True)

            # Flush any remaining text from the chunker
            remaining = chunker.flush()
            if remaining and not self._cancelled:
                logger.info(f"→ ElevenLabs final: {remaining!r}")
                await elevenlabs.send_text(remaining, flush=True)

            # Signal ElevenLabs that text input is complete
            await elevenlabs.finish()

            logger.info(f"Full Gemini response: {full_response!r}")

        except asyncio.CancelledError:
            chunker.reset()
            raise
        except Exception as e:
            logger.error(f"gemini→elevenlabs error: {e}")
            raise

    # =========================================================================
    # Stage 3: ElevenLabs Audio → ESP32
    # =========================================================================

    async def _elevenlabs_to_esp32(self, elevenlabs: ElevenLabsStreamer):
        """
        Receive PCM audio chunks from ElevenLabs and relay them
        directly to the ESP32 WebSocket connection.
        """
        logger.debug("Stage 3: elevenlabs→esp32 started")
        total_bytes = 0

        try:
            async for pcm_bytes in elevenlabs.receive_audio():
                if self._cancelled:
                    break

                await self._send_audio(pcm_bytes)
                total_bytes += len(pcm_bytes)

            logger.info(f"Audio relay complete — {total_bytes:,} bytes sent to ESP32")

        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"elevenlabs→esp32 error: {e}")
            raise

    # =========================================================================
    # Cleanup
    # =========================================================================

    async def _cleanup(
        self, gemini: GeminiLiveSession, elevenlabs: ElevenLabsStreamer
    ):
        """Close API connections gracefully."""
        close_tasks = []
        if gemini.is_connected:
            close_tasks.append(gemini.close())
        if elevenlabs.is_connected:
            close_tasks.append(elevenlabs.close())

        if close_tasks:
            await asyncio.gather(*close_tasks, return_exceptions=True)
