"""Engine behaviour without models: voices are sine tones (see conftest.FakeEmbedder)."""

import json
import threading
import time

import numpy as np
import pytest
from conftest import NullSource, RecordingBus, speak, tone, write_wav

from troubadour import asr, config, levels
from troubadour.engine import (
    PANEL_S,
    STOP,
    Engine,
    LiveSession,
    Message,
    Voice,
    keep_awake,
    sample_path,
    short_id,
)
from troubadour.transcript import utf16_offset, utf16_to_py
from troubadour.voices import normalize

ALICE, BOB, CARA = 200.0, 800.0, 2500.0


def say(eng: Engine, freq: float, seconds: float = 2.0) -> dict:
    """One utterance through the real worker path (Whisper is stubbed); returns the message JSON."""
    t0, t1 = speak(eng, freq, seconds)
    eng._process_utterance(eng.live, t0, t1)
    return eng.bus.of("message.created")[-1]


def named_voice(eng: Engine, player_id: str, freq: float, character: str | None = None) -> str:
    """A named voice for player_id built from a tone, as if assigned in the panel."""
    v = Voice(id=short_id("v"), label="Voice X", status="named", player_id=player_id, character=character)
    eng.voices[v.id] = v
    a = tone(freq, 3)
    eng.matcher.add(v.id, eng.matcher.embed(a, 0, 3))
    return v.id


# ------------------------------------------------------------- helpers


def test_utf16_offset_counts_astral_chars_as_two():
    text = "🐉 deals 12 damage"
    assert utf16_offset(text, text.index("12")) == text.index("12") + 1  # the dragon is 2 UTF-16 units
    assert utf16_to_py(text, utf16_offset(text, 9)) == 9


@pytest.mark.parametrize("name", ["../data/state.json", "/etc/passwd", "sub/x.wav", "notes.txt", "missing.wav", ".."])
def test_sample_path_refuses_anything_outside_samples(dirs, name):
    _, samples = dirs
    (samples / "notes.txt").write_text("x")
    with pytest.raises(KeyError):
        sample_path(name)


def test_sample_path_accepts_a_sample(dirs):
    _, samples = dirs
    path = write_wav(samples / "ok.wav", tone(200, 0.5))
    assert sample_path("ok.wav") == path.resolve()


# ------------------------------------------------------------- discovery


def test_unknown_voice_surfaces_after_panel_seconds(engine):
    m1 = say(engine, ALICE, 2.0)
    assert m1["provisional"] is True
    assert not engine.voices[m1["voice_id"]].visible  # 2 s < PANEL_S: not in the panel yet
    assert engine.bus.of("voice.upserted") == []
    m2 = say(engine, ALICE, 2.0)
    assert m2["voice_id"] == m1["voice_id"]  # joined the same pending group
    voice = engine.voices[m1["voice_id"]]
    assert voice.seconds >= PANEL_S
    assert voice.label == "Voice 1"
    assert engine.bus.of("voice.upserted")[-1]["id"] == voice.id


def test_different_speakers_get_different_groups(engine):
    a, b = say(engine, ALICE, 3.5), say(engine, BOB, 3.5)
    assert a["voice_id"] != b["voice_id"]


def test_lines_carry_no_mechanics(engine):
    assert "annotations" not in say(engine, ALICE)


def test_filler_only_utterances_make_no_line(engine, monkeypatch):
    monkeypatch.setattr("troubadour.asr.transcribe", lambda audio: "Um...")
    t0, t1 = speak(engine, ALICE, 2.0)
    engine._process_utterance(engine.live, t0, t1)
    assert engine.messages == {}
    assert engine.bus.of("message.created") == []


def test_empty_transcript_creates_nothing(engine, monkeypatch):
    monkeypatch.setattr("troubadour.asr.transcribe", lambda audio: "")
    t0, t1 = speak(engine, ALICE, 2)
    engine._process_utterance(engine.live, t0, t1)
    assert engine.messages == {}
    assert all(v.seconds == 0 for v in engine.voices.values())


