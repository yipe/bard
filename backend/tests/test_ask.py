"""Ask: questions answered from the campaign's transcripts and notes, with a scripted LLM."""

import json
import time

import httpx
import pytest
from conftest import RecordingBus
from fastapi.testclient import TestClient

from troubadour import ask, config, server
from troubadour.campaign import Vault
from troubadour.transcript import build

SID = "2026-10-08_193401-eebf"
LINES = [
    "Okay, roll for initiative.",
    "Dilara swings at the kobold.",
    "The kobold runs off toward the marsh.",
    "I bet Magenta has the gem. She was in the vault that night.",
    "We should follow the tracks.",
    "The tracks lead to a cave.",
    "Inside the cave there is a smell of smoke.",
    "Something moves in the dark.",
    *["Pass the dice."] * 12,
    "Who wants pizza?",
]


class FakeLLM:
    """Plan and answer by prompt; records every call."""

    url = "http://127.0.0.1:1235/v1"

    def __init__(self, plan=None, answer=None):
        self.plan = plan or {"terms": [], "entries": []}
        self.answer = answer
        self.calls: list[tuple[str, str, dict]] = []

    def json(self, system, user, schema, max_tokens=4000):
        self.calls.append((system, user, schema))
        if system is ask.PLAN_SYSTEM:
            return self.plan
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer or {"answer": "Someone suspected Magenta.", "found": True, "sources": ["S1"]}

    def prompt(self) -> str:
        return next(user for system, user, _ in self.calls if system is ask.ANSWER_SYSTEM)


def write_session(sessions, sid=SID, lines=LINES):
    players = [
        {"id": "p1", "name": "Theo", "character": "Elara", "is_gm": False, "color": "#000"},
        {"id": "p2", "name": "Nora", "character": "", "is_gm": True, "color": "#111"},
    ]
    voices = [
        {"id": "v1", "label": "Voice 1", "status": "named", "player_id": "p1", "character": None, "trained": True},
        {"id": "v2", "label": "Voice 2", "status": "named", "player_id": "p2", "character": None, "trained": True},
    ]
    msgs = [
        {"id": f"m{i}", "t_start": i * 5.0, "t_end": i * 5.0 + 4, "voice_id": f"v{1 + i % 2}", "provisional": False, "confidence": 0.9, "text": t}
        for i, t in enumerate(lines)
    ]  # fmt: skip
    started = time.mktime((2026, 10, 8, 19, 34, 1, 0, 0, -1))
    (sessions / sid).mkdir(parents=True)
    (sessions / sid / "transcript.json").write_text(
        json.dumps(build({"id": sid, "started_at": started, "source": None, "duration_s": 60}, players, voices, msgs))
    )


@pytest.fixture
def setup(tmp_path):
    vault = Vault(tmp_path / "campaign")
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    state = {"campaign": "main"}

    def make(llm, written_up=dict):
        return ask.Asker(
            vault,
            RecordingBus(),
            sessions=lambda: sorted(sessions.iterdir()),
            written_up=written_up,
            campaign=lambda: state["campaign"],
            seed=lambda: [("Elara", "pc", "Played by Theo."), ("Magenta", "npc", "Voiced by Nora.")],
            llm=llm,
        )

    return vault, sessions, make, state


def wait(a, seconds=5):
    deadline = time.monotonic() + seconds
    while a.state not in ("done", "error") and time.monotonic() < deadline:
        time.sleep(0.01)
    return a


def test_tokens_fold_case_possessives_and_plurals():
    assert ask.tokens("Elara's GEMS, who has the diamond?") == ["elara", "gem", "diamond"]
    assert ask.tokens("the class glass") == ["class", "glass"]  # -ss isn't a plural


def test_an_entry_is_searched_under_its_misheard_spellings(setup):
    vault, sessions, make, _ = setup
    vault.create("Elara", "pc", heard_as=["Dilara"], description="A paladin.")
    write_session(sessions)
    llm = FakeLLM()
    a = wait(make(llm).ask("What did Elara attack?"))
    assert a.state == "done", a.message
    prompt = llm.prompt()
    assert "Dilara swings at the kobold." in prompt  # the transcript's spelling
    assert "Elara (player character)\nA paladin." in prompt  # her note goes in whole
    assert "Theo: a player; plays Elara (a PC)" in prompt
    assert "Nora: the GM" in prompt
    assert "Magenta: an NPC (Voiced by Nora.)" in prompt
    assert "Who wants pizza?" not in prompt


def test_planned_terms_find_synonyms_and_sources_point_at_the_line(setup):
    vault, sessions, make, _ = setup
    write_session(sessions)
    llm = FakeLLM(plan={"terms": ["gem", "jewel", "x" * 41], "entries": ["Nobody"]})
    asker = make(llm, written_up=lambda: {SID: "Sessions/2026-10-08 The Desert Road.md"})
    a = wait(asker.ask("Who did we suspect has the diamond?"))
    assert a.state == "done", a.message
    prompt = llm.prompt()
    assert "[S1] 2026-10-08 The Desert Road · 00:00" in prompt  # windows overlapping the hit, merged
    assert "[00:15] Nora: I bet Magenta has the gem." in prompt
    assert "Pass the dice." not in prompt.split("[00:35]")[1]  # just the window around the hit
    assert a.answer == "Someone suspected Magenta."
    assert a.found is True
    assert a.sources[0] | {"excerpt": ""} == {
        "id": "S1",
        "kind": "transcript",
        "label": "2026-10-08 The Desert Road · 00:00",
        "session": SID,
        "t": 0.0,
        "path": None,
        "entry": None,
        "excerpt": "",
    }
    assert asker.list()[0]["answer"] == "Someone suspected Magenta."


