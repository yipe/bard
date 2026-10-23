"""HTTP/WebSocket API without models: TestClient is used without its context
manager, so the lifespan (model loading) never runs."""

import json

import pytest
from conftest import tone, write_wav
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from troubadour import config, server
from troubadour.engine import MAIN_CAMPAIGN, Campaign

LOCAL = "http://127.0.0.1:8765"
HOST = {"host": "127.0.0.1:8765"}  # TestClient's WebSocket otherwise sends "testserver", which the host check refuses


@pytest.fixture
def client(monkeypatch, tmp_path):
    samples = tmp_path / "samples"
    samples.mkdir()
    write_wav(samples / "scene.wav", tone(440, 1.0))
    (samples / "broken.wav").write_bytes(b"not a wav")
    monkeypatch.setattr(config, "SAMPLES_DIR", samples)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    eng = server.engine
    for d in (eng.players, eng.bench, eng.voices, eng.parked, eng._parked_vecs):
        d.clear()
    eng.campaigns, eng.campaign = {MAIN_CAMPAIGN: Campaign(MAIN_CAMPAIGN, "My campaign")}, MAIN_CAMPAIGN
    return TestClient(server.app, base_url=LOCAL)


def test_state_shape(client):
    s = client.get("/api/state").json()
    assert set(s) == {"session", "campaign", "campaigns", "players", "library", "voices", "messages"}
    assert s["campaign"] == "main"
    assert s["campaigns"] == [{"id": "main", "name": "My campaign"}]
    assert {"status", "source", "t", "llm", "asr", "training", "error", "audio_monitor"} <= set(s["session"])


def test_llm_recheck(client, monkeypatch):
    calls = []

    def healthy():
        calls.append(1)
        return True

    monkeypatch.setattr(server.engine.cleanup_llm, "healthy", healthy)
    server.engine.llm_ok = False
    try:
        body = client.post("/api/llm/recheck").json()
        assert body["ok"] is True
        assert calls == [1]
        assert client.get("/api/state").json()["session"]["llm"]["ok"] is True
    finally:
        server.engine.llm_ok = False


def test_audio_monitor_toggle(client):
    try:
        assert client.put("/api/audio/monitor", json={"on": False}).json() == {"on": False}
        assert client.get("/api/state").json()["session"]["audio_monitor"] is False
        assert client.put("/api/audio/monitor", json={"on": "maybe"}).status_code == 422
    finally:
        client.put("/api/audio/monitor", json={"on": True})  # the engine is shared by every test
    assert client.get("/api/state").json()["session"]["audio_monitor"] is True


def test_sources_lists_readable_wavs_only(client, monkeypatch):
    monkeypatch.setattr(server, "list_mics", lambda: [{"name": "Mic", "default": True}])
    s = client.get("/api/sources").json()
    assert [f["name"] for f in s["files"]] == ["scene.wav"]
    assert s["files"][0]["audio_url"] == "/api/files/scene.wav"
    assert s["mics"] == [{"name": "Mic", "default": True}]


def test_player_crud(client):
    p = client.post("/api/players", json={"name": "  Alice  ", "character": "Elara"}).json()
    assert p["name"] == "Alice"  # trimmed
    assert p["color"].startswith("#")
    r = client.patch(f"/api/players/{p['id']}", json={"is_gm": True, "color": "#00ff00"})
    assert r.json()["is_gm"] is True
    assert client.delete(f"/api/players/{p['id']}").status_code == 204
    assert client.delete(f"/api/players/{p['id']}").status_code == 404


@pytest.mark.parametrize(
    "body",
    [
        {"name": ""},
        {"name": "x" * 61},
        {"name": "A", "character": "c" * 61},
        {"name": "A", "avatar": "https://tracker.example/pixel.png"},
        {"name": "A", "avatar": "data:image/svg+xml;base64,PHN2Zz4="},  # svg can carry script
        {"name": "A", "avatar": "data:image/png;base64," + "A" * 300_001},
    ],
)
def test_player_input_validation(client, body):
    assert client.post("/api/players", json=body).status_code == 422


