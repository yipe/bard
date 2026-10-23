"""HTTP + WebSocket API (contract: web/src/lib/types.ts). Serves web/dist when built.

Local-only by design (binds 127.0.0.1, no auth). Host and Origin checks stop
other websites open in the same browser from reading the transcript over the
WebSocket or calling mutating endpoints (CSRF / DNS rebinding).
"""

import asyncio
import html
import json
import logging
import threading
import wave
from contextlib import asynccontextmanager
from typing import Annotated, Literal
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, StringConstraints
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import config, improve, mics, obsidian, settings, transcript
from .ask import Asker
from .audio import list_mics
from .campaign import TYPES, Vault, mention_counts
from .cloud import AutoBackup, Cloud, CloudError
from .engine import OVERFLOW, SESSION_ID, Bus, Engine, sample_path, session_audio
from .memory import Memory, spelling_suggestions

log = logging.getLogger("troubadour")
bus = Bus()
engine = Engine(bus)
vault = Vault(engine.vault_dir)  # the open campaign's notes, looked up on every use
engine.vault = vault
memory = Memory(vault, bus, sessions_dir=engine.sessions_dir, seed=engine.known_names)
asker = Asker(
    vault,
    bus,
    sessions=engine.campaign_sessions,
    written_up=memory.written_up,
    campaign=lambda: engine.campaign,
    seed=engine.known_names,
)
cloud = Cloud()  # opt-in backup; contacts the service only once the user connects
# Held while switching campaigns and while a job is checked and queued: jobs use the open
# campaign's notes, so one can't be queued for one campaign and then run in another.
campaign_gate = threading.Lock()

LOCAL_HOSTS = {"127.0.0.1", "localhost", config.HOST}


@asynccontextmanager
async def lifespan(app: FastAPI):
    bus.loop = asyncio.get_running_loop()
    _apply_models(settings.current())
    engine.start_background()
    auto_backup.start()  # does nothing until the user connects Troubadour Cloud
    yield
    auto_backup.stop()
    engine.shutdown()


app = FastAPI(title="Troubadour", lifespan=lifespan)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=sorted(LOCAL_HOSTS))


def _origin_ok(origin: str | None) -> bool:
    """No Origin (curl, same-origin GET) or a page served from this machine (any port: Vite dev)."""
    return origin is None or urlparse(origin).hostname in LOCAL_HOSTS


@app.middleware("http")
async def reject_cross_site_writes(request: Request, call_next):
    if request.method not in ("GET", "HEAD", "OPTIONS") and not _origin_ok(request.headers.get("origin")):
        return JSONResponse({"detail": "cross-site request refused"}, status_code=403)
    return await call_next(request)


# ------------------------------------------------------------------ inputs

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=60)]
Label = Annotated[str, StringConstraints(strip_whitespace=True, max_length=60)]
# The UI sends a 128×128 JPEG (a few KB); cap far above that.
Avatar = Annotated[str, StringConstraints(max_length=300_000, pattern=r"^data:image/(jpeg|png|webp);base64,[A-Za-z0-9+/=]+$")]
Color = Annotated[str, StringConstraints(pattern=r"^#[0-9a-fA-F]{6}$")]


class PlayerIn(BaseModel):
    name: Name
    character: Label = ""
    avatar: Avatar | None = None
    is_gm: bool = False


class PlayerPatch(BaseModel):
    name: Name | None = None
    character: Label | None = None
    avatar: Avatar | None = None
    is_gm: bool | None = None
    color: Color | None = None


class SeatIn(BaseModel):
    character: Label | None = None  # unset: their seat before they left this campaign
    is_gm: bool | None = None


class CampaignIn(BaseModel):
    name: Name


class TrainIn(BaseModel):
    seconds: float = Field(20.0, ge=5, le=60)


class AssignIn(BaseModel):
    player_id: str = Field(max_length=40)
    character: Label | None = None


class MicIn(BaseModel):
    kind: Literal["mic"]
    device: str | None = Field(None, max_length=200)


