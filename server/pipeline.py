# =============================================================================
# pipeline.py — ElevenLabs Conversational AI Pipeline (SDK)
# =============================================================================
#
# Uses the official ElevenLabs Python SDK to run a Conversation with a
# custom AudioInterface that bridges ESP32 WebSocket audio to the SDK.
#
# The SDK handles everything internally: ASR, LLM, TTS, turn-taking,
# authentication, ping/pong, and audio encoding/decoding.
#
# Data flow:
#   ESP32 Mic PCM → feed_audio() → SDK → ElevenLabs ConvAI
#   ElevenLabs TTS → output()    → send_audio() → ESP32 Speaker
#
# The agent's personality, voice, model, and system prompt are all
# configured in the ElevenLabs dashboard — not in this code.
# =============================================================================

import asyncio
import logging
import queue
import threading
import websockets
from typing import Callable, Awaitable

from elevenlabs.client import ElevenLabs
from elevenlabs.conversational_ai.conversation import Conversation, AudioInterface

from config import ELEVENLABS_API_KEY, ELEVENLABS_AGENT_ID

logger = logging.getLogger(__name__)

# Type aliases
SendToESP32 = Callable[[bytes], Awaitable[None]]        # Binary PCM
SendCommandToESP32 = Callable[[str], Awaitable[None]]    # JSON text


# =============================================================================
# Custom Audio Interface — bridges ESP32 ↔ ElevenLabs SDK
# =============================================================================

