# pip install websocket-client
import json
import os
import random
import sys
import textwrap
import threading
import time
import wave
from collections import deque
from urllib.parse import urlencode

import websocket

# Local testing only. A shipped companion app should fetch a short-lived
# temporary token from your backend before each connection instead.
API_KEY = os.environ["ASSEMBLYAI_API_KEY"]

SAMPLE_RATE = 16000
CHUNK_MS = 50
CHUNK_BYTES = SAMPLE_RATE * 2 * CHUNK_MS // 1000  # 16-bit mono PCM: 1,600 bytes

REPLAY_SECONDS = 5        # audio kept while offline; anything older is dropped
CATCH_UP_SPEED = 1.2      # backlog send rate, under the ~1.25x real-time throttle
TRANSLATION_WAIT_S = 2.0  # fall back to source text if no translation arrives
HEARTBEAT_TIMEOUT_S = 15  # no message (Heartbeat arrives every 5 s) for this long = dead link

PROMPT = (
    "Earlier in the conversation: {{history_3}}\n\n"
    "Translate the following English into natural Spanish and reply with only "
    "the translation: {{turn}}"
)
LLM_GATEWAY_CONFIG = {
    "model": "qwen3.5-4b-32k-fast",
    "prompt": PROMPT,
    "max_tokens": 400,
}
CONNECTION_PARAMS = {
    "sample_rate": SAMPLE_RATE,
    "speech_model": "universal-3-6-pro",
    "llm_gateway": json.dumps(LLM_GATEWAY_CONFIG),
    "session_heartbeat": "true",  # server sends a Heartbeat message every 5 s
}
API_ENDPOINT = "wss://streaming.assemblyai.com/v3/ws?" + urlencode(CONNECTION_PARAMS)


class GlassesDisplay:
    """Stands in for the BLE link to the glasses. Replace send() with your
    characteristic write; keep the JSON small so it fits a few packets."""

    MAX_CHARS = 32  # per line on a narrow heads-up display
    MAX_LINES = 2

    def send(self, payload):
        print(json.dumps(payload, ensure_ascii=False))

    def show_partial(self, text):
        # Source-language preview: last line only, so it never pushes captions off.
        tail = textwrap.wrap(text, self.MAX_CHARS)[-1:] if text else []
        self.send({"kind": "partial", "lines": tail})

    def show_caption(self, turn, text, translated):
        lines = textwrap.wrap(text, self.MAX_CHARS)
        pages = [lines[i:i + self.MAX_LINES] for i in range(0, len(lines), self.MAX_LINES)]
        for page in pages:
            hold_s = max(1.5, sum(len(line) for line in page) / 15)  # ~15 chars/s
            self.send({"kind": "caption", "turn": turn, "translated": translated,
                       "lines": page, "hold_s": round(hold_s, 1)})

    def show_status(self, status):
        self.send({"kind": "status", "status": status})


