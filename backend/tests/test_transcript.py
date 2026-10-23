import json

import numpy as np
import pytest
from conftest import speak
from fastapi.testclient import TestClient
from test_engine import ALICE, BOB, named_voice, say

from troubadour import config, server
from troubadour.audio import wav_bytes
from troubadour.engine import _audio_seconds
from troubadour.transcript import build, clock, filler_only, markdown, without_fillers


def record_with(text: str, **message) -> dict:
    players = [
        {"id": "p1", "name": "Nora", "character": "", "is_gm": True},
        {"id": "p2", "name": "Priya", "character": "Pip", "is_gm": False},
    ]
    voices = [
        {"id": "v1", "label": "Voice 1", "status": "named", "player_id": "p1", "character": None, "trained": False},
        {"id": "v2", "label": "Voice 2", "status": "named", "player_id": "p2", "character": None, "trained": True},
        {"id": "v3", "label": "Voice 3", "status": "pending", "player_id": None, "character": None, "trained": False},
    ]
    m = {"id": "m1", "voice_id": "v1", "text": text, "t_start": 75.0, "t_end": 78.0, "confidence": 0.9, "provisional": False}
    m |= message
    meta = {
        "id": "2026-10-08_140000-abcd",
        "started_at": 1_791_000_000,
        "source": {"kind": "mic", "device": None},
        "duration_s": 3725,
    }
    return build(meta, players, voices, [m])


def test_markdown_header_and_line():
    md = markdown(record_with("You take 12 slashing damage"))
    assert md.startswith("# Troubadour session · ")
    assert "- Length: 1:02:05 · 1 lines\n" in md
    assert "Nora (GM), Priya as Pip" in md
    assert "**01:15 · Nora (GM):** You take 12 slashing damage" in md
    assert "Mechanics" not in md


def test_old_lines_mechanics_are_not_kept():
    old = {"kind": "damage", "start": 9, "end": 27, "value": "12", "note": None, "source": "regex"}
    rec = record_with("You take 12 slashing damage", annotations=[old])
    assert "annotations" not in rec["messages"][0]
    assert "mechanics" not in rec["session"]


def test_markdown_escapes_transcript_text():
    md = markdown(record_with("roll *two* d20s _now_"))
    assert r"roll \*two\* d20s \_now\_" in md


def test_speaker_names_follow_the_ui():
    rec = record_with("hi", voice_id="v2", provisional=True)
    assert rec["messages"][0]["speaker"] == "Priya"
    assert rec["messages"][0]["character"] == "Pip"
    assert "**01:15 · Priya as Pip (?):** hi" in markdown(rec)
    pending = record_with("hi", voice_id="v3")
    assert pending["messages"][0]["speaker"] == "Voice 3"
    assert record_with("hi", voice_id=None)["messages"][0]["speaker"] == "Unknown"


@pytest.mark.parametrize(("s", "out"), [(0, "00:00"), (75.9, "01:15"), (3600, "1:00:00"), (4 * 3600 + 5, "4:00:05")])
def test_clock(s, out):
    assert clock(s) == out


def test_record_is_json_serializable():
    json.dumps(record_with("hi"))


# ------------------------------------------------------------- autosave


def test_autosave_writes_files_and_skips_when_unchanged(engine, dirs):
    say(engine, ALICE, 2.0)
    folder = engine.save_transcript()
    assert folder is not None
    assert {p.name for p in folder.iterdir()} == {"transcript.json", "transcript.md", "meta.json"}
    assert "12 slashing damage" in (folder / "transcript.md").read_text()
    assert engine.save_transcript() is None  # nothing changed
    p = engine.add_player("Alice")
    engine.assign_voice(next(iter(engine.messages.values())).voice_id, p["id"])
    assert engine.save_transcript() is not None  # speaker names changed
    saved = json.loads((folder / "transcript.json").read_text())
    assert saved["messages"][0]["speaker"] == "Alice"


def test_no_transcript_before_any_line(engine):
    assert engine.save_transcript(force=True) is None


