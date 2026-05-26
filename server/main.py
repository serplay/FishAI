# =============================================================================
# main.py — Billy Bass AI WebSocket Server
# =============================================================================
#
# Entry point for the backend relay server. Hosts a WebSocket server that
# the ESP32 connects to, and orchestrates the AI pipeline:
#
#   ESP32 Mic → Wake Word → ElevenLabs ConvAI → ESP32 Speaker
#
# Each ESP32 connection is managed by a SessionManager that handles the
# state machine: IDLE → LISTENING → RESPONDING → IDLE.
# =============================================================================

import asyncio
import json
import logging
import signal
import sys

import websockets
from websockets.server import ServerConnection
from elevenlabs.client import ElevenLabs

from config import (
    ELEVENLABS_API_KEY,
    ELEVENLABS_AGENT_ID,
    LOG_LEVEL,
    SERVER_HOST,
    SERVER_PORT,
    WS_PATH,
)
from pipeline import ConversationPipeline
from wake_word import WakeWordEngine

# =============================================================================
# Logging
# =============================================================================

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s [%(name)-18s] %(levelname)-7s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("server")


# =============================================================================
# Session Manager — one per ESP32 connection
# =============================================================================

class SessionManager:
    """
    Manages the lifecycle of a single ESP32 connection.

    States:
      IDLE       — Wake word engine is listening.
      ACTIVE     — Wake detected. Audio bridges to ElevenLabs ConvAI.

    The conversation is handled inside the pipeline; from the
    SessionManager's perspective, the pipeline is either running or not.
    """

    def __init__(self, ws: ServerConnection):
        self._ws = ws
        self._wake_engine = WakeWordEngine()
        self._audio_queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=100)
        self._pipeline: ConversationPipeline | None = None
        self._pipeline_task: asyncio.Task | None = None
        self._running = True

    async def run(self):
        """
        Main session loop. Runs for the lifetime of the ESP32 connection.
        """
        logger.info("Session started — initializing wake word engine")

        try:
            self._wake_engine.start()
        except Exception as e:
            logger.error(f"Failed to start wake word engine: {e}")
            logger.info("Continuing without wake word — button press only")

        try:
            async for message in self._ws:
                if not self._running:
                    break

                if isinstance(message, bytes):
                    await self._handle_audio(message)
                elif isinstance(message, str):
                    await self._handle_text(message)

        except websockets.exceptions.ConnectionClosed:
            logger.info("ESP32 connection closed")
        except Exception as e:
            logger.error(f"Session error: {e}", exc_info=True)
        finally:
            await self._shutdown()

    # =========================================================================
    # Message Handlers
    # =========================================================================

    async def _handle_audio(self, pcm_bytes: bytes):
        """Route incoming PCM audio to either wake word engine or pipeline."""
        if self._pipeline_task is not None and not self._pipeline_task.done():
            # Pipeline is active — send audio to ConvAI
            try:
                self._audio_queue.put_nowait(pcm_bytes)
            except asyncio.QueueFull:
                # Drop oldest frame to prevent backpressure
                try:
                    self._audio_queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                self._audio_queue.put_nowait(pcm_bytes)
        else:
            # Idle — feed audio to wake word engine
            detected = await self._wake_engine.feed_audio(pcm_bytes)
            if detected:
                await self._trigger_wake()

    async def _handle_text(self, text: str):
        """Handle JSON text messages from ESP32."""
        try:
            msg = json.loads(text)
        except json.JSONDecodeError:
            logger.warning(f"Invalid JSON from ESP32: {text!r}")
            return

        msg_type = msg.get("type", "")

        if msg_type == "hello":
            device = msg.get("device", "unknown")
            logger.info(f"ESP32 handshake: device={device}")

        elif msg_type == "button_wake":
            logger.info("Button wake received from ESP32")
            await self._trigger_wake()

        elif msg_type == "cancel":
            logger.info("Cancel received from ESP32")
            await self._cancel_pipeline()

        else:
            logger.debug(f"Unknown message type: {msg_type}")

    # =========================================================================
    # Pipeline Control
    # =========================================================================

    async def _trigger_wake(self):
        """Start the AI conversation pipeline."""
        if self._pipeline_task is not None and not self._pipeline_task.done():
            logger.warning("Pipeline already running — ignoring wake")
            return

        logger.info("🐟 Wake triggered — starting conversation")

        # Pause wake word detection
        self._wake_engine.pause()

        # Send wake command to ESP32 (raises head)
        await self._send_command('{"type":"wake"}')

        # Drain any stale audio from the queue
        while not self._audio_queue.empty():
            try:
                self._audio_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

        # Create and start the pipeline
        self._pipeline = ConversationPipeline(
            audio_in_queue=self._audio_queue,
            send_audio=self._send_audio,
            send_command=self._send_command,
        )

        self._pipeline_task = asyncio.create_task(
            self._run_pipeline_with_cleanup(),
            name="pipeline",
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

    async def _cancel_pipeline(self):
        """Cancel the active pipeline (user interrupt or disconnect)."""
        if self._pipeline is not None:
            self._pipeline.cancel()
        if self._pipeline_task is not None and not self._pipeline_task.done():
            self._pipeline_task.cancel()
            try:
                await self._pipeline_task
            except (asyncio.CancelledError, Exception):
                pass

    # =========================================================================
    # ESP32 Communication
    # =========================================================================

    async def _send_audio(self, pcm_bytes: bytes):
        """Send binary PCM audio to the ESP32."""
        try:
            await self._ws.send(pcm_bytes)
        except websockets.exceptions.ConnectionClosed:
            logger.warning("Cannot send audio — ESP32 disconnected")
            raise

    async def _send_command(self, json_str: str):
        """Send a JSON text command to the ESP32."""
        try:
            await self._ws.send(json_str)
            logger.debug(f"→ ESP32: {json_str}")
        except websockets.exceptions.ConnectionClosed:
            logger.warning("Cannot send command — ESP32 disconnected")

    # =========================================================================
    # Shutdown
    # =========================================================================

    async def _shutdown(self):
        """Clean up all resources when the session ends."""
        self._running = False
        logger.info("Session shutting down...")

        # Cancel any active pipeline (saves API costs)
        await self._cancel_pipeline()

        # Signal the audio queue to stop
        await self._audio_queue.put(None)

        # Stop wake word engine
        self._wake_engine.stop()

        logger.info("Session shutdown complete")


# =============================================================================
# WebSocket Server
# =============================================================================

# Track active sessions for clean shutdown
_active_sessions: set[asyncio.Task] = set()


async def handle_connection(websocket: ServerConnection):
    """Handle a new ESP32 WebSocket connection."""
    remote = websocket.remote_address
    logger.info(f"New connection from {remote}")

    session = SessionManager(websocket)
    task = asyncio.current_task()
    _active_sessions.add(task)

    try:
        await session.run()
    finally:
        _active_sessions.discard(task)
        logger.info(f"Connection from {remote} ended")


def _configure_agent():
    """Ensure the ElevenLabs agent uses the right TTS model and output format."""
    try:
        client = ElevenLabs(api_key=ELEVENLABS_API_KEY)
        client.conversational_ai.agents.update(
            agent_id=ELEVENLABS_AGENT_ID,
            conversation_config={
                "tts": {"model_id": "eleven_v3_conversational"},
                "output_format": "pcm_44100",
            },
        )
        logger.info("Agent configured: eleven_v3_conversational, pcm_44100")
    except Exception as e:
        logger.warning(f"Could not configure agent: {e}")
        logger.warning("Set TTS model and output format in the ElevenLabs dashboard")


async def main():
    """Start the WebSocket server."""
    # Configure agent TTS model on startup
    _configure_agent()

    logger.info("=" * 60)
    logger.info("  🐟 Billy Bass AI Server")
    logger.info(f"  Listening on ws://{SERVER_HOST}:{SERVER_PORT}{WS_PATH}")
    logger.info("=" * 60)

    # Graceful shutdown handler
    stop = asyncio.get_event_loop().create_future()

    def signal_handler():
        if not stop.done():
            stop.set_result(None)
            logger.info("Shutdown signal received")

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, signal_handler)
        except NotImplementedError:
            # Windows doesn't support add_signal_handler
            pass

    async with websockets.serve(
        handle_connection,
        SERVER_HOST,
        SERVER_PORT,
        # Match the ESP32 WebSocket path
        # The websockets library routes all paths to the handler by default
        ping_interval=20,
        ping_timeout=10,
        max_size=2**20,        # 1MB max message (PCM chunks are small)
        close_timeout=5,
    ) as server:
        logger.info("Server is ready — waiting for ESP32 connections...")

        try:
            await stop
        except asyncio.CancelledError:
            pass

        logger.info("Shutting down server...")

        # Cancel all active sessions
        for task in _active_sessions:
            task.cancel()

        if _active_sessions:
            await asyncio.gather(*_active_sessions, return_exceptions=True)

    logger.info("Server stopped")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