class FileIn(BaseModel):
    kind: Literal["file"]
    name: str = Field(max_length=200)
    speed: float = Field(1.0, ge=0.5, le=config.MAX_REPLAY_SPEED)


class SessionIn(BaseModel):
    """Replay a saved session's audio through the pipeline."""

    kind: Literal["session"]
    id: str = Field(max_length=40)
    speed: float = Field(1.0, ge=0.5, le=config.MAX_REPLAY_SPEED)


class StartIn(BaseModel):
    source: Annotated[MicIn | FileIn | SessionIn, Field(discriminator="kind")]


EntityName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
Term = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
Fact = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]
Kind = Literal[tuple(TYPES)]  # type: ignore[valid-type]


class EntityIn(BaseModel):
    name: EntityName
    type: Kind = "term"
    description: str = Field("", max_length=4000)
    aliases: list[Term] = Field(default_factory=list, max_length=50)
    heard_as: list[Term] = Field(default_factory=list, max_length=100)


class EntityPatch(BaseModel):
    name: EntityName | None = None
    type: Kind | None = None
    status: Literal["confirmed", "suggested"] | None = None
    description: str | None = Field(None, max_length=4000)
    aliases: list[Term] | None = Field(None, max_length=50)
    heard_as: list[Term] | None = Field(None, max_length=100)
    heard_as_suggested: list[Term] | None = Field(None, max_length=100)
    heard_as_rejected: list[Term] | None = Field(None, max_length=500)
    facts: list[Fact] | None = Field(None, max_length=500)
    suggested_facts: list[Fact] | None = Field(None, max_length=500)


class ReviewIn(BaseModel):
    accept_spellings: list[Term] = Field(default_factory=list, max_length=100)
    reject_spellings: list[Term] = Field(default_factory=list, max_length=100)
    accept_facts: list[Fact] = Field(default_factory=list, max_length=100)
    reject_facts: list[Fact] = Field(default_factory=list, max_length=100)


class MicSpecsIn(BaseModel):
    """Corrections to what Troubadour knows about a mic (the OS can't report pattern or capsule)."""

    model: str | None = Field(None, max_length=80)
    capsule: Literal["dynamic", "condenser", "array"] | None = None
    patterns: (
        list[Literal["omni", "cardioid", "supercardioid", "hypercardioid", "figure-8", "stereo", "boundary", "beamforming"]]
        | None
    ) = Field(None, max_length=8)
    wireless: bool | None = None
    notes: str | None = Field(None, max_length=300)


class MicTestIn(BaseModel):
    device: str | None = Field(None, max_length=200)
    seconds: float = Field(20.0, ge=3, le=120)


class AudioMonitorIn(BaseModel):
    on: bool


class IgnoreIn(BaseModel):
    words: list[Term] = Field(min_length=1, max_length=100)


class MergeIn(BaseModel):
    into: EntityName


class FactIn(BaseModel):
    text: Fact
    suggested: bool = False


class FactsIn(BaseModel):
    facts: list[Fact] = Field(max_length=1000)


class ImportIn(BaseModel):
    title: str = Field("", max_length=120)
    text: str = Field(min_length=1, max_length=2_000_000)


class AskIn(BaseModel):
    question: str = Field(min_length=1, max_length=500)


class CloudOptionsIn(BaseModel):
    include_transcripts: bool | None = None
    auto_backup: bool | None = None


class CloudRestoreIn(BaseModel):
    vault_id: str = Field(pattern=r"^[0-9a-f-]{36}$")


def _call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except KeyError as e:
        raise HTTPException(404, f"not found: {e.args[0] if e.args else ''}") from None
    except ValueError as e:
        raise HTTPException(400, str(e)) from None


def _sample_files() -> list[dict]:
    files = []
    for path in sorted(config.SAMPLES_DIR.glob("*.wav")):
        try:
            with wave.open(str(path)) as w:
                duration = w.getnframes() / w.getframerate()
        except (wave.Error, EOFError, OSError):
            continue  # not a PCM wav we can describe; skip it
        files.append({"name": path.name, "duration": round(duration, 1), "audio_url": f"/api/files/{path.name}"})
    return files