def test_abandoned_session_cannot_write_messages(engine):
    stale = engine.live
    t0, t1 = speak(engine, ALICE, 2)
    engine.live = LiveSession(NullSource(), {"kind": "mic", "device": None})  # a newer session took over
    engine._process_utterance(stale, t0, t1)
    assert engine.messages == {}


def test_message_ids_are_unique():
    assert len({short_id("m") for _ in range(10_000)}) == 10_000


# ------------------------------------------------------------- naming


def test_assign_names_a_pending_voice_and_learns_it(engine):
    p = engine.add_player("Alice")
    m = say(engine, ALICE, 3.5)
    result = engine.assign_voice(m["voice_id"], p["id"])
    assert result["merged_into"] is None
    assert result["voice"]["status"] == "named"
    # From now on Alice is recognized directly, not provisional.
    m2 = say(engine, ALICE, 2.0)
    assert m2["voice_id"] == m["voice_id"]
    assert m2["provisional"] is False


def test_naming_a_second_group_the_same_merges_in_one_batch(engine):
    p = engine.add_player("Alice")
    first = say(engine, ALICE, 3.5)
    engine.assign_voice(first["voice_id"], p["id"])
    # A second pending group for her (e.g. she sounded different) with two lines.
    group = Voice(id=short_id("v"), label="Voice 9", sum=normalize(np.ones(256)) * 3)
    engine.voices[group.id] = group
    for _ in range(2):
        mid = short_id("m")
        engine.messages[mid] = Message(mid, group.id, "hi", 0, 1, 0.5, True)
    engine.bus.events.clear()
    result = engine.assign_voice(group.id, p["id"])
    assert result["merged_into"] == first["voice_id"]
    assert group.id not in engine.voices
    assert engine.bus.of("voice.removed") == [{"id": group.id, "merged_into": first["voice_id"]}]
    (batch,) = engine.bus.of("messages.updated")
    assert len(batch) == 2
    assert all(patch["voice_id"] == first["voice_id"] for patch in batch)


def test_assign_with_a_character_keeps_a_separate_voice(engine):
    p = engine.add_player("Nora", is_gm=True)
    a = say(engine, ALICE, 3.5)
    engine.assign_voice(a["voice_id"], p["id"])
    b = say(engine, BOB, 3.5)
    result = engine.assign_voice(b["voice_id"], p["id"], character="Morvath")
    assert result["merged_into"] is None
    assert engine.voices[b["voice_id"]].character == "Morvath"


def test_assign_unknown_player_or_voice_raises(engine):
    m = say(engine, ALICE, 3.5)
    with pytest.raises(KeyError):
        engine.assign_voice(m["voice_id"], "p-nobody")
    with pytest.raises(KeyError):
        engine.assign_voice("v-nobody", engine.add_player("A")["id"])


def test_unassign_trained_voice_deletes_recording_and_untrains_player(engine, dirs):
    data, _ = dirs
    p = engine.add_player("Alice")
    vid = named_voice(engine, p["id"], ALICE)
    engine.voices[vid].trained = True
    engine.players[p["id"]].trained = True
    wav = write_wav(_mkdir(data / "voices") / f"{vid}.wav", tone(ALICE, 1))
    out = engine.unassign_voice(vid)
    assert out["status"] == "pending"
    assert out["trained"] is False
    assert not wav.exists()
    assert engine.players[p["id"]].trained is False


def test_remove_player_returns_their_voices_to_pending(engine):
    p = engine.add_player("Alice")
    vid = named_voice(engine, p["id"], ALICE)
    engine.remove_player(p["id"])
    assert engine.voices[vid].status == "pending"
    assert vid not in engine.matcher.names
    assert engine.bus.of("player.removed") == [{"id": p["id"]}]


# ------------------------------------------------------------- one line, assigned by hand


