# =============================================================================
# wake_word.py — openWakeWord Wake Word Detection
# =============================================================================
#
# Wraps the openWakeWord engine for async-compatible wake word detection.
# Buffers incoming PCM audio from the ESP32 and feeds openWakeWord in
# 1280-sample chunks (its native processing size at 16kHz). The blocking
# .predict() call runs in a thread executor to keep the asyncio loop alive.
#
# openWakeWord is fully free & open source (Apache 2.0) — no API keys.
# Pre-trained models: "hey_jarvis", "alexa", "hey_mycroft", etc.
# Custom models can be trained with ~5 minutes of audio.
# https://github.com/dscripka/openWakeWord
# =============================================================================

import asyncio
import logging
import struct
from collections import deque

import numpy as np
import openwakeword
from openwakeword.model import Model

from config import (
    OWW_MODEL_NAMES,
    OWW_THRESHOLD,
    SAMPLE_RATE,
)

logger = logging.getLogger(__name__)

# openWakeWord processes audio in chunks of 1280 samples at 16kHz (80ms)
OWW_FRAME_LENGTH = 1280


class WakeWordEngine:
    """Async-compatible wake word detector using openWakeWord."""

    def __init__(self):
        self._model: Model | None = None
        self._sample_buffer: deque[int] = deque()
        self._active: bool = False

    def start(self):
        """Initialize the openWakeWord engine and download models if needed."""
        if self._model is not None:
            return

        try:
            # Download pre-trained models on first run (cached afterwards)
            openwakeword.utils.download_models()

            model_names = [m.strip() for m in OWW_MODEL_NAMES.split(",") if m.strip()]

            self._model = Model(
                wakeword_models=model_names if model_names else None,
                inference_framework="onnx",
            )
            self._active = True

            loaded = list(self._model.models.keys())
            logger.info(
                f"openWakeWord initialized — models: {loaded}, "
                f"threshold: {OWW_THRESHOLD}"
            )

        except Exception as e:
            logger.error(f"Failed to initialize openWakeWord: {e}")
            raise

    async def feed_audio(self, pcm_bytes: bytes) -> bool:
        """
        Feed raw PCM bytes (16-bit LE mono, 16kHz) into the wake word engine.

        Returns True if any wake word was detected in this chunk.
        Runs the blocking openWakeWord predict in a thread executor.
        """
        if not self._active or self._model is None:
            return False

        # Decode 16-bit little-endian PCM bytes into int16 samples
        num_samples = len(pcm_bytes) // 2
        samples = struct.unpack(f"<{num_samples}h", pcm_bytes[: num_samples * 2])
        self._sample_buffer.extend(samples)

        # Process complete frames (1280 samples each)
        loop = asyncio.get_event_loop()
        detected = False

        while len(self._sample_buffer) >= OWW_FRAME_LENGTH:
            # Extract one frame as a numpy array
            frame = np.array(
                [self._sample_buffer.popleft() for _ in range(OWW_FRAME_LENGTH)],
                dtype=np.int16,
            )

            # Run prediction in a thread (it's CPU-bound)
            scores = await loop.run_in_executor(
                None, self._model.predict, frame
            )

            # Check if any model exceeded the threshold
            for model_name, score in scores.items():
                if score >= OWW_THRESHOLD:
                    logger.info(
                        f"🎤 Wake word detected! model={model_name}, "
                        f"score={score:.3f} (threshold={OWW_THRESHOLD})"
                    )
                    # Reset predictions to avoid repeat triggers
                    self._model.reset()
                    detected = True
                    break

            if detected:
                break

        return detected

    def pause(self):
        """Pause detection (e.g., during active conversation)."""
        self._active = False
        self._sample_buffer.clear()
        if self._model is not None:
            self._model.reset()

    def resume(self):
        """Resume detection after conversation ends."""
        self._sample_buffer.clear()
        if self._model is not None:
            self._model.reset()
        self._active = True

    def stop(self):
        """Shut down the engine and release resources."""
        self._active = False
        self._sample_buffer.clear()
        self._model = None
        logger.info("openWakeWord engine stopped")
