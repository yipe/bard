"""Shared fixtures. Tests never load real models or touch backend/data.

A fake speaker embedder stands in for pyannote: its "voiceprint" is the pooled
magnitude spectrum, so sine tones at different pitches act as different
speakers (cosine ≈ 0) and the same pitch as the same speaker (cosine ≈ 1).
"""

import os
import tempfile
from pathlib import Path

# Must happen before anything imports troubadour.config (server.py builds an Engine at import).
_TMP = Path(tempfile.mkdtemp(prefix="troubadour-tests-"))
os.environ["TROUBADOUR_DATA"] = str(_TMP / "data")
os.environ["TROUBADOUR_SAMPLES"] = str(_TMP / "samples")
os.environ["TROUBADOUR_OBSIDIAN_REGISTRY"] = str(_TMP / "obsidian.json")  # never this Mac's Obsidian vaults
(_TMP / "samples").mkdir()

import numpy as np  # noqa: E402
import pytest  # noqa: E402

from troubadour import config  # noqa: E402
from troubadour.audio import wav_bytes  # noqa: E402
from troubadour.engine import Bus, Engine, LiveSession  # noqa: E402
from troubadour.voices import VoiceMatcher  # noqa: E402

SR = config.SR


class FakeEmbedder:
    dimension = 256

    def __call__(self, batch):
        x = batch[:, 0].numpy()
        spec = np.abs(np.fft.rfft(x, axis=1))
        bins = np.array_split(np.arange(spec.shape[1]), self.dimension)
        return np.stack([spec[:, b].sum(axis=1) for b in bins], axis=1) + 1e-6


def tone(freq: float, seconds: float) -> np.ndarray:
    t = np.arange(int(seconds * SR)) / SR
    return (0.3 * np.sin(2 * np.pi * freq * t)).astype(np.float32)


class RecordingBus(Bus):
    def __init__(self):
        super().__init__()
        self.events: list[tuple[str, object]] = []

    def emit(self, type_, data):
        self.events.append((type_, data))

    def of(self, type_: str) -> list:
        return [d for t, d in self.events if t == type_]


class NullSource:
    def __init__(self):
        self.stopped = False

    def start(self, sink, on_end):
        pass

    def stop(self):
        self.stopped = True


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    data, samples = tmp_path / "data", tmp_path / "samples"
    samples.mkdir()
    monkeypatch.setattr(config, "DATA_DIR", data)
    monkeypatch.setattr(config, "SAMPLES_DIR", samples)
    return data, samples


@pytest.fixture
def engine(dirs, monkeypatch):
    """An Engine with the fake embedder, models 'loaded', and a live session whose buffer tests fill."""
    bus = RecordingBus()
    eng = Engine(bus)
    eng.embedder = FakeEmbedder()
    eng.matcher = VoiceMatcher(eng.embedder)
    eng.ready.set()
    sess = LiveSession(NullSource(), {"kind": "file", "name": "x.wav", "speed": 1.0})
    eng.live, eng.buffer, eng.status = sess, sess.buffer, "running"
    monkeypatch.setattr("troubadour.asr.transcribe", lambda audio: "I deal 12 slashing damage")
    monkeypatch.setattr(config, "ASR_PROMPT", False)  # tests that want the Whisper prompt turn it on

    def no_network(*a, **k):
        raise ConnectionError("tests never call the real LLM; patch engine.cleanup_llm.json")

    monkeypatch.setattr(eng.cleanup_llm, "json", no_network)
    return eng


def speak(eng: Engine, freq: float, seconds: float, gap: float = 0.3) -> tuple[float, float]:
    """Append a 'speaker' (tone) to the live session audio; returns its (t0, t1)."""
    buf = eng.live.buffer
    if gap:
        buf.append(np.zeros(int(gap * SR), np.float32))
    t0 = buf.n / SR
    buf.append(tone(freq, seconds))
    return t0, buf.n / SR


def write_wav(path: Path, audio: np.ndarray) -> Path:
    path.write_bytes(wav_bytes(audio))
    return path


@pytest.fixture(autouse=True)
def hermetic_dnd_terms(tmp_path, monkeypatch):
    """Tests use the built-in D&D names, never a local list in data/dnd_terms.json."""
    monkeypatch.setattr("troubadour.dnd_terms.generated_path", lambda: tmp_path / "no-dnd-terms.json")