def test_assigning_one_line_moves_only_that_line_and_refine_leaves_it(engine):
    alice, bob = engine.add_player("Alice"), engine.add_player("Bob")
    a_vid = named_voice(engine, alice["id"], ALICE)
    m1, m2 = say(engine, ALICE, 2.0), say(engine, ALICE, 2.0)  # both heard as Alice
    engine.bus.events.clear()
    out = engine.assign_message(m2["id"], bob["id"])  # but Bob said the second one
    b_vid = out["voice"]["id"]
    assert (out["voice"]["player_id"], out["voice"]["status"]) == (bob["id"], "named")
    assert engine.bus.of("messages.updated") == [[{"id": m2["id"], "voice_id": b_vid, "confidence": 1.0, "provisional": False}]]
    engine._refine()
    assert engine.messages[m2["id"]].voice_id == b_vid  # pinned: Alice's better score doesn't take it back
    assert engine.messages[m1["id"]].voice_id == a_vid  # the other line is untouched


def test_a_hand_assigned_line_teaches_the_voice_and_moving_it_again_takes_that_back(engine):
    p, q = engine.add_player("Nora", is_gm=True), engine.add_player("Felix")
    m = say(engine, CARA, 2.0)
    morvath = engine.assign_message(m["id"], p["id"], character="Morvath")["voice"]["id"]
    assert engine.voices[morvath].character == "Morvath"
    assert morvath in engine.matcher.names  # a new voice, learned from that one line
    assert say(engine, CARA, 2.0)["voice_id"] == morvath  # the next line in that voice is Morvath's
    engine.assign_message(m["id"], q["id"])  # oops, it was Felix
    assert engine.messages[m["id"]].voice_id != morvath
    assert morvath not in engine.matcher.names  # Morvath's print was only that line: gone again


def test_assigning_a_line_to_an_unknown_player_or_line_raises(engine):
    m = say(engine, ALICE, 2.0)
    with pytest.raises(KeyError):
        engine.assign_message(m["id"], "p-nobody")
    with pytest.raises(KeyError):
        engine.assign_message("m-nobody", engine.add_player("A")["id"])


# ------------------------------------------------------------- choosing among one player's voices


def test_character_tiebreak_keeps_previous_on_near_tie(engine):
    p = engine.add_player("Nora")
    normal = named_voice(engine, p["id"], ALICE)
    morvath = named_voice(engine, p["id"], ALICE + 5, character="Morvath")  # nearly the same voice
    names = engine.matcher.names
    scores = np.zeros(len(names))
    scores[names.index(normal)], scores[names.index(morvath)] = 0.80, 0.83  # Morvath wins by < margin
    assert engine._character_tiebreak(scores, morvath, previous=normal) == normal
    assert engine._character_tiebreak(scores, morvath, previous=None) == normal  # default: normal voice
    scores[names.index(morvath)] = 0.95  # clear win
    assert engine._character_tiebreak(scores, morvath, previous=normal) == morvath


# ------------------------------------------------------------- refine


def test_refine_relabels_in_one_batch(engine):
    m1, m2 = say(engine, ALICE, 1.6), say(engine, ALICE, 1.6)
    p = engine.add_player("Alice")
    vid = named_voice(engine, p["id"], ALICE)
    engine.bus.events.clear()
    engine._refine()
    (batch,) = engine.bus.of("messages.updated")
    assert {x["id"] for x in batch} == {m1["id"], m2["id"]}
    assert all(x["voice_id"] == vid for x in batch)
    engine.bus.events.clear()
    engine._refine()
    assert engine.bus.of("messages.updated") == []  # nothing changed: no event


# ------------------------------------------------------------- audio monitor


def test_audio_monitor_switches_the_live_meter_off_and_on(engine):
    engine.live.meter = levels.LevelMeter(engine.bus.emit)
    engine.set_audio_monitor(False)
    assert engine.live.meter is None  # no more per-frame level/quality work
    assert engine.bus.of("audio.level")[-1]["zone"] == "off"
    assert engine.bus.of("session.updated")[-1]["audio_monitor"] is False
    engine.set_audio_monitor(True)
    assert engine.live.meter is not None
    assert engine.session_json()["audio_monitor"] is True


