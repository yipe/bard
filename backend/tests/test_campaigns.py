"""Campaigns and the player library: one set of people (and their voiceprints) shared by every
campaign; characters, NPC voices, notes and sessions per campaign."""

import json
import threading
import time

import numpy as np
import pytest
from conftest import RecordingBus, tone, write_wav

from troubadour import config
from troubadour.engine import MAIN_CAMPAIGN, Engine, Message, Voice, migrate_state, seated_players, short_id

ALICE, BOB = 200.0, 800.0


def named_voice(eng: Engine, player_id: str, freq: float, character: str | None = None) -> str:
    """As if assigned in the voices panel (a character voice belongs to the open campaign)."""
    v = Voice(id=short_id("v"), label="Voice X", status="named", player_id=player_id, character=character)
    v.campaign = eng.campaign if character else None
    eng.voices[v.id] = v
    eng.matcher.add(v.id, eng.matcher.embed(tone(freq, 3), 0, 3))
    return v.id


def idle(eng: Engine) -> Engine:
    eng.status = "idle"
    return eng


V1_STATE = {
    "players": [
        {"id": "p1", "name": "Nora", "character": "DM", "avatar": None, "color": "#f59e0b", "trained": True, "is_gm": True},
        {"id": "p2", "name": "Jun", "character": "Auggie", "avatar": None, "color": "#3b82f6", "trained": True, "is_gm": False},
    ],
    "voices": [
        {
            "id": "v1",
            "label": "Voice 1",
            "player_id": "p1",
            "character": None,
            "seconds": 9.0,
            "trained": True,
            "vec": [1.0] * 256,
        },
        {
            "id": "v2",
            "label": "Voice 2",
            "player_id": "p2",
            "character": None,
            "seconds": 8.0,
            "trained": True,
            "vec": [0.5] * 256,
        },
        {
            "id": "v3",
            "label": "Voice 3",
            "player_id": "p1",
            "character": "Magenta",
            "seconds": 5.0,
            "trained": False,
            "vec": [0.2] * 256,
        },
    ],
    "next_label": 91,
}


# ------------------------------------------------------------- migration


def test_pre_campaign_state_migrates_without_losing_anything(dirs):
    data, _ = dirs
    data.mkdir(parents=True)
    (data / "state.json").write_text(json.dumps(V1_STATE))
    (data / "voices").mkdir()
    for vid in ("v1", "v2"):
        write_wav(data / "voices" / f"{vid}.wav", tone(ALICE, 0.5))

    eng = Engine(RecordingBus())
    assert eng.campaign == MAIN_CAMPAIGN
    assert [(p.name, p.character, p.is_gm, p.trained) for p in eng.players.values()] == [
        ("Nora", "DM", True, True),
        ("Jun", "Auggie", False, True),
    ]
    assert set(eng.voices) == {"v1", "v2", "v3"}
    assert eng.voices["v3"].campaign == MAIN_CAMPAIGN  # an NPC voice is this campaign's
    assert eng.voices["v1"].campaign is None  # their own voice goes everywhere with them
    assert eng.voices["v1"].sample_audio is not None
    assert np.allclose(eng._persisted_vecs["v2"], 0.5)
    assert eng._next_label == 91
    assert json.loads((data / "state.v1.json").read_text()) == V1_STATE  # the original, kept

    eng._save_state()
    saved = json.loads((data / "state.json").read_text())
    assert saved["version"] == 2
    assert {v["id"] for v in saved["voices"]} == {"v1", "v2", "v3"}
    assert (data / "voices" / "v1.wav").exists()
    assert (data / "voices" / "v2.wav").exists()
    assert json.loads((data / "state.v1.json").read_text()) == V1_STATE  # never overwritten


def test_seated_players_reads_old_and_new_state():
    assert [(p["name"], p["character"]) for p in seated_players(V1_STATE)] == [("Nora", "DM"), ("Jun", "Auggie")]
    assert seated_players(migrate_state(V1_STATE)) == seated_players(V1_STATE)


