"""Campaign memory jobs with a scripted LLM: what reaches the vault, and what's refused."""

import json
import time

import httpx
import pytest
from conftest import RecordingBus

from troubadour import memory as mem
from troubadour.campaign import Vault
from troubadour.transcript import build


class FakeLLM:
    """Answers by prompt type, in order; records every call."""

    def __init__(self, notes=(), writeups=(), summary=None):
        self.notes, self.writeups = list(notes), list(writeups)
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
    started = time.mktime((2026, 10, 8, 19, 34, 1, 0, 0, -1))  # local time: page dates use the table's clock
    record = build({"id": sid, "started_at": started, "source": None, "duration_s": 600}, players, voices, msgs)
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
                "claims": [
                    {"at": "00:00", "text": "Elara rolled initiative.", "status": "happened", "fixes": ""},
                    {"at": "99:99", "text": "The party found a cave near Dallon.", "status": "happened", "fixes": ""},
                    {"at": "00:05", "text": "They assassinate her.", "status": "joke", "fixes": ""},
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
    assert "rolled initiative" not in page  # a check is not the session
    assert "- The party found a cave near [[Dallon]]." in page
    assert "assassinate" not in page  # a joke is not the session
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
    beats = [{"at": "00:00", "text": "  ", "status": "happened"}] + [
        {"at": "00:05", "text": f"Beat {i}.", "status": "happened"} for i in range(1, 20)
    ]
    llm = FakeLLM(writeups=[{"claims": beats}] * 20)
    wait(make(llm).write_up(sid))
    parts = sum("Transcript:\n" in c for c in llm.calls)
    assert parts > 1
    happened = _what_happened(vault)  # the page is a short note, not every claim
    assert len(happened) == mem.NOTE_MAX
    note = llm.calls[-1]
    returned = 19  # blank claim dropped; Beat 1..19
    assert sum(line.startswith("- ") for line in note.splitlines()) == (
        min(returned, mem.PART_CLAIMS) * (parts - 1) + min(returned, mem.FINAL_PART_CLAIMS)
    )
    assert "rolled initiative" in mem.WRITEUP_SYSTEM  # the prompt shows what not to write


def _section(vault, heading: str) -> list[str]:
    page = (vault.root / "Sessions" / "2026-10-08 The Desert Road.md").read_text()
    return page.split(f"## {heading}\n", 1)[1].split("\n\n", 1)[0].splitlines()


def _what_happened(vault) -> list[str]:
    return _section(vault, "What happened")


def _still_open(vault) -> list[str]:
    return _section(vault, "Still open")


def test_a_joke_and_a_retraction_never_become_the_session(setup):
    vault, sessions, make = setup
    vault.create("Magenta", "npc")
    sid = _session(sessions)
    claims = [
        {"text": "Magenta died.", "status": "happened", "fixes": ""},
        {"text": "They assassinate her.", "status": "joke", "fixes": ""},
        {"text": "Magenta died.", "status": "retracted", "fixes": "Magenta died"},
        {"text": "Magenta slumped to about one hit point; she did not die.", "status": "corrected", "fixes": "Magenta died"},
        {"text": "Lumberg's letter is still undelivered.", "status": "open", "fixes": ""},
    ]
    llm = FakeLLM(writeups=[{"claims": claims}])
    wait(make(llm).write_up(sid))
    summary_call = llm.calls[-1]
    assert "They assassinate her." not in summary_call
    assert "Magenta died." not in summary_call
    assert "she did not die" in summary_call
    assert "Still open:" in summary_call
    page = (vault.root / "Sessions" / "2026-10-08 The Desert Road.md").read_text()
    assert "assassinate" not in page
    assert "Magenta died." not in page
    assert "- [[Magenta]] slumped to about one hit point; she did not die." in _what_happened(vault)
    assert _still_open(vault) == ["- Lumberg's letter is still undelivered."]


def test_write_up_again_replaces_the_page_and_respects_rejections(setup):
    vault, sessions, make = setup
    sid = _session(sessions)
    spelling = {
        "claims": [{"at": "00:05", "text": "x", "status": "happened"}],
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


def test_the_page_is_the_recap_not_the_claim_list(setup):
    vault, sessions, make = setup
    sid = _session(sessions)
    claims = [
        {"text": "The party rolled initiative.", "status": "happened"},
        {"text": "Sathrin attacked the worm, dealing 8 damage.", "status": "happened"},
        {"text": "Elara found a dead autognome beside the wagon.", "status": "happened"},
        {"text": "Sathrin plans to travel north tomorrow.", "status": "open"},
        {"text": "The letter to Lumberg is still undelivered.", "status": "open"},
    ]
    note = {
        "title": "The Desert Road",
        "summary": "Elara found a dead autognome beside the wagon.",
        "scenes": [
            "Elara found a dead autognome beside the wagon.",
            "A mephit animated a dune of bone cactus.",  # not in the claims
        ],
        "open": ["Sathrin plans to travel north tomorrow.", "The letter to Lumberg is still undelivered."],
        "highlights": [
            "Elara found a dead autognome beside the wagon.",
            "Sathrin attacked the worm, dealing 8 damage.",
        ],
    }
    wait(make(FakeLLM(writeups=[{"claims": claims}], summary=note)).write_up(sid))
    assert _what_happened(vault) == ["- [[Elara]] found a dead autognome beside the wagon."]
    assert _still_open(vault) == ["- The letter to Lumberg is still undelivered."]
    page = (vault.root / "Sessions" / "2026-10-08 The Desert Road.md").read_text()
    assert "initiative" not in page
    assert "8 damage" not in page
    assert "bone cactus" not in page
    assert "plans to travel" not in page
    assert page.split("## Highlights\n", 1)[1].split("\n\n", 1)[0] == (
        "- [[Elara]] found a dead autognome beside the wagon."
    )


def test_an_everyday_alias_does_not_eat_the_word(tmp_path):
    vault = Vault(tmp_path)
    vault.create("Hank", "npc", aliases=["Dead"])
    assert mem.link_names("They found a dead lizardfolk.", vault) == "They found a dead lizardfolk."


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
    from troubadour.campaign import Entity

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


def test_settle_keeps_the_correction_and_drops_the_joke():
    happened, open_ = mem._settle(
        [
            {"text": "Magenta died.", "status": "happened"},
            {"text": "They assassinate her.", "status": "joke"},
            {"text": "", "status": "happened"},
            {"text": "taken back", "status": "retracted", "fixes": "died"},
            {"text": "Magenta was at about one hit point.", "status": "corrected", "fixes": "died"},
            {"text": "The eggs have about seven days.", "status": "open"},
            {"text": "nope", "status": "imagined"},
        ]
    )
    assert happened == ["Magenta was at about one hit point."]
    assert open_ == ["The eggs have about seven days."]
