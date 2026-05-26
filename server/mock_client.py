# =============================================================================
# mock_client.py — Billy Bass AI Mock ESP32 Client
# =============================================================================
#
# A desktop GUI that simulates the ESP32 hardware for end-to-end testing
# of the AI relay server. Streams 16kHz PCM from a local microphone to
# the server (triggering wake word + Gemini + ElevenLabs), and plays
# back the TTS response through local speakers.
#
# Usage:
#   python mock_client.py
#
# This lets you verify the entire AI pipeline without touching the fish.
# =============================================================================

import asyncio
import json
import logging
import struct
import threading
import time as _time
import tkinter as tk
from datetime import datetime
from tkinter import ttk
from typing import Optional

import numpy as np
import sounddevice as sd
import websockets

# =============================================================================
# Logging
# =============================================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)-7s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("mock_client")

# Audio format — must match ESP32 / server config
MIC_SAMPLE_RATE = 16000    # Mic input: 16kHz for wake word + ConvAI
SPK_SAMPLE_RATE = 44100    # Speaker output: 44.1kHz for HQ TTS playback
CHANNELS = 1
DTYPE = "int16"
MIC_BLOCKSIZE = 512        # 512 samples @ 16kHz = 32ms
SPK_BLOCKSIZE = 1024       # 1024 samples @ 44.1kHz = ~23ms

# Noise gate — mic RMS below this threshold sends silence instead of noise.
# Prevents ElevenLabs VAD from triggering on background noise.
# Raise this if you still get false interruptions; lower if your voice is gated.
MIC_NOISE_GATE = 300       # RMS threshold (int16 scale, ~0.01 of max 32767)


# =============================================================================
# Dark Theme
# =============================================================================

COLORS = {
    "bg":           "#1e1e2e",
    "surface":      "#282840",
    "surface_alt":  "#313150",
    "border":       "#45455a",
    "text":         "#cdd6f4",
    "text_dim":     "#6c7086",
    "accent":       "#89b4fa",
    "green":        "#a6e3a1",
    "red":          "#f38ba8",
    "yellow":       "#f9e2af",
    "peach":        "#fab387",
    "log_bg":       "#11111b",
    "log_server":   "#89b4fa",
    "log_client":   "#a6e3a1",
    "log_error":    "#f38ba8",
    "log_info":     "#6c7086",
    "meter_bg":     "#313150",
    "meter_mic":    "#a6e3a1",
    "meter_spk":    "#89b4fa",
}


def apply_theme(root: tk.Tk):
    """Apply a dark Catppuccin-inspired theme to the ttk widgets."""
    root.configure(bg=COLORS["bg"])

    style = ttk.Style()
    style.theme_use("clam")

    # General
    style.configure(".", background=COLORS["bg"], foreground=COLORS["text"],
                    fieldbackground=COLORS["surface"], borderwidth=0,
                    font=("Segoe UI", 10))

    # Frames
    style.configure("TFrame", background=COLORS["bg"])
    style.configure("TLabelframe", background=COLORS["bg"],
                    foreground=COLORS["text_dim"], bordercolor=COLORS["border"],
                    relief="groove")
    style.configure("TLabelframe.Label", background=COLORS["bg"],
                    foreground=COLORS["text_dim"], font=("Segoe UI", 9, "bold"))

    # Labels
    style.configure("TLabel", background=COLORS["bg"], foreground=COLORS["text"])
    style.configure("Status.TLabel", font=("Segoe UI", 10, "bold"))
    style.configure("Header.TLabel", font=("Segoe UI", 14, "bold"),
                    foreground=COLORS["accent"])

    # Entry
    style.configure("TEntry", fieldbackground=COLORS["surface"],
                    foreground=COLORS["text"], insertcolor=COLORS["text"])
    style.map("TEntry",
              fieldbackground=[("focus", COLORS["surface_alt"])],
              bordercolor=[("focus", COLORS["accent"])])

    # Combobox
    style.configure("TCombobox", fieldbackground=COLORS["surface"],
                    foreground=COLORS["text"], arrowcolor=COLORS["text_dim"],
                    selectbackground=COLORS["surface_alt"],
                    selectforeground=COLORS["text"])
    style.map("TCombobox",
              fieldbackground=[("readonly", COLORS["surface"])],
              selectbackground=[("readonly", COLORS["surface"])],
              selectforeground=[("readonly", COLORS["text"])])

    # Buttons
    style.configure("TButton", background=COLORS["surface_alt"],
                    foreground=COLORS["text"], padding=(12, 6),
                    font=("Segoe UI", 10))
    style.map("TButton",
              background=[("active", COLORS["border"]),
                          ("disabled", COLORS["surface"])],
              foreground=[("disabled", COLORS["text_dim"])])

    style.configure("Accent.TButton", background=COLORS["accent"],
                    foreground=COLORS["bg"], font=("Segoe UI", 10, "bold"))
    style.map("Accent.TButton",
              background=[("active", "#7aa2e8"), ("disabled", COLORS["surface"])],
              foreground=[("disabled", COLORS["text_dim"])])

    style.configure("Danger.TButton", background=COLORS["red"],
                    foreground=COLORS["bg"], font=("Segoe UI", 10, "bold"))
    style.map("Danger.TButton",
              background=[("active", "#e07090"), ("disabled", COLORS["surface"])],
              foreground=[("disabled", COLORS["text_dim"])])


