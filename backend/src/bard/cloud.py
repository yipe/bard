"""Bard Cloud: an optional, opt-in backup of the open campaign's vault, readable later in a browser.

Nothing here touches the network until the user clicks Connect. The service (a separate project) keeps
snapshots of the vault's files; this module only reads the vault and never changes it.

Sign-in is OAuth 2 with PKCE and a loopback redirect to this server (`/api/cloud/callback`); the service
says where to sign in (`GET /api/v1/client`). Tokens live in `data/cloud.json` (mode 600).

A backup: list the vault's files (hidden files skipped, transcripts only if the campaign opts in), hash
them, ask the service which it lacks (`plan`), PUT those to the signed URLs it returns, then `commit` the
file list as a snapshot. The API contract is `tests/cloud_contract.schema.json`, copied from the service.
"""

import base64
import hashlib
import json
import logging
import os
import secrets
import threading
import time
import unicodedata
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlencode

import httpx

from . import config

log = logging.getLogger("bard")

# The service's hard limits (contract.schema.json `limits`); a file over them is skipped, not fatal.
MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_FILES = 20_000
MAX_PATH_CHARS = 512
MAX_SEGMENT_CHARS = 255

TRANSCRIPTS = "Sessions/Transcripts/"
LOGIN_TTL_S = 600  # an unfinished sign-in is forgotten after 10 minutes
REFRESH_EARLY_S = 60


class CloudError(Exception):
    """The service refused or failed; `code` is the contract's error code (or "network")."""

    def __init__(self, code: str, message: str, body: dict | None = None):
        super().__init__(message)
        self.code = code
        self.body = body or {}


class NotConnected(CloudError):
    def __init__(self):
        super().__init__("unauthorized", "Not connected to Bard Cloud: connect first.")


def path_problem(path: str) -> str | None:
    """The service's path rules (contract `path_rules`); None when the path is fine."""
    if not path:
        return "path is empty"
    if len(path) > MAX_PATH_CHARS:
        return f"path is longer than {MAX_PATH_CHARS} characters"
    if unicodedata.normalize("NFC", path) != path:
        return "path must be Unicode NFC"
    if "\\" in path:
        return "path must use forward slashes"
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in path):
        return "path contains a control character"
    if path.startswith("/") or path.endswith("/"):
        return "path must be relative to the vault, without a trailing slash"
    for segment in path.split("/"):
        if not segment:
            return "path has an empty segment"
        if segment.startswith("."):
            return "hidden files and dot segments are not synced"
        if len(segment) > MAX_SEGMENT_CHARS:
            return f"a path segment is longer than {MAX_SEGMENT_CHARS} characters"
        if segment != segment.strip():
            return "a path segment starts or ends with whitespace"
    return None


def scan(root: Path, include_transcripts: bool) -> tuple[list[dict], dict[str, Path], list[str]]:
    """The vault's files as a manifest ({path, sha256, size}), where each hash's bytes live, and the
    files left out with why. Hidden folders (.obsidian, .trash) and symlinks are never followed."""
    files: list[dict] = []
    where: dict[str, Path] = {}
    skipped: list[str] = []
    by_lower: dict[str, str] = {}
    if not root.is_dir():
        return files, where, skipped
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        for name in sorted(filenames):
            full = Path(dirpath) / name
            if name.startswith(".") or full.is_symlink() or not full.is_file():
                continue
            rel = unicodedata.normalize("NFC", full.relative_to(root).as_posix())
            if rel.startswith(TRANSCRIPTS) and not include_transcripts:
                continue
            problem = path_problem(rel)
            size = full.stat().st_size
            if problem is None and size > MAX_FILE_BYTES:
                problem = f"larger than {MAX_FILE_BYTES // (1024 * 1024)} MB"
            if problem is None and rel.lower() in by_lower:
                problem = f"differs only in case from {by_lower[rel.lower()]}"
            if problem is None and len(files) >= MAX_FILES:
                problem = f"more than {MAX_FILES} files"
            if problem:
                skipped.append(f"{rel}: {problem}")
                continue
            data = full.read_bytes()
            sha = hashlib.sha256(data).hexdigest()
            by_lower[rel.lower()] = rel
            files.append({"path": rel, "sha256": sha, "size": len(data)})
            where[sha] = full
    return files, where, skipped