def test_audio_monitor_setting_applies_to_the_next_session(engine, monkeypatch):
    monkeypatch.setattr("troubadour.engine.subprocess.Popen", lambda cmd, **kw: type("P", (), {"terminate": lambda s: None})())
    monkeypatch.setattr("troubadour.engine.MicSource.start", lambda self, sink, on_end: None)
    monkeypatch.setattr(engine, "_segment_loop", lambda sess: None)
    monkeypatch.setattr(engine, "_worker_loop", lambda sess: None)
    engine.status, engine.live = "idle", None
    engine.start({"kind": "mic", "device": None})
    assert engine.live.meter is not None  # on by default
    engine.set_audio_monitor(False)
    engine.start({"kind": "mic", "device": None})
    assert engine.live.meter is None


# ------------------------------------------------------------- sessions


def test_start_with_missing_mic_changes_nothing(engine, monkeypatch):
    engine.status, engine.live = "idle", None
    say_before = len(engine.messages)

    def broken_start(self, sink, on_end):
        raise RuntimeError("Error querying device -1")

    monkeypatch.setattr("troubadour.audio.MicSource.start", broken_start)
    with pytest.raises(ValueError, match="Couldn't open mic"):
        engine.start({"kind": "mic", "device": "Nope"})
    assert engine.status == "idle"
    assert len(engine.messages) == say_before


def test_session_ends_when_models_fail_to_load(engine):
    # Started while the models were still loading, then loading failed: the session must not
    # stay "running" with nobody consuming its frames.
    engine.ready.clear()
    sess = engine.live
    threads = [threading.Thread(target=t, args=(sess,), daemon=True) for t in (engine._segment_loop, engine._worker_loop)]
    for t in threads:
        t.start()
    time.sleep(0.2)
    assert engine.status == "running"
    engine.load_error = "Models failed to load"
    for t in threads:
        t.join(5)
    assert not any(t.is_alive() for t in threads)
    assert engine.status == "idle"
    assert sess.source.stopped


def test_utterance_failures_surface_once(engine, monkeypatch):
    def broken(audio):
        raise RuntimeError("whisper exploded")

    monkeypatch.setattr("troubadour.asr.transcribe", broken)
    sess = engine.live
    for _ in range(2):
        sess.utt_q.put(speak(engine, ALICE, 2))
    sess.utt_q.put(STOP)
    engine._worker_loop(sess)
    errors = engine.bus.of("error")
    assert len(errors) == 1
    assert "whisper exploded" in errors[0]["message"]
    assert engine.status == "idle"  # the session still ends normally


def test_keep_awake_survives_a_spawn_failure(monkeypatch):
    def boom(*args, **kwargs):
        raise OSError("fork failed")

    monkeypatch.setattr("troubadour.engine.shutil.which", lambda name: "/usr/bin/caffeinate")
    monkeypatch.setattr("troubadour.engine.subprocess.Popen", boom)
    assert keep_awake() is None


def test_start_refused_when_models_failed(engine):
    engine.load_error = "Models failed to load"
    with pytest.raises(ValueError, match="Models failed"):
        engine.start({"kind": "file", "name": "x.wav", "speed": 1})
    with pytest.raises(ValueError, match="Models failed"):
        engine.train(engine.add_player("A")["id"])


def test_session_json_reports_training_and_error(engine):
    p = engine.add_player("A")
    engine.train_job = type("Job", (), {"player_id": p["id"]})()
    s = engine.session_json()
    assert s["training"] == {"player_id": p["id"]}
    assert s["error"] is None


def test_session_json_reports_whisper_loading_ready_and_failed(engine):
    engine.ready.clear()
    assert engine.session_json()["asr"]["state"] == "loading"
    engine.ready.set()
    assert engine.session_json()["asr"]["state"] == "ready"
    engine.load_error = "Models failed to load"
    assert engine.session_json()["asr"]["state"] == "error"