# ------------------------------------------------------------------ routes


class ModelsIn(BaseModel):
    whisper: str | None = None
    whisper_improve: str | None = None
    llm: str | None = None
    memory: str | None = None
    llm_url: str | None = None
    api_key: str | None = None


def _llm_choices() -> list[str]:
    """LM Studio's current models, plus any this Mac has chosen before."""
    installed = _installed_llms()
    out = list(installed)
    for model in settings.known_llms():
        if model not in out:
            out.append(model)
    return out


def _installed_llms() -> list[str]:
    """Model ids the configured server is offering. Empty when it is down."""
    import httpx

    chosen = settings.current()
    headers = {"Authorization": f"Bearer {chosen['api_key']}"} if chosen.get("api_key") else {}
    try:
        r = httpx.get(f"{chosen['llm_url'].rstrip('/')}/models", headers=headers, timeout=8)
        data = r.json().get("data", []) if r.status_code == 200 else []
    except Exception:
        return []
    ids = [m["id"] for m in data if isinstance(m, dict) and isinstance(m.get("id"), str)]
    return ids[:500]


def _apply_models(chosen: dict) -> None:
    for llm, model in (
        (engine.cleanup_llm, chosen["llm"]),
        (memory.llm, chosen["memory"]),
        (asker.llm, chosen["memory"]),
    ):
        llm.model = model
        llm.url = chosen["llm_url"].rstrip("/")
        llm.api_key = chosen.get("api_key") or ""


@app.get("/api/settings/models")
def get_models():
    chosen = settings.current()
    return {
        **chosen,
        "speaker": settings.SPEAKER_MODEL,
        "whisper_options": list(settings.WHISPER_OPTIONS),
        "installed": _llm_choices(),
    }


@app.put("/api/settings/models")
def put_models(body: ModelsIn):
    try:
        chosen = settings.update(body.model_dump())
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    _apply_models(chosen)
    return {**chosen, "speaker": settings.SPEAKER_MODEL, "whisper_options": list(settings.WHISPER_OPTIONS), "installed": _llm_choices()}


@app.get("/api/state")
def state():
    return engine.snapshot()


@app.post("/api/llm/recheck")
def recheck_llm():
    """Try the local LLM again right now. The session event carries the new status."""
    return engine.recheck_llm()


@app.get("/api/debug/memory")
def debug_memory():
    return engine.memory_stats()


def _download(body: str | bytes, sid: str, fmt: str) -> Response:
    media = "text/markdown; charset=utf-8" if fmt == "md" else "application/json"
    return Response(body, media_type=media, headers={"Content-Disposition": f'attachment; filename="troubadour-{sid}.{fmt}"'})


@app.get("/api/transcript.{fmt}")
def current_transcript(fmt: Literal["md", "json"]):
    """The current (or just finished) session, freshly rendered."""
    record = engine.transcript_record()
    if record is None:
        raise HTTPException(404, "no session yet")
    body = transcript.markdown(record) if fmt == "md" else json.dumps(record, ensure_ascii=False, indent=1)
    return _download(body, record["session"]["id"], fmt)


class SessionMergeIn(BaseModel):
    ids: list[str] = Field(min_length=2, max_length=2)


@app.get("/api/sessions")
def sessions():
    """Saved sessions (autosaved every few seconds and when a session ends), newest first."""
    return engine.list_sessions()


@app.post("/api/sessions/merge")
def merge_sessions(body: SessionMergeIn):
    """Append the later of two saved sessions onto the earlier one. The later folder is removed."""
    return _call(engine.merge_sessions, body.ids)


def _require_open_campaign(sid: str) -> None:
    """These jobs use the open campaign's glossary and notes: refuse another campaign's session."""
    campaign = engine.session_campaign(sid)
    if campaign is not None and campaign != engine.campaign:
        raise HTTPException(400, "That session belongs to another campaign; open it first.")


