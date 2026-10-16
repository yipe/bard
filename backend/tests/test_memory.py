"""Campaign memory jobs with a scripted LLM: what reaches the vault, and what's refused."""

import json
import time

import httpx
import pytest
from conftest import RecordingBus

from bard import memory as mem
from bard.campaign import Vault
from bard.transcript import build


class FakeLLM:
    """Answers by prompt type, in order; records every call."""

    def __init__(self, notes=(), writeups=(), summary=None, story=None):
        self.notes, self.writeups, self.story = list(notes), list(writeups), story
        self.summary = summary or {
            "title": "The Desert Road",
            "summary": "Elara led the way.",
            "highlights": ["Elara found the cave."],
        }
        self.calls: list[str] = []

    def json(self, system, user, schema, max_tokens=4000):
        self.calls.append(user)
        if system is mem.NOTES_SYSTEM:
            return self.notes.pop(0) if self.notes else {}
        if system is mem.WRITEUP_SYSTEM:
            return self.writeups.pop(0) if self.writeups else {}
        if system is mem.STORY_SYSTEM:
            return {"story": self.story} if self.story is not None else {}
        return self.summary


KNOWN = [("Elara", "pc", "Played by Theo."), ("DM", "pc", "Played by Nora.")]  # "DM" is never an entry


@pytest.fixture
def setup(tmp_path):
    vault = Vault(tmp_path / "campaign")
    sessions = tmp_path / "sessions"

    def make(llm):
        return mem.Memory(vault, RecordingBus(), llm=llm, sessions_dir=lambda: sessions, seed=lambda: KNOWN)

    return vault, sessions, make


def wait(job, seconds=5):
    deadline = time.monotonic() + seconds
    while job.state in ("queued", "running") and time.monotonic() < deadline:
        time.sleep(0.01)
    assert job.state == "done", job.message


# ------------------------------------------------------------- import notes


def test_import_creates_entities_keeps_notes_and_seeds_pcs(setup):
    vault, _, make = setup
    llm = FakeLLM(
        notes=[
            {
                "entities": [
                    {
                        "name": "Magenta",
                        "type": "npc",
                        "aliases": ["Maggie"],
                        "description": "Runs the Rusty Flagon.",
                        "facts": ["Owes Kessia a favour."],
                    },
                    {"name": "Dallon", "type": "place", "aliases": [], "description": "A desert city.", "facts": []},
                    {"name": "the party", "type": "term", "aliases": [], "description": "", "facts": []},  # generic: refused
                    {"name": "Zorbo", "type": "wizard", "aliases": [], "description": "x" * 999, "facts": [3]},  # bad type, junk
                    "not an object",
                ],
                "campaign_facts": ["The party is heading to Dallon."],
            }
        ]
    )
    m = make(llm)
    job = m.import_notes("Session 12", "Elara met Magenta in Dallon.\n\nThey talked about Zorbo.")
    wait(job)
    names = {e.name: e for e in vault.entities()}
    assert set(names) == {"Elara", "Magenta", "Dallon", "Zorbo"}
    assert names["Elara"].type == "pc"  # from Setup, not the model
    assert names["Magenta"].aliases == ["Maggie"]
    assert names["Magenta"].facts == ["Owes Kessia a favour. ([[Session 12]])"]
    assert names["Zorbo"].type == "term"
    assert len(names["Zorbo"].description) == mem.TEXT_MAX
    assert vault.campaign_facts() == ["The party is heading to Dallon. ([[Session 12]])"]
    note = (vault.root / "Notes" / "Session 12.md").read_text()
    assert "Elara met Magenta in Dallon.\n\nThey talked about Zorbo." in note
    assert (vault.root / "Glossary.md").exists()
    assert sorted(job.created) == ["Dallon", "Magenta", "Zorbo"]
    assert "Existing glossary" in llm.calls[0]


def test_import_merges_into_existing_entries(setup):
    vault, _, make = setup
    vault.create("Kessia", "pc", facts=["Is a tiefling."])
    vault.create("Auggie", "pc")
    llm = FakeLLM(
        notes=[
            {
                "entities": [
                    # "Cassia" is how these notes spell Kessia: reached through the alias.
                    {
                        "name": "Cassia",
                        "type": "npc",
                        "aliases": ["Kessia", "Auggie"],
                        "description": "",
                        "facts": ["is a tiefling", "Plays the lute."],
                    },
                ],
                "campaign_facts": [],
            }
        ]
    )
    job = make(llm).import_notes("Old notes", "Cassia plays the lute.")
    wait(job)
    k = vault.get("Kessia")
    assert "Cassia" in k.aliases
    assert "Auggie" not in k.aliases  # already another entry's name
    assert k.facts == ["Is a tiefling.", "Plays the lute. ([[Old notes]])"]
    assert job.updated == ["Kessia"]
    assert len(vault.entities()) == 3  # + Elara from Setup