def test_second_training_is_refused(engine, monkeypatch):
    monkeypatch.setattr(engine, "_train_run", lambda job: None)  # keep the job "running"
    p = engine.add_player("A")
    engine.train(p["id"], 10)
    with pytest.raises(ValueError, match="Already training"):
        engine.train(p["id"], 10)
    engine.cancel_training()
    assert engine.train_job.cancelled


# ------------------------------------------------------------- persistence


def _mkdir(path):
    path.mkdir(parents=True, exist_ok=True)
    return path


def test_state_round_trips(engine, dirs):
    data, _ = dirs
    p = engine.add_player("Alice", character="Elara")
    vid = named_voice(engine, p["id"], ALICE)
    engine._save_state()
    fresh = Engine(RecordingBus())
    assert fresh.players[p["id"]].character == "Elara"
    assert fresh.voices[vid].status == "named"
    assert vid in fresh._persisted_vecs  # handed to the matcher once models load
    json.loads((data / "state.json").read_text())
    assert not list(data.glob("*.tmp"))  # atomic write leaves no temp files


def test_saving_before_models_load_keeps_voiceprints(engine, dirs):
    # Regression: saving while the matcher was still None wrote "voices": [].
    p = engine.add_player("Alice")
    vid = named_voice(engine, p["id"], ALICE)
    engine._save_state()
    loading = Engine(RecordingBus())  # matcher is None until models load
    assert loading.matcher is None
    loading.add_player("Bob")  # any edit saves state
    saved = json.loads((config.DATA_DIR / "state.json").read_text())
    assert [v["id"] for v in saved["voices"]] == [vid]


def test_orphan_recordings_are_cleaned_on_load(engine, dirs):
    data, _ = dirs
    engine._save_state()
    orphan = write_wav(_mkdir(data / "voices") / "v-gone.wav", tone(ALICE, 0.5))
    Engine(RecordingBus())
    assert not orphan.exists()


def test_voice_sample_prefers_training_audio(engine):
    p = engine.add_player("A")
    vid = named_voice(engine, p["id"], ALICE)
    engine.voices[vid].sample_audio = tone(ALICE, 20)
    wav = engine.voice_sample(vid)
    assert wav[:4] == b"RIFF"
    assert len(wav) < 20 * config.SR * 2  # capped at SAMPLE_MAX_S


def test_sample_url_is_stable_for_a_training_clip_and_changes_on_retrain(engine):
    p = engine.add_player("A")
    vid = named_voice(engine, p["id"], ALICE)
    v = engine.voices[vid]
    v.sample_audio = tone(ALICE, 5)
    url = v.to_json()["sample_url"]
    v.spans.append((0.0, 2.0))  # another line from her must not change the URL (the UI's player would remount)
    assert v.to_json()["sample_url"] == url
    v.sample_rev += 1  # a retrain does
    assert v.to_json()["sample_url"] != url


def test_voice_without_audio_has_no_sample(engine):
    p = engine.add_player("A")
    vid = named_voice(engine, p["id"], ALICE)
    assert engine.voices[vid].to_json()["sample_url"] is None
    with pytest.raises(KeyError):
        engine.voice_sample(vid)


# ------------------------------------------------------------- long sessions


def test_session_holds_a_sleep_assertion_until_it_ends(engine, monkeypatch):
    spawned = []

    class FakeProc:
        def __init__(self, cmd, **kwargs):
            self.cmd, self.terminated = cmd, False
            spawned.append(self)

        def terminate(self):
            self.terminated = True

    monkeypatch.setattr("troubadour.engine.shutil.which", lambda name: "/usr/bin/caffeinate")
    monkeypatch.setattr("troubadour.engine.subprocess.Popen", FakeProc)
    monkeypatch.setattr("troubadour.engine.MicSource.start", lambda self, sink, on_end: None)
    monkeypatch.setattr(engine, "_segment_loop", lambda sess: None)
    monkeypatch.setattr(engine, "_worker_loop", lambda sess: None)
    engine.status, engine.live = "idle", None
    engine.start({"kind": "mic", "device": None})
    (proc,) = spawned
    assert proc.cmd[:3] == ["/usr/bin/caffeinate", "-i", "-w"]  # -w: dies with the server
    first = engine.live
    engine.start({"kind": "mic", "device": None})  # replacing a session releases its assertion
    assert proc.terminated
    engine._finish(engine.live)
    assert spawned[1].terminated
    assert first.abandoned


