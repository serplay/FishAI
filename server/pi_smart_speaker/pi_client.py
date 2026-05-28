# =============================================================================
# pi_client.py — Standalone Raspberry Pi Client (Bluetooth Billy Bass)
# =============================================================================
#
# A Command Line Application that acts as a complete Smart Speaker.
# It uses a local microphone for input, runs wake word detection locally,
# and plays ElevenLabs responses to a local output device (e.g., a Bluetooth
# speaker like Billy Bass).
#
# Usage:
#   python pi_client.py
# =============================================================================

import asyncio
import logging
import sys
import threading
from typing import Optional

import sounddevice as sd

from config import LOG_LEVEL
from wake_word import WakeWordEngine
from pi_pipeline import LocalConversationPipeline

# =============================================================================
# Logging
# =============================================================================

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s [%(name)-18s] %(levelname)-7s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("pi_client")

# Constants
MIC_SAMPLE_RATE = 16000
MIC_BLOCKSIZE = 1024


class PiSmartSpeaker:
    def __init__(self, input_idx: int, output_idx: int):
        self.input_idx = input_idx
        self.output_idx = output_idx
        self._wake_engine = WakeWordEngine()
        self._audio_queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=100)
        self._pipeline: LocalConversationPipeline | None = None
        self._pipeline_task: asyncio.Task | None = None
        self._mic_stream: sd.InputStream | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    async def run(self):
        """Main smart speaker loop."""
        self._loop = asyncio.get_event_loop()
        
        logger.info("Initializing wake word engine...")
        try:
            self._wake_engine.start()
        except Exception as e:
            logger.error(f"Failed to start wake word engine: {e}")
            return

        logger.info(f"Opening microphone stream on device {self.input_idx}...")
        try:
            self._mic_stream = sd.InputStream(
                device=self.input_idx,
                samplerate=MIC_SAMPLE_RATE,
                channels=1,
                dtype="int16",
                blocksize=MIC_BLOCKSIZE,
                callback=self._mic_callback
            )
            self._mic_stream.start()
        except Exception as e:
            logger.error(f"Failed to open microphone: {e}")
            return

        logger.info("🐟 Billy Bass is ready! Say the wake word to start.")

        try:
            while True:
                # Get audio chunks from the microphone callback queue
                pcm_bytes = await self._audio_queue.get()
                if pcm_bytes is None:
                    break

                await self._process_audio(pcm_bytes)

        except asyncio.CancelledError:
            pass
        finally:
            await self._shutdown()

    def _mic_callback(self, indata, frames, time_info, status):
        """sounddevice callback: pushes mic PCM into the async queue."""
        if status:
            logger.warning(f"Mic status: {status}")

        if self._loop and self._loop.is_running():
            try:
                self._loop.call_soon_threadsafe(
                    self._audio_queue.put_nowait, indata.tobytes()
                )
            except Exception:
                pass

    async def _process_audio(self, pcm_bytes: bytes):
        """Route incoming PCM audio to either wake word engine or pipeline."""
        if self._pipeline_task is not None and not self._pipeline_task.done():
            # Pipeline is active — forward audio to ConvAI
            if self._pipeline:
                self._pipeline.feed_audio(pcm_bytes)
        else:
            # Idle — feed audio to wake word engine
            detected = await self._wake_engine.feed_audio(pcm_bytes)
            if detected:
                await self._trigger_wake()

    async def _trigger_wake(self):
        """Start the AI conversation pipeline."""
        if self._pipeline_task is not None and not self._pipeline_task.done():
            return

        logger.info("🐟 Wake triggered — starting conversation")

        # Pause wake word detection
        self._wake_engine.pause()

        # Create and start the local pipeline
        self._pipeline = LocalConversationPipeline(output_device_idx=self.output_idx)

        self._pipeline_task = asyncio.create_task(
            self._run_pipeline_with_cleanup(),
            name="local-pipeline",
        )

    async def _run_pipeline_with_cleanup(self):
        """Run the pipeline and return to idle state when done."""
        try:
            await self._pipeline.run()
        except Exception as e:
            logger.error(f"Pipeline crashed: {e}", exc_info=True)
        finally:
            # Return to wake word listening
            self._pipeline = None
            self._wake_engine.resume()
            logger.info("Returned to wake word listening state")

    async def _shutdown(self):
        """Clean up all resources."""
        logger.info("Shutting down smart speaker...")
        if self._mic_stream:
            self._mic_stream.stop()
            self._mic_stream.close()

        if self._pipeline:
            self._pipeline.cancel()
        if self._pipeline_task and not self._pipeline_task.done():
            self._pipeline_task.cancel()

        self._wake_engine.stop()
        logger.info("Shutdown complete")


def select_device(prompt: str, is_input: bool) -> int:
    """Prompt the user to select an audio device."""
    devices = sd.query_devices()
    valid_devices = []
    
    print(f"\n{'-'*50}\n{prompt}\n{'-'*50}")
    for i, dev in enumerate(devices):
        if is_input and dev['max_input_channels'] > 0:
            valid_devices.append(i)
            print(f"[{i}] {dev['name']}")
        elif not is_input and dev['max_output_channels'] > 0:
            valid_devices.append(i)
            print(f"[{i}] {dev['name']}")

    while True:
        try:
            choice = input("\nEnter device ID: ")
            idx = int(choice)
            if idx in valid_devices:
                return idx
            else:
                print("Invalid ID. Please try again.")
        except ValueError:
            print("Please enter a number.")
        except KeyboardInterrupt:
            sys.exit(0)


async def main():
    print("=" * 60)
    print("  🐟 Billy Bass AI — Standalone Raspberry Pi Client")
    print("=" * 60)

    try:
        # Ask user for devices
        input_idx = select_device("Select Microphone (Input):", is_input=True)
        output_idx = select_device("Select Bluetooth Speaker (Output):", is_input=False)
        
        print("\nStarting...")
        speaker = PiSmartSpeaker(input_idx, output_idx)
        await speaker.run()
        
    except KeyboardInterrupt:
        print("\nExiting...")


if __name__ == "__main__":
    asyncio.run(main())