def test_starting_a_new_session_saves_the_old_one_first(engine, monkeypatch):
    monkeypatch.setattr("troubadour.engine.keep_awake", lambda: None)
    monkeypatch.setattr("troubadour.engine.MicSource.start", lambda self, sink, on_end: None)
    monkeypatch.setattr(engine, "_segment_loop", lambda sess: None)
    monkeypatch.setattr(engine, "_worker_loop", lambda sess: None)
    say(engine, ALICE, 2.0)
    say(engine, BOB, 2.0)
    old_id = engine.live.id
    engine.start({"kind": "mic", "device": None})
    assert engine.messages == {}
    saved = json.loads((engine.sessions_dir() / old_id / "transcript.json").read_text())
    assert saved["session"]["lines"] == 2
    assert [s["id"] for s in engine.list_sessions()] == [old_id]


def test_finished_session_is_saved(engine):
    say(engine, ALICE, 2.0)
    engine._finish(engine.live)
    assert (engine.sessions_dir() / engine.live.id / "transcript.md").exists()


@pytest.mark.parametrize("sid", ["../state", "2026-10-08_140000-abcd/../../x", "nope", ""])
def test_session_file_rejects_bad_ids(engine, sid):
    with pytest.raises(KeyError):
        engine.session_file(sid, "md")


def test_list_sessions_skips_corrupt_meta(engine, dirs):
    folder = engine.sessions_dir() / "2026-10-08_140000-abcd"
    folder.mkdir(parents=True)
    (folder / "meta.json").write_text("{not json")
    assert engine.list_sessions() == []


# ------------------------------------------------------------- API


@pytest.fixture
def api_engine(engine, monkeypatch):
    monkeypatch.setattr(server, "engine", engine)
    return TestClient(server.app, base_url="http://127.0.0.1:8765")


def test_current_transcript_download(api_engine, engine):
    p = engine.add_player("Alice")
    named_voice(engine, p["id"], ALICE)
    t0, t1 = speak(engine, ALICE, 2.0)
    engine._process_utterance(engine.live, t0, t1)
    r = api_engine.get("/api/transcript.md")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/markdown")
    assert f'filename="troubadour-{engine.live.id}.md"' in r.headers["content-disposition"]
    assert "**00:00 · Alice:** I deal 12 slashing damage" in r.text
    assert api_engine.get("/api/transcript.json").json()["messages"][0]["speaker"] == "Alice"
    assert api_engine.get("/api/transcript.txt").status_code == 422


def test_current_transcript_404_without_session(api_engine, engine):
    engine.live = None
    assert api_engine.get("/api/transcript.md").status_code == 404


def test_saved_sessions_endpoints(api_engine, engine):
    say(engine, ALICE, 2.0)
    engine.save_transcript(force=True)
    (listing,) = api_engine.get("/api/sessions").json()
    assert listing["id"] == engine.live.id
    assert listing["lines"] == 1
    r = api_engine.get(f"/api/sessions/{listing['id']}/transcript.json")
    assert r.status_code == 200
    assert r.json()["session"]["id"] == listing["id"]
    assert api_engine.get("/api/sessions/..%2Fstate/transcript.json").status_code == 404
    assert api_engine.get("/api/sessions/2026-01-01_000000-ffff/transcript.md").status_code == 404


def test_saved_transcripts_hide_old_filler_lines_and_mechanics(api_engine, engine):
    say(engine, ALICE, 2.0)
    folder = engine.save_transcript(force=True)
    old = json.loads((folder / "transcript.json").read_text())  # as an older version saved it:
    real = old["messages"][0]
    old["messages"].append(real | {"id": "m-um", "t_start": 9.0, "text": "Um..."})
    old["session"] |= {"lines": 2, "mechanics": 1}
    (folder / "transcript.json").write_text(json.dumps(old))
    (folder / "transcript.md").write_text("## Mechanics\n")
    sid = old["session"]["id"]
    record = api_engine.get(f"/api/sessions/{sid}/transcript.json").json()
    assert [m["id"] for m in record["messages"]] == [real["id"]]
    assert record["session"]["lines"] == 1
    md = api_engine.get(f"/api/sessions/{sid}/transcript.md").text
    assert "I deal 12 slashing damage" in md
    assert "Um" not in md
    assert "Mechanics" not in md