def _run_segmenter(engine, kind: str) -> tuple[LiveSession, threading.Thread]:
    desc = {"kind": "mic", "device": None} if kind == "mic" else {"kind": "file", "name": "x.wav", "speed": 1}
    sess = LiveSession(NullSource(), desc)
    engine.live = sess
    t = threading.Thread(target=engine._segment, args=(sess,), daemon=True)
    t.start()  # no frames ever arrive: like an unplugged USB mic
    return sess, t


def test_silent_mic_warns_once(engine, monkeypatch):
    monkeypatch.setattr("troubadour.engine.MIC_STALL_S", 0.6)
    sess, t = _run_segmenter(engine, "mic")
    deadline = time.monotonic() + 5
    while not engine.bus.of("error") and time.monotonic() < deadline:
        time.sleep(0.05)
    time.sleep(0.8)  # still silent: must not repeat
    sess.abandon()
    t.join(3)
    errors = engine.bus.of("error")
    assert len(errors) == 1
    assert "microphone" in errors[0]["message"]
    assert not t.is_alive()


def test_silent_file_replay_does_not_warn(engine, monkeypatch):
    monkeypatch.setattr("troubadour.engine.MIC_STALL_S", 0.3)
    sess, t = _run_segmenter(engine, "file")
    time.sleep(1.2)
    sess.abandon()
    t.join(3)
    assert engine.bus.of("error") == []
    assert not t.is_alive()


# ------------------------------------------------------------- pending voice tidy-up


def _pending(engine, freq: float, seconds: float, n_messages: int) -> Voice:
    v = Voice(id=short_id("v"), label=None, seconds=seconds)
    v.sum = engine.matcher.embed(tone(freq, 3), 0, 3) * seconds
    if seconds >= PANEL_S:
        engine._surface(v)
    engine.voices[v.id] = v
    vec = engine.matcher.embed(tone(freq, 2), 0, 2)
    for _ in range(n_messages):
        mid = short_id("m")
        engine.messages[mid] = Message(mid, v.id, "hi", 0, 1, 0.3, True, vec=vec)
    return v


def test_refine_merges_pending_voices_that_converged(engine):
    big, twin = _pending(engine, ALICE, 12, 3), _pending(engine, ALICE + 3, 4, 2)  # same voice, two groups
    other = _pending(engine, BOB, 5, 1)
    engine.bus.events.clear()
    engine._refine()
    assert twin.id not in engine.voices
    assert engine.voices[big.id].seconds == 16
    assert other.id in engine.voices
    assert engine.bus.of("voice.removed") == [{"id": twin.id, "merged_into": big.id}]
    (batch,) = engine.bus.of("messages.updated")
    assert len(batch) == 2
    assert all(p["voice_id"] == big.id for p in batch)


def test_refine_drops_pending_voices_with_no_lines_left(engine):
    p = engine.add_player("Alice")
    vid = named_voice(engine, p["id"], ALICE)
    stale = _pending(engine, ALICE, 6, 2)  # her lines from before she was named
    engine.bus.events.clear()
    engine._refine()  # relabels both lines to the named voice, then the empty card goes away
    assert stale.id not in engine.voices
    assert {"id": stale.id, "merged_into": None} in engine.bus.of("voice.removed")
    assert all(m.voice_id == vid for m in engine.messages.values())


def test_refine_tidies_pending_even_before_anyone_is_named(engine):
    a, b = _pending(engine, ALICE, 10, 1), _pending(engine, ALICE + 2, 3, 1)
    engine._refine()
    assert b.id not in engine.voices
    assert a.id in engine.voices