def test_bad_color_rejected(client):
    p = client.post("/api/players", json={"name": "A"}).json()
    assert client.patch(f"/api/players/{p['id']}", json={"color": "red; background:url(x)"}).status_code == 422


def test_avatar_can_be_cleared(client):
    small = "data:image/png;base64,iVBORw0KGgo="
    p = client.post("/api/players", json={"name": "A", "avatar": small}).json()
    assert client.patch(f"/api/players/{p['id']}", json={"avatar": None}).json()["avatar"] is None


def test_campaigns_switch_and_share_the_library(client, monkeypatch):
    monkeypatch.setattr(server.memory, "busy", lambda: False)
    jun = client.post("/api/players", json={"name": "Jun", "character": "Auggie"}).json()
    c = client.post("/api/campaigns", json={"name": "  Sunken Crown "}).json()
    assert c["name"] == "Sunken Crown"
    assert client.post(f"/api/campaigns/{c['id']}/open").json()["campaign"] == c["id"]
    s = client.get("/api/state").json()
    assert s["players"] == []
    assert [p["name"] for p in s["library"]] == ["Jun"]
    seated = client.post(f"/api/players/{jun['id']}/seat", json={"character": "Liss"}).json()
    assert (seated["id"], seated["character"]) == (jun["id"], "Liss")
    assert client.post(f"/api/players/{jun['id']}/seat", json={}).status_code == 400  # already playing
    client.post(f"/api/campaigns/{MAIN_CAMPAIGN}/open").raise_for_status()
    assert [p["character"] for p in client.get("/api/state").json()["players"]] == ["Auggie"]
    assert client.patch(f"/api/campaigns/{c['id']}", json={"name": "Morvath"}).json()["name"] == "Morvath"
    assert client.post(f"/api/players/{jun['id']}/bench").status_code == 204
    assert client.get("/api/state").json()["players"] == []


def test_campaign_validation_and_unknown_ids(client, monkeypatch):
    monkeypatch.setattr(server.memory, "busy", lambda: False)
    assert client.post("/api/campaigns", json={"name": ""}).status_code == 422
    assert client.post("/api/campaigns", json={"name": "x" * 61}).status_code == 422
    assert client.post("/api/campaigns/c-nope/open").status_code == 404
    assert client.patch("/api/campaigns/c-nope", json={"name": "A"}).status_code == 404
    assert client.post("/api/players/p-nope/seat", json={}).status_code == 404
    assert client.post("/api/players/p-nope/bench").status_code == 404


def test_no_switching_while_campaign_jobs_run(client, monkeypatch):
    monkeypatch.setattr(server.memory, "busy", lambda: True)
    c = client.post("/api/campaigns", json={"name": "B"}).json()
    r = client.post(f"/api/campaigns/{c['id']}/open")
    assert r.status_code == 400
    assert server.engine.campaign == MAIN_CAMPAIGN


def test_jobs_refuse_another_campaigns_session(client):
    sid = "2026-10-02_190000-bbbb"
    folder = config.DATA_DIR / "sessions" / sid
    folder.mkdir(parents=True)
    (folder / "meta.json").write_text(json.dumps({"id": sid, "campaign": "c-other"}))
    (folder / "transcript.json").write_text(json.dumps({"session": {"id": sid}, "messages": []}))
    for path in (f"/api/campaign/writeup/{sid}", f"/api/sessions/{sid}/spelling"):
        r = client.post(path)
        assert r.status_code == 400, path
        assert "another campaign" in r.json()["detail"]


@pytest.mark.parametrize(
    "source",
    [
        {"kind": "tape"},
        {"kind": "file", "name": "scene.wav", "speed": 10},
        {"kind": "file", "name": "scene.wav", "speed": 0},
        {"kind": "file"},
    ],
)
def test_start_validation(client, source):
    assert client.post("/api/session/start", json={"source": source}).status_code == 422