# =============================================================================
# Audio Level Meter (canvas-based)
# =============================================================================

class LevelMeter(tk.Canvas):
    """A simple horizontal audio level meter drawn on a Canvas."""

    def __init__(self, parent, color: str, width: int = 200, height: int = 12, **kw):
        super().__init__(parent, width=width, height=height,
                         bg=COLORS["meter_bg"], highlightthickness=0, **kw)
        self._color = color
        self._width = width
        self._height = height
        self._level = 0.0  # 0.0 – 1.0
        self._bar = self.create_rectangle(0, 0, 0, height, fill=color, outline="")

    def set_level(self, level: float):
        """Set the meter level (0.0 – 1.0), thread-safe via after()."""
        self._level = max(0.0, min(1.0, level))
        self.after_idle(self._draw)

    def _draw(self):
        w = int(self._level * self._width)
        self.coords(self._bar, 0, 0, w, self._height)


# =============================================================================
# Main GUI
# =============================================================================

class MockClientApp:
    """Desktop mock client that simulates the ESP32 for server testing."""

    WINDOW_TITLE = "Billy Bass AI — Mock Client"
    WINDOW_SIZE = "680x720"

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(self.WINDOW_TITLE)
        self.root.geometry(self.WINDOW_SIZE)
        self.root.minsize(580, 600)

        apply_theme(root)

        # --- State ---
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.ws: Optional[websockets.WebSocketClientProtocol] = None
        self.connected = False
        self.mic_stream: Optional[sd.InputStream] = None
        self.spk_stream: Optional[sd.OutputStream] = None
        self.playback_buffer = bytearray()
        self.buffer_lock = threading.Lock()

        # Audio level tracking (written from audio callbacks, read from UI)
        self._mic_rms = 0.0
        self._spk_rms = 0.0

        # Stats
        self._bytes_sent = 0
        self._bytes_received = 0
        self._connect_time: Optional[float] = None

        self._build_ui()
        self._load_audio_devices()
        self._poll_meters()

    # =========================================================================
    # UI Construction
    # =========================================================================

    def _build_ui(self):
        pad = {"padx": 8, "pady": 4}

        # --- Header ---
        hdr = ttk.Frame(self.root)
        hdr.pack(fill=tk.X, padx=12, pady=(12, 4))
        ttk.Label(hdr, text="Billy Bass AI — Mock Client",
                  style="Header.TLabel").pack(side=tk.LEFT)

        # --- Connection ---
        conn = ttk.LabelFrame(self.root, text="CONNECTION", padding=8)
        conn.pack(fill=tk.X, padx=12, pady=4)

        row0 = ttk.Frame(conn)
        row0.pack(fill=tk.X, pady=2)
        ttk.Label(row0, text="Server URL").pack(side=tk.LEFT, **pad)
        self.url_var = tk.StringVar(value="ws://localhost:8765/ws")
        url_entry = ttk.Entry(row0, textvariable=self.url_var, width=36)
        url_entry.pack(side=tk.LEFT, **pad)
        self.btn_connect = ttk.Button(row0, text="Connect",
                                      style="Accent.TButton",
                                      command=self._toggle_connection)
        self.btn_connect.pack(side=tk.LEFT, **pad)
        self.lbl_status = ttk.Label(row0, text="  OFFLINE",
                                    style="Status.TLabel",
                                    foreground=COLORS["red"])
        self.lbl_status.pack(side=tk.LEFT, **pad)

        # --- Audio Devices ---
        audio = ttk.LabelFrame(self.root, text="AUDIO DEVICES", padding=8)
        audio.pack(fill=tk.X, padx=12, pady=4)

        # Mic row
        mic_row = ttk.Frame(audio)
        mic_row.pack(fill=tk.X, pady=2)
        ttk.Label(mic_row, text="Input ").pack(side=tk.LEFT, **pad)
        self.mic_combo = ttk.Combobox(mic_row, state="readonly", width=44)
        self.mic_combo.pack(side=tk.LEFT, **pad)
        self.mic_meter = LevelMeter(mic_row, COLORS["meter_mic"], width=120, height=14)
        self.mic_meter.pack(side=tk.LEFT, padx=8)

        # Speaker row
        spk_row = ttk.Frame(audio)
        spk_row.pack(fill=tk.X, pady=2)
        ttk.Label(spk_row, text="Output").pack(side=tk.LEFT, **pad)
        self.spk_combo = ttk.Combobox(spk_row, state="readonly", width=44)
        self.spk_combo.pack(side=tk.LEFT, **pad)
        self.spk_meter = LevelMeter(spk_row, COLORS["meter_spk"], width=120, height=14)
        self.spk_meter.pack(side=tk.LEFT, padx=8)

        # Refresh button
        btn_row = ttk.Frame(audio)
        btn_row.pack(fill=tk.X, pady=2)
        ttk.Button(btn_row, text="Refresh Devices",
                   command=self._load_audio_devices).pack(side=tk.RIGHT, **pad)

        # --- Controls ---
        ctrl = ttk.LabelFrame(self.root, text="CONTROLS", padding=8)
        ctrl.pack(fill=tk.X, padx=12, pady=4)

        ctrl_row = ttk.Frame(ctrl)
        ctrl_row.pack(fill=tk.X)
        self.btn_wake = ttk.Button(ctrl_row, text="Simulate Wake Button",
                                   state=tk.DISABLED,
                                   command=self._send_button_wake)
        self.btn_wake.pack(side=tk.LEFT, **pad)
        self.btn_cancel = ttk.Button(ctrl_row, text="Cancel / Interrupt",
                                     style="Danger.TButton",
                                     state=tk.DISABLED,
                                     command=self._send_cancel)
        self.btn_cancel.pack(side=tk.LEFT, **pad)

        # Stats label
        self.lbl_stats = ttk.Label(ctrl_row, text="", foreground=COLORS["text_dim"])
        self.lbl_stats.pack(side=tk.RIGHT, **pad)

        # --- Log ---
        log_frame = ttk.LabelFrame(self.root, text="EVENT LOG", padding=8)
        log_frame.pack(fill=tk.BOTH, expand=True, padx=12, pady=(4, 12))

        # Use a Text widget with monospace font and dark background
        self.txt_log = tk.Text(
            log_frame, wrap=tk.WORD, font=("Consolas", 9),
            bg=COLORS["log_bg"], fg=COLORS["text"],
            insertbackground=COLORS["text"],
            selectbackground=COLORS["surface_alt"],
            borderwidth=0, highlightthickness=0,
            state=tk.DISABLED,
        )
        scrollbar = ttk.Scrollbar(log_frame, orient=tk.VERTICAL,
                                  command=self.txt_log.yview)
        self.txt_log.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.txt_log.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Configure log tags for colored text
        self.txt_log.tag_configure("ts", foreground=COLORS["text_dim"])
        self.txt_log.tag_configure("server", foreground=COLORS["log_server"])
        self.txt_log.tag_configure("client", foreground=COLORS["log_client"])
        self.txt_log.tag_configure("error", foreground=COLORS["log_error"])
        self.txt_log.tag_configure("info", foreground=COLORS["text"])
        self.txt_log.tag_configure("dim", foreground=COLORS["text_dim"])

    # =========================================================================
    # Audio Device Management
    # =========================================================================

    def _load_audio_devices(self):
        try:
            devices = sd.query_devices()
            inputs, outputs = [], []
            for i, dev in enumerate(devices):
                label = f"[{i}] {dev['name']}"
                if dev["max_input_channels"] > 0:
                    inputs.append(label)
                if dev["max_output_channels"] > 0:
                    outputs.append(label)

            self.mic_combo["values"] = inputs
            if inputs:
                self.mic_combo.current(0)
            self.spk_combo["values"] = outputs
            if outputs:
                self.spk_combo.current(0)

            self._log("Audio devices refreshed", tag="dim")
        except Exception as e:
            self._log(f"Error loading audio devices: {e}", tag="error")

    # =========================================================================
    # Logging
    # =========================================================================

    def _log(self, message: str, tag: str = "info"):
        """Append a timestamped, color-coded line to the log widget."""
        logger.info(message)
        self.root.after(0, self._append_log, message, tag)

    def _append_log(self, message: str, tag: str):
        ts = datetime.now().strftime("%H:%M:%S")
        self.txt_log.configure(state=tk.NORMAL)
        self.txt_log.insert(tk.END, f"[{ts}] ", "ts")
        self.txt_log.insert(tk.END, f"{message}\n", tag)
        self.txt_log.see(tk.END)
        self.txt_log.configure(state=tk.DISABLED)

    # =========================================================================
    # Connection
    # =========================================================================

    def _toggle_connection(self):
        if self.connected:
            self._disconnect()
        else:
            self._connect()

    def _connect(self):
        mic_sel = self.mic_combo.get()
        spk_sel = self.spk_combo.get()
        if not mic_sel or not spk_sel:
            self._log("Select both input and output devices first", tag="error")
            return

        self.mic_idx = int(mic_sel.split("]")[0][1:])
        self.spk_idx = int(spk_sel.split("]")[0][1:])

        self.btn_connect.config(text="Connecting...", state=tk.DISABLED)
        self.mic_combo.config(state=tk.DISABLED)
        self.spk_combo.config(state=tk.DISABLED)

        self._bytes_sent = 0
        self._bytes_received = 0

        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self._run_async_loop, daemon=True).start()

    def _run_async_loop(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self._websocket_handler())

    async def _websocket_handler(self):
        url = self.url_var.get()
        try:
            self._log(f"Connecting to {url} ...", tag="dim")
            async with websockets.connect(
                url, ping_interval=20, ping_timeout=10, max_size=2**20
            ) as ws:
                self.ws = ws
                self._connect_time = _time.monotonic()

                # Handshake — same JSON the real ESP32 sends
                hello = json.dumps({"type": "hello", "device": "MockClient-Desktop"})
                await ws.send(hello)

                self._start_audio_streams()
                self.root.after(0, self._set_ui_connected)
                self._log("Connected — streaming microphone to server", tag="client")

                # Receive loop
                async for msg in ws:
                    if isinstance(msg, bytes):
                        self._bytes_received += len(msg)
                        with self.buffer_lock:
                            self.playback_buffer.extend(msg)
                    else:
                        # Parse JSON commands from server
                        try:
                            data = json.loads(msg)
                            msg_type = data.get("type", "")
                            if msg_type == "interrupt":
                                # Agent was interrupted — flush stale audio
                                with self.buffer_lock:
                                    self.playback_buffer.clear()
                                self._log("⚡ Interrupt — playback buffer cleared", tag="server")
                            else:
                                self._log(f"Server: {msg}", tag="server")
                        except json.JSONDecodeError:
                            self._log(f"Server: {msg}", tag="server")

        except websockets.exceptions.ConnectionClosedOK:
            self._log("Connection closed cleanly", tag="dim")
        except websockets.exceptions.ConnectionClosedError as e:
            self._log(f"Connection lost: {e}", tag="error")
        except ConnectionRefusedError:
            self._log(f"Connection refused — is the server running at {url}?", tag="error")
        except Exception as e:
            self._log(f"Connection error: {e}", tag="error")
        finally:
            self._stop_audio_streams()
            self.root.after(0, self._set_ui_disconnected)

    def _disconnect(self):
        self._log("Disconnecting...", tag="dim")
        if self.loop and self.loop.is_running() and self.ws:
            asyncio.run_coroutine_threadsafe(self.ws.close(), self.loop)

    def _set_ui_connected(self):
        self.connected = True
        self.btn_connect.config(text="Disconnect", state=tk.NORMAL,
                                style="Danger.TButton")
        self.lbl_status.config(text="  ONLINE", foreground=COLORS["green"])
        self.btn_wake.config(state=tk.NORMAL)
        self.btn_cancel.config(state=tk.NORMAL)

    def _set_ui_disconnected(self):
        self.connected = False
        self._connect_time = None
        self.btn_connect.config(text="Connect", state=tk.NORMAL,
                                style="Accent.TButton")
        self.mic_combo.config(state="readonly")
        self.spk_combo.config(state="readonly")
        self.lbl_status.config(text="  OFFLINE", foreground=COLORS["red"])
        self.btn_wake.config(state=tk.DISABLED)
        self.btn_cancel.config(state=tk.DISABLED)
        self.lbl_stats.config(text="")
        self.mic_meter.set_level(0)
        self.spk_meter.set_level(0)
        self._log("Disconnected", tag="dim")

    # =========================================================================
    # Controls
    # =========================================================================

    def _send_button_wake(self):
        if self.ws and self.loop:
            msg = json.dumps({"type": "button_wake"})
            self._log(f"Sent: {msg}", tag="client")
            asyncio.run_coroutine_threadsafe(self.ws.send(msg), self.loop)

    def _send_cancel(self):
        if self.ws and self.loop:
            msg = json.dumps({"type": "cancel"})
            self._log(f"Sent: {msg}", tag="client")
            asyncio.run_coroutine_threadsafe(self.ws.send(msg), self.loop)
            with self.buffer_lock:
                self.playback_buffer.clear()

    # =========================================================================
    # Audio I/O
    # =========================================================================

    def _audio_input_callback(self, indata, frames, time_info, status):
        """Sounddevice callback: sends mic PCM to the server."""
        if status:
            logger.warning(f"Mic: {status}")
        if not (self.connected and self.ws and self.loop):
            return

        # Compute RMS for the level meter and noise gate
        samples = indata[:, 0].astype(np.float32)
        rms = np.sqrt(np.mean(samples * samples))
        self._mic_rms = min(1.0, rms / 8000.0)

        # Noise gate: send silence if below threshold to avoid false interrupts
        if rms < MIC_NOISE_GATE:
            pcm_bytes = b"\x00" * (frames * 2)  # 16-bit silence
        else:
            pcm_bytes = indata.tobytes()

        self._bytes_sent += len(pcm_bytes)

        try:
            asyncio.run_coroutine_threadsafe(self.ws.send(pcm_bytes), self.loop)
        except Exception:
            pass

    def _audio_output_callback(self, outdata, frames, time_info, status):
        """Sounddevice callback: plays server audio through local speakers."""
        if status:
            logger.warning(f"Spk: {status}")

        bytes_needed = frames * 2  # 16-bit mono

        with self.buffer_lock:
            available = len(self.playback_buffer)
            if available >= bytes_needed:
                chunk = bytes(self.playback_buffer[:bytes_needed])
                del self.playback_buffer[:bytes_needed]
            else:
                chunk = bytes(self.playback_buffer) + b"\x00" * (bytes_needed - available)
                self.playback_buffer.clear()

        arr = np.frombuffer(chunk, dtype=np.int16).reshape(-1, 1)
        outdata[:] = arr

        # Compute RMS for the level meter
        samples = arr[:, 0].astype(np.float32)
        rms = np.sqrt(np.mean(samples * samples))
        self._spk_rms = min(1.0, rms / 8000.0)

    def _start_audio_streams(self):
        self._log(f"Starting audio: mic {MIC_SAMPLE_RATE}Hz / spk {SPK_SAMPLE_RATE}Hz, 16-bit mono", tag="dim")
        with self.buffer_lock:
            self.playback_buffer.clear()

        try:
            self.mic_stream = sd.InputStream(
                device=self.mic_idx, samplerate=MIC_SAMPLE_RATE,
                channels=CHANNELS, dtype=DTYPE, blocksize=MIC_BLOCKSIZE,
                callback=self._audio_input_callback,
            )
            self.mic_stream.start()

            self.spk_stream = sd.OutputStream(
                device=self.spk_idx, samplerate=SPK_SAMPLE_RATE,
                channels=CHANNELS, dtype=DTYPE, blocksize=SPK_BLOCKSIZE,
                callback=self._audio_output_callback,
            )
            self.spk_stream.start()
        except Exception as e:
            self._log(f"Failed to start audio: {e}", tag="error")

    def _stop_audio_streams(self):
        for name, stream in [("mic", self.mic_stream), ("spk", self.spk_stream)]:
            if stream:
                try:
                    stream.stop()
                    stream.close()
                except Exception:
                    pass
        self.mic_stream = None
        self.spk_stream = None

    # =========================================================================
    # Periodic UI Updates
    # =========================================================================

    def _poll_meters(self):
        """Update level meters and stats label at ~30 fps."""
        self.mic_meter.set_level(self._mic_rms)
        self.spk_meter.set_level(self._spk_rms)

        # Decay towards zero when no audio
        self._mic_rms *= 0.7
        self._spk_rms *= 0.7

        # Update stats
        if self.connected and self._connect_time:
            elapsed = int(_time.monotonic() - self._connect_time)
            m, s = divmod(elapsed, 60)
            sent_kb = self._bytes_sent / 1024
            recv_kb = self._bytes_received / 1024
            self.lbl_stats.config(
                text=f"{m:02d}:{s:02d}  |  Sent {sent_kb:.0f} KB  |  Recv {recv_kb:.0f} KB"
            )

        self.root.after(33, self._poll_meters)  # ~30 fps


# =============================================================================
# Entry Point
# =============================================================================

if __name__ == "__main__":
    root = tk.Tk()
    app = MockClientApp(root)
    root.mainloop()