def test_notes_are_searched_but_not_generated_or_trashed_ones(setup):
    vault, _, make, _ = setup
    vault.write_note("Notes", "Session 3", "---\ntype: notes\n---\nThe duke keeps the diamond in his tower.\n")
    for rel in ("Glossary.md", ".trash/Old.md", "Sessions/Transcripts/x.md"):
        (vault.root / rel).parent.mkdir(parents=True, exist_ok=True)
        (vault.root / rel).write_text("The diamond is fake.")
    llm = FakeLLM(answer={"answer": "In the duke's tower.", "found": True, "sources": ["S1", "S9", "S1"]})
    a = wait(make(llm).ask("Where is the diamond?"))
    prompt = llm.prompt()
    assert "[S1] Notes/Session 3\nThe duke keeps the diamond in his tower." in prompt
    assert "fake" not in prompt
    assert [s["id"] for s in a.sources] == ["S1"]  # unknown and repeated ids dropped
    assert a.sources[0]["path"] == "Notes/Session 3.md"


def test_model_output_is_validated(setup):
    vault, sessions, make, _ = setup
    write_session(sessions)
    llm = FakeLLM(plan={"terms": ["gem"], "entries": []}, answer={"answer": "x " * 2000, "found": "yes", "sources": "S1"})
    a = wait(make(llm).ask("gem?"))
    assert a.state == "done"
    assert len(a.answer) == ask.ANSWER_MAX
    assert a.found is False
    assert a.sources == []
    schema = llm.calls[-1][2]
    assert schema["properties"]["sources"]["items"]["enum"] == ["S1"]  # only offered excerpts


def test_nothing_found_skips_the_answer_call(setup):
    _, sessions, make, _ = setup
    write_session(sessions)
    llm = FakeLLM()
    a = wait(make(llm).ask("Where is the dragon egg?"))
    assert (a.state, a.answer, a.found) == ("done", ask.NOT_FOUND, False)
    assert [system for system, _, _ in llm.calls] == [ask.PLAN_SYSTEM]


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (RuntimeError("LM Studio couldn't answer (500: The model has crashed)"), "LM Studio couldn't answer"),
        (httpx.ConnectError("refused"), "Can't reach LM Studio"),
    ],
)
def test_llm_errors_are_reported(setup, error, message):
    vault, sessions, make, _ = setup
    write_session(sessions)
    a = wait(make(FakeLLM(plan={"terms": ["gem"], "entries": []}, answer=error)).ask("gem?"))
    assert a.state == "error"
    assert message in a.message


def test_answers_belong_to_their_campaign(setup):
    _, sessions, make, state = setup
    write_session(sessions)
    asker = make(FakeLLM(plan={"terms": ["gem"], "entries": []}))
    wait(asker.ask("gem?"))
    state["campaign"] = "other"
    assert asker.list() == []
    with pytest.raises(ValueError, match="Ask a question"):
        asker.ask("   ")


def test_ask_api(dirs, monkeypatch):
    monkeypatch.delenv("TROUBADOUR_VAULT", raising=False)
    assert str(config.vault_dir()).startswith(str(dirs[0]))  # never the real vault
    monkeypatch.setattr(server.asker, "llm", FakeLLM())
    monkeypatch.setattr(server.asker, "answers", {})
    client = TestClient(server.app, base_url="http://127.0.0.1:8765")
    assert client.post("/api/campaign/ask", json={"question": ""}).status_code == 422
    assert client.post("/api/campaign/ask", json={"question": "x" * 501}).status_code == 422
    assert client.post("/api/campaign/ask", json={"question": "  "}).status_code == 400
    r = client.post("/api/campaign/ask", json={"question": "Who has the gem?"})
    assert r.status_code == 200
    assert r.json()["question"] == "Who has the gem?"
    a = wait(server.asker.answers[r.json()["id"]])
    assert (a.state, a.answer) == ("done", ask.NOT_FOUND)  # an empty campaign
    assert client.get("/api/campaign").json()["answers"][0]["id"] == a.id


def test_a_long_list_note_splits_between_items(setup):
    vault, _, _, _ = setup
    events = "\n".join(f"- 1:{i:02d}:00 The party met kobold number {i} on the road north." for i in range(60))
    vault.write_note("Sessions", "2026-10-15 Kobolds", f"# Kobolds\n\n## What happened\n{events}\n")
    pieces = [p for p in ask.note_passages(vault) if p.kind == "note"]
    assert len(pieces) > 1
    assert all(p.text.startswith(("- ", "# ")) for p in pieces)
    assert {p.label for p in pieces} == {"Sessions/2026-10-15 Kobolds"}
