"""Bard Cloud client against an in-process fake of the service (httpx.MockTransport): no network.

The fake follows tests/cloud_contract.schema.json, copied from the service; the drift test below fails when
the copy and this module disagree on limits or error codes."""

import base64
import hashlib
import json
import stat
import threading
import time
import unicodedata
import uuid
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient

from bard import cloud, config, server
from bard.cloud import AutoBackup, Cloud, CloudError, NotConnected, fingerprint, path_problem, scan

SERVICE = "https://cloud.test"
AUTH = "https://auth.test"
REDIRECT = "http://127.0.0.1:8765/api/cloud/callback"
CONTRACT = json.loads((Path(__file__).parent / "cloud_contract.schema.json").read_text())


class FakeService:
    """Sign-in, the sync API and signed uploads, with just enough rules to catch a misbehaving client."""

    def __init__(self):
        self.requests: list[tuple[str, str]] = []
        self.challenges: dict[str, str] = {}  # code -> PKCE challenge
        self.tokens: dict[str, str] = {}  # access token -> user
        self.refresh: dict[str, str] = {}  # refresh token -> user
        self.vaults: dict[str, dict] = {}
        self.snapshots: dict[str, list[dict]] = {}
        self.blobs: dict[str, bytes] = {}
        self.fail_next_commit: str | None = None  # an error code to answer the next commit with
        self.tampered: dict[str, bytes] = {}  # sha256 -> bytes served instead of the real ones
        self.rejecting: threading.Barrier | None = None
        self.foreign_sign_in = False  # tokens still refresh, but the API accepts none (another sign-in provider)

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def approve(self, authorize_url: str) -> tuple[str, str]:
        """What the user's browser does: sign in, get sent back with ?code&state."""
        q = parse_qs(urlparse(authorize_url).query)
        assert q["code_challenge_method"] == ["S256"]
        assert q["redirect_uri"] == [REDIRECT]
        assert "offline_access" in q["scope"][0].split()
        code = uuid.uuid4().hex
        self.challenges[code] = q["code_challenge"][0]
        return code, q["state"][0]

    def _issue(self, user: str) -> dict:
        access, refresh = f"at-{uuid.uuid4().hex}", f"rt-{uuid.uuid4().hex}"
        self.tokens[access], self.refresh[refresh] = user, user
        return {"access_token": access, "refresh_token": refresh, "expires_in": 86400, "token_type": "Bearer"}

    def handle(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.requests.append((request.method, url))
        if url == f"{SERVICE}/api/v1/client":
            return httpx.Response(
                200,
                json={
                    "authorize_url": f"{AUTH}/oauth/authorize",
                    "token_url": f"{AUTH}/oauth/token",
                    "client_id": "bard-test",
                    "scopes": ["profile", "email", "offline_access"],
                    "redirect_uri": REDIRECT,
                },
            )
        if url == f"{AUTH}/oauth/token":
            form = parse_qs(request.content.decode())
            if form["grant_type"] == ["authorization_code"]:
                challenge = self.challenges.pop(form["code"][0], None)
                verifier = form["code_verifier"][0].encode()
                expected = base64.urlsafe_b64encode(hashlib.sha256(verifier).digest()).rstrip(b"=").decode()
                if challenge != expected:
                    return httpx.Response(400, json={"error": "invalid_grant"})
                return httpx.Response(200, json=self._issue("user_gm"))
            user = self.refresh.pop(form["refresh_token"][0], None)
            return httpx.Response(200, json=self._issue(user)) if user else httpx.Response(400, json={"error": "invalid_grant"})
        if url.startswith("https://download.test/"):
            data = self.tampered.get(url.rsplit("/", 1)[1]) or self.blobs.get(url.rsplit("/", 1)[1])
            return httpx.Response(200, content=data) if data is not None else httpx.Response(404)
        if url.startswith("https://upload.test/"):
            sha = url.rsplit("/", 1)[1]
            if sha in self.blobs:
                return httpx.Response(400)
            assert request.headers["content-type"] == "application/octet-stream"
            self.blobs[sha] = request.content
            return httpx.Response(200)
        if url.startswith(f"{SERVICE}/api/v1/"):
            user = self.tokens.get(request.headers.get("authorization", "").removeprefix("Bearer "))
            if not user or self.foreign_sign_in:
                if self.rejecting is not None:
                    self.rejecting.wait(timeout=5)  # line rejected callers up, so their refreshes race
                return self.error(401, "unauthorized")
            return self.api(request.method, url.removeprefix(f"{SERVICE}/api/v1"), json.loads(request.content or b"null"))
        raise AssertionError(f"unexpected request {request.method} {url}")

    @staticmethod
    def error(status: int, code: str, **extra) -> httpx.Response:
        assert code in CONTRACT["schemas"]["ErrorResponse"]["properties"]["error"]["properties"]["code"]["enum"]
        return httpx.Response(status, json={"error": {"code": code, "message": code, **extra}})

    def api(self, method: str, path: str, body) -> httpx.Response:
        if (method, path) == ("POST", "/vaults"):
            assert set(body) == {"name", "bard_campaign_id"}
            assert 1 <= len(body["name"]) <= 100
            for v in self.vaults.values():
                if v["bard_campaign_id"] == body["bard_campaign_id"]:
                    return httpx.Response(200, json=v)
            v = {"id": str(uuid.uuid4()), "head_snapshot": None, "created_at": "2026-10-17T00:00:00Z", **body}
            self.vaults[v["id"]] = v
            return httpx.Response(200, json=v)
        if (method, path) == ("GET", "/vaults"):
            return httpx.Response(200, json=list(self.vaults.values()))
        if method == "GET" and "/snapshots/" in path:
            vault_id, snap_id = path.removeprefix("/vaults/").split("/snapshots/")
            files = self.snapshots.get(snap_id) if vault_id in self.vaults else None
            if files is None:
                return self.error(404, "not_found")
            listed = [f | {"url": f"https://download.test/{f['sha256']}"} for f in files]
            snap = {"id": snap_id, "parent": None, "created_at": "2026-10-17T00:00:00Z", "file_count": len(files)}
            return httpx.Response(200, json={"snapshot": snap | {"total_bytes": 0, "message": None}, "files": listed})
        vault_id, action = path.removeprefix("/vaults/").split("/")
        vault = self.vaults[vault_id]
        files = body["files"]
        for f in files:
            assert set(f) == {"path", "sha256", "size"}
            assert path_problem(f["path"]) is None, f
        if body["base_snapshot"] != vault["head_snapshot"]:
            return self.error(409, "conflict", head_snapshot=vault["head_snapshot"])
        if action == "plan":
            need = {f["sha256"]: f["size"] for f in files if f["sha256"] not in self.blobs}
            uploads = [
                {
                    "sha256": s,
                    "size": n,
                    "url": f"https://upload.test/{s}",
                    "headers": {"content-type": "application/octet-stream"},
                }
                for s, n in sorted(need.items())
            ]
            used = sum(len(b) for b in self.blobs.values())
            return httpx.Response(
                200,
                json={
                    "uploads": uploads,
                    "quota": {"used_bytes": used, "limit_bytes": 10**9, "after_bytes": used + sum(need.values())},
                },
            )
        assert action == "commit"
        if self.fail_next_commit:
            code, self.fail_next_commit = self.fail_next_commit, None
            if code == "conflict":  # someone else pushed in between
                vault["head_snapshot"] = str(uuid.uuid4())
                self.snapshots[vault["head_snapshot"]] = []
                return self.error(409, "conflict", head_snapshot=vault["head_snapshot"])
            return self.error(422, code, missing=[])
        for f in files:
            data = self.blobs.get(f["sha256"])
            if data is None or hashlib.sha256(data).hexdigest() != f["sha256"]:
                return self.error(422, "missing_blobs", missing=[f["sha256"]])
        head = vault["head_snapshot"]
        snap = {"parent": head, "created_at": "2026-10-17T00:00:00Z", "file_count": len(files)}
        snap["total_bytes"], snap["message"] = sum(f["size"] for f in files), None
        if head and sorted(map(json.dumps, self.snapshots[head])) == sorted(map(json.dumps, files)):
            return httpx.Response(200, json={"snapshot": {"id": head, **snap, "parent": None}, "unchanged": True})
        snap_id = str(uuid.uuid4())
        self.snapshots[snap_id], vault["head_snapshot"] = files, snap_id
        return httpx.Response(200, json={"snapshot": {"id": snap_id, **snap}, "unchanged": False})


@pytest.fixture
def service():
    return FakeService()


@pytest.fixture
def client(service, tmp_path):
    return Cloud(url=SERVICE, path=lambda: tmp_path / "data" / "cloud.json", http=httpx.Client(transport=service.transport()))


def connect(client: Cloud, service: FakeService) -> None:
    code, state = service.approve(client.start_login())
    client.finish_login(code, state)


@pytest.fixture
def vault(tmp_path):
    root = tmp_path / "campaign"
    (root / "People").mkdir(parents=True)
    (root / "People" / "Elara.md").write_text("# Elara\nA half-elf ranger.\n")
    (root / "Facts.md").write_text("The gate opens at dawn.\n")
    (root / "Sessions" / "Transcripts").mkdir(parents=True)
    (root / "Sessions" / "Transcripts" / "s1.md").write_text("GM: You see a gate.\n")
    (root / ".obsidian").mkdir()
    (root / ".obsidian" / "workspace.json").write_text("{}")
    (root / ".trash").mkdir()
    (root / ".trash" / "Old.md").write_text("gone")
    (root / ".Facts.md.tmp").write_text("half-written")
    return root


def noop(*_):
    pass


# ---------------------------------------------------------------- contract and paths


def test_limits_and_codes_match_the_service_contract():
    limits = CONTRACT["limits"]
    ours = {"maxFileBytes": cloud.MAX_FILE_BYTES, "maxFiles": cloud.MAX_FILES, "maxPathChars": cloud.MAX_PATH_CHARS}
    ours["maxSegmentChars"] = cloud.MAX_SEGMENT_CHARS
    assert {k: limits[k] for k in ours} == ours
    codes = set(CONTRACT["schemas"]["ErrorResponse"]["properties"]["error"]["properties"]["code"]["enum"])
    assert {"conflict", "missing_blobs", "unauthorized"} <= codes  # the codes backup() branches on


@pytest.mark.parametrize(
    "path", ["People/Elara.md", "Sessions/2026-10-01 The Sunken Gate.md", "Facts.md", "Places/Ül Dar's Keep.md"]
)
def test_path_problem_accepts(path):
    assert path_problem(path) is None


@pytest.mark.parametrize(
    ("path", "why"),
    [
        ("", "empty"),
        (unicodedata.normalize("NFD", "Notes/café.md"), "NFC"),
        (".obsidian/workspace.json", "hidden"),
        ("../etc/passwd", "hidden"),
        ("People//Elara.md", "empty segment"),
        ("/People/Elara.md", "relative"),
        ("People\\Elara.md", "forward slashes"),
        ("People/El\x00ara.md", "control"),
        ("People/Elara.md ", "whitespace"),
        ("a/" * 300 + "b.md", "longer than"),
        ("x" * 256 + ".md", "segment is longer"),
    ],
)
def test_path_problem_rejects(path, why):
    assert why in path_problem(path)


def test_scan_skips_hidden_files_and_transcripts_unless_asked(vault):
    files, where, skipped = scan(vault, include_transcripts=False)
    assert sorted(f["path"] for f in files) == ["Facts.md", "People/Elara.md"]
    assert skipped == []
    elara = next(f for f in files if f["path"] == "People/Elara.md")
    assert elara["sha256"] == hashlib.sha256(b"# Elara\nA half-elf ranger.\n").hexdigest()
    assert where[elara["sha256"]] == vault / "People" / "Elara.md"
    with_transcripts, _, _ = scan(vault, include_transcripts=True)
    assert "Sessions/Transcripts/s1.md" in [f["path"] for f in with_transcripts]


def test_scan_normalizes_names_and_reports_what_it_leaves_out(vault, monkeypatch):
    (vault / unicodedata.normalize("NFD", "Café.md")).write_text("menu")
    (vault / "Places").mkdir()
    (vault / "Places" / "Keep.md ").write_text("trailing space")
    (vault / "map.png").write_bytes(b"x" * 11)
    (vault / "link.md").symlink_to(vault / "Facts.md")
    monkeypatch.setattr(cloud, "MAX_FILE_BYTES", 10)
    files, _, skipped = scan(vault, include_transcripts=False)
    assert "Café.md" in [f["path"] for f in files]  # NFC, whatever the disk returned
    assert "link.md" not in [f["path"] for f in files]
    assert any(s.startswith("Places/Keep.md : ") and "whitespace" in s for s in skipped)
    assert any(s.startswith("map.png: larger than") for s in skipped)


def test_scan_of_a_missing_vault_is_empty(tmp_path):
    assert scan(tmp_path / "nowhere", include_transcripts=False) == ([], {}, [])


# ---------------------------------------------------------------- sign-in


def test_nothing_is_sent_before_connecting(client, service):
    assert client.status("main")["connected"] is False
    with pytest.raises(NotConnected):
        client.backup("main", "My campaign", Path("/nowhere"), noop)
    assert service.requests == []


def test_sign_in_with_pkce_stores_tokens_privately(client, service, tmp_path):
    connect(client, service)
    assert client.status("main")["connected"] is True
    saved = tmp_path / "data" / "cloud.json"
    assert stat.S_IMODE(saved.stat().st_mode) == 0o600
    assert json.loads(saved.read_text())["refresh_token"].startswith("rt-")


def test_sign_in_refuses_an_unknown_or_tampered_state(client, service):
    code, _ = service.approve(client.start_login())
    with pytest.raises(ValueError, match="expired or didn't start here"):
        client.finish_login(code, "someone-elses-state")
    assert client.status("main")["connected"] is False


def test_sign_in_state_works_once(client, service):
    code, state = service.approve(client.start_login())
    client.finish_login(code, state)
    with pytest.raises(ValueError, match="expired or didn't start here"):
        client.finish_login(code, state)


def test_sign_in_needs_the_port_the_service_redirects_to(client, monkeypatch):
    monkeypatch.setattr(config, "PORT", 8767)
    with pytest.raises(ValueError, match="this Bard runs at http://127.0.0.1:8767"):
        client.start_login()


def test_an_expired_access_token_is_refreshed(client, service):
    connect(client, service)
    service.tokens.clear()  # the service no longer accepts the access token
    client.backup("main", "My campaign", Path("/nowhere"), noop)
    assert any(url == f"{AUTH}/oauth/token" for _, url in service.requests[-6:])


def test_concurrent_callers_refresh_once(client, service, vault):
    """A job and a request both find the access token rejected: one refresh, and the sign-in survives
    (refresh tokens are single-use, so refreshing twice with the same one would sign the user out)."""
    connect(client, service)
    client.backup("main", "The Sunken Gate", vault, noop)
    service.tokens.clear()
    service.rejecting = threading.Barrier(2)
    results: list = []
    threads = [threading.Thread(target=lambda: results.append(client.vaults())) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert [len(r) for r in results] == [1, 1]
    assert sum(1 for _, url in service.requests if url == f"{AUTH}/oauth/token") == 2  # sign-in + one refresh
    assert client.status("main")["connected"] is True


def test_a_revoked_sign_in_disconnects(client, service):
    connect(client, service)
    service.tokens.clear()
    service.refresh.clear()
    with pytest.raises(NotConnected):
        client.backup("main", "My campaign", Path("/nowhere"), noop)
    assert client.status("main")["connected"] is False


def test_a_sign_in_the_service_no_longer_accepts_disconnects(client, service):
    """The service moved to another sign-in provider: the old tokens still refresh, but every one is refused.
    Bard must offer Connect again rather than fail every backup."""
    connect(client, service)
    service.foreign_sign_in = True
    with pytest.raises(NotConnected):
        client.vaults()
    assert client.status("main")["connected"] is False


def test_disconnect_forgets_tokens_but_keeps_settings(client, service):
    connect(client, service)
    client.set_options("main", include_transcripts=True)
    client.logout()
    assert client.status("main") | {"url": None} == {
        "url": None,
        "connected": False,
        "include_transcripts": True,
        "auto_backup": True,
        "last_backup": None,
        "last_error": None,
    }


# ---------------------------------------------------------------- backup


def test_first_backup_uploads_then_nothing_changes(client, service, vault):
    connect(client, service)
    steps: list[str] = []
    first = client.backup("main", "The Sunken Gate", vault, lambda d, t, m: steps.append(m))
    assert (first["files"], first["uploaded"], first["unchanged"]) == (2, 2, False)
    assert set(service.blobs.values()) == {b"# Elara\nA half-elf ranger.\n", b"The gate opens at dawn.\n"}
    assert "Saving the snapshot" in steps
    again = client.backup("main", "The Sunken Gate", vault, noop)
    assert (again["uploaded"], again["unchanged"], again["snapshot"]) == (0, True, first["snapshot"])
    assert client.status("main")["last_backup"]["snapshot"] == first["snapshot"]


def test_an_edit_uploads_only_that_note(client, service, vault):
    connect(client, service)
    client.backup("main", "The Sunken Gate", vault, noop)
    (vault / "Facts.md").write_text("The gate opens at dusk.\n")
    edited = client.backup("main", "The Sunken Gate", vault, noop)
    assert (edited["uploaded"], edited["unchanged"]) == (1, False)


def test_transcripts_go_up_only_when_the_campaign_opts_in(client, service, vault):
    connect(client, service)
    client.backup("main", "The Sunken Gate", vault, noop)
    assert b"GM: You see a gate.\n" not in service.blobs.values()
    client.set_options("main", include_transcripts=True)
    assert client.backup("main", "The Sunken Gate", vault, noop)["files"] == 3
    assert b"GM: You see a gate.\n" in service.blobs.values()


def test_campaigns_are_separate_vaults(client, service, vault):
    connect(client, service)
    client.backup("main", "The Sunken Gate", vault, noop)
    client.backup("c0123456789", "Second campaign", vault, noop)
    assert sorted(v["bard_campaign_id"] for v in service.vaults.values()) == ["c0123456789", "main"]


@pytest.mark.parametrize("code", ["conflict", "missing_blobs"])
def test_a_lost_race_or_lost_upload_retries_once(client, service, vault, code):
    connect(client, service)
    service.fail_next_commit = code
    result = client.backup("main", "The Sunken Gate", vault, noop)
    assert result["unchanged"] is False


def test_a_note_changing_mid_upload_starts_over(client, service, vault, monkeypatch):
    connect(client, service)
    real_scan, calls = cloud.scan, []

    def scan_then_edit(root, include, cache=None):
        out = real_scan(root, include, cache)
        calls.append(1)
        if len(calls) == 1:
            (vault / "Facts.md").write_text("Edited in Obsidian meanwhile.\n")
        return out

    monkeypatch.setattr(cloud, "scan", scan_then_edit)
    result = client.backup("main", "The Sunken Gate", vault, noop)
    assert len(calls) == 2
    assert result["unchanged"] is False
    assert b"Edited in Obsidian meanwhile.\n" in service.blobs.values()


def test_other_errors_surface(client, service, vault):
    connect(client, service)
    service.fail_next_commit = "quota_exceeded"
    with pytest.raises(CloudError) as e:
        client.backup("main", "The Sunken Gate", vault, noop)
    assert e.value.code == "quota_exceeded"


def test_network_failure_is_a_clean_error(tmp_path):
    def down(request):
        raise httpx.ConnectError("offline")

    c = Cloud(url=SERVICE, path=lambda: tmp_path / "cloud.json", http=httpx.Client(transport=httpx.MockTransport(down)))
    with pytest.raises(CloudError, match="Couldn't reach Bard Cloud"):
        c.start_login()


# ---------------------------------------------------------------- HTTP API


@pytest.fixture
def api(dirs, service, monkeypatch):
    monkeypatch.delenv("BARD_VAULT", raising=False)
    fake = Cloud(url=SERVICE, http=httpx.Client(transport=service.transport()))
    monkeypatch.setattr(server, "cloud", fake)
    return TestClient(server.app, base_url="http://127.0.0.1:8765")


def test_api_status_and_backup_needs_connecting(api, service):
    status = api.get("/api/cloud").json()
    assert (status["connected"], status["include_transcripts"], status["auto_backup"]) == (False, False, True)
    assert status["last_backup"] is None
    assert api.post("/api/cloud/backup").status_code == 400
    assert service.requests == []


def test_api_sign_in_round_trip(api, service):
    url = api.post("/api/cloud/connect").json()["authorize_url"]
    code, state = service.approve(url)
    page = api.get("/api/cloud/callback", params={"code": code, "state": state})
    assert page.status_code == 200
    assert "<h1>Connected</h1>" in page.text
    assert api.get("/api/cloud").json()["connected"] is True


def test_api_callback_escapes_and_refuses_strangers(api):
    page = api.get("/api/cloud/callback", params={"code": "x", "state": "<script>alert(1)</script>"})
    assert "Not connected" in page.text
    assert "<script>" not in page.text
    denied = api.get("/api/cloud/callback", params={"error": "access_denied"})
    assert "access_denied" in denied.text


def test_api_options_and_cross_site_refusal(api):
    assert api.put("/api/cloud/options", json={"include_transcripts": True}).json()["include_transcripts"] is True
    assert api.post("/api/cloud/connect", headers={"origin": "https://evil.example"}).status_code == 403


def test_api_backup_runs_as_a_job(api, service, dirs):
    code, state = service.approve(api.post("/api/cloud/connect").json()["authorize_url"])
    api.get("/api/cloud/callback", params={"code": code, "state": state})
    notes = config.vault_dir()
    notes.mkdir(parents=True, exist_ok=True)
    (notes / "Facts.md").write_text("The gate opens at dawn.\n")
    job = api.post("/api/cloud/backup").json()
    assert job["kind"] == "backup"
    for _ in range(200):
        job = next(j for j in server.memory.list_jobs() if j["id"] == job["id"])
        if job["state"] in ("done", "error"):
            break
        time.sleep(0.02)
    assert job["state"] == "done", job["message"]
    assert job["message"].startswith("Backed up")
    assert api.get("/api/cloud").json()["last_backup"]["files"] >= 1


# ---------------------------------------------------------------- failures are remembered


def test_a_failed_backup_is_remembered_until_one_succeeds(client, service, vault):
    connect(client, service)
    service.fail_next_commit = "quota_exceeded"
    with pytest.raises(CloudError):
        client.backup("main", "The Sunken Gate", vault, noop)
    assert client.status("main")["last_error"]["message"] == "quota_exceeded"
    client.backup("main", "The Sunken Gate", vault, noop)
    assert client.status("main")["last_error"] is None


# ---------------------------------------------------------------- fingerprint and automatic backups


def test_fingerprint_follows_backed_up_files_only(vault):
    base = fingerprint(vault, include_transcripts=False)
    (vault / ".obsidian" / "workspace.json").write_text('{"open": 2}')
    (vault / "Sessions" / "Transcripts" / "s1.md").write_text("GM: You see two gates.\n")
    assert fingerprint(vault, include_transcripts=False) == base
    assert fingerprint(vault, include_transcripts=True) != fingerprint(vault, include_transcripts=False)
    (vault / "People" / "Elara.md").write_text("# Elara\nA half-elf ranger, now a captain.\n")
    edited = fingerprint(vault, include_transcripts=False)
    assert edited != base
    (vault / "Places").mkdir()
    (vault / "Places" / "Ül Dar.md").write_text("# Ül Dar\n")
    assert fingerprint(vault, include_transcripts=False) != edited
    (vault / "Places" / "Ül Dar.md").unlink()
    assert fingerprint(vault, include_transcripts=False) == edited


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def auto(client, service, vault):
    """An AutoBackup on a fake clock whose submit runs the backup right away (as the job thread would)."""
    clock, submitted, state = Clock(), [], {"campaign": "main", "busy": False}

    def submit(cid, name, root):
        submitted.append(cid)
        client.backup(cid, name, root, noop)

    runner = AutoBackup(
        client,
        current=lambda: (state["campaign"], "The Sunken Gate", vault),
        busy=lambda: state["busy"],
        submit=submit,
        clock=clock,
    )
    return runner, clock, submitted, state


def test_auto_backup_waits_for_connecting(auto, service):
    runner, clock, submitted, _ = auto
    for _ in range(10):
        clock.t += 60
        runner.tick()
    assert submitted == []
    assert service.requests == []


def test_auto_backup_waits_for_the_notes_to_settle_then_backs_up_once(auto, client, service, vault):
    runner, clock, submitted, _ = auto
    connect(client, service)
    assert runner.tick() is False  # first sight of these notes
    clock.t += 60
    assert runner.tick() is False  # not quiet for long enough yet
    clock.t += 61
    assert runner.tick() is True
    assert submitted == ["main"]
    for _ in range(5):
        clock.t += 300
        assert runner.tick() is False  # backed up as they are


def test_auto_backup_follows_an_edit_but_not_more_often_than_the_gap(auto, client, service, vault):
    runner, clock, submitted, _ = auto
    connect(client, service)
    runner.tick()
    clock.t += AutoBackup.QUIET_S
    runner.tick()
    (vault / "Facts.md").write_text("The gate opens at dusk.\n")
    runner.tick()
    clock.t += AutoBackup.QUIET_S
    assert runner.tick() is False  # quiet, but the last automatic backup was too recent
    clock.t += AutoBackup.MIN_GAP_S
    assert runner.tick() is True
    assert len(submitted) == 2
    assert b"The gate opens at dusk.\n" in service.blobs.values()


def test_auto_backup_respects_the_switch_and_busy_jobs(auto, client, service):
    runner, clock, submitted, state = auto
    connect(client, service)
    client.set_options("main", auto_backup=False)
    for _ in range(3):
        clock.t += 300
        runner.tick()
    assert submitted == []
    client.set_options("main", auto_backup=True)
    state["busy"] = True
    runner.tick()
    clock.t += 300
    assert runner.tick() is False
    state["busy"] = False
    assert runner.tick() is True


def test_auto_backup_keeps_each_campaign_apart(auto, client, service):
    runner, clock, submitted, state = auto
    connect(client, service)
    runner.tick()
    state["campaign"] = "c0123456789"
    clock.t += 300
    assert runner.tick() is False  # a different campaign: its own quiet period starts now
    clock.t += AutoBackup.QUIET_S
    assert runner.tick() is True
    assert submitted == ["c0123456789"]


# ---------------------------------------------------------------- restore


def backed_up(client, service, vault) -> str:
    connect(client, service)
    client.backup("main", "The Sunken Gate", vault, noop)
    return next(iter(service.vaults))


def test_restore_brings_notes_back_and_keeps_what_it_replaces(client, service, vault):
    vault_id = backed_up(client, service, vault)
    (vault / "People" / "Elara.md").unlink()
    (vault / "Facts.md").write_text("Scribbled over.\n")
    (vault / "Only here.md").write_text("Written after the backup.\n")
    result = client.restore(vault_id, vault, noop)
    assert (result["restored"], result["replaced"], result["unchanged"]) == (2, 1, 0)
    assert (vault / "People" / "Elara.md").read_text() == "# Elara\nA half-elf ranger.\n"
    assert (vault / "Facts.md").read_text() == "The gate opens at dawn.\n"
    (kept,) = (vault / ".trash").glob("Before restore */Facts.md")
    assert kept.read_text() == "Scribbled over.\n"
    assert (vault / "Only here.md").read_text() == "Written after the backup.\n"  # a restore never deletes
    assert not list(vault.rglob("*.restore.tmp"))


def test_restore_into_an_empty_folder_on_a_new_mac(client, service, vault, tmp_path):
    vault_id = backed_up(client, service, vault)
    fresh = tmp_path / "new mac" / "campaign"
    result = client.restore(vault_id, fresh, noop)
    assert result["restored"] == 2
    assert (fresh / "People" / "Elara.md").read_text() == "# Elara\nA half-elf ranger.\n"
    again = client.restore(vault_id, fresh, noop)
    assert (again["restored"], again["unchanged"]) == (0, 2)


def test_restore_refuses_damaged_downloads(client, service, vault, tmp_path):
    vault_id = backed_up(client, service, vault)
    facts = hashlib.sha256(b"The gate opens at dawn.\n").hexdigest()
    service.tampered[facts] = b"The gate opens at dusk.\n"
    fresh = tmp_path / "fresh"
    with pytest.raises(CloudError, match="didn't arrive intact"):
        client.restore(vault_id, fresh, noop)
    assert not (fresh / "Facts.md").exists()


def test_restore_never_writes_outside_the_campaign_folder(client, service, vault, tmp_path):
    vault_id = backed_up(client, service, vault)
    outside = tmp_path / "outside"
    outside.mkdir()
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    (fresh / "People").symlink_to(outside, target_is_directory=True)
    head = service.vaults[vault_id]["head_snapshot"]
    sha = hashlib.sha256(b"The gate opens at dawn.\n").hexdigest()
    service.snapshots[head] += [{"path": "../escape.md", "sha256": sha, "size": 24}]
    result = client.restore(vault_id, fresh, noop)
    assert not list(outside.iterdir())
    assert not (tmp_path / "escape.md").exists()
    assert sorted(s.split(":")[0] for s in result["skipped"]) == ["../escape.md", "People/Elara.md"]


def test_restore_of_unknown_or_empty_vaults(client, service, vault):
    connect(client, service)
    with pytest.raises(CloudError, match="isn't in Bard Cloud"):
        client.restore(str(uuid.uuid4()), vault, noop)
    service.api("POST", "/vaults", {"name": "Empty", "bard_campaign_id": "empty"})
    empty = next(v["id"] for v in service.vaults.values() if v["bard_campaign_id"] == "empty")
    with pytest.raises(CloudError, match="hasn't been backed up yet"):
        client.restore(empty, vault, noop)


def wait_job(job_id: str) -> dict:
    for _ in range(300):
        job = next(j for j in server.memory.list_jobs() if j["id"] == job_id)
        if job["state"] in ("done", "error"):
            return job
        time.sleep(0.02)
    raise AssertionError("job didn't finish")


def test_api_restore_runs_as_a_job(api, service):
    assert api.get("/api/cloud/vaults").status_code == 401
    code, state = service.approve(api.post("/api/cloud/connect").json()["authorize_url"])
    api.get("/api/cloud/callback", params={"code": code, "state": state})
    notes = config.vault_dir()
    notes.mkdir(parents=True, exist_ok=True)
    (notes / "Facts.md").write_text("The gate opens at dawn.\n")
    assert wait_job(api.post("/api/cloud/backup").json()["id"])["state"] == "done"
    (notes / "Facts.md").unlink()
    (vault_id,) = [v["id"] for v in api.get("/api/cloud/vaults").json()]
    assert api.post("/api/cloud/restore", json={"vault_id": "not-an-id"}).status_code == 422
    job = wait_job(api.post("/api/cloud/restore", json={"vault_id": vault_id}).json()["id"])
    assert job["state"] == "done", job["message"]
    assert job["message"].startswith("Restored 1 files")
    assert (notes / "Facts.md").read_text() == "The gate opens at dawn.\n"


def test_api_options_turn_automatic_backups_off(api):
    status = api.put("/api/cloud/options", json={"auto_backup": False}).json()
    assert (status["auto_backup"], status["include_transcripts"]) == (False, False)


def test_auto_backup_leaves_a_campaign_with_no_notes_alone(client, service, tmp_path):
    connect(client, service)
    clock, submitted = Clock(), []
    runner = AutoBackup(
        client,
        current=lambda: ("c-new", "New campaign", tmp_path / "no notes yet"),
        busy=lambda: False,
        submit=lambda *a: submitted.append(a),
        clock=clock,
    )
    for _ in range(5):
        clock.t += 300
        runner.tick()
    assert submitted == []
    assert not service.vaults  # no vault used up on an empty campaign


def test_restore_skips_what_it_cant_write_and_keeps_going(client, service, vault, tmp_path):
    vault_id = backed_up(client, service, vault)
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    (fresh / "Facts.md").mkdir()  # a folder where the note should go
    (fresh / "People").write_text("a file where a folder should be")
    result = client.restore(vault_id, fresh, noop)
    assert result["restored"] == 0
    assert sorted(s.split(":")[0] for s in result["skipped"]) == ["Facts.md", "People/Elara.md"]
    assert (fresh / "People").read_text() == "a file where a folder should be"
    assert not list(fresh.rglob("*.restore.tmp"))


def test_api_automatic_failure_is_quiet_but_remembered(api, service):
    code, state = service.approve(api.post("/api/cloud/connect").json()["authorize_url"])
    api.get("/api/cloud/callback", params={"code": code, "state": state})
    notes = config.vault_dir()
    notes.mkdir(parents=True, exist_ok=True)
    (notes / "Facts.md").write_text("The gate opens at dawn.\n")
    service.fail_next_commit = "quota_exceeded"
    cid, name, root = server._open_campaign()
    job = wait_job(server._submit_backup(cid, name, root, automatic=True).id)
    assert job["state"] == "done"  # no red chip in the header
    assert job["message"].startswith("Automatic backup didn't run")
    assert api.get("/api/cloud").json()["last_error"]["message"] == "quota_exceeded"