@app.get("/api/sessions/{sid}/transcript.{fmt}")
def saved_transcript(sid: str, fmt: Literal["md", "json"]):
    """Rendered from transcript.json, so records saved by older versions read like new ones."""
    record = transcript.without_fillers(json.loads(_call(engine.session_file, sid, "json").read_text()))
    body = transcript.markdown(record) if fmt == "md" else json.dumps(record, ensure_ascii=False, indent=1)
    return _download(body, sid, fmt)


@app.post("/api/sessions/{sid}/improve")
def improve_transcript(sid: str):
    """Re-transcribe a saved session with what the campaign knows now (a background job; it
    waits while a live session runs). The original is kept as transcript.orig.json."""
    _call(engine.session_file, sid, "json")
    _call(session_audio, sid)
    with campaign_gate:
        _require_open_campaign(sid)
        return memory.submit(
            "improve",
            sid,
            lambda job: improve.run(
                job, engine, sid, lambda: memory.emit(job), model=settings.current()["whisper_improve"]
            ),
        ).to_json()


@app.post("/api/sessions/{sid}/retranscribe")
def retranscribe(sid: str):
    """Re-assign who spoke each line from the recording. Words stay; hand-set speakers can move.
    The first original transcript is kept."""
    _call(engine.session_file, sid, "json")
    _call(session_audio, sid)
    with campaign_gate:
        _require_open_campaign(sid)
        return memory.submit(
            "retranscribe", sid, lambda job: improve.reassign_speakers(job, engine, sid, lambda: memory.emit(job))
        ).to_json()


@app.post("/api/sessions/{sid}/spelling")
def fix_spellings(sid: str):
    """The spelling cleanup over a saved session (a background job; the original is kept)."""
    _call(engine.session_file, sid, "json")
    with campaign_gate:
        _require_open_campaign(sid)
        return memory.submit(
            "spelling", sid, lambda job: improve.fix_spellings(job, engine, sid, lambda: memory.emit(job))
        ).to_json()


@app.post("/api/sessions/{sid}/restore")
def restore_transcript(sid: str):
    """Undo improvements: put the session's original transcript back."""
    _call(engine.session_file, sid, "json")
    if not improve.restore(engine.sessions_dir() / sid):
        raise HTTPException(404, "this transcript was never improved")
    return {"restored": sid}


@app.get("/api/sessions/{sid}/quality")
def session_quality(sid: str):
    """How good a recording is (levels, noise, rumble, per-speaker loudness, verdict)."""
    _call(engine.session_file, sid, "json")
    return _call(engine.session_quality, sid)


@app.get("/api/mics")
def list_mic_details():
    """Every input: what macOS reports, specs (catalog or yours), measurements, ✓/✗ checklist."""
    return mics.describe(engine.sessions_dir())


@app.patch("/api/mics/{name}")
def correct_mic(name: str, body: MicSpecsIn):
    if len(name) > 200:
        raise HTTPException(422, "name too long")
    mics.set_override(name, body.model_dump(exclude_unset=True))
    return next((m for m in mics.describe(engine.sessions_dir()) if m["name"] == name), {"name": name, "specs": mics.specs(name)})


@app.post("/api/mic/test")
def mic_test(body: MicTestIn):
    """Level meter and quality for the mic without a session (events: audio.level, audio.quality, mic.test)."""
    return _call(engine.mic_test, body.device, body.seconds)


@app.post("/api/mic/test/stop")
def stop_mic_test():
    return engine.stop_mic_test()


@app.put("/api/audio/monitor")
def audio_monitor(body: AudioMonitorIn):
    """Switch the live session's level + quality read-out on or off (reported as session.audio_monitor)."""
    engine.set_audio_monitor(body.on)
    return {"on": body.on}


@app.get("/api/sessions/{sid}/audio.wav")
def saved_audio(sid: str):
    """A mic session's recording (Range supported, so the browser can seek)."""
    return FileResponse(_call(engine.session_file, sid, "wav"), media_type="audio/wav", filename=f"troubadour-{sid}.wav")


# ------------------------------------------------------------------ campaign memory