def test_import_refuses_empty_notes(setup):
    _, _, make = setup
    with pytest.raises(ValueError, match="Paste"):
        make(FakeLLM()).import_notes("x", "   ")


def test_failed_job_reports_the_error(setup):
    _, _, make = setup

    class Broken(FakeLLM):
        def json(self, *a, **k):
            raise ConnectionError("LM Studio is not running")

    job = make(Broken()).import_notes("x", "Some notes.")
    deadline = time.monotonic() + 5
    while job.state != "error" and time.monotonic() < deadline:
        time.sleep(0.01)
    assert job.state == "error"
    assert "LM Studio" in job.message


# ------------------------------------------------------------- session write-up


def _session(sessions, sid="2026-10-08_193401-eebf", lines=None):
    players = [{"id": "p1", "name": "Theo", "character": "Elara", "is_gm": False, "color": "#000"}]
    voices = [{"id": "v1", "label": "Voice 1", "status": "named", "player_id": "p1", "character": None, "trained": True}]
    msgs = [
        {
            "id": f"m{i}",
            "t_start": i * 5.0,
            "t_end": i * 5.0 + 4,
            "voice_id": "v1",
            "provisional": False,
            "confidence": 0.9,
            "text": t,
        }
        for i, t in enumerate(lines or ["Dilara, three.", "We found a cave near Dallon.", "Magento waved at us."])
    ]
    record = build({"id": sid, "started_at": 1791513241.0, "source": None, "duration_s": 600}, players, voices, msgs)
    (sessions / sid).mkdir(parents=True)
    (sessions / sid / "transcript.json").write_text(json.dumps(record))
    return sid


def test_write_up_makes_a_session_page_and_suggestions(setup):
    vault, sessions, make = setup
    vault.create("Magenta", "npc")
    sid = _session(sessions)
    llm = FakeLLM(
        writeups=[
            {
                "events": [
                    {"at": "00:00", "text": "Elara rolled initiative."},
                    {"at": "99:99", "text": "The party found a cave near Dallon."},
                ],
                "facts": [{"about": "Magenta", "text": "Waved at the party from the road."}, {"about": "Nobody", "text": "x"}],
                "new_names": [
                    {"name": "Dallon", "type": "place", "description": "A town."},
                    {"name": "Magenta", "type": "npc", "description": "dup"},
                ],
                "spellings": [
                    {"heard": "Dilara", "means": "Elara"},
                    {"heard": "Magento", "means": "Magenta"},
                    {"heard": "Ellarina", "means": "Elara"},  # not in the transcript: refused
                    {"heard": "Dallon", "means": "Magenta"},  # in the transcript, but nothing like Magenta: refused
                ],
            }
        ]
    )
    job = make(llm).write_up(sid)
    wait(job)
    page = (vault.root / "Sessions" / "2026-10-08 The Desert Road.md").read_text()
    assert f"session_id: {sid}" in page
    assert "[[Elara]] led the way." in page
    assert "- 00:00 [[Elara]] rolled initiative." in page
    assert "- 00:00 The party found a cave near [[Dallon]]." in page  # "99:99" isn't a time in the transcript
    assert f"[[Transcripts/{sid}|Full transcript]]" in page
    assert (vault.root / "Sessions" / "Transcripts" / f"{sid}.md").exists()
    dallon = vault.get("Dallon")
    assert dallon.status == "suggested"
    assert vault.get("Magenta").suggested_facts == ["Waved at the party from the road. ([[2026-10-08 The Desert Road]])"]
    assert vault.get("Elara").heard_as_suggested == ["Dilara"]
    assert vault.get("Magenta").heard_as_suggested == ["Magento"]
    assert job.note == "Sessions/2026-10-08 The Desert Road.md"


def test_what_happened_caps_beats_per_part_with_room_for_the_ending(setup, monkeypatch):
    vault, sessions, make = setup
    monkeypatch.setattr(mem, "TRANSCRIPT_CHUNK_CHARS", 60)  # one line per part
    sid = _session(sessions)
    beats = [{"at": "00:00", "text": "  "}] + [{"at": "00:05", "text": f"Beat {i}."} for i in range(1, 10)]
    llm = FakeLLM(writeups=[{"events": beats}] * 20)
    wait(make(llm).write_up(sid))
    parts = sum("Transcript:\n" in c for c in llm.calls)
    assert parts > 1
    happened = _what_happened(vault)  # no story: the beats themselves; empty ones dropped
    assert len(happened) == mem.PART_EVENTS * (parts - 1) + mem.FINAL_PART_EVENTS
    assert "rolled initiative" in mem.WRITEUP_SYSTEM  # the prompt shows what not to write


