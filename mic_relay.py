"""Optional: use your computer's microphone as the mock glasses instead of a WAV file.

Reuses relay.py unchanged: it swaps the WAV player for a mic reader, records
for N seconds (default 30), then the relay sends Terminate and exits.
Requires PyAudio (on macOS, run `brew install portaudio` first).

    python mic_relay.py 30
"""
import sys
import time

import pyaudio

import relay

FRAMES_PER_CHUNK = relay.CHUNK_BYTES // 2  # 800 frames = 50 ms of 16-bit mono audio


def mic_device(seconds, buffer):
    """Reads 50 ms chunks from the default mic, like a glasses mic over BLE."""
    pa = pyaudio.PyAudio()
    stream = pa.open(format=pyaudio.paInt16, channels=1, rate=relay.SAMPLE_RATE,
                     input=True, frames_per_buffer=FRAMES_PER_CHUNK)
    try:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            buffer.push(stream.read(FRAMES_PER_CHUNK, exception_on_overflow=False))
    finally:
        stream.stop_stream()
        stream.close()
        pa.terminate()
        buffer.finish()


if __name__ == "__main__":
    relay.mock_device = mic_device  # relay.main() looks this up at call time
    relay.main(float(sys.argv[1]) if len(sys.argv) > 1 else 30)