def _entity(e, counts: dict[str, int] | None = None) -> dict:
    data = e.to_json(vault.root)
    data["sessions"] = (counts if counts is not None else _mention_counts()).get(e.name, 0)
    return data


def _mention_counts() -> dict[str, int]:
    try:
        return mention_counts(vault, engine.sessions_dir())
    except OSError:
        return {}


@app.get("/api/campaign")
def campaign():
    """The whole glossary (a few hundred notes at most), campaign facts, jobs and write-ups."""
    counts = _mention_counts()
    return {
        "vault": str(vault.root),
        "obsidian": obsidian.vault_for(vault.root),  # None: Obsidian doesn't know this folder yet
        "types": list(TYPES),
        "entities": [_entity(e, counts) for e in vault.entities()],
        "facts": vault.campaign_facts(),
        "jobs": memory.list_jobs(),
        "answers": asker.list(),
        "written_up": memory.written_up(),
        "ignored": vault.ignored(),
    }


@app.post("/api/campaign/entities")
def create_entity(body: EntityIn):
    e = _call(vault.create, body.name, body.type, description=body.description, aliases=body.aliases, heard_as=body.heard_as)
    memory.changed()
    return _entity(e)


@app.get("/api/campaign/entities/{name}")
def get_entity(name: str):
    return _entity(_call(vault.get, name))


@app.patch("/api/campaign/entities/{name}")
def update_entity(name: str, body: EntityPatch):
    e = _call(vault.update, name, **body.model_dump(exclude_unset=True, exclude_none=True))
    memory.changed()
    return _entity(e)


@app.delete("/api/campaign/entities/{name}", status_code=204)
def delete_entity(name: str):
    _call(vault.delete, name)
    memory.changed()


@app.post("/api/campaign/entities/{name}/review")
def review_entity(name: str, body: ReviewIn):
    """Accept or reject suggestions as changes (safe to click quickly; lists aren't replaced)."""
    e = _call(vault.review, name, **body.model_dump())
    memory.changed()
    return _entity(e)


@app.post("/api/campaign/entities/{name}/merge")
def merge_entity(name: str, body: MergeIn):
    e = _call(vault.merge, name, body.into)
    memory.changed()
    return _entity(e)


@app.post("/api/campaign/entities/{name}/facts/search")
def seek_facts(name: str):
    """Scrub transcripts and imported notes for lasting facts about this entry."""
    with campaign_gate:
        return _call(memory.seek_facts, name).to_json()


@app.post("/api/campaign/entities/{name}/facts")
def add_entity_fact(name: str, body: FactIn):
    e = _call(vault.add_fact, name, body.text, body.suggested)
    memory.changed()
    return _entity(e)


@app.put("/api/campaign/facts")
def set_campaign_facts(body: FactsIn):
    facts = vault.set_campaign_facts(body.facts)
    memory.changed()
    return facts


@app.post("/api/campaign/facts")
def add_campaign_fact(body: FactIn):
    facts = vault.add_campaign_fact(body.text)
    memory.changed()
    return facts


@app.post("/api/campaign/import")
def import_notes(body: ImportIn):
    """Pasted notes → a background job (progress arrives as campaign.job events)."""
    with campaign_gate:
        return _call(memory.import_notes, body.title, body.text).to_json()


@app.post("/api/campaign/ask")
def ask(body: AskIn):
    """A question about the campaign → answered in the background (`campaign.answer` events),
    from the open campaign's transcripts and notes."""
    return _call(asker.ask, body.question).to_json()


@app.post("/api/campaign/writeup/{sid}")
def write_up(sid: str):
    if not SESSION_ID.fullmatch(sid):
        raise HTTPException(404, "not found")
    with campaign_gate:
        _require_open_campaign(sid)
        return _call(memory.write_up, sid).to_json()


@app.post("/api/campaign/ignore")
def ignore_words(body: IgnoreIn):
    """Never suggest these words as names again (kept in the vault's "Ignored words" note)."""
    words = vault.ignore(body.words)
    memory.changed()
    return words