def test_new_lines_use_the_campaign_glossary(engine, tmp_path, monkeypatch):
    from troubadour.campaign import Vault

    monkeypatch.setattr("troubadour.engine.GLOSSARY_CHECK_S", 0.0)
    monkeypatch.setattr("troubadour.asr.transcribe", lambda audio: "Dilara takes 12 slashing damage")
    engine.vault = Vault(tmp_path / "campaign")
    engine.vault.create("Elara", "pc", heard_as=["Dilara"])
    m = say(engine, ALICE)
    assert m["text"] == "Elara takes 12 slashing damage"
    engine.vault.update("Elara", heard_as=[])  # an edit in the Campaign tab (or Obsidian) applies to the next line
    assert say(engine, ALICE)["text"] == "Dilara takes 12 slashing damage"


def test_a_broken_vault_never_stops_transcription(engine, monkeypatch):
    class Broken:
        def signature(self):
            raise OSError("disk gone")

    engine.vault = Broken()
    assert say(engine, ALICE)["text"] == "I deal 12 slashing damage"


def test_known_names_seed_the_glossary(engine):
    p = engine.add_player("Theo", character="Elara")
    gm = engine.add_player("Nora", character="DM", is_gm=True)
    named_voice(engine, gm["id"], BOB, character="Magenta")
    assert sorted(engine.known_names()) == [("Elara", "pc", "Played by Theo."), ("Magenta", "npc", "Voiced by Nora.")]
    assert p["character"] == "Elara"


def test_whisper_hears_the_tables_names_and_the_last_lines(engine, tmp_path, monkeypatch):
    from troubadour.campaign import Vault

    monkeypatch.setattr(config, "ASR_PROMPT", True)
    prompts = []
    monkeypatch.setattr("troubadour.asr.transcribe", lambda audio, prompt=None: prompts.append(prompt) or f"line {len(prompts)}")
    engine.add_player("Theo", character="Elara")
    engine.vault = Vault(tmp_path / "campaign")
    engine.vault.create("Magenta", "npc")
    engine.vault.create("Dallon", "place")
    engine.vault.create("Zorbo", "npc", status="suggested")  # not confirmed: not in the prompt
    for _ in range(3):
        say(engine, ALICE)
    assert prompts[0] == "A D&D session. Elara, Theo, Magenta, Dallon. Initiative, perception check, saving throw."
    assert prompts[2].endswith("saving throw. line 1 line 2")  # the two most recent lines
    assert "Zorbo" not in prompts[2]


def test_a_big_glossary_never_pushes_the_tables_names_out_of_whispers_prompt(engine, tmp_path, monkeypatch):
    from troubadour.campaign import Vault

    monkeypatch.setattr(config, "ASR_PROMPT", True)
    monkeypatch.setattr("troubadour.engine.GLOSSARY_CHECK_S", 0.0)
    heard = iter(["We sail past Npc7 Stormwhistle. " * 8, "And then?"])
    prompts = []
    monkeypatch.setattr("troubadour.asr.transcribe", lambda audio, prompt=None: prompts.append(prompt) or next(heard))
    engine.add_player("Theo", character="Elara")
    engine.vault = Vault(tmp_path / "campaign")
    for i in range(60):
        engine.vault.create(f"Npc{i} Stormwhistle", "npc")
    say(engine, ALICE)
    say(engine, ALICE)
    assert asr.prompt_tokens(prompts[1]) <= asr.PROMPT_TOKENS
    assert prompts[1].startswith("A D&D session. Elara, Theo, Npc7 Stormwhistle, Npc0 Stormwhistle,")
    assert prompts[1].split(" Initiative")[0].count("Stormwhistle") == 18  # capped: Elara, Theo and 18 more


