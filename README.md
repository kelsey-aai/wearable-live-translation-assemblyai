# Live translation relay for smart glasses and wearables

A companion-app relay for translation glasses: it takes 16 kHz mono audio from the device, streams it to [Universal-3.6 Pro Realtime](https://www.assemblyai.com/blog/universal-3-6-pro-realtime) (`universal-3-6-pro`) with the `llm_gateway` connection parameter set, and turns the server's events into small JSON caption payloads for a heads-up display. Partials become a dim source-language preview line, and each final turn comes back translated as an `LLMGatewayResponse` on the same WebSocket.

This is the companion repo for the tutorial: [How to build live translation into smart glasses and wearables](https://www.assemblyai.com/blog/live-translation-smart-glasses-wearables). For every `llm_gateway` field and the prompt template variables, see the [real-time translation docs](https://www.assemblyai.com/docs/streaming/real-time-translation).

**This is a reference relay, not a device SDK.** It mocks the glasses with a WAV file (or your computer's mic) and prints the payloads you'd write to a Bluetooth characteristic. In a real app, you'd port the same logic to Swift or Kotlin and swap `GlassesDisplay.send()` for your BLE write.

## What's in the repo

| File | What it does |
|------|--------------|
| `relay.py` | The relay from the tutorial: caption wrapping and paging, untranslated fallback after 2 seconds, a 5-second replay buffer with paced catch-up, `1008` handling and reconnect with backoff. Plays a WAV file in real time as the mock device |
| `mic_relay.py` | Optional: runs the same relay with your microphone as the mock device, for a set number of seconds |
| `requirements.txt` | `websocket-client`, plus `pyaudio` for the mic option |
| `.env.example` | The one environment variable the scripts read |

## Quick start

Requires Python 3.11 or later and an AssemblyAI API key. Translation runs on LLM Gateway, which is billed per token.

```bash
git clone https://github.com/kelsey-aai/wearable-live-translation-assemblyai
cd wearable-live-translation-assemblyai

python -m venv .venv
source .venv/bin/activate   # On Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

PyAudio is only needed for `mic_relay.py`. On macOS it needs PortAudio first, so if the install fails, run `brew install portaudio` and install again. If you only want the WAV option, `pip install websocket-client==1.9.2` is enough.

Configure your key:

```bash
cp .env.example .env
# Edit .env and set ASSEMBLYAI_API_KEY, then load it into your shell:
set -a; source .env; set +a
```

The scripts read `ASSEMBLYAI_API_KEY` from the environment, so `export ASSEMBLYAI_API_KEY=<your-key>` works too. The key is for local testing only. A shipped companion app should fetch a short-lived [temporary token](https://www.assemblyai.com/docs/temporary-tokens) from your backend before each connection.

### Run with a WAV file as the mock device

The relay expects 16 kHz, mono, 16-bit PCM WAV. Convert any recording of someone speaking English with ffmpeg:

```bash
ffmpeg -i input.m4a -ar 16000 -ac 1 -sample_fmt s16 conversation_16k_mono.wav
python relay.py conversation_16k_mono.wav
```

The file plays in real time, in 50 ms chunks. When it ends, the relay sends `Terminate` and exits.

### Run with your microphone as the mock device

```bash
python mic_relay.py 30   # listen for 30 seconds, then terminate the session
```

### Output

One JSON payload per display update:

```text
{"kind": "status", "status": "live"}
{"kind": "partial", "lines": ["Hi, I'd like to make a"]}
{"kind": "caption", "turn": 0, "translated": true, "lines": ["¡Hola! Me gustaría hacer una", "reserva en un restaurante."], "hold_s": 3.6}
```

Captions marked `"translated": false` are final turns whose translation didn't arrive within 2 seconds, shown in the source language so the display never goes blank.

## Customize it

- **Target language:** edit `PROMPT` in `relay.py`. To translate into French, change both mentions of Spanish. To accept any source language, say so in the prompt.
- **Translation model:** `LLM_GATEWAY_CONFIG["model"]`. See the [LLM Gateway models list](https://www.assemblyai.com/docs/llm-gateway/available-models).
- **Caption layout:** `GlassesDisplay.MAX_CHARS`, `MAX_LINES` and the hold-time formula. These, along with `REPLAY_SECONDS`, `CATCH_UP_SPEED` and `TRANSLATION_WAIT_S`, are tunable starting points for the tutorial, not AssemblyAI figures. Tune them on your own hardware.

## Learn more

- [How to build live translation into smart glasses and wearables](https://www.assemblyai.com/blog/live-translation-smart-glasses-wearables)
- [Real-time translation docs](https://www.assemblyai.com/docs/streaming/real-time-translation)
- [Common session errors and closures](https://www.assemblyai.com/docs/streaming/common-session-errors-and-closures)