@pytest.mark.parametrize("name", ["../data/state.json", "broken.txt", "missing.wav"])
def test_start_file_must_be_a_sample(client, name):
    assert client.post("/api/session/start", json={"source": {"kind": "file", "name": name}}).status_code == 404


@pytest.mark.parametrize("path", ["/api/files/..%2Fdata%2Fstate.json", "/api/files/missing.wav", "/api/files/broken.txt"])
def test_file_endpoint_refuses_traversal(client, path):
    assert client.get(path).status_code in (400, 404)


def test_file_endpoint_serves_samples_with_range(client):
    r = client.get("/api/files/scene.wav", headers={"Range": "bytes=0-3"})
    assert r.status_code == 206
    assert r.content == b"RIFF"


def test_train_validation(client):
    p = client.post("/api/players", json={"name": "A"}).json()
    assert client.post(f"/api/players/{p['id']}/train", json={"seconds": 1}).status_code == 422
    assert client.post(f"/api/players/{p['id']}/train", json={"seconds": 600}).status_code == 422
    assert client.post("/api/players/p-nobody/train", json={"seconds": 20}).status_code in (400, 404)


def test_cancel_training_when_idle_is_harmless(client):
    assert client.post("/api/train/cancel").status_code == 204


def test_unknown_voice_is_404(client):
    assert client.post("/api/voices/v-nobody/unassign").status_code == 404
    assert client.get("/api/voices/v-nobody/sample.wav").status_code == 404


def test_unknown_line_is_404(client):
    p = client.post("/api/players", json={"name": "A"}).json()
    assert client.post("/api/messages/m-nobody/speaker", json={"player_id": p["id"]}).status_code == 404
    assert client.post("/api/messages/m-nobody/speaker", json={}).status_code == 422


def test_cross_site_writes_refused(client):
    r = client.post("/api/players", json={"name": "A"}, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    ok = client.post("/api/players", json={"name": "A"}, headers={"Origin": "http://localhost:5173"})  # Vite dev
    assert ok.status_code == 200


def test_foreign_host_refused(client):
    assert client.get("/api/state", headers={"Host": "evil.example"}).status_code == 400


def test_websocket_sends_snapshot_first(client):
    with client.websocket_connect("/ws", headers=HOST) as ws:
        first = ws.receive_json()
    assert first["type"] == "snapshot"
    assert "players" in first["data"]


def test_websocket_refuses_other_origins(client):
    headers = {**HOST, "origin": "https://evil.example"}
    with pytest.raises(WebSocketDisconnect) as exc, client.websocket_connect("/ws", headers=headers) as ws:
        ws.receive_json()
    assert exc.value.code == 1008


def test_websocket_refuses_foreign_host(client):
    from starlette.testclient import WebSocketDenialResponse

    with pytest.raises(WebSocketDenialResponse), client.websocket_connect("/ws") as ws:
        ws.receive_json()


def test_web_cache_headers(tmp_path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<html></html>")
    (tmp_path / "assets" / "index-abc.js").write_text("x")
    from starlette.applications import Starlette
    from starlette.routing import Mount

    app = Starlette(routes=[Mount("/", server.WebFiles(directory=tmp_path, html=True))])
    c = TestClient(app)
    assert c.get("/").headers["cache-control"] == "no-cache"
    assert "immutable" in c.get("/assets/index-abc.js").headers["cache-control"]


def test_web_serves_index_for_ui_routes(tmp_path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<html>ui</html>")
    from starlette.applications import Starlette
    from starlette.routing import Mount

    app = Starlette(routes=[Mount("/", server.WebFiles(directory=tmp_path, html=True))])
    c = TestClient(app)
    for path in ("/history", "/history/2026-10-15_193130-762b", "/campaign/St.%20Cuthbert"):
        r = c.get(path)
        assert (r.status_code, r.text, r.headers["cache-control"]) == (200, "<html>ui</html>", "no-cache")
    assert c.get("/api/nope").status_code == 404
    assert c.get("/assets/gone.js").status_code == 404