def test_a_short_clip_gets_the_names_but_not_the_last_lines(engine, monkeypatch):
    monkeypatch.setattr(config, "ASR_PROMPT", True)
    prompts = []
    monkeypatch.setattr("troubadour.asr.transcribe", lambda audio, prompt=None: prompts.append(prompt) or f"line {len(prompts)}")
    engine.add_player("Theo", character="Elara")
    say(engine, ALICE, 2.0)
    say(engine, ALICE, 0.6)  # a blip: the last lines would tempt Whisper into a sentence
    say(engine, ALICE, 1.0)
    assert prompts[1] == "A D&D session. Elara, Theo. Initiative, perception check, saving throw."
    assert prompts[2].endswith("saving throw. line 1 line 2")


def test_a_nickname_said_lately_reaches_whispers_prompt(engine, tmp_path, monkeypatch):
    from troubadour.campaign import Vault

    monkeypatch.setattr(config, "ASR_PROMPT", True)
    monkeypatch.setattr("troubadour.engine.GLOSSARY_CHECK_S", 0.0)
    heard = iter(["Ask Faryn about it.", "Sure."])
    prompts = []
    monkeypatch.setattr("troubadour.asr.transcribe", lambda audio, prompt=None: prompts.append(prompt) or next(heard))
    engine.vault = Vault(tmp_path / "campaign")
    engine.vault.create("Faryn Castellan", "npc", aliases=["Faryn"])
    say(engine, ALICE)
    say(engine, ALICE)
    assert prompts[1].startswith("A D&D session. Faryn, Faryn Castellan.")


def test_spelling_cleanup_live(engine, tmp_path, monkeypatch):
    from troubadour.campaign import Vault

    monkeypatch.setattr("troubadour.engine.GLOSSARY_CHECK_S", 0.0)
    engine.vault = Vault(tmp_path / "campaign")
    engine.vault.create("Elara", "pc", description="Theo's paladin.")
    engine.vault.create("Delara", "npc", description="An NPC Theo plays.")
    engine.vault.create("Auggie", "pc")
    monkeypatch.setattr("troubadour.asr.transcribe", lambda audio: "augie hits Dallara for 12 slashing damage")
    m = say(engine, ALICE)
    assert m["text"] == "Auggie hits Dallara for 12 slashing damage"  # the sure fix, before anyone sees it
    assert m["fixes"] == [{"start": 0, "end": 6, "heard": "augie"}]
    assert engine.cleanup_q.get_nowait() == m["id"]  # "Dallara" waits for the LLM
    asked = []

    def llm(system, user, schema):
        asked.append(user)
        return {"answers": [{"q": 0, "choice": "Delara"}]}

    monkeypatch.setattr(engine.cleanup_llm, "json", llm)
    engine.bus.events.clear()
    engine._cleanup([m["id"]])
    fixed = engine.messages[m["id"]]
    assert fixed.text == "Auggie hits Delara for 12 slashing damage"
    assert [f["heard"] for f in fixed.fixes] == ["augie", "Dallara"]
    (patch,) = [e for t, e in engine.bus.events if t == "messages.updated"][0]
    assert patch["text"] == fixed.text
    assert "['Delara', 'Elara']" in asked[0]
    say(engine, ALICE)  # the same call a second time: now it's a heard-as suggestion
    engine._cleanup([list(engine.messages)[-1]])
    assert engine.vault.get("Delara").heard_as_suggested == ["Dallara"]


def test_cleanup_skips_a_line_that_changed_meanwhile(engine, monkeypatch, tmp_path):
    from troubadour.campaign import Vault

    engine.vault = Vault(tmp_path / "campaign")
    engine.vault.create("Elara", "pc")
    engine.vault.create("Delara", "npc")
    monkeypatch.setattr("troubadour.asr.transcribe", lambda audio: "Dallara rolls")
    m = say(engine, ALICE)

    def llm(system, user, schema):
        engine.messages[m["id"]].text = "Dallara rolls a 20"  # edited while the LLM thought
        return {"answers": [{"q": 0, "choice": "Delara"}]}

    monkeypatch.setattr(engine.cleanup_llm, "json", llm)
    engine._cleanup([m["id"]])
    assert engine.messages[m["id"]].text == "Dallara rolls a 20"