class ESP32AudioInterface(AudioInterface):
    """
    AudioInterface implementation that bridges the ESP32's WebSocket
    audio stream to the ElevenLabs Conversation SDK.

    Input path:  ESP32 mic PCM → feed_audio() → thread queue → SDK
    Output path: SDK TTS PCM   → output()     → ESP32 speaker WebSocket
    """

    def __init__(
        self,
        event_loop: asyncio.AbstractEventLoop,
        send_audio: SendToESP32,
        send_command: SendCommandToESP32,
    ):
        self._loop = event_loop
        self._send_audio = send_audio
        self._send_command = send_command
        self._input_queue: queue.Queue[bytes | None] = queue.Queue()
        self._input_callback = None
        self._running = False
        self._input_thread: threading.Thread | None = None

        # Diagnostics counters
        self._chunks_fed = 0         # Chunks fed to SDK from ESP32 mic
        self._chunks_output = 0      # Chunks received from SDK (TTS)
        self._bytes_in = 0
        self._bytes_out = 0
        self._diag_timer: threading.Timer | None = None
        
        self._output_queue: asyncio.Queue[bytes] = asyncio.Queue()
        self._output_task: asyncio.Task | None = None

    def start(self, input_callback):
        """Called by the SDK to start audio capture."""
        self._input_callback = input_callback
        self._running = True
        self._input_thread = threading.Thread(
            target=self._input_loop, daemon=True, name="esp32-audio-in"
        )
        self._input_thread.start()
        
        # Start async output pump to pace audio to the ESP32
        self._output_task = self._loop.create_task(self._output_pump())
        
        self._start_diagnostics()
        logger.info("ESP32 audio interface started — waiting for mic data")

    def stop(self):
        """Called by the SDK to stop audio capture."""
        self._running = False
        self._stop_diagnostics()
        self._input_queue.put(None)  # Unblock the reader thread
        if self._input_thread is not None:
            self._input_thread.join(timeout=2)
            self._input_thread = None
            
        if self._output_task is not None:
            self._output_task.cancel()
            self._output_task = None
        logger.info(
            f"ESP32 audio interface stopped — "
            f"mic→SDK: {self._chunks_fed} chunks ({self._bytes_in:,}B), "
            f"SDK→ESP: {self._chunks_output} chunks ({self._bytes_out:,}B)"
        )

    # Max bytes per WebSocket frame sent to ESP32.
    # The ESP32 WebSocketsClient must allocate a buffer for each frame.
    # With ~100KB free heap, we keep frames small to avoid OOM disconnects.
    MAX_WS_FRAME = 2048  # ~23ms at 44.1kHz 16-bit mono

    def output(self, audio: bytes):
        """
        Called by the SDK (from its thread) when agent TTS audio is ready.
        The SDK may deliver large blobs (50KB+). We chunk them and enqueue
        them for the async pump to send at real-time speed.
        """
        self._bytes_out += len(audio)

        # Split into ESP32-friendly chunks and queue them
        offset = 0
        while offset < len(audio):
            chunk = audio[offset : offset + self.MAX_WS_FRAME]
            offset += len(chunk)
            self._chunks_output += 1
            self._loop.call_soon_threadsafe(self._output_queue.put_nowait, chunk)

    def interrupt(self):
        """Called by the SDK when the user interrupts the agent mid-speech."""
        logger.info("User interrupted the agent")
        # Clear any queued audio that hasn't been sent yet
        self._loop.call_soon_threadsafe(self._clear_output_queue)
        
        # Tell ESP32/mock client to flush its playback buffer immediately
        try:
            asyncio.run_coroutine_threadsafe(
                self._send_command('{"type":"interrupt"}'), self._loop
            )
        except Exception as e:
            logger.warning(f"Failed to send interrupt to ESP32: {e}")

    def _clear_output_queue(self):
        """Drain the output queue (must be called from event loop thread)."""
        while not self._output_queue.empty():
            try:
                self._output_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

    # -- Public API for the pipeline --

    def feed_audio(self, pcm_bytes: bytes):
        """
        Feed ESP32 microphone PCM into the SDK.
        Called from the async pump task — safe to use from any thread.
        """
        self._bytes_in += len(pcm_bytes)
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
                self._chunks_fed += 1
                if not first_chunk_logged:
                    logger.info(f"✓ First mic chunk fed to SDK ({len(chunk)} bytes)")
                    first_chunk_logged = True
                self._input_callback(chunk)

    async def _output_pump(self):
        """
        Async task that reads from _output_queue and sends to ESP32.
        Paces the sending to match real-time playback (44.1kHz 16-bit mono)
        so the ESP32's tiny 4KB playback buffer and TCP window don't overflow.
        """
        # 44.1kHz 16-bit mono = 88,200 bytes per second
        BYTES_PER_SEC = 44100 * 2
        
        try:
            while self._running:
                chunk = await self._output_queue.get()
                
                # Send the chunk
                try:
                    await self._send_audio(chunk)
                except websockets.exceptions.ConnectionClosed:
                    break
                except Exception as e:
                    logger.warning(f"Failed to send audio to ESP32: {e}")
                    break
                    
                # Sleep exactly the duration of the audio we just sent
                duration = len(chunk) / BYTES_PER_SEC
                
                # Sleep slightly less (80%) to ensure we keep the ESP32 buffer full,
                # but not so fast that we overwhelm its TCP/WebSocket buffers.
                await asyncio.sleep(duration * 0.80)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Output pump crashed: {e}")

    def _start_diagnostics(self):
        """Log audio flow stats every 5 seconds."""
        def _log_stats():
            if not self._running:
                return
            logger.info(
                f"📊 Audio: mic→SDK {self._chunks_fed} chunks ({self._bytes_in:,}B) | "
                f"SDK→ESP {self._chunks_output} chunks ({self._bytes_out:,}B) | "
                f"queue: {self._input_queue.qsize()}"
            )
            self._diag_timer = threading.Timer(5.0, _log_stats)
            self._diag_timer.daemon = True
            self._diag_timer.start()

        self._diag_timer = threading.Timer(5.0, _log_stats)
        self._diag_timer.daemon = True
        self._diag_timer.start()

    def _stop_diagnostics(self):
        if self._diag_timer is not None:
            self._diag_timer.cancel()
            self._diag_timer = None


