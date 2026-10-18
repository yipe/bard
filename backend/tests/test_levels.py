"""Input level and recording quality, on synthetic audio with known answers."""

import json

import numpy as np
import pytest
from conftest import write_wav
from fastapi.testclient import TestClient

from troubadour import config, levels, server
from troubadour.config import SR
from troubadour.transcript import build

RNG = np.random.default_rng(7)


def talk(seconds: float, level_db: float, freq: float = 300.0) -> np.ndarray:
    t = np.arange(int(seconds * SR)) / SR
    return (10 ** (level_db / 20) * np.sqrt(2) * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def room(seconds: float, level_db: float) -> np.ndarray:
    return (10 ** (level_db / 20) * RNG.standard_normal(int(seconds * SR))).astype(np.float32)


def table(speech_db=-20.0, noise_db=-65.0, seconds=20) -> np.ndarray:
    """Talking half the time over a quiet room."""
    parts = [np.concatenate([talk(1, speech_db), room(1, noise_db)]) for _ in range(seconds // 2)]
    return np.concatenate(parts)


@pytest.mark.parametrize(
    ("level", "clip", "zone"),
    [
        (-70, False, "silent"),
        (-40, False, "quiet"),
        (-20, False, "good"),
        (-8, False, "hot"),
        (-2, False, "clip"),
        (-20, True, "clip"),
    ],
)
def test_zones(level, clip, zone):
    assert levels.zone(level, clip) == zone


def test_a_well_set_up_mic_grades_good():
    q = levels.quality(table())
    assert -24 <= q["speech_db"] <= -18
    assert q["snr_db"] > 30
    assert set(q["grades"].values()) == {"good"}


def test_quiet_rumbly_clipping_audio_gets_the_right_hints():
    quiet = levels.quality(table(speech_db=-42))
    assert quiet["grades"]["speech"] == "poor"
    assert any("raise the mic gain" in h for h in quiet["hints"])
    rumble = levels.quality(table() + talk(20, -18, freq=50.0))  # a 50 Hz hum under everything
    assert rumble["rumble_pct"] > 50
    assert any("high-pass" in h for h in rumble["hints"])
    hot = table()
    hot[::200] = 1.0
    assert levels.quality(hot)["grades"]["clipping"] == "poor"


def test_meter_emits_levels_and_quality(monkeypatch):
    clock = iter(np.arange(0, 100, 0.05))
    monkeypatch.setattr(levels.time, "monotonic", lambda: next(clock))
    events = []
    meter = levels.LevelMeter(lambda t, d: events.append((t, d)))
    audio = table(seconds=6)
    for i in range(0, len(audio) - 512, 512):
        meter.feed(audio[i : i + 512])
    meter.stop()
    kinds = {t for t, _ in events}
    assert kinds == {"audio.level", "audio.quality"}
    assert {d["zone"] for t, d in events if t == "audio.level"} >= {"good"}
    assert events[-1] == ("audio.level", {"source": "session", "db": None, "peak_db": None, "clipping": False, "zone": "off"})


def lines(speakers: dict[str, float], each: int = 12) -> tuple[np.ndarray, list[dict]]:
    audio, msgs, t = [], [], 0.0
    for _ in range(each):
        for name, level in speakers.items():
            audio += [talk(2, level), room(1, -65)]
            msgs.append({"id": f"m{len(msgs)}", "t_start": t, "t_end": t + 2, "speaker": name, "player_id": name})
            t += 3
    return np.concatenate(audio), msgs


def test_report_says_change_the_microphone_when_one_side_is_quiet():
    audio, msgs = lines({"Nora": -18, "Jun": -20, "Omar": -33})
    rep = levels.report(audio, msgs, minute_s=30)
    assert [s["speaker"] for s in rep["speakers"]] == ["Nora", "Jun", "Omar"]
    assert rep["spread_db"] == pytest.approx(15, abs=1)
    assert rep["verdict"]["kind"] == "microphone"
    assert "Omar comes through 15 dB quieter than Nora" in rep["verdict"]["reasons"][0]


def test_report_says_settings_when_everyone_is_quiet():
    audio, msgs = lines({"Nora": -38, "Jun": -40})
    rep = levels.report(audio, msgs, minute_s=30)
    assert rep["verdict"]["kind"] == "settings"
    assert "raise the gain" in " ".join(rep["verdict"]["reasons"])


def test_report_good():
    audio, msgs = lines({"Nora": -18, "Jun": -21})
    assert levels.report(audio, msgs, minute_s=30)["verdict"]["kind"] == "good"


# ------------------------------------------------------------- engine + API


def test_lines_carry_their_loudness(engine):
    from test_engine import ALICE, say

    m = say(engine, ALICE)
    assert m["level_db"] is not None
    assert -20 < m["level_db"] < 0  # conftest tones are 0.3 amplitude: about -13 dBFS


def test_mic_test_is_refused_during_a_session(engine):
    engine.status = "running"
    with pytest.raises(ValueError, match="Stop the session"):
        engine.mic_test(None)


def test_quality_endpoint_measures_and_caches(engine, dirs, monkeypatch):
    monkeypatch.setattr(server, "engine", engine)
    sid = "2026-10-08_193401-eebf"
    folder = config.DATA_DIR / "sessions" / sid
    folder.mkdir(parents=True)
    audio, msgs = lines({"Nora": -18, "Omar": -33})
    write_wav(folder / "audio.wav", audio)
    players = [{"id": "p1", "name": "Nora", "character": "DM", "is_gm": True, "color": "#000"}]
    rec = build({"id": sid, "started_at": 1, "source": {"kind": "mic", "device": "Yeti GX"}, "duration_s": len(audio) / SR}, players, [],
                [{"id": m["id"], "t_start": m["t_start"], "t_end": m["t_end"], "voice_id": None, "provisional": False, "confidence": 1, "text": "hi"} for m in msgs])  # fmt: skip
    for r, m in zip(rec["messages"], msgs, strict=True):
        r["speaker"], r["player_id"] = m["speaker"], m["player_id"]
    (folder / "transcript.json").write_text(json.dumps(rec))
    (folder / "meta.json").write_text(json.dumps(rec["session"]))
    client = TestClient(server.app, base_url="http://127.0.0.1:8765")
    rep = client.get(f"/api/sessions/{sid}/quality").json()
    assert rep["mic"] == "Yeti GX"
    assert rep["verdict"]["kind"] == "microphone"
    assert (folder / "quality.json").exists()
    assert client.get("/api/sessions").json()[0]["quality"] == "microphone"
    assert client.get("/api/sessions/2026-01-01_000000-ffff/quality").status_code == 404
