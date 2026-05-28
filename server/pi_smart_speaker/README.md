# Billy Bass AI — Raspberry Pi Smart Speaker

Standalone Raspberry Pi client for the Billy Bass AI setup. It runs wake word detection locally, captures audio from a local microphone, and plays ElevenLabs responses through a selected output device such as Bluetooth headphones or a Bluetooth speaker.

## What It Does

- Listens on a local microphone.
- Runs wake word detection locally.
- Starts an ElevenLabs Conversational AI session when the wake word is detected.
- Streams mic audio into the ElevenLabs pipeline.
- Plays TTS audio on a chosen output device.

## Files

- `pi_client.py` — interactive smart speaker client.
- `pi_pipeline.py` — local ElevenLabs audio interface and conversation pipeline.

## Prerequisites

- Python 3.11+
- A working microphone
- A Bluetooth speaker or other output device
- An ElevenLabs API key
- An ElevenLabs Conversational AI agent ID
- A Picovoice access key for wake word detection

## Setup

From the `server` directory:

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

Make sure your `server/.env` contains at least:

- `ELEVENLABS_API_KEY`
- `ELEVENLABS_AGENT_ID`
- `PICOVOICE_ACCESS_KEY`

## Run

```bash
cd pi_smart_speaker
python pi_client.py
```

The program will list available input and output devices and ask you to choose one of each.

## Notes

- The input microphone is sampled at 16 kHz.
- The output stream is 44.1 kHz mono PCM.
- The wake word engine is started locally on the Pi, so this mode does not need the ESP32 WebSocket link.
- If the audio device selection fails, verify that `sounddevice` can see your Bluetooth speaker and microphone on the host OS.
