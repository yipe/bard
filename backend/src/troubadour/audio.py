"""Audio sources. Both deliver 16 kHz mono float32 frames of FRAME samples to a callback."""

import io
import queue
import subprocess
import threading
import time
import wave
from collections.abc import Callable
from pathlib import Path

import numpy as np

from . import config
from .config import SR

FRAME = 512  # Silero VAD's frame size at 16 kHz (32 ms)

Sink = Callable[[np.ndarray], None]

# PortAudio reads the device list once, when it initializes, so a mic plugged in later stays
# invisible until it re-initializes (sounddevice's documented `_terminate()` + `_initialize()`).
# Re-initializing closes every open stream, so it happens only while none is open; this lock
# keeps it from racing a stream being opened or closed, or a device lookup.
_portaudio = threading.RLock()
_open_streams = 0


def load_audio(path: Path | str, start: float = 0.0, end: float | None = None) -> np.ndarray:
    span = ["-ss", str(start)] + (["-t", str(end - start)] if end is not None else [])
    pcm = subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", *span, "-i", str(path), "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"],
        check=True,
        capture_output=True,
    ).stdout
    return np.frombuffer(pcm, np.float32).copy()


def pcm16(audio: np.ndarray) -> bytes:
    return (np.clip(audio, -1, 1) * 32767).astype("<i2").tobytes()


def wav_bytes(audio: np.ndarray) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm16(audio))
    return buf.getvalue()


class Recorder:
    """Writes a session's audio at the mic's own rate to a mono 16-bit WAV (48 kHz: ~345 MB per
    hour; 16 bits sits ~40 dB below a table mic's own noise floor, so 24 would add nothing).
    Disk writes happen on a background thread, never in the audio callback. `wave` re-patches the
    header on every write, so after a crash the file is still valid, short by < FLUSH_S. A disk
    error stops the recording, never the session: `on_error` is called once, at the moment it
    happens (not hours later when the session ends); writes after close() are ignored."""

    FLUSH_S = 1.0

    def __init__(self, path: Path, rate: int = SR, on_error: Callable[[str], None] | None = None):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path, self.rate = path, rate
        self.error: str | None = None
        self._on_error = on_error
        self._q: queue.SimpleQueue = queue.SimpleQueue()
        self._closed = False
        w = wave.open(str(path), "wb")  # noqa: SIM115 — the writer thread closes it
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        self._thread = threading.Thread(target=self._run, args=(w,), name="recorder", daemon=True)
        self._thread.start()

    def write(self, block: np.ndarray) -> None:
        if not self._closed:
            self._q.put(block)

    def _run(self, w: wave.Wave_write) -> None:
        pending: list[np.ndarray] = []
        samples, ok = 0, True
        while (block := self._q.get()) is not None:
            if not ok:
                continue  # keep draining so write() never backs up
            pending.append(block)
            samples += len(block)
            if samples >= self.rate * self.FLUSH_S:
                ok = self._write(w, pending)
                pending, samples = [], 0
        if ok and self._write(w, pending):
            try:
                w.close()
            except OSError as e:
                self._fail(e)

    def _write(self, w: wave.Wave_write, blocks: list[np.ndarray]) -> bool:
        if not blocks:
            return True
        try:
            w.writeframes(pcm16(np.concatenate(blocks)))
            return True
        except OSError as e:
            self._fail(e)  # the file stays valid up to the last good write
            return False

    def _fail(self, e: OSError) -> None:
        self.error = str(e)
        if self._on_error is not None:
            self._on_error(self.error)

    def close(self, timeout: float = 10.0) -> None:
        if self._closed:
            return
        self._closed = True
        self._q.put(None)
        self._thread.join(timeout)


def list_mics() -> list[dict]:
    """Input devices as they are now; empty if PortAudio can't enumerate (no permission, no devices)."""
    try:
        import sounddevice as sd

        with _portaudio:
            if _open_streams == 0:
                sd._terminate()
                sd._initialize()
            default_in = sd.default.device[0]
            return [
                {"name": d["name"], "default": i == default_in}
                for i, d in enumerate(sd.query_devices())
                if d["max_input_channels"] > 0
            ]
    except Exception:
        return []


class FileSource:
    """Replays a file at speed × real time, as if it were a live mic."""

    MIN_SPEED, MAX_SPEED = 0.5, config.MAX_REPLAY_SPEED

    def __init__(self, path: Path, speed: float = 1.0, end: float | None = None):
        self.audio = load_audio(path, 0.0, end)
        self.speed = min(max(float(speed), self.MIN_SPEED), self.MAX_SPEED)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self, sink: Sink, on_end: Callable[[], None]) -> None:
        def pump():
            t0 = time.monotonic()
            for i, start in enumerate(range(0, len(self.audio) - FRAME + 1, FRAME)):
                if self._stop.is_set():
                    return
                # Pace against the wall clock so drift doesn't accumulate.
                due = t0 + (i * FRAME / SR) / self.speed
                delay = due - time.monotonic()
                if delay > 0:
                    time.sleep(delay)
                sink(self.audio[start : start + FRAME])
            on_end()

        self._thread = threading.Thread(target=pump, name="file-source", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()


class MicSource:
    """Default (or named) input device, opened at its own rate (usually 48 kHz). `tap`, if set
    before start(), gets that untouched audio (the full-quality recording); the pipeline gets a
    16 kHz copy (soxr, streaming), re-chunked to exact FRAME-sized blocks for the VAD."""

    def __init__(self, device: str | None = None):
        self.device = device
        self.tap: Sink | None = None
        self._stream = None
        self._lock = threading.Lock()
        self._stopped = False

    def device_name(self) -> str | None:
        """The device's own name, even for "the default mic" (None if it can't be read)."""
        try:
            import sounddevice as sd

            with _portaudio:
                return str(sd.query_devices(self.device, "input")["name"])
        except Exception:
            return None

    def native_rate(self) -> int:
        import sounddevice as sd

        with _portaudio:
            return int(sd.query_devices(self.device, "input")["default_samplerate"])

    def start(self, sink: Sink, on_end: Callable[[], None]) -> None:
        global _open_streams
        import sounddevice as sd
        import soxr

        tap = self.tap
        carry = np.zeros(0, np.float32)

        def callback(indata, frames, time_info, status):
            nonlocal carry
            block = indata[:, 0].copy()
            if tap is not None:
                tap(block)
            carry = np.concatenate([carry, to_16k.resample_chunk(block) if to_16k else block])
            while len(carry) >= FRAME:
                sink(carry[:FRAME].copy())
                carry = carry[FRAME:]

        with _portaudio:
            rate = self.native_rate()
            to_16k = soxr.ResampleStream(rate, SR, 1, dtype="float32", quality="HQ") if rate != SR else None
            stream = sd.InputStream(
                samplerate=rate,
                channels=1,
                dtype="float32",
                blocksize=FRAME * rate // SR,  # same 32 ms cadence as before
                device=self.device,
                callback=callback,
            )
            _open_streams += 1
        with self._lock:
            if self._stopped:  # stop() won the race: don't leave the mic open
                _close(stream)
                return
            self._stream = stream
            stream.start()

    def stop(self) -> None:
        with self._lock:
            self._stopped = True
            stream, self._stream = self._stream, None
        if stream is not None:
            _close(stream)


def _close(stream) -> None:
    global _open_streams
    with _portaudio:
        try:
            stream.stop()
            stream.close()
        finally:
            _open_streams -= 1