def _what_happened(vault) -> list[str]:
    page = (vault.root / "Sessions" / "2026-10-08 The Desert Road.md").read_text()
    return page.split("## What happened\n", 1)[1].split("\n\n", 1)[0].splitlines()


def test_what_happened_is_the_retold_story(setup):
    vault, sessions, make = setup
    sid = _session(sessions)
    beats = [{"at": "00:00", "text": "Elara's attack failed."}, {"at": "00:05", "text": "Elara found a cave."}]
    story = [
        {"at": "00:05", "text": "After a scrappy fight, Elara stumbled on a hidden cave."},
        {"at": "12:34", "text": "Its mouth breathed cold air."},  # not a beat's time: the previous one's
        {"at": "00:00", "text": "  "},  # empty: dropped
    ]
    llm = FakeLLM(writeups=[{"events": beats}], story=story)
    wait(make(llm).write_up(sid))
    story_call = llm.calls[-2]  # then the summary
    assert story_call.splitlines() == ["- 00:00 Elara's attack failed.", "- 00:05 Elara found a cave."]
    assert _what_happened(vault) == [
        "- 00:05 After a scrappy fight, [[Elara]] stumbled on a hidden cave.",
        "- 00:05 Its mouth breathed cold air.",
    ]


def test_an_echoed_story_too_long_to_keep_falls_back_to_the_beats(setup):
    vault, sessions, make = setup
    sid = _session(sessions)
    echo = [{"at": "00:00", "text": f"Line {i}."} for i in range(mem.STORY_MAX + 1)]
    wait(make(FakeLLM(writeups=[{"events": [{"at": "00:05", "text": "Elara found a cave."}]}], story=echo)).write_up(sid))
    assert _what_happened(vault) == ["- 00:05 [[Elara]] found a cave."]


def test_write_up_again_replaces_the_page_and_respects_rejections(setup):
    vault, sessions, make = setup
    sid = _session(sessions)
    spelling = {
        "events": [{"at": "00:05", "text": "x"}],
        "facts": [],
        "new_names": [],
        "spellings": [{"heard": "Dilara", "means": "Elara"}],
    }
    m = make(FakeLLM(writeups=[dict(spelling), dict(spelling)]))
    wait(m.write_up(sid))
    vault.update("Elara", heard_as_rejected=["Dilara"])
    assert vault.get("Elara").heard_as_suggested == []
    wait(m.write_up(sid))
    assert vault.get("Elara").heard_as_suggested == []  # rejected stays rejected
    assert len(list((vault.root / "Sessions").glob("*.md"))) == 1
    assert m.written_up() == {sid: "Sessions/2026-10-08 The Desert Road.md"}


def test_write_up_unknown_session(setup):
    _, _, make = setup
    with pytest.raises(KeyError):
        make(FakeLLM()).write_up("2026-01-01_000000-ffff")


# ------------------------------------------------------------- helpers


def test_link_names_links_once_and_uses_aliases(tmp_path):
    vault = Vault(tmp_path)
    vault.create("Elara", "pc", aliases=["Ellie"])
    vault.create("Dallon", "place")
    out = mem.link_names("Ellie and Elara went to Dallon, then Dallon again.", vault)
    assert out == "[[Elara|Ellie]] and Elara went to [[Dallon]], then Dallon again."
    assert mem.link_names(out, vault) == out  # already linked: unchanged


def test_chunks_are_paragraph_aligned_and_bounded():
    text = "\n\n".join(["a" * 40] * 10 + ["b" * 250])
    parts = mem.chunks(text, 100)
    assert all(len(p) <= 100 for p in parts)
    assert "".join(parts).replace("\n", "") == text.replace("\n", "")


@pytest.mark.parametrize(
    ("heard", "name", "expected"),
    [("Dilara", "Elara", True), ("Dallara", "Elara", True), ("Cassia", "Kessia", True), ("Magento", "Magenta", True),
     ("Augie", "Auggie", True), ("Priya", "Kessia", False), ("Elara", "Elara", False), ("Ox", "Oxford", False),
     ("SLR", "Elara", False), ("Elkari", "Elara", False)],
)  # fmt: skip
def test_looks_like(heard, name, expected):
    assert mem.looks_like(heard, name) is expected


