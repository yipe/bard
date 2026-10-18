"""Campaign endpoints, against a tmp vault (config.vault_dir() follows the dirs fixture)."""

import json
import time

import pytest
from fastapi.testclient import TestClient
from test_memory import FakeLLM

from troubadour import config, server

LOCAL = "http://127.0.0.1:8765"


@pytest.fixture
def api(dirs, monkeypatch):
    monkeypatch.delenv("TROUBADOUR_VAULT", raising=False)
    assert str(config.vault_dir()).startswith(str(dirs[0]))  # never the real vault
    return TestClient(server.app, base_url=LOCAL)


def test_entity_lifecycle(api):
    r = api.post("/api/campaign/entities", json={"name": "Elara", "type": "pc", "heard_as": ["Dilara"]})
    assert r.status_code == 200, r.text
    assert r.json()["path"] == "People/Elara.md"
    assert api.post("/api/campaign/entities", json={"name": "dilara"}).status_code == 400  # taken (heard-as)
    r = api.patch("/api/campaign/entities/Elara", json={"name": "Elara Moonwhisper", "description": "Ranger."})
    assert r.json()["aliases"] == ["Elara"]
    assert api.get("/api/campaign/entities/Elara%20Moonwhisper").json()["description"] == "Ranger."
    r = api.post("/api/campaign/entities/Elara%20Moonwhisper/facts", json={"text": "Has an owl.", "suggested": True})
    assert r.json()["suggested_facts"] == ["Has an owl."]
    listing = api.get("/api/campaign").json()
    assert [e["name"] for e in listing["entities"]] == ["Elara Moonwhisper"]
    assert listing["types"][0] == "pc"
    assert api.delete("/api/campaign/entities/Elara%20Moonwhisper").status_code == 204
    assert api.get("/api/campaign/entities/Elara%20Moonwhisper").status_code == 404


def test_merge_and_campaign_facts(api):
    api.post("/api/campaign/entities", json={"name": "Kessia", "type": "pc"})
    api.post("/api/campaign/entities", json={"name": "Cassia", "type": "npc"})
    r = api.post("/api/campaign/entities/Cassia/merge", json={"into": "Kessia"})
    assert r.json()["aliases"] == ["Cassia"]
    assert api.post("/api/campaign/facts", json={"text": "The party is in Dallon."}).json() == ["The party is in Dallon."]
    assert api.put("/api/campaign/facts", json={"facts": ["A", "B"]}).json() == ["A", "B"]


def test_campaign_names_its_obsidian_vault_by_registry_id(api, tmp_path, monkeypatch):
    assert api.get("/api/campaign").json()["obsidian"] is None  # not registered in Obsidian
    root = api.get("/api/campaign").json()["vault"]
    registry = tmp_path / "obsidian.json"
    registry.write_text(json.dumps({"vaults": {"543fe3e0024d1d47": {"path": root}}}))
    monkeypatch.setenv("TROUBADOUR_OBSIDIAN_REGISTRY", str(registry))
    assert api.get("/api/campaign").json()["obsidian"] == {"id": "543fe3e0024d1d47", "folder": ""}


@pytest.mark.parametrize(
    "body",
    [{"name": "x", "type": "wizard"}, {"name": ""}, {"name": "x" * 121}, {"name": "x", "aliases": ["a"] * 51}],
)
def test_entity_validation(api, body):
    assert api.post("/api/campaign/entities", json=body).status_code == 422


def test_import_runs_as_a_job(api, monkeypatch):
    llm = FakeLLM(
        notes=[
            {
                "entities": [{"name": "Magenta", "type": "npc", "aliases": [], "description": "A bard.", "facts": []}],
                "campaign_facts": [],
            }
        ]
    )
    monkeypatch.setattr(server.memory, "llm", llm)
    job = api.post("/api/campaign/import", json={"title": "Session 3", "text": "Magenta sang."}).json()
    assert job["state"] in ("queued", "running", "done")
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        jobs = {j["id"]: j for j in api.get("/api/campaign").json()["jobs"]}
        if jobs[job["id"]]["state"] == "done":
            break
        time.sleep(0.02)
    assert jobs[job["id"]]["created"] == ["Magenta"]
    assert api.post("/api/campaign/import", json={"title": "x", "text": ""}).status_code == 422


def test_writeup_rejects_unknown_or_bad_ids(api):
    assert api.post("/api/campaign/writeup/2026-01-01_000000-ffff").status_code == 404
    assert api.post("/api/campaign/writeup/..%2F..%2Fetc").status_code in (404, 405)  # never reaches a handler


def test_suggestions_shape(api):
    assert api.get("/api/campaign/suggestions").json() == {"misheard": [], "unknown": []}


def test_cross_site_writes_are_refused(api):
    r = api.post("/api/campaign/entities", json={"name": "Evil"}, headers={"origin": "https://evil.example"})
    assert r.status_code == 403


def test_review_endpoint(api):
    api.post("/api/campaign/entities", json={"name": "Elara", "type": "pc"})
    api.patch("/api/campaign/entities/Elara", json={"heard_as_suggested": ["Dallara", "Dilara"]})
    api.post("/api/campaign/entities/Elara/review", json={"accept_spellings": ["Dallara"]})
    r = api.post("/api/campaign/entities/Elara/review", json={"accept_spellings": ["Dilara"]})
    assert r.json()["heard_as"] == ["Dallara", "Dilara"]
    assert r.json()["heard_as_suggested"] == []


def test_ignore_endpoints(api):
    assert api.post("/api/campaign/ignore", json={"words": ["T-Mobile"]}).json() == ["T-Mobile"]
    assert api.get("/api/campaign").json()["ignored"] == ["T-Mobile"]
    assert api.delete("/api/campaign/ignore/T-Mobile").json() == []
    assert api.post("/api/campaign/ignore", json={"words": []}).status_code == 422