@app.delete("/api/campaign/ignore/{word}")
def unignore_word(word: str):
    words = vault.unignore(word)
    memory.changed()
    return words


@app.get("/api/campaign/suggestions")
def suggestions():
    """Transcript words that look like misspelled glossary names, and unknown names heard."""
    return spelling_suggestions(vault, engine.campaign_sessions())


# ------------------------------------------------------------------ Troubadour Cloud (opt-in backup)


def _cloud_status() -> dict:
    return cloud.status(engine.campaign) | {"campaign": engine.campaign}


@app.get("/api/cloud")
def cloud_status():
    """Connected or not, and the open campaign's backup settings and last backup. Never calls the service."""
    return _cloud_status()


@app.post("/api/cloud/connect")
def cloud_connect():
    """Start signing in: the first time Troubadour contacts the service. Returns the page to open."""
    try:
        return {"authorize_url": _call(cloud.start_login)}
    except CloudError as e:
        raise HTTPException(502, str(e)) from None


@app.get("/api/cloud/callback", response_class=HTMLResponse)
def cloud_callback(
    code: str = Query("", max_length=2000), state: str = Query("", max_length=200), error: str = Query("", max_length=200)
):
    """Where the service's sign-in page sends the browser back (state ties it to a sign-in started here)."""
    if error or not code or not state:
        return _cloud_page("Not connected", f"Sign-in didn't finish{f' ({error})' if error else ''}. Try again from Troubadour.")
    try:
        cloud.finish_login(code, state)
    except (ValueError, CloudError) as e:
        return _cloud_page("Not connected", str(e))
    bus.emit("cloud.updated", _cloud_status())
    return _cloud_page("Connected", "Troubadour is connected to Troubadour Cloud. You can close this tab.")


def _cloud_page(title: str, text: str) -> str:
    return (
        f"<!doctype html><meta charset=utf-8><title>Troubadour Cloud: {html.escape(title)}</title>"
        f'<body style="font-family:system-ui;max-width:32rem;margin:4rem auto;padding:0 1rem">'
        f'<h1>{html.escape(title)}</h1><p>{html.escape(text)}</p><p><a href="/campaign">Back to Troubadour</a></p>'
    )


@app.post("/api/cloud/disconnect")
def cloud_disconnect():
    """Forget the sign-in on this Mac (backups already made stay in the cloud)."""
    cloud.logout()
    status = _cloud_status()
    bus.emit("cloud.updated", status)
    return status


@app.put("/api/cloud/options")
def cloud_options(body: CloudOptionsIn):
    cloud.set_options(engine.campaign, include_transcripts=body.include_transcripts, auto_backup=body.auto_backup)
    return _cloud_status()


def _job_progress(job):
    def progress(done: int, total: int, message: str) -> None:
        job.done, job.total, job.message = done, total, message
        memory.emit(job)

    return progress


def _submit_backup(cid: str, name: str, root, automatic: bool = False):
    """Queue a backup job (callers hold campaign_gate, so the campaign can't switch underneath)."""

    def work(job):
        try:
            last = cloud.backup(cid, name, root, _job_progress(job))
        except Exception as e:
            if not automatic:
                raise
            # Offline at the table is normal: no red chip every 10 minutes. The failure stays in the
            # Cloud backup panel (last_error) and the next automatic attempt retries.
            log.info("automatic cloud backup failed: %s", e)
            job.message = f"Automatic backup didn't run: {e}"
            return
        finally:
            bus.emit("cloud.updated", cloud.status(cid) | {"campaign": cid})
        job.done = job.total
        if last["unchanged"]:
            job.message = f"Nothing changed since the last backup ({last['files']} files)"
        else:
            job.message = f"Backed up {last['files']} files ({last['uploaded']} new or changed)"
        if last["skipped"]:
            job.message += f"; skipped {len(last['skipped'])}"

    return memory.submit("backup", f"{name} (automatic)" if automatic else name, work)


def _open_campaign() -> tuple[str, str, object]:
    with engine.lock:
        return engine.campaign, engine.campaigns[engine.campaign].name, engine.vault_dir()


