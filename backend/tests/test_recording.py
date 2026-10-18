"""Session recordings, setup snapshots, replaying a saved session, and troubadour-sim's comparison."""

import json
import time
import wave

import numpy as np
import pytest
from conftest import NullSource, tone, write_wav
from fastapi.testclient import TestClient
from test_engine import ALICE, BOB, named_voice

from troubadour import config, server, sim
from troubadour.audio import FRAME, Recorder
from troubadour.config import SR
from troubadour.engine import LiveSession, session_audio
from troubadour.transcript import build

A, B, C, D = (f"2026-10-08_1400{i:02d}-abcd" for i in range(4))


def frames(audio: np.ndarray, size: int = FRAME):
    for i in range(0, len(audio) - size + 1, size):
        yield audio[i : i + size]


def nframes(path, rate: int = SR) -> int:
    with wave.open(str(path)) as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, rate)
        return w.getnframes()


def wait_for(check, seconds: float = 5.0) -> bool:
    """Poll check(); a read that races the writer thread (half-written header) counts as not yet."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            if check():
                return True
        except (EOFError, wave.Error):
            pass
        time.sleep(0.01)
    return False


# ------------------------------------------------------------- Recorder


def test_recording_is_a_valid_wav_even_before_close(tmp_path):
    rec = Recorder(tmp_path / "s" / "audio.wav")
    audio = tone(ALICE, 50 * FRAME / SR)  # 1.6 s: one 1 s flush (32 frames), 18 frames pending
    for f in frames(audio):
        rec.write(f)
    # Not closed (as after a crash): everything up to the last flush is readable.
    assert wait_for(lambda: nframes(rec.path) == 32 * FRAME)
    rec.close()
    assert nframes(rec.path) == 50 * FRAME
    rec.write(audio[:FRAME])  # an abandoned session still draining: ignored
    rec.close()
    assert nframes(rec.path) == 50 * FRAME


def test_recording_keeps_the_mics_own_rate(tmp_path):
    rec = Recorder(tmp_path / "audio.wav", 48000)
    audio = tone(1000, 0.5 * 3)[:24000]  # 0.5 s at 48 kHz
    rec.write(audio)
    rec.close()
    assert nframes(rec.path, 48000) == 24000


def test_disk_error_stops_the_recording_not_the_session(tmp_path, monkeypatch):
    def full(self, data):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(wave.Wave_write, "writeframes", full)
    reported = []
    rec = Recorder(tmp_path / "audio.wav", on_error=reported.append)
    for f in frames(tone(ALICE, 2.0)):
        rec.write(f)  # must not raise
    assert wait_for(lambda: bool(reported))  # reported as it happens, not at close()
    rec.close()
    assert "No space left" in rec.error
    assert reported == [rec.error]  # once, even though more blocks arrived after the failure


# ------------------------------------------------------------- MicSource


def test_mic_records_native_audio_and_feeds_the_pipeline_16k(monkeypatch):
    import sounddevice

    from troubadour.audio import MicSource

    opened = {}

    class FakeStream:
        def __init__(self, samplerate, channels, dtype, blocksize, device, callback):
            opened.update(rate=samplerate, blocksize=blocksize, callback=callback)

        def start(self):
            pass

        def stop(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(sounddevice, "InputStream", FakeStream)
    monkeypatch.setattr(MicSource, "native_rate", lambda self: 48000)
    mic, tapped, fed = MicSource(), [], []
    mic.tap = tapped.append
    mic.start(fed.append, lambda: None)
    assert (opened["rate"], opened["blocksize"]) == (48000, 3 * FRAME)
    native = (0.3 * np.sin(2 * np.pi * 1000 * np.arange(48000) / 48000)).astype(np.float32)  # 1 s, 1 kHz
    for block in frames(native, 3 * FRAME):
        opened["callback"](block[:, None], len(block), None, None)
    assert np.array_equal(np.concatenate(tapped), native[: len(np.concatenate(tapped))])  # untouched
    assert len(np.concatenate(tapped)) == 31 * 3 * FRAME
    assert {len(f) for f in fed} == {FRAME}
    out = np.concatenate(fed)
    assert abs(len(out) - 31 * FRAME) <= 2 * FRAME  # 16 kHz, minus the resampler's short delay
    spectrum = np.abs(np.fft.rfft(out[FRAME:]))
    assert abs(np.argmax(spectrum) * SR / (len(out) - FRAME) - 1000) < 20  # still a 1 kHz tone


# ------------------------------------------------------------- engine


@pytest.fixture
def mic_start(engine, monkeypatch):
    """engine.start for a 48 kHz mic, with the real segmenter; returns (tap, sink, end)."""
    monkeypatch.setattr("troubadour.engine.keep_awake", lambda: None)
    monkeypatch.setattr(engine, "_worker_loop", lambda sess: None)
    monkeypatch.setattr("troubadour.engine.MicSource.native_rate", lambda self: 48000)
    fed = {}

    def capture(self, sink, on_end):
        fed.update(tap=self.tap, sink=sink, end=on_end)

    monkeypatch.setattr("troubadour.engine.MicSource.start", capture)

    def start():
        engine.start({"kind": "mic", "device": None})
        return fed["tap"], fed["sink"], fed["end"]

    return start


def test_mic_session_records_full_quality_audio(engine, mic_start):
    tap, _, _ = mic_start()
    sess = engine.live
    native = (0.3 * np.sin(2 * np.pi * 1000 * np.arange(48000) / 48000)).astype(np.float32)
    for block in frames(native, 3 * FRAME):
        tap(block)
    sess.release()
    path = engine.sessions_dir() / sess.id / "audio.wav"
    n = 31 * 3 * FRAME
    assert nframes(path, 48000) == n
    with wave.open(str(path)) as w:
        back = np.frombuffer(w.readframes(w.getnframes()), "<i2") / 32767
    assert np.allclose(back, native[:n], atol=1e-4)


def test_recording_failure_mid_session_reaches_the_ui(engine, mic_start, monkeypatch):
    def full(self, data):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(wave.Wave_write, "writeframes", full)
    tap, _, _ = mic_start()
    for block in frames(np.zeros(48000 * 2, np.float32), 3 * FRAME):  # 2 s: past the first flush
        tap(block)
    assert wait_for(lambda: any("recording stopped" in e["message"] for e in engine.bus.of("error")))
    assert engine.status == "running"  # the session itself carries on


def test_mic_that_fails_to_open_leaves_no_recording(engine, monkeypatch):
    monkeypatch.setattr("troubadour.engine.MicSource.native_rate", lambda self: 48000)

    def broken(self, sink, on_end):
        raise RuntimeError("device busy")

    monkeypatch.setattr("troubadour.engine.MicSource.start", broken)
    with pytest.raises(ValueError, match="Couldn't open mic"):
        engine.start({"kind": "mic", "device": None})
    assert list(engine.sessions_dir().glob("*/audio.wav")) == []


def test_recording_can_be_turned_off(engine, mic_start, monkeypatch):
    monkeypatch.setattr(config, "RECORD_AUDIO", False)
    mic_start()
    assert engine.live.recorder is None


def test_file_replays_are_not_recorded(engine, dirs, monkeypatch):
    monkeypatch.setattr("troubadour.engine.keep_awake", lambda: None)
    monkeypatch.setattr("troubadour.engine.FileSource.start", lambda self, sink, on_end: None)
    monkeypatch.setattr(engine, "_segment_loop", lambda sess: None)
    monkeypatch.setattr(engine, "_worker_loop", lambda sess: None)
    write_wav(dirs[1] / "scene.wav", tone(ALICE, 1.0))
    engine.start({"kind": "file", "name": "scene.wav", "speed": 1})
    assert engine.live.recorder is None
    assert not (engine.sessions_dir() / engine.live.id / "audio.wav").exists()


def test_setup_snapshot_has_voiceprints_and_training_clips(engine, dirs, mic_start):
    p = engine.add_player("Alice", character="Elara")
    vid = named_voice(engine, p["id"], ALICE)
    (dirs[0] / "voices").mkdir(parents=True)
    write_wav(dirs[0] / "voices" / f"{vid}.wav", tone(ALICE, 1))  # the Train clip
    mic_start()
    setup = engine.sessions_dir() / engine.live.id / "setup"
    start = json.loads((setup / "start.json").read_text())
    assert [pl["name"] for pl in start["players"]] == ["Alice"]
    assert [v["id"] for v in start["voices"]] == [vid]
    assert len(start["voices"][0]["vec"]) > 0
    assert (setup / "voices" / f"{vid}.wav").is_file()


def test_end_setup_includes_voices_named_during_play(engine, mic_start):
    mic_start()
    sess = engine.live
    assert json.loads((engine.sessions_dir() / sess.id / "setup" / "start.json").read_text())["voices"] == []
    p = engine.add_player("Bob")
    vid = named_voice(engine, p["id"], BOB)
    engine._finish(sess)
    end = json.loads((engine.sessions_dir() / sess.id / "setup" / "end.json").read_text())
    assert [v["id"] for v in end["voices"]] == [vid]


def test_silent_mic_session_is_still_saved(engine):
    engine.live = LiveSession(NullSource(), {"kind": "mic", "device": None})
    engine.messages.clear()
    assert engine.save_transcript(force=True) is not None
    engine.live = LiveSession(NullSource(), {"kind": "file", "name": "x.wav", "speed": 1})
    assert engine.save_transcript(force=True) is None  # an empty replay is just clutter


# ------------------------------------------------------------- replay sources


def _session(sid: str, source: dict | None, audio: bool = False, heard: float | None = None) -> None:
    folder = config.DATA_DIR / "sessions" / sid
    folder.mkdir(parents=True)
    if source is not None:
        (folder / "meta.json").write_text(json.dumps({"id": sid, "started_at": 1, "source": source, "duration_s": heard}))
    if audio:
        write_wav(folder / "audio.wav", tone(ALICE, 0.5))


def test_session_audio_follows_recordings_samples_and_replays(dirs):
    write_wav(dirs[1] / "scene.wav", tone(BOB, 0.5))
    _session(A, {"kind": "mic", "device": None}, audio=True, heard=0.5)
    _session(B, {"kind": "file", "name": "scene.wav", "speed": 2})
    _session(C, {"kind": "session", "id": A, "speed": 1})
    _session(D, {"kind": "session", "id": "../../etc", "speed": 1})
    assert session_audio(A) == (config.DATA_DIR / "sessions" / A / "audio.wav", None)  # a recording is all heard
    assert session_audio(B) == ((dirs[1] / "scene.wav").resolve(), None)
    assert session_audio(C) == session_audio(A)
    for bad in (D, "../state", "2026-01-01_000000-ffff", ""):
        with pytest.raises(KeyError):
            session_audio(bad)


def test_replay_stops_where_the_session_stopped(dirs):
    # A replay stopped after 30 s of a 600 s sample, then a replay of that stopped at 12 s.
    write_wav(dirs[1] / "long.wav", tone(BOB, 0.5))
    _session(A, {"kind": "file", "name": "long.wav", "speed": 1}, heard=30.0)
    _session(B, {"kind": "session", "id": A, "speed": 1}, heard=12.0)
    _session(C, {"kind": "session", "id": A, "speed": 1}, heard=45.0)  # can't hear more than A did
    assert session_audio(A)[1] == 30.0
    assert session_audio(B)[1] == 12.0
    assert session_audio(C)[1] == 30.0


def test_file_source_honours_end(dirs):
    from troubadour.audio import FileSource

    path = write_wav(dirs[1] / "two.wav", tone(BOB, 2.0))
    assert len(FileSource(path, 1, end=0.5).audio) == SR // 2
    assert len(FileSource(path, 1).audio) == 2 * SR


def test_api_cannot_pass_end(engine, monkeypatch, dirs):
    seen = {}
    monkeypatch.setattr(engine, "start", lambda source: seen.update(source) or {})
    monkeypatch.setattr(server, "engine", engine)
    client = TestClient(server.app, base_url="http://127.0.0.1:8765")
    r = client.post("/api/session/start", json={"source": {"kind": "file", "name": "x.wav", "speed": 1, "end": 1}})
    assert r.status_code == 200
    assert seen["name"] == "x.wav"  # start() was reached: the check below isn't vacuous
    assert "end" not in seen


def test_session_audio_survives_a_replay_loop(dirs):
    _session(A, {"kind": "session", "id": B, "speed": 1})
    _session(B, {"kind": "session", "id": A, "speed": 1})
    with pytest.raises(KeyError):
        session_audio(A)


def test_list_sessions_says_what_can_be_replayed(engine):
    _session(A, {"kind": "mic", "device": None}, audio=True)
    _session(B, {"kind": "file", "name": "deleted.wav", "speed": 1})
    flags = {s["id"]: (s["audio"], s["replayable"]) for s in engine.list_sessions()}
    assert flags == {A: (True, True), B: (False, False)}


@pytest.fixture
def api(engine, monkeypatch):
    monkeypatch.setattr(server, "engine", engine)
    return TestClient(server.app, base_url="http://127.0.0.1:8765")


def test_replay_api(api, engine, monkeypatch):
    _session(A, {"kind": "mic", "device": None}, audio=True)
    r = api.get(f"/api/sessions/{A}/audio.wav")
    assert r.status_code == 200
    assert r.headers["content-type"] == "audio/wav"
    assert api.get("/api/sessions/2026-01-01_000000-ffff/audio.wav").status_code == 404
    start = lambda src: api.post("/api/session/start", json={"source": src})  # noqa: E731
    assert start({"kind": "session", "id": "2026-01-01_000000-ffff", "speed": 1}).status_code == 404
    assert start({"kind": "session", "id": A, "speed": 99}).status_code == 422
    monkeypatch.setattr("troubadour.engine.keep_awake", lambda: None)
    monkeypatch.setattr("troubadour.engine.FileSource.start", lambda self, sink, on_end: None)
    monkeypatch.setattr(engine, "_segment_loop", lambda sess: None)
    monkeypatch.setattr(engine, "_worker_loop", lambda sess: None)
    r = start({"kind": "session", "id": A, "speed": 2})
    assert r.status_code == 200, r.text
    assert r.json()["source"] == {"kind": "session", "id": A, "speed": 2.0}
    assert engine.live.recorder is None  # replays aren't re-recorded


# ------------------------------------------------------------- troubadour-sim


def _record(lines: list[tuple[float, float, str, str]]) -> dict:
    players = [
        {"id": n, "name": n, "character": "", "is_gm": False, "color": "#000", "avatar": None, "trained": False}
        for n in ("Alice", "Bob")
    ]
    voices = [
        {"id": f"v{n}", "label": None, "status": "named", "player_id": n, "character": None, "trained": False}
        for n in ("Alice", "Bob")
    ]
    msgs = [
        {
            "id": f"m{i}",
            "t_start": s,
            "t_end": e,
            "voice_id": f"v{who}",
            "provisional": False,
            "confidence": 0.9,
            "text": text,
        }
        for i, (s, e, who, text) in enumerate(lines)
    ]
    return build({"id": A, "started_at": 1, "source": None, "duration_s": 60}, players, voices, msgs)


def test_compare_identical_runs():
    rec = _record([(0, 4, "Alice", "I attack the orc."), (5, 9, "Bob", "Roll for initiative!")])
    out = sim.compare(rec, rec)
    assert out["speaker_agreement"] == 1.0
    assert out["text_similarity"] == 1.0
    assert out["lines"] == {"original": 2, "rerun": 2}


def test_compare_counts_speaker_and_word_changes():
    original = _record([(0, 4, "Alice", "I attack the orc"), (5, 9, "Bob", "roll for initiative")])
    rerun = _record([(0, 4, "Alice", "I attack the ogre"), (5, 9, "Alice", "roll for initiative")])
    out = sim.compare(original, rerun)
    assert out["speaker_agreement"] == 0.5  # Bob's 4 s went to Alice
    assert out["text_similarity"] == pytest.approx(6 / 7, abs=1e-4)
    assert out["speaker_seconds"]["rerun"] == {"Alice": 8}


def _raw(lines: list[tuple[float, float, str]], players=("Alice",)) -> dict:
    msgs = [{"t_start": s, "t_end": e, "speaker": who, "text": "hi"} for s, e, who in lines]
    return {"players": [{"name": n} for n in players], "messages": msgs}


def test_compare_ignores_unnamed_voice_numbering():
    original = _raw([(0, 4, "Voice 38"), (5, 9, "Alice"), (10, 14, "Voice 39")])
    rerun = _raw([(0, 4, "Voice 41"), (5, 9, "Alice"), (10, 14, "Voice 42")])
    assert sim.compare(original, rerun)["speaker_agreement"] == 1.0


def test_compare_still_counts_unnamed_merges_and_wrong_names():
    original = _raw([(0, 4, "Voice 38"), (5, 9, "Voice 39")])
    merged = _raw([(0, 4, "Voice 41"), (5, 9, "Voice 41")])  # two people became one voice
    assert sim.compare(original, merged)["speaker_agreement"] == 0.5
    named_wrong = _raw([(0, 4, "Alice"), (5, 9, "Voice 41")])  # a player name must match exactly
    assert sim.compare(original, named_wrong)["speaker_agreement"] == 0.5


def test_sim_needs_the_requested_setup(tmp_path):
    with pytest.raises(SystemExit, match="no start setup"):
        sim._seed(tmp_path / "work", "start", tmp_path / "session", tmp_path / "data")
