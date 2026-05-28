import asyncio
import logging
import queue
import threading
import sounddevice as sd

from elevenlabs.client import ElevenLabs
from elevenlabs.conversational_ai.conversation import Conversation, AudioInterface

from config import ELEVENLABS_API_KEY, ELEVENLABS_AGENT_ID

logger = logging.getLogger("pi_pipeline")

# =============================================================================
# Local Audio Interface — bridges Raspberry Pi Audio ↔ ElevenLabs SDK
# =============================================================================

class LocalAudioInterface(AudioInterface):
    """
    AudioInterface that plays TTS audio directly to a specified sounddevice
    (like a Bluetooth speaker) and accepts mic chunks via feed_audio().
    """

    def __init__(self, output_device_idx: int):
        self._output_device_idx = output_device_idx
        self._input_queue: queue.Queue[bytes | None] = queue.Queue()
        self._input_callback = None
        self._running = False
        self._input_thread: threading.Thread | None = None

        # Playback buffer for TTS audio
        self._playback_buffer = bytearray()
        self._playback_lock = threading.Lock()
        self._playback_stream: sd.RawOutputStream | None = None

    def start(self, input_callback):
        """Called by the SDK to start audio capture."""
        self._input_callback = input_callback
        self._running = True

        # Background thread to feed mic data to the SDK
        self._input_thread = threading.Thread(
            target=self._input_loop, daemon=True, name="local-audio-in"
        )
        self._input_thread.start()

        # Start sounddevice output stream (ElevenLabs pcm_44100 format)
        try:
            self._playback_stream = sd.RawOutputStream(
                device=self._output_device_idx,
                samplerate=44100,
                channels=1,
                dtype="int16",
                callback=self._audio_out_callback
            )
            self._playback_stream.start()
            logger.info(f"Playback stream started on device {self._output_device_idx} at 44.1kHz")
        except Exception as e:
            logger.error(f"Failed to start playback stream: {e}")

    def stop(self):
        """Called by the SDK to stop audio capture."""
        self._running = False
        self._input_queue.put(None)  # Unblock the reader thread
        
        if self._input_thread is not None:
            self._input_thread.join(timeout=2)
            self._input_thread = None

        if self._playback_stream is not None:
            self._playback_stream.stop()
            self._playback_stream.close()
            self._playback_stream = None

        # Clear playback buffer
        with self._playback_lock:
            self._playback_buffer.clear()

        logger.info("Local audio interface stopped")

    def output(self, audio: bytes):
        """Called by the SDK when agent TTS audio is ready."""
        with self._playback_lock:
            self._playback_buffer.extend(audio)

    def interrupt(self):
        """Called by the SDK when the user interrupts the agent mid-speech."""
        logger.info("User interrupted the agent — flushing playback buffer")
        with self._playback_lock:
            self._playback_buffer.clear()

    # -- Public API --

    def feed_audio(self, pcm_bytes: bytes):
        """
        Feed local microphone PCM (16kHz) into the SDK.
        Called from the main capture loop.
        """
        try:
            self._input_queue.put_nowait(pcm_bytes)
        except queue.Full:
            pass  # Drop frame if backed up

    # -- Internal --

    def _input_loop(self):
        """Background thread: drains the queue and feeds the SDK."""
        first_chunk_logged = False
        while self._running:
            try:
                chunk = self._input_queue.get(timeout=0.1)
            except queue.Empty:
                continue
            
            if chunk is None:
                break
                
            if self._input_callback is not None:
                if not first_chunk_logged:
                    logger.info(f"✓ First mic chunk fed to SDK ({len(chunk)} bytes)")
                    first_chunk_logged = True
                self._input_callback(chunk)

    def _audio_out_callback(self, outdata, frames, time_info, status):
        """sounddevice callback pulling from the TTS playback buffer."""
        if status:
            logger.warning(f"Playback status: {status}")
            
        bytes_needed = frames * 2  # 16-bit mono
        
        with self._playback_lock:
            available = len(self._playback_buffer)
            if available >= bytes_needed:
                chunk = bytes(self._playback_buffer[:bytes_needed])
                del self._playback_buffer[:bytes_needed]
            else:
                chunk = bytes(self._playback_buffer) + (b"\x00" * (bytes_needed - available))
                self._playback_buffer.clear()
        
        outdata[:] = chunk


# =============================================================================
# Pipeline Runner
# =============================================================================

class LocalConversationPipeline:
    """
    Runs a single conversation between the local microphone and ElevenLabs.
    """

    def __init__(self, output_device_idx: int):
        self._output_device_idx = output_device_idx
        self._cancelled = False
        self._conversation: Conversation | None = None
        self._audio_iface: LocalAudioInterface | None = None

    def feed_audio(self, pcm_bytes: bytes):
        """Forward mic audio to the interface if active."""
        if self._audio_iface:
            self._audio_iface.feed_audio(pcm_bytes)

    async def run(self):
        """Execute the conversation pipeline."""
        logger.info("━━━ Local Conversation Pipeline Started ━━━")

        self._audio_iface = LocalAudioInterface(self._output_device_idx)
        client = ElevenLabs(api_key=ELEVENLABS_API_KEY)

        self._conversation = Conversation(
            client,
            ELEVENLABS_AGENT_ID,
            requires_auth=bool(ELEVENLABS_API_KEY),
            audio_interface=self._audio_iface,
            callback_agent_response=lambda r: logger.info(f"Agent: {r}"),
            callback_user_transcript=lambda t: logger.info(f"User: {t}"),
        )

        loop = asyncio.get_event_loop()
        try:
            self._conversation.start_session()
            logger.info("ElevenLabs ConvAI session started")

            # Wait for SDK session to end (blocking -> executor)
            conv_id = await loop.run_in_executor(
                None, self._conversation.wait_for_session_end
            )
            logger.info(f"Conversation ended gracefully. ID: {conv_id}")

        except asyncio.CancelledError:
            logger.info("Pipeline cancelled by user/system")
            if self._conversation:
                self._conversation.end_session()
        except Exception as e:
            logger.error(f"Pipeline error: {e}", exc_info=True)
        finally:
            self._conversation = None
            self._audio_iface = None
            logger.info("━━━ Local Conversation Pipeline Ended ━━━")

    def cancel(self):
        """Signal cancellation (e.g., Ctrl+C)."""
        self._cancelled = True
        if self._conversation is not None:
            self._conversation.end_session()