def test_spelling_suggestions_from_transcripts(tmp_path):
    vault = Vault(tmp_path / "campaign")
    vault.create("Elara", "pc", heard_as=["Dallara"])
    vault.create("Kessia", "pc", heard_as_rejected=["Cassia"])
    sessions = tmp_path / "sessions"
    _session(
        sessions,
        lines=[
            "Dilara, three.",
            "Dilara again.",
            "Dallara four.",
            "Cassia five.",
            "Cassia six.",
            "Olly waves.",
            "Olly's back.",
            "Water water.",
        ],
    )
    out = mem.spelling_suggestions(vault, sessions.iterdir())
    assert [(s["entity"], s["heard"], s["count"]) for s in out["misheard"]] == [("Elara", "Dilara", 2)]
    assert [u["heard"] for u in out["unknown"]] == ["Olly"]


@pytest.mark.parametrize(
    ("raw", "ok"),
    [("Magenta", True), ("Pipe of Smoke Monsters", True), ("The Rusty Flagon", True), ("El-Khari", True),
     ("The rooster", False), ("the party", False), ("Willie, Ollie, and Wally", False), ("initiative", False)],
)  # fmt: skip
def test_name_filter(raw, ok):
    assert bool(mem._name(raw)) is ok


def test_misspelling_hints():
    from bard.campaign import Entity

    hints = mem.misspelling_hints(
        "Dallara, three. Dilara four. Dallara again. Water is Augie for.", [Entity("Elara"), Entity("Auggie")]
    )
    assert hints == "- Dallara, Dilara → Elara\n- Augie → Auggie"


def test_write_up_turns_sound_alike_new_names_into_spellings(setup):
    vault, sessions, make = setup
    sid = _session(
        sessions, lines=["Dallara attacks.", "Willie Olly Wally waves.", "Willy Olly Wally again.", "Willie Ollie Wally."]
    )
    llm = FakeLLM(
        writeups=[
            {
                "events": [],
                "facts": [],
                "new_names": [
                    {"name": "Dallara", "type": "npc", "description": "An NPC."},  # really Elara
                    {"name": "Willie Olly Wally", "type": "npc", "description": "Three-headed guide."},
                    {"name": "Willy Olly Wally", "type": "npc", "description": "dup"},
                ],
                "spellings": [],
            }
        ]
    )
    wait(make(llm).write_up(sid))
    names = {e.name for e in vault.entities()}
    assert names == {"Elara", "Willie Olly Wally"}
    assert vault.get("Elara").heard_as_suggested == ["Dallara"]
    assert vault.get("Willie Olly Wally").heard_as_suggested == ["Willy Olly Wally"]
    assert "Likely misspellings" in llm.calls[0]


def test_write_up_caps_and_drops_fleeting_facts(setup):
    vault, sessions, make = setup
    sid = _session(sessions)
    lasting = [
        "owns a grey owl",
        "fears deep water",
        "was born in Dallon",
        "sings off key",
        "hates goblins",
        "owes Cletus money",
        "reads Elvish",
        "lost a brother",
        "keeps a journal",
    ]
    facts = [{"about": "Elara", "text": f"Elara {x}."} for x in lasting]
    facts.insert(0, {"about": "Elara", "text": "Elara has one hit point."})
    llm = FakeLLM(writeups=[{"events": [], "facts": facts, "new_names": [], "spellings": []}])
    wait(make(llm).write_up(sid))
    got = vault.get("Elara").suggested_facts
    assert len(got) == mem.PART_FACTS
    assert not any("hit point" in f for f in got)


def test_word_scan_ignores_everyday_words_and_sentence_starts():
    words = mem.scan_words(
        ["Okay. We'll check the slots.", "Slots are full, said Olly.", "We met Olly and olly-ish folk.", "Zorbo!"]
    )
    names = {w.spelling for w in words.values() if mem.name_like(w)}
    assert names == {"Olly", "Zorbo"}  # "Slots" only capitalised at a sentence start; "We'll" a contraction


def test_notes_confirm_a_suggested_entry_and_replace_its_guess(setup):
    vault, _, make = setup
    vault.create("Torred Logan", "npc", status="suggested", description="A person Auggie thinks is cool.")
    llm = FakeLLM(
        notes=[
            {
                "entities": [
                    {
                        "name": "Torred Logan",
                        "type": "npc",
                        "aliases": [],
                        "description": "Leader of the Bedin skirmishers.",
                        "facts": [],
                    }
                ],
                "campaign_facts": [],
            }
        ]
    )
    wait(make(llm).import_notes("Session 11", "Torred Logan leads the Bedin."))
    e = vault.get("Torred Logan")
    assert (e.status, e.description) == ("confirmed", "Leader of the Bedin skirmishers.")