@pytest.mark.parametrize(
    ("text", "filler"),
    [
        ("Um...", True),
        ("Uh, hmm.", True),
        ("Umm", True),
        ("Um-", True),
        ("Um, I attack.", False),
        ("Mm-hmm.", False),  # yes
        ("Uh-huh", False),
        ("Oh!", False),
        ("...", False),  # not filler; empty text is handled elsewhere
    ],
)
def test_filler_only(text, filler):
    assert filler_only(text) is filler


def test_records_drop_filler_lines():
    rec = build({"id": "x", "started_at": 0, "source": None, "duration_s": 1}, [], [], [
        {"id": "a", "voice_id": None, "text": "Um...", "t_start": 0, "t_end": 1, "confidence": 1, "provisional": False},
        {"id": "b", "voice_id": None, "text": "I attack.", "t_start": 1, "t_end": 2, "confidence": 1, "provisional": False},
    ])  # fmt: skip
    assert [m["id"] for m in rec["messages"]] == ["b"]
    assert rec["session"]["lines"] == 1
    assert without_fillers(rec) is rec


def test_data_dir_is_isolated(dirs):
    assert str(config.DATA_DIR).startswith(str(dirs[0].parent))


def _saved(engine, sid, started, text, seconds=1.0):
    folder = engine.sessions_dir() / sid
    folder.mkdir(parents=True)
    record = {
        "version": 1,
        "session": {
            "id": sid,
            "started_at": started,
            "source": {"kind": "mic", "device": None},
            "duration_s": seconds,
            "lines": 1,
            "campaign": engine.campaign,
        },
        "players": [{"id": "p", "name": "Nora", "character": "", "is_gm": True, "color": "#fff"}],
        "voices": [],
        "messages": [{
            "id": "m", "t_start": 0.0, "t_end": 0.5, "speaker": "Nora", "player_id": "p",
            "character": None, "voice_id": None, "provisional": False, "confidence": 1,
            "text": text, "fixes": [], "level_db": None,
        }],
    }
    (folder / "transcript.json").write_text(json.dumps(record))
    (folder / "meta.json").write_text(json.dumps(record["session"]))
    (folder / "transcript.md").write_text("x")
    (folder / "audio.wav").write_bytes(wav_bytes(np.zeros(int(16000 * seconds), np.float32)))
    return folder


def test_merge_appends_the_later_session(engine):
    early = "2026-10-08_120000-aaaa"
    late = "2026-10-08_120200-bbbb"
    _saved(engine, early, 1_000, "First half.")
    _saved(engine, late, 1_000 + 70, "Second half.")  # 70s after the first started; first is 1s
    out = engine.merge_sessions([late, early])
    assert out["id"] == early
    assert not (engine.sessions_dir() / late).exists()
    saved = json.loads((engine.sessions_dir() / early / "transcript.json").read_text())
    assert [m["text"] for m in saved["messages"]] == ["First half.", "Second half."]
    assert saved["messages"][1]["t_start"] == 1.0
    assert saved["session"]["merged_from"] == [late]
    assert _audio_seconds(engine.sessions_dir() / early / "audio.wav") == 2.0


def test_merge_refuses_a_live_session(engine):
    early = "2026-10-08_120000-aaaa"
    _saved(engine, early, 1_000, "First half.")
    engine.live = type("S", (), {"id": early})()
    with pytest.raises(ValueError, match="live"):
        engine.merge_sessions([early, "2026-10-08_120200-bbbb"])


def test_record_keeps_player_colors_for_the_viewer():
    players = [{"id": "p1", "name": "Nora", "character": "", "is_gm": True, "color": "#f59e0b"}]
    assert (
        build({"id": "x", "started_at": 1, "source": None, "duration_s": 0}, players, [], [])["players"][0]["color"] == "#f59e0b"
    )
