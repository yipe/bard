"""Improving a saved transcript: re-transcription with the current glossary, never losing lines."""

import json
import time

import numpy as np
import pytest
from conftest import tone, write_wav
from fastapi.testclient import TestClient

from bard import config, improve, server
from bard.campaign import Corrector, Entity
from bard.transcript import build

SID = "2026-10-08_193401-eebf"


def record(texts):
    players = [{"id": "p1", "name": "Theo", "character": "Elara", "is_gm": False, "color": "#000"}]
    voices = [{"id": "v1", "label": "Voice 1", "status": "named", "player_id": "p1", "character": None, "trained": True}]
    msgs = [
        {"id": f"m{i}", "t_start": i * 2.0, "t_end": i * 2.0 + 1.5, "voice_id": "v1", "provisional": False, "confidence": 0.9, "text": t}
        for i, t in enumerate(texts)
    ]  # fmt: skip
    return build(
        {"id": SID, "started_at": 1791513241.0, "source": {"kind": "mic", "device": None}, "duration_s": 10},
        players,
        voices,
        msgs,
    )


def test_improve_record_uses_glossary_and_context_and_never_drops_lines():
    rec = record(["Dallara, three.", "I take 5 damage.", "magento waves"])
    # Saved before mechanics were removed: the old tags go.
    rec["messages"][1]["annotations"] = [{"kind": "damage", "start": 7, "end": 15, "quote": "5 damage"}]
    rec["session"]["mechanics"] = 1
    heard = iter(["Dilara, three.", "", "Magento waves at Dilara."])
    prompts = []

    def transcribe(clip, prompt):
        prompts.append(prompt)
        return next(heard)

    fix = Corrector([Entity("Elara", heard_as=["Dilara"]), Entity("Magenta", heard_as=["Magento"])])
    new, changed = improve.improve_record(
        rec, np.zeros(16000 * 10, np.float32), transcribe, fix, lambda history, seconds: " | ".join(history[-2:])
    )
    assert [m["text"] for m in new["messages"]] == ["Elara, three.", "I take 5 damage.", "Magenta waves at Elara."]
    assert changed == 2
    assert prompts == ["", "Elara, three.", "Elara, three. | I take 5 damage."]  # after the improved lines
    assert rec["messages"][0]["text"] == "Dallara, three."  # the input isn't modified
    assert all("annotations" not in m for m in new["messages"])
    assert "mechanics" not in new["session"]


def test_improve_never_turns_a_line_into_filler():
    rec = record(["I take 5 damage."])
    new, changed = improve.improve_record(rec, np.zeros(16000 * 4, np.float32), lambda clip, prompt: "Um...", None, None)
    assert [m["text"] for m in new["messages"]] == ["I take 5 damage."]
    assert changed == 0


@pytest.fixture
def api(engine, dirs, monkeypatch):
    monkeypatch.setattr(server, "engine", engine)
    engine.status, engine.live = "idle", None
    folder = config.DATA_DIR / "sessions" / SID
    folder.mkdir(parents=True)
    (folder / "transcript.json").write_text(json.dumps(record(["Dallara, three.", "Hello."])))
    write_wav(folder / "audio.wav", tone(220, 6.0))
    monkeypatch.setattr("bard.asr.transcribe", lambda clip, prompt=None, model=None: "Elara, three!")
    return TestClient(server.app, base_url="http://127.0.0.1:8765"), folder


def wait_job(client, job_id):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        job = {j["id"]: j for j in client.get("/api/campaign").json()["jobs"]}[job_id]
        if job["state"] in ("done", "error"):
            return job
        time.sleep(0.02)
    raise AssertionError("job didn't finish")


def test_improve_backs_up_once_and_restore_puts_it_back(api):
    client, folder = api
    original = (folder / "transcript.json").read_text()
    job = wait_job(client, client.post(f"/api/sessions/{SID}/improve").json()["id"])
    assert job["state"] == "done", job["message"]
    assert job["message"] == "2 of 2 lines changed"
    saved = json.loads((folder / "transcript.json").read_text())
    assert [m["text"] for m in saved["messages"]] == ["Elara, three!", "Elara, three!"]
    assert saved["session"]["improved"]["changed"] == 2
    assert "Elara, three!" in (folder / "transcript.md").read_text()
    wait_job(client, client.post(f"/api/sessions/{SID}/improve").json()["id"])
    assert (folder / "transcript.orig.json").read_text() == original  # the first backup is kept
    assert client.get("/api/sessions").json()[0]["restorable"] is True
    assert client.post(f"/api/sessions/{SID}/restore").status_code == 200
    assert (folder / "transcript.json").read_text() == original
    assert client.post(f"/api/sessions/{SID}/restore").status_code == 404


def test_improve_unknown_session(api):
    client, _ = api
    assert client.post("/api/sessions/2026-01-01_000000-ffff/improve").status_code == 404


def test_fix_spellings_job(api, engine, monkeypatch):
    client, folder = api
    (folder / "transcript.json").write_text(json.dumps(record(["augie rolls.", "Dallara, three."])))
    engine.add_player("Theo", character="Elara")
    engine.add_player("Jun", character="Auggie")
    monkeypatch.setattr(engine.cleanup_llm, "json", lambda *a: {"answers": [{"q": 0, "choice": "Elara"}]})
    job = wait_job(client, client.post(f"/api/sessions/{SID}/spelling").json()["id"])
    assert job["state"] == "done", job["message"]
    saved = json.loads((folder / "transcript.json").read_text())
    assert [m["text"] for m in saved["messages"]] == ["Auggie rolls.", "Elara, three."]
    assert saved["messages"][1]["fixes"] == [{"start": 0, "end": 5, "heard": "Dallara"}]
    assert (folder / "transcript.orig.json").exists()
    assert client.post("/api/sessions/2026-01-01_000000-ffff/spelling").status_code == 404