def test_ignored_words_are_never_suggested(tmp_path):
    vault = Vault(tmp_path / "campaign")
    vault.create("Elara", "pc")
    sessions = tmp_path / "sessions"
    _session(
        sessions,
        lines=["We love T-Mobile.", "Our T-Mobile plan.", "Olly waves.", "Olly again.", "Dilara, three.", "Dilara four."],
    )
    vault.ignore(["T-Mobile", "Dilara"])
    out = mem.spelling_suggestions(vault, sessions.iterdir())
    assert [u["heard"] for u in out["unknown"]] == ["Olly"]
    assert out["misheard"] == []


def test_a_rewrite_keeps_the_page_name(setup):
    vault, sessions, make = setup
    sid = _session(sessions)
    m = make(FakeLLM(writeups=[{"events": [], "facts": [], "new_names": [], "spellings": []}] * 2))
    wait(m.write_up(sid))
    m.llm.summary = {"title": "A New Title", "summary": "x", "highlights": []}
    wait(m.write_up(sid))
    (page,) = (vault.root / "Sessions").glob("*.md")
    assert page.stem == "2026-10-08 The Desert Road"
    assert page.read_text().count("# 2026-10-08 The Desert Road") == 1


def test_unknown_names_skip_vocabulary_acronyms_and_fillers(tmp_path):
    vault = Vault(tmp_path / "campaign")
    sessions = tmp_path / "sessions"
    lines = [
        "I cast Thunderwave.",
        "More Thunderwave.",
        "He is an NPC.",
        "Another NPC here.",
        "Bye-bye now.",
        "Bye-bye.",
        "Olly waves.",
        "Olly again.",
    ]
    _session(sessions, lines=lines)
    assert [u["heard"] for u in mem.spelling_suggestions(vault, sessions.iterdir())["unknown"]] == ["Olly"]


# ------------------------------------------------------------- LLM health


def _llm_with(handler) -> mem.MemoryLLM:
    llm = mem.MemoryLLM(url="http://llm.test/v1", model="m")
    llm.client = httpx.Client(transport=httpx.MockTransport(handler))
    return llm


@pytest.mark.parametrize(
    ("response", "healthy"),
    [
        (httpx.Response(200, json={"data": [{"id": "m"}]}), True),
        (httpx.Response(200, json={"data": [{"id": "other"}]}), False),  # our model isn't loaded
        (httpx.Response(200, json={"data": ["not-a-dict"]}), False),
        (httpx.Response(200, text="<html>not an LLM</html>"), False),  # some other service on the port
        (httpx.Response(500), False),
    ],
)
def test_llm_healthy_never_raises(response, healthy):
    assert _llm_with(lambda request: response).healthy() is healthy


def test_llm_healthy_when_server_down():
    def handler(request):
        raise httpx.ConnectError("refused")

    assert _llm_with(handler).healthy() is False


def test_an_lm_studio_error_says_why():
    body = {
        "error": "Error in iterating prediction stream: Traceback ...\n  File x\nThe model has crashed without additional information. (Exit code: null)"
    }
    llm = _llm_with(lambda request: httpx.Response(400, json=body))
    with pytest.raises(RuntimeError, match=r"LM Studio couldn't answer \(400\): The model has crashed"):
        llm.json("s", "u", {})
    llm = _llm_with(lambda request: httpx.Response(500, text="<html>oops</html>"))
    with pytest.raises(RuntimeError, match="oops"):
        llm.json("s", "u", {})


def test_only_the_last_part_is_told_it_ends_the_session(setup, monkeypatch):
    vault, sessions, make = setup
    monkeypatch.setattr(mem, "TRANSCRIPT_CHUNK_CHARS", 60)  # one line per part
    sid = _session(sessions)
    llm = FakeLLM()
    wait(make(llm).write_up(sid))
    parts = [c for c in llm.calls if "Transcript:\n" in c]
    assert len(parts) > 1
    assert [mem.SESSION_END in c for c in parts] == [False] * (len(parts) - 1) + [True]


def test_a_retelling_that_stops_before_the_ending_is_not_used():
    beats = [("00:05", "Found a cave."), ("2:30:00", "Made Thoronn king."), ("2:32:00", "Got a sword.")]
    ending = {"2:30:00", "2:32:00"}
    short = [{"at": "00:05", "text": "They found a cave."}]
    assert mem._story(short, beats, ending) == beats  # never got to the finale
    full = short + [{"at": "2:30:00", "text": "The kobolds crowned Thoronn and gave him a sword."}]
    assert mem._story(full, beats, ending) == [("00:05", "They found a cave."), ("2:30:00", full[1]["text"])]