# ------------------------------------------------------------- switching


def test_a_shared_player_keeps_their_training_in_a_new_campaign(engine):
    jun = engine.add_player("Jun", character="Auggie")
    own = named_voice(engine, jun["id"], ALICE)
    npc = named_voice(engine, jun["id"], BOB, character="Magenta")
    engine.voices[own].trained = engine.players[jun["id"]].trained = True
    other = engine.create_campaign("Morvath")
    idle(engine).open_campaign(other["id"])

    assert engine.players == {}
    assert engine.voices == {}
    assert engine.matcher.names == []  # nobody at this table yet
    seated = engine.seat_player(jun["id"], character="Liss")
    assert (seated["character"], seated["trained"]) == ("Liss", True)
    assert list(engine.voices) == [own]  # his own voice, not the other campaign's NPC
    assert engine.matcher.names == [own]
    assert npc in engine.parked

    engine.open_campaign(MAIN_CAMPAIGN)
    assert engine.players[jun["id"]].character == "Auggie"
    assert set(engine.voices) == {own, npc}
    assert set(engine.matcher.names) == {own, npc}
    engine.open_campaign(other["id"])
    assert engine.players[jun["id"]].character == "Liss"  # each campaign remembers its own seat


def test_switching_keeps_voiceprints_across_restarts(engine, dirs):
    data, _ = dirs
    jun = engine.add_player("Jun")
    own = named_voice(engine, jun["id"], ALICE)
    npc = named_voice(engine, jun["id"], BOB, character="Magenta")
    (data / "voices").mkdir(parents=True, exist_ok=True)
    wav = write_wav(data / "voices" / f"{npc}.wav", tone(BOB, 0.5))
    vec = engine.matcher.vector(npc).copy()
    other = engine.create_campaign("Morvath")
    idle(engine).open_campaign(other["id"])

    fresh = Engine(RecordingBus())  # restart with the parked NPC voice
    assert fresh.campaign == other["id"]
    assert set(fresh.parked) == {own, npc}
    assert wav.exists()  # a parked voice's recording isn't an orphan
    assert np.allclose(fresh._parked_vecs[npc], vec)
    fresh.open_campaign(MAIN_CAMPAIGN)
    assert set(fresh.voices) == {own, npc}
    assert np.allclose(fresh._persisted_vecs[npc], vec)  # handed to the matcher once models load


def test_voice_named_as_a_character_belongs_to_the_open_campaign(engine):
    p = engine.add_player("Nora", is_gm=True)
    m = _say(engine)
    engine.assign_voice(m, p["id"], character="Morvath")
    assert engine.voices[m].campaign == MAIN_CAMPAIGN
    engine.assign_voice(m, p["id"])  # re-pointed to his own voice: shared again
    assert engine.voices[m].campaign is None


def _say(eng: Engine) -> str:
    """A pending voice with one line; returns its id."""
    v = Voice(id=short_id("v"), label="Voice 9", seconds=4.0)
    v.sum = eng.matcher.embed(tone(ALICE, 3), 0, 3) * 4.0
    eng.voices[v.id] = v
    mid = short_id("m")
    eng.messages[mid] = Message(mid, v.id, "hi", 0, 1, 0.5, True)
    return v.id


def test_switching_waits_for_the_session_to_stop(engine):
    other = engine.create_campaign("Morvath")
    with pytest.raises(ValueError, match="Stop the session"):
        engine.open_campaign(other["id"])  # the fixture's session is running
    assert engine.campaign == MAIN_CAMPAIGN
    with pytest.raises(KeyError):
        engine.open_campaign("c-nope")


