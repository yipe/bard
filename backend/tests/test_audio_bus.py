import asyncio
import io
import threading
import time
import wave

import numpy as np
import pytest
from conftest import tone, write_wav

from troubadour import audio
from troubadour.audio import FRAME, FileSource, MicSource, wav_bytes
from troubadour.config import SR
from troubadour.engine import OVERFLOW, STOP, AudioBuffer, Bus


def test_wav_bytes_is_valid_16k_mono():
    data = wav_bytes(tone(440, 0.5))
    with wave.open(io.BytesIO(data)) as w:
        assert (w.getnchannels(), w.getframerate(), w.getsampwidth()) == (1, 16000, 2)
        assert w.getnframes() == 8000


def test_wav_bytes_clips_out_of_range_samples():
    data = wav_bytes(np.array([2.0, -2.0], np.float32))
    with wave.open(io.BytesIO(data)) as w:
        samples = np.frombuffer(w.readframes(2), "<i2")
    assert list(samples) == [32767, -32767]


@pytest.mark.parametrize(("asked", "clamped"), [(0.01, 0.5), (100, 4.0), (2, 2.0)])
def test_file_source_speed_is_clamped(tmp_path, asked, clamped):
    src = FileSource(write_wav(tmp_path / "a.wav", tone(440, 0.2)), asked)
    assert src.speed == clamped


def test_file_source_pause_holds_then_finishes(tmp_path):
    src = FileSource(write_wav(tmp_path / "a.wav", tone(440, 0.5)), 4.0)
    frames, done = [], threading.Event()
    src.pause()
    src.start(frames.append, done.set)
    time.sleep(0.2)
    assert frames == [] and not done.is_set()
    src.resume()
    assert done.wait(5)
    assert frames


def test_mic_pause_stops_the_stream_until_resume(fake_sounddevice):
    mic = MicSource()
    mic.start(lambda f: None, lambda: None)
    stream = fake_sounddevice.instances[0]
    assert stream.started
    mic.pause()
    assert not stream.started
    mic.resume()
    assert stream.started
    mic.stop()
    assert stream.closed


def test_file_source_delivers_exact_frames_then_ends(tmp_path):
    src = FileSource(write_wav(tmp_path / "a.wav", tone(440, 0.5)), 4.0)
    frames, done = [], threading.Event()
    src.start(frames.append, done.set)
    assert done.wait(5)
    assert frames
    assert all(len(f) == FRAME for f in frames)


class FakeStream:
    instances: list["FakeStream"] = []

    def __init__(self, callback=None, **kwargs):
        self.callback, self.started, self.closed = callback, False, False
        FakeStream.instances.append(self)

    def start(self):
        self.started = True

    def stop(self):
        self.started = False

    def close(self):
        self.closed = True


@pytest.fixture
def fake_sounddevice(monkeypatch):
    import sounddevice

    FakeStream.instances.clear()
    monkeypatch.setattr(sounddevice, "InputStream", FakeStream)
    monkeypatch.setattr(MicSource, "native_rate", lambda self: SR)  # no resampling; never ask real hardware
    return FakeStream


def test_mic_rechunks_to_exact_frames(fake_sounddevice):
    got = []
    mic = MicSource()
    mic.start(got.append, lambda: None)
    stream = fake_sounddevice.instances[0]
    for size in (300, 300, 1000):  # PortAudio may deliver odd block sizes
        stream.callback(np.ones((size, 1), np.float32), size, None, None)
    assert [len(f) for f in got] == [FRAME] * 3  # 1600 samples → three 512 frames, 64 carried over
    mic.stop()
    assert stream.closed


def test_mic_stop_before_start_never_leaves_stream_open(fake_sounddevice):
    mic = MicSource()
    mic.stop()  # e.g. the session was replaced while the device was opening
    mic.start(lambda f: None, lambda: None)
    stream = fake_sounddevice.instances[0]
    assert stream.closed
    assert not stream.started


@pytest.fixture
def portaudio_inits(monkeypatch):
    """Count PortAudio re-initializations instead of doing them."""
    import sounddevice

    calls = []
    monkeypatch.setattr(sounddevice, "_terminate", lambda: calls.append("terminate"))
    monkeypatch.setattr(sounddevice, "_initialize", lambda: calls.append("initialize"))
    return calls


def test_list_mics_survives_portaudio_errors(monkeypatch, portaudio_inits):
    import sounddevice

    def boom(*a, **k):
        raise sounddevice.PortAudioError("no devices")

    monkeypatch.setattr(sounddevice, "query_devices", boom)
    assert audio.list_mics() == []


def test_list_mics_rereads_devices_only_while_no_mic_is_open(monkeypatch, portaudio_inits, fake_sounddevice):
    import sounddevice

    monkeypatch.setattr(
        sounddevice,
        "query_devices",
        lambda: [{"name": "Speakers", "max_input_channels": 0}, {"name": "Yeti", "max_input_channels": 2}],
    )
    assert [m["name"] for m in audio.list_mics()] == ["Yeti"]
    assert portaudio_inits == ["terminate", "initialize"]  # a mic plugged in since start-up shows up
    mic = MicSource()
    mic.start(lambda f: None, lambda: None)
    assert [m["name"] for m in audio.list_mics()] == ["Yeti"]
    assert portaudio_inits == ["terminate", "initialize"]  # re-initializing would close the open mic
    mic.stop()
    audio.list_mics()
    assert portaudio_inits == ["terminate", "initialize"] * 2


def test_mic_stopped_before_start_still_frees_portaudio(portaudio_inits, fake_sounddevice, monkeypatch):
    import sounddevice

    monkeypatch.setattr(sounddevice, "query_devices", lambda: [])
    mic = MicSource()
    mic.stop()
    mic.start(lambda f: None, lambda: None)
    audio.list_mics()
    assert portaudio_inits == ["terminate", "initialize"]


def test_audio_buffer_grows_and_keeps_old_views():
    buf = AudioBuffer()
    first = np.ones(512, np.float32)
    buf.append(first)
    view = buf.view()
    for _ in range(4000):  # forces at least one regrowth (initial capacity is 60 s)
        buf.append(np.zeros(512, np.float32))
    assert buf.n == 512 * 4001
    assert np.all(view == 1)
    assert np.all(buf.view()[:512] == 1)


def test_bus_overflow_replaces_backlog_with_marker():
    async def scenario():
        bus = Bus()
        bus.loop = asyncio.get_running_loop()
        q = bus.subscribe()
        for i in range(Bus.QUEUE_MAX + 5):
            bus.emit("x", i)
        await asyncio.sleep(0.05)
        return [q.get_nowait() for _ in range(q.qsize())]

    items = asyncio.run(scenario())
    assert items[0] == OVERFLOW  # the client is told to resync instead of silently missing events
    assert len(items) <= 6


def test_bus_emit_after_loop_closed_is_a_no_op():
    bus = Bus()
    loop = asyncio.new_event_loop()
    bus.loop = loop
    bus.subscribe()
    loop.close()
    bus.emit("x", 1)  # must not raise


def test_stop_sentinel_is_unique():
    assert STOP is not OVERFLOW
