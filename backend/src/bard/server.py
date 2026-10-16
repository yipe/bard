"""HTTP + WebSocket API (contract: web/src/lib/types.ts). Serves web/dist when built.

Local-only by design (binds 127.0.0.1, no auth). Host and Origin checks stop
other websites open in the same browser from reading the transcript over the
WebSocket or calling mutating endpoints (CSRF / DNS rebinding).
"""

import asyncio
import json
import logging
import threading
import wave
from contextlib import asynccontextmanager
from typing import Annotated, Literal
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, StringConstraints
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import config, improve, mics, obsidian, transcript
from .audio import list_mics
from .campaign import TYPES, Vault
from .engine import OVERFLOW, SESSION_ID, Bus, Engine, sample_path, session_audio
from .memory import Memory, spelling_suggestions

log = logging.getLogger("bard")
bus = Bus()
engine = Engine(bus)
vault = Vault(engine.vault_dir)  # the open campaign's notes, looked up on every use
engine.vault = vault
memory = Memory(vault, bus, sessions_dir=engine.sessions_dir, seed=engine.known_names)
# Held while switching campaigns and while a job is checked and queued: jobs use the open
# campaign's notes, so one can't be queued for one campaign and then run in another.
campaign_gate = threading.Lock()

LOCAL_HOSTS = {"127.0.0.1", "localhost", config.HOST}


@asynccontextmanager
async def lifespan(app: FastAPI):
    bus.loop = asyncio.get_running_loop()
    engine.start_background()
    yield
    engine.shutdown()


app = FastAPI(title="Bard", lifespan=lifespan)
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
    """Corrections to what Bard knows about a mic (the OS can't report pattern or capsule)."""

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


@app.get("/api/state")
def state():
    return engine.snapshot()


@app.get("/api/debug/memory")
def debug_memory():
    return engine.memory_stats()


def _download(body: str | bytes, sid: str, fmt: str) -> Response:
    media = "text/markdown; charset=utf-8" if fmt == "md" else "application/json"
    return Response(body, media_type=media, headers={"Content-Disposition": f'attachment; filename="bard-{sid}.{fmt}"'})


@app.get("/api/transcript.{fmt}")
def current_transcript(fmt: Literal["md", "json"]):
    """The current (or just finished) session, freshly rendered."""
    record = engine.transcript_record()
    if record is None:
        raise HTTPException(404, "no session yet")
    body = transcript.markdown(record) if fmt == "md" else json.dumps(record, ensure_ascii=False, indent=1)
    return _download(body, record["session"]["id"], fmt)


@app.get("/api/sessions")
def sessions():
    """Saved sessions (autosaved every few seconds and when a session ends), newest first."""
    return engine.list_sessions()


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
        return memory.submit("improve", sid, lambda job: improve.run(job, engine, sid, lambda: memory.emit(job))).to_json()


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
    return FileResponse(_call(engine.session_file, sid, "wav"), media_type="audio/wav", filename=f"bard-{sid}.wav")


# ------------------------------------------------------------------ campaign memory


def _entity(e) -> dict:
    return e.to_json(vault.root)


@app.get("/api/campaign")
def campaign():
    """The whole glossary (a few hundred notes at most), campaign facts, jobs and write-ups."""
    return {
        "vault": str(vault.root),
        "obsidian": obsidian.vault_for(vault.root),  # None: Obsidian doesn't know this folder yet
        "types": list(TYPES),
        "entities": [_entity(e) for e in vault.entities()],
        "facts": vault.campaign_facts(),
        "jobs": memory.list_jobs(),
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