@app.post("/api/cloud/backup")
def cloud_backup():
    """Back up the open campaign's notes now (a background job: `campaign.job` with kind "backup")."""
    with campaign_gate:
        cid, name, root = _open_campaign()
        if not cloud.status(cid)["connected"]:
            raise HTTPException(400, "Connect Troubadour Cloud first.")
        return _submit_backup(cid, name, root).to_json()


@app.get("/api/cloud/vaults")
def cloud_vaults():
    """This account's campaigns in Troubadour Cloud (from any Mac), to restore from."""
    try:
        return cloud.vaults()
    except CloudError as e:
        raise HTTPException(401 if e.code == "unauthorized" else 502, str(e)) from None


@app.get("/api/cloud/account")
def cloud_account():
    """Who is signed in to Troubadour Cloud (name, email, avatar) for the header menu; 401 when not connected."""
    try:
        return cloud.account()
    except CloudError as e:
        raise HTTPException(401 if e.code == "unauthorized" else 502, str(e)) from None


@app.post("/api/cloud/restore")
def cloud_restore(body: CloudRestoreIn):
    """Bring a backed-up campaign's newest snapshot back (a background job, kind "restore"). It goes into the
    campaign with the vault's id: one this Mac doesn't have yet is created (so it shows in the picker),
    one it has keeps its other notes. Notes that differ go to .trash first; nothing is deleted."""
    with campaign_gate:
        cid, name, _ = _open_campaign()
        if not cloud.status(cid)["connected"]:
            raise HTTPException(400, "Connect Troubadour Cloud first.")

        def work(job):
            vault = cloud.vault(body.vault_id)
            target = vault.get("campaign_id")
            if not isinstance(target, str):
                raise CloudError("not_found", "That campaign has no id Troubadour can use.")
            job.title = str(vault.get("name") or "Restored campaign")[:60]
            engine.adopt_campaign(target, job.title)
            result = cloud.restore(body.vault_id, engine.vault_dir(target), _job_progress(job))
            job.done = job.total
            job.message = (
                f"Restored {result['restored']} files"
                + (f", {result['replaced']} older versions moved to .trash" if result["replaced"] else "")
                + (f"; {result['unchanged']} already up to date" if result["unchanged"] else "")
                + (f"; skipped {len(result['skipped'])}" if result["skipped"] else "")
            )

        return memory.submit("restore", name, work).to_json()


def _auto_backup_tick_submit(cid: str, name: str, root) -> None:
    with campaign_gate:
        if engine.campaign == cid:  # still the open campaign
            _submit_backup(cid, name, root, automatic=True)


auto_backup = AutoBackup(cloud, current=_open_campaign, busy=memory.busy, submit=_auto_backup_tick_submit)


@app.get("/api/sources")
def sources():
    return {"files": _sample_files(), "mics": list_mics()}


@app.get("/api/files/{name}")
def sample_file(name: str):
    return FileResponse(_call(sample_path, name), media_type="audio/wav")  # supports Range for seeking


@app.post("/api/players")
def add_player(body: PlayerIn):
    return engine.add_player(body.name, body.character, body.avatar, body.is_gm)


@app.patch("/api/players/{pid}")
def update_player(pid: str, body: PlayerPatch):
    return _call(engine.update_player, pid, body.model_dump(exclude_unset=True))


@app.delete("/api/players/{pid}", status_code=204)
def remove_player(pid: str):
    """Delete from the library: every campaign, voiceprints and training."""
    _call(engine.remove_player, pid)


@app.post("/api/players/{pid}/seat")
def seat_player(pid: str, body: SeatIn):
    """A library player joins the open campaign (their voice training comes along)."""
    return _call(engine.seat_player, pid, body.character, body.is_gm)


@app.post("/api/players/{pid}/bench", status_code=204)
def bench_player(pid: str):
    """Out of the open campaign, back to the library (training kept). Between sessions only."""
    with campaign_gate:  # it saves the finished session for good: not while a job rewrites it
        if memory.busy():
            raise HTTPException(400, "Wait for the campaign jobs to finish before removing a player.")
        _call(engine.bench_player, pid)


