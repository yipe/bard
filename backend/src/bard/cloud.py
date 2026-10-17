"""Bard Cloud: an optional, opt-in backup of the open campaign's vault, readable later in a browser.

Nothing here touches the network until the user clicks Connect. The service (a separate project) keeps
snapshots of the vault's files. A backup only reads the vault; only a restore, which the user starts, writes
to it (and moves what it replaces to `.trash/`). Automatic backups (`AutoBackup`) follow notes as they change.

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
import stat
import threading
import time
import unicodedata
from collections.abc import Callable, Iterator
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


def _vault_files(root: Path, include_transcripts: bool) -> Iterator[tuple[Path, str, os.stat_result]]:
    """(file, vault-relative NFC path, stat) for every regular file a backup considers. Hidden files and
    folders (.obsidian, .trash, temp files) and symlinks are never followed."""
    if not root.is_dir():
        return
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        for name in sorted(filenames):
            full = Path(dirpath) / name
            if name.startswith(".") or full.is_symlink():
                continue
            rel = unicodedata.normalize("NFC", full.relative_to(root).as_posix())
            if rel.startswith(TRANSCRIPTS) and not include_transcripts:
                continue
            try:
                st = full.stat()
            except OSError:
                continue  # deleted while we looked
            if stat.S_ISREG(st.st_mode):
                yield full, rel, st


def fingerprint(root: Path, include_transcripts: bool) -> str:
    """Changes when any backed-up file is added, removed, resized or touched; stats only, no reading."""
    h = hashlib.sha256()
    for _, rel, st in _vault_files(root, include_transcripts):
        h.update(f"{rel}\0{st.st_size}\0{st.st_mtime_ns}\n".encode())
    return h.hexdigest()


def scan(
    root: Path, include_transcripts: bool, cache: dict[Path, tuple[int, int, str]] | None = None
) -> tuple[list[dict], dict[str, Path], list[str]]:
    """The vault's files as a manifest ({path, sha256, size}), where each hash's bytes live, and the
    files left out with why. `cache` (file → size, mtime, sha256) skips re-reading unchanged files."""
    files: list[dict] = []
    where: dict[str, Path] = {}
    skipped: list[str] = []
    by_lower: dict[str, str] = {}
    for full, rel, st in _vault_files(root, include_transcripts):
        problem = path_problem(rel)
        if problem is None and st.st_size > MAX_FILE_BYTES:
            problem = f"larger than {MAX_FILE_BYTES // (1024 * 1024)} MB"
        if problem is None and rel.lower() in by_lower:
            problem = f"differs only in case from {by_lower[rel.lower()]}"
        if problem is None and len(files) >= MAX_FILES:
            problem = f"more than {MAX_FILES} files"
        if problem:
            skipped.append(f"{rel}: {problem}")
            continue
        known = cache.get(full) if cache is not None else None
        if known and known[:2] == (st.st_size, st.st_mtime_ns):
            size, sha = st.st_size, known[2]
        else:
            try:
                data = full.read_bytes()
            except OSError:
                continue
            if len(data) > MAX_FILE_BYTES:  # grew since stat
                skipped.append(f"{rel}: larger than {MAX_FILE_BYTES // (1024 * 1024)} MB")
                continue
            size, sha = len(data), hashlib.sha256(data).hexdigest()
            if cache is not None and len(data) == st.st_size:
                cache[full] = (st.st_size, st.st_mtime_ns, sha)
        by_lower[rel.lower()] = rel
        files.append({"path": rel, "sha256": sha, "size": size})
        where[sha] = full
    return files, where, skipped


def _inside(target: Path, root: Path) -> bool:
    """`target` lands inside `root` even after following symlinked folders on the way."""
    real_root = root.resolve()
    real = target.parent.resolve() / target.name
    return real_root == real.parent or real_root in real.parents


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
        self._hashes: dict[Path, tuple[int, int, str]] = {}  # file -> (size, mtime_ns, sha256), for scan()

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

    DEFAULTS = {"include_transcripts": False, "auto_backup": True, "last_backup": None, "last_error": None}

    def campaign_settings(self, campaign_id: str) -> dict:
        return self.DEFAULTS | self._load().get("campaigns", {}).get(campaign_id, {})

    def _set(self, campaign_id: str, **values) -> None:
        self._update(lambda d: d.setdefault("campaigns", {}).setdefault(campaign_id, {}).update(values))

    def set_options(self, campaign_id: str, include_transcripts: bool | None = None, auto_backup: bool | None = None) -> dict:
        options = {"include_transcripts": include_transcripts, "auto_backup": auto_backup}
        self._set(campaign_id, **{k: v for k, v in options.items() if v is not None})
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
        client = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        fields = ("authorize_url", "token_url", "client_id", "redirect_uri")
        if not all(isinstance(client.get(k), str) for k in fields) or not isinstance(client.get("scopes"), list):
            raise CloudError("internal", "Bard Cloud's sign-in settings don't look right.")
        if not all(client[k].startswith("https://") for k in ("authorize_url", "token_url")):
            raise CloudError("internal", "Bard Cloud's sign-in must use HTTPS.")
        expected = f"http://127.0.0.1:{config.PORT}/api/cloud/callback"
        if client["redirect_uri"] != expected:
            raise ValueError(f"Bard Cloud signs in back to {client['redirect_uri']}; this Bard runs at {expected}.")
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
        """Push the vault as a new snapshot (or nothing, if it hasn't changed). Runs on a job thread.
        A failure is remembered (`last_error`) until the next backup succeeds."""
        try:
            try:
                last = self._push(campaign_id, name, root, progress)
            except CloudError as e:
                # Another Mac pushed, a note changed mid-way, or an upload went missing: start over once.
                if e.code not in ("conflict", "missing_blobs", "changed", "upload"):
                    raise
                log.info("cloud backup: retrying after %s", e.code)
                last = self._push(campaign_id, name, root, progress)
        except Exception as e:
            self._set(campaign_id, last_error={"at": time.time(), "message": str(e) or type(e).__name__})
            raise
        self._set(campaign_id, last_backup=last, last_error=None)
        return last

    def _push(self, campaign_id: str, name: str, root: Path, progress: Callable[[int, int, str], None]) -> dict:
        progress(0, 0, "Reading the notes")
        include = self.campaign_settings(campaign_id)["include_transcripts"]
        # Taken before reading: an edit made during the backup changes it, so a later backup picks the edit up.
        before = fingerprint(root, include)
        files, where, skipped = scan(root, include, self._hashes)
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
        return {
            "at": time.time(),
            "snapshot": snapshot["id"],
            "files": snapshot["file_count"],
            "bytes": snapshot["total_bytes"],
            "uploaded": len(uploads),
            "unchanged": result["unchanged"],
            "skipped": skipped[:50],
            "quota": plan["quota"],
            "fingerprint": before,
        }

    # ---------------------------------------------------------------- restore

    def vaults(self) -> list[dict]:
        """The campaigns this account has in Bard Cloud (any Mac's)."""
        result = self._api("GET", "/vaults")
        return result if isinstance(result, list) else []

    def restore(self, vault_id: str, root: Path, progress: Callable[[int, int, str], None]) -> dict:
        """Bring a vault's newest snapshot back into `root`. Notes that differ are replaced and their current
        versions moved to `.trash/`; notes the snapshot doesn't have are left alone (a restore never deletes).
        Every file is checked against its sha256 before it's written. Runs on a job thread."""
        progress(0, 0, "Finding the newest backup")
        vault = next((v for v in self.vaults() if v.get("id") == vault_id), None)
        if vault is None:
            raise CloudError("not_found", "That campaign isn't in Bard Cloud.")
        if not vault.get("head_snapshot"):
            raise CloudError("not_found", "That campaign hasn't been backed up yet.")
        listing = self._api("GET", f"/vaults/{vault_id}/snapshots/{vault['head_snapshot']}")
        files = listing["files"]
        root.mkdir(parents=True, exist_ok=True)
        trash = root / ".trash" / f"Before restore {time.strftime('%Y-%m-%d %H%M%S')}"
        counts = {"restored": 0, "unchanged": 0, "replaced": 0}
        skipped: list[str] = []
        for i, f in enumerate(files):
            progress(i, len(files), f"Restoring {len(files)} files")
            path = f.get("path", "")
            problem = path_problem(path) if isinstance(path, str) else "not a path"
            target = root / path
            if problem is None and (not _inside(target, root) or target.is_symlink()):
                problem = "would land outside the campaign folder"
            if problem:
                skipped.append(f"{path}: {problem}")
                continue
            if target.is_file() and hashlib.sha256(target.read_bytes()).hexdigest() == f["sha256"]:
                counts["unchanged"] += 1
                continue
            data = self._download(f["url"], f["size"])
            if hashlib.sha256(data).hexdigest() != f["sha256"]:
                raise CloudError("integrity", f"{path} didn't arrive intact; nothing more was restored.")
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                kept = trash / path
                kept.parent.mkdir(parents=True, exist_ok=True)
                target.replace(kept)
                counts["replaced"] += 1
            tmp = target.with_name(f".{target.name}.restore.tmp")
            tmp.write_bytes(data)
            tmp.replace(target)
            counts["restored"] += 1
        return {**counts, "files": len(files), "snapshot": listing["snapshot"]["id"], "skipped": skipped[:50]}

    def _download(self, url: str, size: int) -> bytes:
        """At most `size` bytes from a signed URL (the service says how big each file is)."""
        if not isinstance(url, str) or not url.startswith("https://"):
            raise CloudError("integrity", "Bard Cloud sent a download link that isn't HTTPS.")
        try:
            with self.http.stream("GET", url) as r:
                if not r.is_success:
                    raise CloudError("download", f"A download failed ({r.status_code}).")
                data = bytearray()
                for chunk in r.iter_bytes():
                    data += chunk
                    if len(data) > size:
                        raise CloudError("integrity", "A download was larger than it should be.")
                return bytes(data)
        except httpx.HTTPError as e:
            raise CloudError("network", f"Couldn't reach Bard Cloud ({type(e).__name__}).") from None


class AutoBackup:
    """Backs up the open campaign on its own: once its notes have changed and then stayed still for
    QUIET_S, at most every MIN_GAP_S, only while connected and the campaign has it on. `tick()` decides;
    `run()` calls it every CHECK_S on a thread."""

    CHECK_S = 30
    QUIET_S = 120
    MIN_GAP_S = 600

    def __init__(
        self,
        cloud: Cloud,
        current: Callable[[], tuple[str, str, Path]],
        busy: Callable[[], bool],
        submit: Callable[[str, str, Path], None],
        clock: Callable[[], float] = time.monotonic,
    ):
        self.cloud, self.current, self.busy, self.submit, self.clock = cloud, current, busy, submit, clock
        self._seen: dict[str, tuple[str, float]] = {}  # campaign -> (fingerprint, when first seen)
        self._attempted: dict[str, float] = {}  # campaign -> last automatic backup started
        self._stop = threading.Event()

    def tick(self) -> bool:
        """Start a backup if one is due; True when it did."""
        cid, name, root = self.current()
        status = self.cloud.status(cid)
        if not (status["connected"] and status["auto_backup"]):
            self._seen.pop(cid, None)
            return False
        now = self.clock()
        fp = fingerprint(root, status["include_transcripts"])
        if fp == (status["last_backup"] or {}).get("fingerprint"):
            return False  # backed up as it is now
        seen = self._seen.get(cid)
        if seen is None or seen[0] != fp:
            self._seen[cid] = (fp, now)  # just changed: wait until it stays still
            return False
        if now - seen[1] < self.QUIET_S:
            return False
        if cid in self._attempted and now - self._attempted[cid] < self.MIN_GAP_S:
            return False
        if self.busy():
            return False  # a write-up or import is running: it will change the notes anyway
        self._attempted[cid] = now
        self.submit(cid, name, root)
        return True

    def run(self) -> None:
        while not self._stop.wait(self.CHECK_S):
            try:
                self.tick()
            except Exception:
                log.exception("automatic cloud backup check failed")

    def start(self) -> None:
        threading.Thread(target=self.run, name="cloud-auto-backup", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