def test_switching_saves_the_finished_session_with_its_speakers(engine):
    p = engine.add_player("Jun", character="Auggie")
    vid = named_voice(engine, p["id"], ALICE)
    mid = short_id("m")
    engine.messages[mid] = Message(mid, vid, "I attack", 0, 1, 0.9, False)
    sid = engine.live.id
    other = engine.create_campaign("Morvath")
    idle(engine).open_campaign(other["id"])

    assert engine.live is None
    assert engine.messages == {}
    saved = json.loads((engine.sessions_dir() / sid / "transcript.json").read_text())
    assert saved["session"]["campaign"] == MAIN_CAMPAIGN
    assert saved["messages"][0]["speaker"] == "Jun"
    assert saved["messages"][0]["character"] == "Auggie"
    engine.save_transcript(force=True)  # nothing left to rewrite it
    assert json.loads((engine.sessions_dir() / sid / "transcript.json").read_text()) == saved


def test_history_lists_the_open_campaigns_sessions(engine):
    root = engine.sessions_dir()
    for sid, campaign in (("2026-10-01_190000-aaaa", None), ("2026-10-02_190000-bbbb", "c-other")):
        (root / sid).mkdir(parents=True)
        meta = {"id": sid, "started_at": 1, "source": {"kind": "mic", "device": None}, "duration_s": 1}
        (root / sid / "meta.json").write_text(json.dumps(meta | ({"campaign": campaign} if campaign else {})))
    assert [s["id"] for s in engine.list_sessions()] == ["2026-10-01_190000-aaaa"]  # from before campaigns
    engine.campaign = "c-other"
    assert [s["id"] for s in engine.list_sessions()] == ["2026-10-02_190000-bbbb"]


def test_switching_never_overwrites_an_improved_transcript(engine):
    p = engine.add_player("Jun")
    vid = named_voice(engine, p["id"], ALICE)
    mid = short_id("m")
    engine.messages[mid] = Message(mid, vid, "Dallara attacks", 0, 1, 0.9, False)
    folder = engine.save_transcript(force=True)
    # Improve ran on the finished session: it keeps the original and writes its own version.
    (folder / "transcript.orig.json").write_text((folder / "transcript.json").read_text())
    improved = (folder / "transcript.json").read_text().replace("Dallara", "Delara")
    (folder / "transcript.json").write_text(improved)
    idle(engine).open_campaign(engine.create_campaign("Morvath")["id"])
    assert (folder / "transcript.json").read_text() == improved


def test_switching_lets_the_last_spelling_fixes_land_before_saving(engine, monkeypatch):
    p = engine.add_player("Jun")
    vid = named_voice(engine, p["id"], ALICE)
    mid = short_id("m")
    engine.messages[mid] = Message(mid, vid, "Dallara rolls", 0, 1, 0.9, False)
    engine.cleanup_q.put(mid)  # the spelling cleanup is a little behind the finished session

    def cleaner():
        got = engine.cleanup_q.get()
        time.sleep(0.3)
        with engine.lock:
            engine.messages[got].text = "Delara rolls"
        engine.cleanup_q.task_done()

    threading.Thread(target=cleaner, daemon=True).start()
    sid = engine.live.id
    idle(engine).open_campaign(engine.create_campaign("Morvath")["id"])
    saved = json.loads((engine.sessions_dir() / sid / "transcript.json").read_text())
    assert saved["messages"][0]["text"] == "Delara rolls"


def test_switching_waits_for_the_cleanup_only_so_long(engine, monkeypatch):
    monkeypatch.setattr("troubadour.engine.SETTLE_S", 0.2)
    engine.cleanup_q.put("m-stuck")  # nobody will ever take it (the cleanup loop is wedged)
    t0 = time.monotonic()
    idle(engine).open_campaign(engine.create_campaign("Morvath")["id"])
    assert time.monotonic() - t0 < 2


def test_a_session_from_another_campaign_is_identified(engine):
    root = engine.sessions_dir()
    sid = "2026-10-02_190000-bbbb"
    (root / sid).mkdir(parents=True)
    (root / sid / "meta.json").write_text(json.dumps({"id": sid, "campaign": "c-other"}))
    assert engine.session_campaign(sid) == "c-other"
    assert engine.session_campaign("2026-10-03_190000-cccc") is None  # no such session
    assert engine.session_campaign("../../etc") is None


