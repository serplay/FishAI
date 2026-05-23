# =============================================================================
# wake_word.py — Picovoice Porcupine Wake Word Detection
# =============================================================================
#
# Wraps the Porcupine engine for async-compatible wake word detection.
# Buffers incoming PCM audio from the ESP32 and feeds Porcupine
# frame-by-frame. The blocking `.process()` call runs in a thread
# executor to keep the asyncio event loop responsive.
# =============================================================================

import asyncio
import logging
import struct
from collections import deque

import pvporcupine

from config import (
    PICOVOICE_ACCESS_KEY,
    PORCUPINE_BUILTIN_KEYWORD,
    PORCUPINE_KEYWORD_PATH,
    PORCUPINE_SENSITIVITY,
    SAMPLE_RATE,
)

logger = logging.getLogger(__name__)


class WakeWordEngine:
    """Async-compatible wake word detector using Picovoice Porcupine."""

    def __init__(self):
        self._porcupine: pvporcupine.Porcupine | None = None
        self._frame_length: int = 0
        self._sample_buffer: deque[int] = deque()
        self._active: bool = False

    def start(self):
        """Initialize the Porcupine engine."""
        if self._porcupine is not None:
            return

        try:
            kwargs = {
                "access_key": PICOVOICE_ACCESS_KEY,
                "sensitivities": [PORCUPINE_SENSITIVITY],
            }

            if PORCUPINE_KEYWORD_PATH:
                kwargs["keyword_paths"] = [PORCUPINE_KEYWORD_PATH]
                logger.info(f"Porcupine: using custom keyword from {PORCUPINE_KEYWORD_PATH}")
            else:
                kwargs["keywords"] = [PORCUPINE_BUILTIN_KEYWORD]
                logger.info(f"Porcupine: using built-in keyword '{PORCUPINE_BUILTIN_KEYWORD}'")

            self._porcupine = pvporcupine.create(**kwargs)
            self._frame_length = self._porcupine.frame_length
            self._active = True

            logger.info(
                f"Porcupine initialized — frame_length={self._frame_length}, "
                f"sample_rate={self._porcupine.sample_rate}"
            )

            # Sanity check: Porcupine expects 16kHz
            if self._porcupine.sample_rate != SAMPLE_RATE:
                logger.warning(
                    f"Sample rate mismatch! Porcupine wants {self._porcupine.sample_rate}Hz, "
                    f"ESP32 sends {SAMPLE_RATE}Hz"
                )

        except pvporcupine.PorcupineError as e:
            logger.error(f"Failed to initialize Porcupine: {e}")
            raise

    async def feed_audio(self, pcm_bytes: bytes) -> bool:
        """
        Feed raw PCM bytes (16-bit LE mono) into the wake word engine.

        Returns True if the wake word was detected in this chunk.
        Runs the blocking Porcupine process in a thread executor.
        """
        if not self._active or self._porcupine is None:
            return False

        # Decode 16-bit little-endian PCM bytes into int16 samples
        num_samples = len(pcm_bytes) // 2
        samples = struct.unpack(f"<{num_samples}h", pcm_bytes[:num_samples * 2])
        self._sample_buffer.extend(samples)

        # Process complete frames
        loop = asyncio.get_event_loop()
        detected = False

        while len(self._sample_buffer) >= self._frame_length:
            # Extract one frame
            frame = [self._sample_buffer.popleft() for _ in range(self._frame_length)]

            # Run Porcupine in a thread (it's CPU-bound)
            keyword_index = await loop.run_in_executor(
                None, self._porcupine.process, frame
            )

            if keyword_index >= 0:
                logger.info("🎤 Wake word detected!")
                detected = True
                break

        return detected

    def pause(self):
        """Pause detection (e.g., during active conversation)."""
        self._active = False
        self._sample_buffer.clear()

    def resume(self):
        """Resume detection after conversation ends."""
        self._sample_buffer.clear()
        self._active = True

    def stop(self):
        """Shut down the Porcupine engine and release resources."""
        self._active = False
        self._sample_buffer.clear()
        if self._porcupine is not None:
            self._porcupine.delete()
            self._porcupine = None
            logger.info("Porcupine engine stopped")