class Cloud:
    def __init__(
        self,
        url: str | None = None,
        path: Callable[[], Path] | None = None,
        http: httpx.Client | None = None,
    ):
        self.url = (url or config.CLOUD_URL).rstrip("/")
        self.path = path or (lambda: config.DATA_DIR / "cloud.json")
        self.http = http or httpx.Client(timeout=httpx.Timeout(30.0, connect=10.0), follow_redirects=False)
        self._lock = threading.Lock()
        self._pending: dict[str, tuple[str, dict, float]] = {}  # state -> (verifier, client config, started)

    # ---------------------------------------------------------------- stored state

    def _load(self) -> dict:
        try:
            data = json.loads(self.path().read_text())
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save(self, data: dict) -> None:
        """Atomically, readable by this user only: it holds the refresh token."""
        path = self.path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        os.chmod(tmp, 0o600)
        tmp.replace(path)

    def _update(self, change: Callable[[dict], None]) -> dict:
        with self._lock:
            data = self._load()
            change(data)
            self._save(data)
            return data

    def campaign_settings(self, campaign_id: str) -> dict:
        return {"include_transcripts": False, "last_backup": None} | self._load().get("campaigns", {}).get(campaign_id, {})

    def set_include_transcripts(self, campaign_id: str, on: bool) -> dict:
        self._update(lambda d: d.setdefault("campaigns", {}).setdefault(campaign_id, {}).update(include_transcripts=on))
        return self.campaign_settings(campaign_id)

    def status(self, campaign_id: str) -> dict:
        data = self._load()
        return {
            "url": self.url,
            "connected": bool(data.get("refresh_token") or data.get("access_token")),
            **self.campaign_settings(campaign_id),
        }

    # ---------------------------------------------------------------- sign-in

    def _request(self, method: str, url: str, **kwargs) -> httpx.Response:
        try:
            return self.http.request(method, url, **kwargs)
        except httpx.HTTPError as e:
            raise CloudError("network", f"Couldn't reach Bard Cloud ({type(e).__name__}).") from None

    def start_login(self) -> str:
        """The URL to open in the browser; the service's sign-in page comes back to /api/cloud/callback."""
        r = self._request("GET", f"{self.url}/api/v1/client")
        if r.status_code != 200:
            raise CloudError("internal", f"Bard Cloud sign-in isn't available ({r.status_code}).")
        client = r.json()
        expected = f"http://127.0.0.1:{config.PORT}/api/cloud/callback"
        if client.get("redirect_uri") != expected:
            raise ValueError(f"Bard Cloud signs in back to {client.get('redirect_uri')}; this Bard runs at {expected}.")
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        state = secrets.token_urlsafe(24)
        now = time.time()
        with self._lock:
            self._pending = {s: p for s, p in self._pending.items() if now - p[2] < LOGIN_TTL_S}
            self._pending[state] = (verifier, client, now)
        query = {
            "response_type": "code",
            "client_id": client["client_id"],
            "redirect_uri": client["redirect_uri"],
            "scope": " ".join(client["scopes"]),
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        return f"{client['authorize_url']}?{urlencode(query)}"

    def finish_login(self, code: str, state: str) -> None:
        with self._lock:
            pending = self._pending.pop(state, None)
        if pending is None or time.time() - pending[2] >= LOGIN_TTL_S:
            raise ValueError("This sign-in expired or didn't start here. Connect again from Bard.")
        verifier, client, _ = pending
        r = self._request(
            "POST",
            client["token_url"],
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": client["redirect_uri"],
                "client_id": client["client_id"],
                "code_verifier": verifier,
            },
        )
        if r.status_code != 200:
            raise CloudError("unauthorized", f"Bard Cloud didn't accept the sign-in ({r.status_code}).")
        self._store_tokens(r.json(), client)

    def _store_tokens(self, tokens: dict, client: dict) -> None:
        if not isinstance(tokens.get("access_token"), str):
            raise CloudError("unauthorized", "Bard Cloud sent no access token.")

        def change(d: dict) -> None:
            d["access_token"] = tokens["access_token"]
            d["refresh_token"] = tokens.get("refresh_token") or d.get("refresh_token")
            d["expires_at"] = time.time() + float(tokens.get("expires_in") or 3600)
            d["token_url"], d["client_id"] = client["token_url"], client["client_id"]

        self._update(change)

    def logout(self) -> None:
        """Forget the tokens (backup settings and history stay)."""

        def change(d: dict) -> None:
            for key in ("access_token", "refresh_token", "expires_at", "token_url", "client_id"):
                d.pop(key, None)

        self._update(change)

    def _token(self, force_refresh: bool = False) -> str:
        data = self._load()
        if not data.get("access_token"):
            raise NotConnected()
        if not force_refresh and time.time() < float(data.get("expires_at", 0)) - REFRESH_EARLY_S:
            return data["access_token"]
        if not data.get("refresh_token"):
            self.logout()
            raise NotConnected()
        r = self._request(
            "POST",
            data["token_url"],
            data={"grant_type": "refresh_token", "refresh_token": data["refresh_token"], "client_id": data["client_id"]},
        )
        if r.status_code in (400, 401):  # revoked or expired for good: sign in again
            self.logout()
            raise NotConnected()
        if r.status_code != 200:
            raise CloudError("internal", f"Bard Cloud couldn't refresh the sign-in ({r.status_code}).")
        self._store_tokens(r.json(), {"token_url": data["token_url"], "client_id": data["client_id"]})
        return self._load()["access_token"]

    # ---------------------------------------------------------------- API

    def _api(self, method: str, path: str, body: dict | None = None) -> dict | list:
        url = f"{self.url}/api/v1{path}"
        r = self._request(method, url, json=body, headers={"Authorization": f"Bearer {self._token()}"})
        if r.status_code == 401:  # the access token may have just expired or been revoked: refresh once
            r = self._request(method, url, json=body, headers={"Authorization": f"Bearer {self._token(force_refresh=True)}"})
        try:
            payload = r.json()
        except ValueError:
            payload = {}
        if r.is_success:
            return payload
        error = payload.get("error", {}) if isinstance(payload, dict) else {}
        raise CloudError(error.get("code", "internal"), error.get("message", f"Bard Cloud said {r.status_code}."), error)

    def backup(self, campaign_id: str, name: str, root: Path, progress: Callable[[int, int, str], None]) -> dict:
        """Push the vault as a new snapshot (or nothing, if it hasn't changed). Runs on a job thread."""
        try:
            return self._push(campaign_id, name, root, progress)
        except CloudError as e:
            # Another Mac pushed, a note changed mid-way, or an upload went missing: start over once.
            if e.code not in ("conflict", "missing_blobs", "changed", "upload"):
                raise
            log.info("cloud backup: retrying after %s", e.code)
            return self._push(campaign_id, name, root, progress)

    def _push(self, campaign_id: str, name: str, root: Path, progress: Callable[[int, int, str], None]) -> dict:
        progress(0, 0, "Reading the notes")
        files, where, skipped = scan(root, self.campaign_settings(campaign_id)["include_transcripts"])
        vault = self._api("POST", "/vaults", {"name": name.strip()[:100] or "Campaign", "bard_campaign_id": campaign_id})
        base = vault["head_snapshot"]
        plan = self._api("POST", f"/vaults/{vault['id']}/plan", {"base_snapshot": base, "files": files})
        uploads = plan["uploads"]
        for i, upload in enumerate(uploads):
            progress(i, len(uploads) + 1, f"Uploading {len(uploads)} changed files")
            data = where[upload["sha256"]].read_bytes()
            if hashlib.sha256(data).hexdigest() != upload["sha256"]:
                raise CloudError("changed", "A note changed during the backup.")
            r = self._request("PUT", upload["url"], content=data, headers=upload["headers"])
            if not r.is_success:
                raise CloudError("upload", f"An upload failed ({r.status_code}).")
        progress(len(uploads), len(uploads) + 1, "Saving the snapshot")
        result = self._api("POST", f"/vaults/{vault['id']}/commit", {"base_snapshot": base, "files": files})
        snapshot = result["snapshot"]
        last = {
            "at": time.time(),
            "snapshot": snapshot["id"],
            "files": snapshot["file_count"],
            "bytes": snapshot["total_bytes"],
            "uploaded": len(uploads),
            "unchanged": result["unchanged"],
            "skipped": skipped[:50],
            "quota": plan["quota"],
        }
        self._update(lambda d: d.setdefault("campaigns", {}).setdefault(campaign_id, {}).update(last_backup=last))
        return last