# =============================================================================
# Conversation Pipeline
# =============================================================================

class ConversationPipeline:
    """
    Runs a single conversation between the ESP32 and ElevenLabs ConvAI.

    Lifecycle:
      1. Called when wake word or button press is detected
      2. Creates an ElevenLabs Conversation with a custom AudioInterface
      3. Pumps ESP32 mic audio into the SDK while the session is active
      4. SDK sends TTS audio back through the AudioInterface to the ESP32
      5. Ends when the SDK closes the session or cancellation is requested
      6. Sends {"type":"done"} to ESP32 on completion
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
        self._conversation: Conversation | None = None

    async def run(self):
        """
        Execute the conversation pipeline.
        Blocks until the conversation is complete.
        """
        logger.info("━━━ Conversation pipeline started ━━━")

        loop = asyncio.get_event_loop()
        audio_iface = ESP32AudioInterface(loop, self._send_audio, self._send_command)

        client = ElevenLabs(api_key=ELEVENLABS_API_KEY)

        self._conversation = Conversation(
            client,
            ELEVENLABS_AGENT_ID,
            requires_auth=bool(ELEVENLABS_API_KEY),
            audio_interface=audio_iface,
            callback_agent_response=lambda r: logger.info(f"Agent: {r}"),
            callback_agent_response_correction=lambda orig, corrected: (
                logger.info(f"Agent (corrected): {corrected}")
            ),
            callback_user_transcript=lambda t: logger.info(f"User: {t}"),
            callback_latency_measurement=lambda latency: (
                logger.debug(f"Latency: {latency}ms")
            ),
        )

        try:
            # Start the SDK session (spawns internal threads)
            self._conversation.start_session()
            logger.info("ElevenLabs ConvAI session started")

            # Run two tasks concurrently:
            #   1. Pump ESP32 mic audio → AudioInterface (async)
            #   2. Wait for SDK session to end (blocking → executor)
            pump_task = asyncio.create_task(
                self._pump_audio(audio_iface), name="audio-pump"
            )

            async def _await_session_end():
                return await loop.run_in_executor(
                    None, self._conversation.wait_for_session_end
                )

            wait_task = asyncio.create_task(
                _await_session_end(), name="session-wait"
            )

            done, pending = await asyncio.wait(
                [pump_task, wait_task],
                return_when=asyncio.FIRST_COMPLETED,
            )

            # Cancel whichever is still running
            for task in pending:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

            # Log the conversation ID
            if wait_task in done:
                # Session ended naturally
                try:
                    conv_id = wait_task.result()
                    logger.info(f"Conversation ID: {conv_id}")
                except Exception:
                    pass
            else:
                # Audio pump ended first (ESP32 disconnected / cancelled)
                self._conversation.end_session()
                try:
                    conv_id = await loop.run_in_executor(
                        None, self._conversation.wait_for_session_end
                    )
                    logger.info(f"Conversation ID: {conv_id}")
                except Exception:
                    pass

        except asyncio.CancelledError:
            logger.info("Pipeline cancelled")
            if self._conversation is not None:
                self._conversation.end_session()
        except Exception as e:
            logger.error(f"Pipeline error: {e}", exc_info=True)
        finally:
            self._conversation = None

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
        if self._conversation is not None:
            self._conversation.end_session()

    async def _pump_audio(self, audio_iface: ESP32AudioInterface):
        """
        Continuously drain the asyncio audio queue and feed chunks
        into the ESP32AudioInterface's thread-safe input queue.
        """
        while not self._cancelled:
            try:
                pcm_bytes = await asyncio.wait_for(
                    self._audio_in.get(), timeout=0.1
                )
            except asyncio.TimeoutError:
                continue

            if pcm_bytes is None:
                # Sentinel: ESP32 disconnected
                logger.info("Audio stream ended (sentinel)")
                break

            audio_iface.feed_audio(pcm_bytes)
