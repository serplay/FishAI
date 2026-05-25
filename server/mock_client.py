import asyncio
import json
import logging
import queue
import threading
import tkinter as tk
from tkinter import ttk, scrolledtext
from typing import Optional

import numpy as np
import sounddevice as sd
import websockets

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("MockClient")

class MockClientGUI:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("🐟 Billy Bass AI — Mock ESP32 Client")
        self.root.geometry("600x650")
        self.root.resizable(False, False)

        # State
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.ws: Optional[websockets.WebSocketClientProtocol] = None
        self.connected = False
        
        # Audio streams
        self.mic_stream: Optional[sd.InputStream] = None
        self.spk_stream: Optional[sd.OutputStream] = None
        
        # Playback buffer
        self.playback_buffer = bytearray()
        self.buffer_lock = threading.Lock()

        self._build_ui()
        self._load_audio_devices()

    def _build_ui(self):
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)

        # --- Connection Frame ---
        conn_frame = ttk.LabelFrame(main_frame, text="1. Server Connection", padding="10")
        conn_frame.pack(fill=tk.X, pady=(0, 10))

        ttk.Label(conn_frame, text="WebSocket URL:").grid(row=0, column=0, sticky=tk.W, pady=5)
        self.url_var = tk.StringVar(value="ws://localhost:8765/ws")
        ttk.Entry(conn_frame, textvariable=self.url_var, width=40).grid(row=0, column=1, padx=10, pady=5)

        self.btn_connect = ttk.Button(conn_frame, text="Connect", command=self.toggle_connection)
        self.btn_connect.grid(row=0, column=2, padx=5, pady=5)

        self.lbl_status = ttk.Label(conn_frame, text="Disconnected", foreground="red")
        self.lbl_status.grid(row=0, column=3, padx=10, pady=5)

        # --- Audio Devices Frame ---
        audio_frame = ttk.LabelFrame(main_frame, text="2. Audio Devices (Select before connecting)", padding="10")
        audio_frame.pack(fill=tk.X, pady=(0, 10))

        ttk.Label(audio_frame, text="Microphone (Input):").grid(row=0, column=0, sticky=tk.W, pady=5)
        self.mic_combo = ttk.Combobox(audio_frame, state="readonly", width=50)
        self.mic_combo.grid(row=0, column=1, padx=10, pady=5)

        ttk.Label(audio_frame, text="Speaker (Output):").grid(row=1, column=0, sticky=tk.W, pady=5)
        self.spk_combo = ttk.Combobox(audio_frame, state="readonly", width=50)
        self.spk_combo.grid(row=1, column=1, padx=10, pady=5)

        self.btn_refresh = ttk.Button(audio_frame, text="Refresh", command=self._load_audio_devices)
        self.btn_refresh.grid(row=0, column=2, rowspan=2, padx=5, pady=5)

        # --- Controls Frame ---
        ctrl_frame = ttk.LabelFrame(main_frame, text="3. AI Interaction Controls", padding="10")
        ctrl_frame.pack(fill=tk.X, pady=(0, 10))

        self.btn_wake = ttk.Button(ctrl_frame, text="🔔 Simulate Button Wake", state=tk.DISABLED, command=self.send_button_wake)
        self.btn_wake.pack(side=tk.LEFT, padx=5)

        self.btn_cancel = ttk.Button(ctrl_frame, text="🛑 Cancel / Interrupt", state=tk.DISABLED, command=self.send_cancel)
        self.btn_cancel.pack(side=tk.LEFT, padx=5)

        # --- Logs Frame ---
        log_frame = ttk.LabelFrame(main_frame, text="Event Logs (JSON & Status)", padding="10")
        log_frame.pack(fill=tk.BOTH, expand=True)

        self.txt_log = scrolledtext.ScrolledText(log_frame, wrap=tk.WORD, width=60, height=15)
        self.txt_log.pack(fill=tk.BOTH, expand=True)

    def _load_audio_devices(self):
        try:
            devices = sd.query_devices()
            inputs = []
            outputs = []
            
            for i, dev in enumerate(devices):
                name = f"[{i}] {dev['name']}"
                if dev['max_input_channels'] > 0:
                    inputs.append(name)
                if dev['max_output_channels'] > 0:
                    outputs.append(name)
            
            self.mic_combo['values'] = inputs
            if inputs:
                self.mic_combo.current(0)
                
            self.spk_combo['values'] = outputs
            if outputs:
                self.spk_combo.current(0)
                
            self.log("Audio devices refreshed.")
        except Exception as e:
            self.log(f"Error loading audio devices: {e}")

    def log(self, message: str):
        logger.info(message)
        self.root.after(0, self._append_log, message)

    def _append_log(self, message: str):
        self.txt_log.insert(tk.END, message + "\n")
        self.txt_log.see(tk.END)

    # =========================================================================
    # Connection Logic
    # =========================================================================

    def toggle_connection(self):
        if self.connected:
            self.disconnect()
        else:
            self.connect()

    def connect(self):
        mic_sel = self.mic_combo.get()
        spk_sel = self.spk_combo.get()
        
        if not mic_sel or not spk_sel:
            self.log("Please select both input and output devices!")
            return

        self.mic_idx = int(mic_sel.split("]")[0][1:])
        self.spk_idx = int(spk_sel.split("]")[0][1:])

        self.btn_connect.config(text="Connecting...", state=tk.DISABLED)
        self.mic_combo.config(state=tk.DISABLED)
        self.spk_combo.config(state=tk.DISABLED)
        
        # Start Asyncio loop in a background thread
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self._run_async_loop, daemon=True).start()

    def _run_async_loop(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self._websocket_handler())

    async def _websocket_handler(self):
        url = self.url_var.get()
        try:
            self.log(f"Connecting to {url}...")
            async with websockets.connect(url, ping_interval=20, ping_timeout=10) as ws:
                self.ws = ws
                
                # Handshake
                await ws.send(json.dumps({"type": "hello", "device": "MockClient-GUI"}))
                
                # Start Audio Streams
                self._start_audio_streams()
                
                # Update UI
                self.root.after(0, self._set_ui_connected)
                self.log("Connected and streaming microphone.")

                # Receive loop
                async for msg in ws:
                    if isinstance(msg, bytes):
                        # Binary audio data from ElevenLabs
                        with self.buffer_lock:
                            self.playback_buffer.extend(msg)
                    else:
                        # JSON message from Server
                        self.log(f"⬇️ Server: {msg}")

        except Exception as e:
            self.log(f"Connection error: {e}")
        finally:
            self._stop_audio_streams()
            self.root.after(0, self._set_ui_disconnected)

    def disconnect(self):
        self.log("Disconnecting...")
        if self.loop and self.loop.is_running() and self.ws:
            asyncio.run_coroutine_threadsafe(self.ws.close(), self.loop)

    def _set_ui_connected(self):
        self.connected = True
        self.btn_connect.config(text="Disconnect", state=tk.NORMAL)
        self.lbl_status.config(text="Connected", foreground="green")
        self.btn_wake.config(state=tk.NORMAL)
        self.btn_cancel.config(state=tk.NORMAL)

    def _set_ui_disconnected(self):
        self.connected = False
        self.btn_connect.config(text="Connect", state=tk.NORMAL)
        self.mic_combo.config(state="readonly")
        self.spk_combo.config(state="readonly")
        self.lbl_status.config(text="Disconnected", foreground="red")
        self.btn_wake.config(state=tk.DISABLED)
        self.btn_cancel.config(state=tk.DISABLED)
        self.log("Disconnected.")

    # =========================================================================
    # Control Actions
    # =========================================================================

    def send_button_wake(self):
        if self.ws and self.loop:
            msg = json.dumps({"type": "button_wake"})
            self.log(f"Sending: {msg}")
            asyncio.run_coroutine_threadsafe(self.ws.send(msg), self.loop)

    def send_cancel(self):
        if self.ws and self.loop:
            msg = json.dumps({"type": "cancel"})
            self.log(f"Sending: {msg}")
            asyncio.run_coroutine_threadsafe(self.ws.send(msg), self.loop)
            # Clear playback buffer to stop audio immediately
            with self.buffer_lock:
                self.playback_buffer.clear()

    # =========================================================================
    # Audio I/O
    # =========================================================================

    def _audio_input_callback(self, indata, frames, time, status):
        """Called by sounddevice for each chunk of microphone audio."""
        if status:
            logger.warning(f"Mic status: {status}")
        if self.connected and self.ws and self.loop:
            # Send binary PCM to server
            # indata is shape (frames, channels), dtype int16
            pcm_bytes = indata.tobytes()
            try:
                # We use run_coroutine_threadsafe to schedule the async send
                asyncio.run_coroutine_threadsafe(self.ws.send(pcm_bytes), self.loop)
            except Exception:
                pass

    def _audio_output_callback(self, outdata, frames, time, status):
        """Called by sounddevice when it needs audio to play on the speaker."""
        if status:
            logger.warning(f"Spk status: {status}")
            
        bytes_needed = frames * 2  # 16-bit mono = 2 bytes per frame
        
        with self.buffer_lock:
            if len(self.playback_buffer) >= bytes_needed:
                chunk = self.playback_buffer[:bytes_needed]
                del self.playback_buffer[:bytes_needed]
            else:
                # Pad with zeros if we don't have enough data
                chunk = self.playback_buffer + b'\x00' * (bytes_needed - len(self.playback_buffer))
                self.playback_buffer.clear()
                
        # Convert bytes to numpy array
        arr = np.frombuffer(chunk, dtype=np.int16).reshape(-1, 1)
        outdata[:] = arr

    def _start_audio_streams(self):
        self.log("Starting audio streams (16kHz, 16-bit Mono)...")
        with self.buffer_lock:
            self.playback_buffer.clear()
            
        try:
            self.mic_stream = sd.InputStream(
                device=self.mic_idx,
                samplerate=16000,
                channels=1,
                dtype='int16',
                blocksize=512,  # Match ESP32 chunk size (1024 bytes)
                callback=self._audio_input_callback
            )
            self.mic_stream.start()
            
            self.spk_stream = sd.OutputStream(
                device=self.spk_idx,
                samplerate=16000,
                channels=1,
                dtype='int16',
                blocksize=512,
                callback=self._audio_output_callback
            )
            self.spk_stream.start()
        except Exception as e:
            self.log(f"Failed to start audio streams: {e}")

    def _stop_audio_streams(self):
        self.log("Stopping audio streams...")
        if self.mic_stream:
            self.mic_stream.stop()
            self.mic_stream.close()
            self.mic_stream = None
        if self.spk_stream:
            self.spk_stream.stop()
            self.spk_stream.close()
            self.spk_stream = None


if __name__ == "__main__":
    root = tk.Tk()
    
    # Set a nice style if available
    style = ttk.Style()
    if "clam" in style.theme_names():
        style.theme_use("clam")
        
    app = MockClientGUI(root)
    root.mainloop()