class AudioBuffer:
    """Holds device audio. While offline it keeps only the last REPLAY_SECONDS."""

    def __init__(self):
        self.chunks = deque(maxlen=REPLAY_SECONDS * 1000 // CHUNK_MS)
        self.cond = threading.Condition()
        self.finished = False

    def push(self, chunk):
        with self.cond:
            self.chunks.append(chunk)
            self.cond.notify()

    def pop(self, timeout=0.5):
        with self.cond:
            if not self.chunks:
                self.cond.wait(timeout)
            if not self.chunks:
                return None, 0
            return self.chunks.popleft(), len(self.chunks)

    def requeue(self, chunk):
        with self.cond:
            if len(self.chunks) < self.chunks.maxlen:
                self.chunks.appendleft(chunk)

    def finish(self):
        with self.cond:
            self.finished = True
            self.cond.notify_all()


def mock_device(path, buffer):
    """Plays a 16 kHz mono 16-bit WAV in real time, like a glasses mic over BLE."""
    with wave.open(path, "rb") as wav:
        assert wav.getframerate() == SAMPLE_RATE and wav.getnchannels() == 1
        while True:
            chunk = wav.readframes(CHUNK_BYTES // 2)
            if len(chunk) < CHUNK_BYTES:
                break
            buffer.push(chunk)
            time.sleep(CHUNK_MS / 1000)
    buffer.finish()


class Relay:
    def __init__(self, buffer, display):
        self.buffer = buffer
        self.display = display
        self.session_id = None
        self.pending = {}  # turn_order -> (source_text, deadline)
        self.lock = threading.Lock()
        self.last_error = None
        self.ended = False
        self.last_heard = time.monotonic()

    # --- WebSocket callbacks ---
    def on_open(self, ws):
        self.last_heard = time.monotonic()
        self.display.show_status("live")
        threading.Thread(target=self.send_audio, args=(ws,), daemon=True).start()

    def on_message(self, ws, message):
        data = json.loads(message)
        kind = data.get("type")
        self.last_heard = time.monotonic()  # any message, Heartbeat included

        if kind == "Begin":
            self.session_id = data["id"]
        elif kind == "Turn":
            if data["end_of_turn"]:
                deadline = time.monotonic() + TRANSLATION_WAIT_S
                with self.lock:
                    self.pending[data["turn_order"]] = (data["transcript"], deadline)
            else:
                self.display.show_partial(data["transcript"])
        elif kind == "LLMGatewayResponse":
            turn = data["turn_order"]
            translation = data["data"]["choices"][0]["message"]["content"].strip()
            with self.lock:
                if self.pending.pop(turn, None) is None:
                    return  # already shown untranslated; don't repeat it
            self.display.show_caption(turn, translation, translated=True)
        elif kind == "Error":
            self.last_error = data
        elif kind == "Termination":
            self.ended = True

    def on_error(self, ws, error):
        if isinstance(error, websocket.ABNF) and error.opcode == websocket.ABNF.OPCODE_CLOSE:
            return
        print(f"WebSocket error: {error}", file=sys.stderr)

    def on_close(self, ws, status, msg):
        self.flush_untranslated(force=True)
        if not self.ended:
            self.display.show_status("reconnecting")

    # --- helpers ---
    def flush_untranslated(self, force=False):
        """No LLMGatewayResponse arrives if the gateway call fails, so after a
        short wait show the source-language text rather than nothing."""
        now = time.monotonic()
        with self.lock:
            due = [(t, text) for t, (text, deadline) in sorted(self.pending.items())
                   if force or now >= deadline]
            for turn, _ in due:
                del self.pending[turn]
        for turn, text in due:
            self.display.show_caption(turn, text, translated=False)

    def send_audio(self, ws):
        while ws.sock and ws.sock.connected:
            self.flush_untranslated()
            if time.monotonic() - self.last_heard > HEARTBEAT_TIMEOUT_S:
                ws.close()  # silent link: reconnect instead of waiting on TCP
                return
            chunk, backlog = self.buffer.pop()
            if chunk is None:
                if self.buffer.finished:
                    ws.send(json.dumps({"type": "Terminate"}))
                    return
                continue
            try:
                ws.send(chunk, websocket.ABNF.OPCODE_BINARY)
            except websocket.WebSocketConnectionClosedException:
                self.buffer.requeue(chunk)  # keep it for the next session
                return
            if backlog:
                time.sleep(CHUNK_MS / 1000 / CATCH_UP_SPEED)  # paced catch-up

    def should_retry(self):
        if self.ended:
            return False, 0
        err = self.last_error or {}
        self.last_error = None
        if err.get("error_code") == 1008:
            if "Too many concurrent sessions" in err.get("error", ""):
                return True, 60  # new-session rate limit: try again next minute
            return False, 0      # bad key, token or account: retrying won't help
        return True, None


def main(wav_path):
    buffer = AudioBuffer()
    display = GlassesDisplay()
    relay = Relay(buffer, display)
    threading.Thread(target=mock_device, args=(wav_path, buffer), daemon=True).start()

    attempt = 0
    while True:
        ws = websocket.WebSocketApp(
            API_ENDPOINT,
            header={"Authorization": API_KEY},
            on_open=relay.on_open,
            on_message=relay.on_message,
            on_error=relay.on_error,
            on_close=relay.on_close,
        )
        started = time.monotonic()
        ws.run_forever()

        retry, wait = relay.should_retry()
        if not retry:
            break
        if time.monotonic() - started > 30:
            attempt = 0  # the last session was healthy; start backoff over
        if wait is None:
            wait = min(30, 2 ** attempt) + random.uniform(0, 1)
        attempt += 1
        time.sleep(wait)
    display.show_status("ended")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "sample_16k_mono.wav")