@app.post("/api/campaigns")
def create_campaign(body: CampaignIn):
    return engine.create_campaign(body.name)


@app.patch("/api/campaigns/{cid}")
def rename_campaign(cid: str, body: CampaignIn):
    return _call(engine.rename_campaign, cid, body.name)


@app.post("/api/campaigns/{cid}/open")
def open_campaign(cid: str):
    """Switch campaigns (between sessions): players, voices, notes and History follow."""
    with campaign_gate:
        if memory.busy():
            raise HTTPException(400, "Wait for the campaign jobs to finish before switching campaigns.")
        out = _call(engine.open_campaign, cid)
    bus.emit("campaign.updated", {})  # the Campaign tab reloads from the new vault
    return out


@app.post("/api/players/{pid}/train", status_code=202)
def train(pid: str, body: TrainIn | None = None):
    _call(engine.train, pid, (body or TrainIn()).seconds)
    return {"ok": True}


@app.post("/api/train/cancel", status_code=204)
def cancel_training():
    engine.cancel_training()


@app.post("/api/voices/{vid}/assign")
def assign(vid: str, body: AssignIn):
    return _call(engine.assign_voice, vid, body.player_id, body.character)


@app.post("/api/voices/{vid}/unassign")
def unassign(vid: str):
    return _call(engine.unassign_voice, vid)


@app.post("/api/messages/{mid}/speaker")
def assign_message(mid: str, body: AssignIn):
    """Who spoke this one line (it stays theirs; its voiceprint teaches their voice)."""
    return _call(engine.assign_message, mid, body.player_id, body.character)


@app.get("/api/voices/{vid}/sample.wav")
def voice_sample(vid: str):
    return Response(_call(engine.voice_sample, vid), media_type="audio/wav", headers={"Cache-Control": "no-store"})


@app.post("/api/session/start")
def start(body: StartIn):
    return _call(engine.start, body.source.model_dump())


@app.post("/api/session/pause")
def pause():
    return engine.pause()


@app.post("/api/session/resume")
def resume():
    return engine.resume()


@app.post("/api/session/stop")
def stop():
    return engine.stop()


@app.websocket("/ws")
async def ws(socket: WebSocket):
    if not _origin_ok(socket.headers.get("origin")):
        await socket.close(code=1008)
        return
    await socket.accept()
    q = bus.subscribe()  # before the snapshot, so nothing is missed (the UI applies events idempotently)

    async def until_disconnect():
        while True:
            await socket.receive_text()  # raises WebSocketDisconnect when the tab goes away

    gone = asyncio.create_task(until_disconnect())
    try:
        await socket.send_json({"type": "snapshot", "data": engine.snapshot()})
        while True:
            nxt = asyncio.create_task(q.get())
            done, _ = await asyncio.wait({nxt, gone}, return_when=asyncio.FIRST_COMPLETED)
            if gone in done:
                nxt.cancel()
                break
            msg = nxt.result()
            if msg == OVERFLOW:
                await socket.close(code=1013)  # client fell behind; it reconnects and gets a fresh snapshot
                break
            await socket.send_text(msg)
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        gone.cancel()
        bus.unsubscribe(q)


class WebFiles(StaticFiles):
    """The built UI. Hashed /assets/* never change; index.html must be revalidated so a rebuild shows
    up on reload. Unknown paths outside /api and /assets are UI routes (/history/<id>, …): they get
    index.html so deep links and reloads work."""

    async def get_response(self, path: str, scope) -> Response:
        try:
            response = await super().get_response(path, scope)
        except StarletteHTTPException as e:
            if e.status_code != 404 or path.split("/", 1)[0] in ("api", "assets"):
                raise
            response = await super().get_response("index.html", scope)
        if path.startswith("assets/"):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        else:
            response.headers["Cache-Control"] = "no-cache"
        return response


if config.WEB_DIST.is_dir():
    app.mount("/", WebFiles(directory=config.WEB_DIST, html=True), name="web")