def test_each_campaign_has_its_own_vault(engine):
    assert engine.vault_dir() == config.vault_dir()
    other = engine.create_campaign("Morvath")
    idle(engine).open_campaign(other["id"])
    assert engine.vault_dir() == config.DATA_DIR / "campaigns" / other["id"]


def test_rename_campaign(engine):
    assert engine.rename_campaign(MAIN_CAMPAIGN, "Phandelver") == {"id": MAIN_CAMPAIGN, "name": "Phandelver"}
    assert engine.bus.of("campaigns.updated")[-1]["campaigns"] == [{"id": MAIN_CAMPAIGN, "name": "Phandelver"}]
    with pytest.raises(KeyError):
        engine.rename_campaign("c-nope", "X")


# ------------------------------------------------------------- the library


def test_removing_from_a_campaign_keeps_the_training(engine):
    p = engine.add_player("Jun", character="Auggie")
    vid = named_voice(engine, p["id"], ALICE)
    idle(engine).bench_player(p["id"])
    assert p["id"] not in engine.players
    assert engine.library_json() == [{k: p[k] for k in ("id", "name", "avatar", "color", "trained")}]
    assert vid in engine.parked
    assert vid not in engine.matcher.names
    engine.seat_player(p["id"])
    assert vid in engine.matcher.names


def test_adding_someone_back_restores_their_seat(engine, dirs):
    p = engine.add_player("Jun", character="Auggie", is_gm=True)
    idle(engine).bench_player(p["id"])
    fresh = Engine(RecordingBus())  # remembered across restarts
    seated = fresh.seat_player(p["id"])
    assert (seated["character"], seated["is_gm"]) == ("Auggie", True)
    fresh.bench_player(p["id"])
    assert fresh.seat_player(p["id"], character="Liss", is_gm=False)["character"] == "Liss"  # asked for


def test_first_player_seated_at_an_empty_table_is_the_gm(engine):
    a, b = engine.add_player("Nora"), engine.add_player("Jun")
    other = engine.create_campaign("Morvath")
    idle(engine).open_campaign(other["id"])
    assert engine.seat_player(a["id"])["is_gm"] is True
    assert engine.seat_player(b["id"])["is_gm"] is False


def test_new_players_get_a_color_nobody_in_the_library_has(engine):
    a = engine.add_player("Nora")
    idle(engine).open_campaign(engine.create_campaign("Morvath")["id"])
    assert engine.add_player("Ana")["color"] != a["color"]


def test_state_without_campaigns_still_loads_everyone(dirs):
    data, _ = dirs
    data.mkdir(parents=True)
    state = migrate_state(V1_STATE) | {"campaigns": []}
    (data / "state.json").write_text(json.dumps(state))
    eng = Engine(RecordingBus())
    assert eng.campaign == MAIN_CAMPAIGN
    assert {p["name"] for p in eng.library_json()} == {"Nora", "Jun"}


def test_removing_from_a_campaign_waits_for_the_session_to_stop(engine):
    p = engine.add_player("Jun")
    with pytest.raises(ValueError, match="Stop the session"):
        engine.bench_player(p["id"])
    assert p["id"] in engine.players


def test_deleting_from_the_library_drops_parked_voices_and_recordings(engine, dirs):
    data, _ = dirs
    p = engine.add_player("Jun")
    vid = named_voice(engine, p["id"], ALICE)
    (data / "voices").mkdir(parents=True, exist_ok=True)
    wav = write_wav(data / "voices" / f"{vid}.wav", tone(ALICE, 0.5))
    idle(engine).bench_player(p["id"])
    engine.remove_player(p["id"])
    assert engine.library_json() == []
    assert engine.parked == {}
    assert not wav.exists()
    assert engine.bus.of("library.updated")[-1] == []
    with pytest.raises(KeyError):
        engine.remove_player(p["id"])
