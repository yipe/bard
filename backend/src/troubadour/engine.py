"""Live engine. Threads:

source → frames → [segmenter] Silero VAD → utterances, fast speaker.active
utterances → [worker] Whisper + voice match / discovery → message.created
[llm] health check of the local LLM (spelling cleanup and campaign jobs use it)
[refine] every REFINE_EVERY_S: re-score past messages against matured voiceprints → messages.updated

All state lives here behind `self.lock`; the server only calls public methods
and forwards events from the Bus. Torch-MPS work (speaker embeddings) is
serialized by `embed_lock`: concurrent MPS use aborts the process. Thresholds
come from the benchmark (PLAN.md). Event shapes: web/src/lib/types.ts.
"""

import asyncio
import json
import logging
import os
import queue
import re
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
import wave
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from . import asr, cleanup, config, levels, settings, transcript
from .audio import FileSource, MicSource, Recorder, load_audio, wav_bytes
from .campaign import Corrector, Entity, english
from .memory import MemoryLLM
from .segment import SegmentConfig, Segmenter
from .voices import VoiceMatcher, centroid, normalize


def keep_awake() -> subprocess.Popen | None:
    """macOS: hold an idle-sleep assertion while a session runs; it also dies with this process."""
    exe = shutil.which("caffeinate")
    if exe is None:
        return None
    try:
        return subprocess.Popen([exe, "-i", "-w", str(os.getpid())], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:  # start() has already taken the session live; a missing sleep assertion is no reason to fail it
        log.warning("couldn't start caffeinate; the Mac may idle-sleep during this session", exc_info=True)
        return None


log = logging.getLogger("troubadour")

SR = config.SR
SEGMENT = SegmentConfig()  # utterance cutting; values measured in PLAN.md ("Utterance cutting")
ACTIVE_EVERY_S = 0.5  # fast speaker.active cadence while someone talks
ACTIVE_WINDOW_S = 1.5
PANEL_S = 3.0  # speech a new voice needs before it shows in the panel (benchmark default)
JOIN = 0.5  # cosine to a pending voice's centroid to join it
SUGGEST_MIN = 0.25
PROVISIONAL_BELOW = 0.55
CHARACTER_MARGIN = 0.08  # one player's other voiceprint must win by this much to switch (no flicker)
REFINE_EVERY_S = 15.0
SAMPLE_MAX_S = 8.0
MIC_STALL_S = 5.0  # mic silent this long (no frames at all, not just quiet) → warn the UI
TRAIN_MIN_SPEECH_S = 4.0
GLOSSARY_CHECK_S = 5.0  # how often new lines re-check the vault for glossary edits
PROMPT_NAMES = 20  # more names make Whisper worse at everything else (PLAN.md, "Whisper prompt")
PROMPT_MENTION_LINES = 100  # glossary names said this recently go first in the Whisper prompt
CONTEXT_MIN_S = 1.0  # shorter clips get the names but not the last lines in their Whisper prompt
CLEANUP_BATCH, CLEANUP_GATHER_S, CLEANUP_CONTEXT = 8, 1.5, 4  # lines per LLM call, wait to fill it, lines before
LLM_HEALTH_EVERY_S = 30.0
SETTLE_S = 15.0  # a switch waits this long at most for the finished session's spelling fixes
PALETTE = ["#f59e0b", "#10b981", "#3b82f6", "#ec4899", "#8b5cf6", "#ef4444", "#14b8a6", "#f97316", "#84cc16", "#06b6d4"]

STOP = object()
SESSION_ID = re.compile(r"\d{4}-\d{2}-\d{2}_\d{6}-[0-9a-f]{4}")
OVERFLOW = "__overflow__"  # queued to a WebSocket whose client fell behind: server closes it, client resyncs


CAMPAIGN_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")  # also Troubadour Cloud's rule for a campaign id


def short_id(prefix: str) -> str:
    return f"{prefix}{uuid.uuid4().hex[:10]}"


def _first_by_id(earlier: list[dict], later: list[dict]) -> list[dict]:
    out: dict[str, dict] = {}
    for item in earlier + later:
        out.setdefault(item["id"], item)
    return list(out.values())


def _audio_seconds(path: Path) -> float:
    with wave.open(str(path)) as w:
        return w.getnframes() / w.getframerate()


def _concat_wav(keep: Path, extra: Path) -> None:
    """Append extra's samples onto keep. Same format is copied; otherwise ffmpeg matches keep."""
    with wave.open(str(keep)) as a, wave.open(str(extra)) as b:
        same = (a.getnchannels(), a.getsampwidth(), a.getframerate()) == (
            b.getnchannels(),
            b.getsampwidth(),
            b.getframerate(),
        )
        spec = (a.getnchannels(), a.getsampwidth(), a.getframerate())
        a_frames = a.readframes(a.getnframes()) if same else b""
        b_frames = b.readframes(b.getnframes()) if same else b""
    tmp = keep.with_name(".audio.wav.merging")
    if same:
        with wave.open(str(tmp), "wb") as dst:
            dst.setnchannels(spec[0])
            dst.setsampwidth(spec[1])
            dst.setframerate(spec[2])
            dst.writeframes(a_frames + b_frames)
    else:
        subprocess.run(
            [
                "ffmpeg", "-y", "-nostdin", "-v", "error",
                "-i", str(keep), "-i", str(extra),
                "-filter_complex", "[0:a][1:a]concat=n=2:v=0:a=1",
                "-ac", str(spec[0]), "-ar", str(spec[2]),
                str(tmp),
            ],
            check=True,
        )
    tmp.replace(keep)


def sample_path(name: str) -> Path:
    """A replay file inside SAMPLES_DIR; KeyError for anything else (no traversal)."""
    root = config.SAMPLES_DIR.resolve()
    path = (root / name).resolve()
    if path.parent != root or path.suffix != ".wav" or not path.is_file():
        raise KeyError(name)
    return path


def session_audio(sid: str) -> tuple[Path, float | None]:
    """(path, end_s) of the audio a saved session heard: its own recording (mic sessions), else
    the sample file it replayed; replays of replays lead back to the original. end_s caps the
    replay at what the session actually heard (a replay stopped after 30 s of a 10 min sample);
    None means all of it. KeyError if there is no audio."""
    end: float | None = None
    for _ in range(8):
        if not isinstance(sid, str) or not SESSION_ID.fullmatch(sid):
            raise KeyError(sid)
        folder = config.DATA_DIR / "sessions" / sid
        if (folder / "audio.wav").is_file():
            return folder / "audio.wav", end
        try:
            meta = json.loads((folder / "meta.json").read_text())
            src = meta["source"]
        except (OSError, ValueError, KeyError, TypeError):
            raise KeyError(sid) from None
        heard = meta.get("duration_s")
        if isinstance(heard, int | float) and heard > 0:
            end = heard if end is None else min(end, heard)
        if src.get("kind") == "file":
            return sample_path(src["name"]), end
        if src.get("kind") != "session":
            raise KeyError(sid)
        sid = src.get("id")
    raise KeyError(sid)


@dataclass
class Player:
    """A person in the library. character and is_gm are their seat in the current campaign
    ("" / False for people not playing in it)."""

    id: str
    name: str
    character: str = ""
    avatar: str | None = None
    color: str = PALETTE[0]
    trained: bool = False
    is_gm: bool = False

    def to_json(self) -> dict:
        return dict(self.__dict__)

    def person_json(self) -> dict:
        """The campaign-independent part: what the library stores."""
        return {k: getattr(self, k) for k in PERSON_KEYS}


PERSON_KEYS = ("id", "name", "avatar", "color", "trained")
MAIN_CAMPAIGN = "main"  # everything from before campaigns existed: its players, sessions and vault
STATE_VERSION = 2


@dataclass
class Campaign:
    id: str
    name: str
    # player id -> {"character", "is_gm"}, in table order. Unused for the current campaign, whose
    # seats live on Engine.players.
    roster: dict[str, dict] = field(default_factory=dict)
    # Seats of people taken out of this campaign, so adding them back restores their character.
    left: dict[str, dict] = field(default_factory=dict)

    def to_json(self) -> dict:
        return {"id": self.id, "name": self.name}


def migrate_state(data: dict) -> dict:
    """state.json from before campaigns (characters on the players) → one campaign, MAIN_CAMPAIGN,
    seating everyone. Voices named as a character were that campaign's NPCs; the rest are people's
    own voices, shared by every campaign they play in."""
    if data.get("version") == STATE_VERSION:
        return data
    players = data.get("players", [])
    roster = [{"player_id": p["id"], "character": p.get("character", ""), "is_gm": p.get("is_gm", False)} for p in players]
    return {
        "version": STATE_VERSION,
        "campaign": MAIN_CAMPAIGN,
        "campaigns": [{"id": MAIN_CAMPAIGN, "name": "My campaign", "roster": roster}],
        "players": [{k: p[k] for k in PERSON_KEYS if k in p} for p in players],
        "voices": [v | {"campaign": MAIN_CAMPAIGN if v.get("character") else None} for v in data.get("voices", [])],
        "next_label": data.get("next_label", len(data.get("voices", [])) + 1),
    }


def seated_players(state: dict) -> list[dict]:
    """The current campaign's players with their seats (the pre-campaign player shape), from any
    state.json or setup snapshot. For scripts that read saved state."""
    state = migrate_state(state)
    people = {p["id"]: p for p in state["players"]}
    campaign = next(c for c in state["campaigns"] if c["id"] == state["campaign"])
    return [
        people[s["player_id"]] | {"character": s["character"], "is_gm": s["is_gm"]}
        for s in campaign["roster"]
        if s["player_id"] in people
    ]


@dataclass
class Voice:
    id: str
    label: str | None  # "Voice 3"; None until surfaced in the panel
    status: str = "pending"  # pending | named
    player_id: str | None = None
    character: str | None = None
    campaign: str | None = None  # named as a character in this campaign; None: the person's own voice, shared
    seconds: float = 0.0
    spans: list[tuple[float, float]] = field(default_factory=list)  # current-session audio, for the sample
    sample_audio: np.ndarray | None = None  # Train recording (also saved under data/voices/)
    sample_rev: int = 0  # bumped per Train recording so a retrain changes sample_url
    trained: bool = False
    sum: np.ndarray | None = None  # pending only: duration-weighted embedding sum
    suggested_player_id: str | None = None
    suggested_score: float | None = None

    @property
    def visible(self) -> bool:
        return self.status == "named" or self.label is not None

    def to_json(self) -> dict:
        has_sample = bool(self.spans) or self.sample_audio is not None
        # The query changes whenever the sample would, so <audio> refetches (and the UI's player
        # remounts). A Train clip is served as-is: spans mustn't churn its URL every utterance.
        rev = f"t{self.sample_rev}" if self.sample_audio is not None else str(len(self.spans))
        return {
            "id": self.id,
            "label": self.label or "Voice",
            "status": self.status,
            "player_id": self.player_id,
            "character": self.character,
            "seconds": round(self.seconds, 1),
            "sample_url": f"/api/voices/{self.id}/sample.wav?v={rev}" if has_sample else None,
            "suggested_player_id": self.suggested_player_id,
            "suggested_score": None if self.suggested_score is None else round(self.suggested_score, 3),
            "trained": self.trained,
        }


@dataclass
class Message:
    id: str
    voice_id: str | None
    text: str
    t_start: float
    t_end: float
    confidence: float
    provisional: bool
    fixes: list[dict] = field(default_factory=list)  # spelling cleanup: [{start, end, heard}], UTF-16
    level_db: float | None = None  # how loud the line arrived (dBFS): uneven = the mic favours someone
    vec: np.ndarray | None = None
    pinned: bool = False  # the user said who spoke it: refine never moves it
    taught: bool = False  # its voiceprint was learned into its (pinned) voice

    def to_json(self) -> dict:
        return {
            "id": self.id,
            "voice_id": self.voice_id,
            "text": self.text,
            "t_start": round(self.t_start, 2),
            "t_end": round(self.t_end, 2),
            "confidence": round(self.confidence, 3),
            "provisional": self.provisional,
            "fixes": self.fixes,
            "level_db": self.level_db,
        }


class Bus:
    """Fan-out of events from any thread to every WebSocket's asyncio queue."""

    QUEUE_MAX = 2000

    def __init__(self):
        self.loop: asyncio.AbstractEventLoop | None = None
        self._subs: set[asyncio.Queue] = set()
        self._lock = threading.Lock()

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=self.QUEUE_MAX)
        with self._lock:
            self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        with self._lock:
            self._subs.discard(q)

    def emit(self, type_: str, data) -> None:
        loop = self.loop
        if loop is None or loop.is_closed():
            return
        msg = json.dumps({"type": type_, "data": data})
        with self._lock:
            subs = list(self._subs)
        for q in subs:
            try:
                loop.call_soon_threadsafe(self._put, q, msg)
            except RuntimeError:  # loop shut down between the check and the call
                return

    @staticmethod
    def _put(q: asyncio.Queue, msg: str) -> None:
        try:
            q.put_nowait(msg)
        except asyncio.QueueFull:
            # The client missed events; replace its backlog with a marker so the
            # server closes the socket and the client reconnects to a fresh snapshot.
            while not q.empty():
                q.get_nowait()
            q.put_nowait(OVERFLOW)


class AudioBuffer:
    """Whole-session audio, append-only. view() is safe to keep across appends.

    float32 at 16 kHz: ~230 MB per hour of session.
    """

    def __init__(self):
        self._data = np.zeros(SR * 60, np.float32)
        self.n = 0

    def append(self, frame: np.ndarray) -> None:
        if self.n + len(frame) > len(self._data):
            grown = np.zeros(len(self._data) * 2, np.float32)
            grown[: self.n] = self._data[: self.n]
            self._data = grown
        self._data[self.n : self.n + len(frame)] = frame
        self.n += len(frame)

    def view(self) -> np.ndarray:
        return self._data[: self.n]


class LiveSession:
    """One listening run: its own audio, queues, and threads. Starting a new
    session abandons the old one; abandoned threads exit without side effects."""

    def __init__(self, source, desc: dict, campaign: str = MAIN_CAMPAIGN):
        self.campaign = campaign  # saved in meta.json: History lists only the open campaign's sessions
        self.source = source
        self.desc = desc
        self.started_at = time.time()
        self.id = time.strftime("%Y-%m-%d_%H%M%S", time.localtime(self.started_at)) + "-" + uuid.uuid4().hex[:4]
        self.buffer = AudioBuffer()
        self.frames_q: queue.Queue = queue.Queue()
        self.utt_q: queue.Queue = queue.Queue()
        self.abandoned = False
        self.paused = False  # source is held; the segmenter must not treat the silence as a dead mic
        self.awake: subprocess.Popen | None = None  # sleep assertion while listening
        self.recorder: Recorder | None = None  # mic sessions: audio.wav in the session folder
        self.meter: levels.LevelMeter | None = None  # live level + quality for the UI

    def release(self) -> None:
        if self.awake is not None:
            self.awake.terminate()
            self.awake = None
        if self.recorder is not None:
            self.recorder.close()
        if self.meter is not None:
            self.meter.stop()
            self.meter = None

    def abandon(self) -> None:
        self.abandoned = True
        self.source.stop()
        self.release()
        self.frames_q.put(STOP)
        self.utt_q.put(STOP)


class TrainJob:
    def __init__(self, player_id: str, seconds: float, from_session: bool):
        self.player_id = player_id
        self.seconds = seconds
        self.from_session = from_session  # True: fed by the running session's segmenter; False: own mic
        self.frames: list[np.ndarray] = []
        self.done = threading.Event()
        self.cancelled = False

    @property
    def captured_s(self) -> float:
        return sum(len(f) for f in self.frames) / SR

    def feed(self, frame: np.ndarray) -> None:
        if not self.done.is_set():
            self.frames.append(frame)
            if self.captured_s >= self.seconds:
                self.done.set()

    def cancel(self) -> None:
        self.cancelled = True
        self.done.set()


class Engine:
    def __init__(self, bus: Bus):
        self.bus = bus
        self.lock = threading.RLock()
        self.embed_lock = threading.Lock()  # torch MPS isn't safe across threads
        self._save_lock = threading.Lock()
        self._transcript_lock = threading.Lock()
        self._saved_signature: tuple | None = None
        self._start_lock = threading.Lock()  # serializes start/stop so sources can't leak
        # The open campaign's players (seated); everyone else in the library waits on the bench.
        self.players: dict[str, Player] = {}
        self.bench: dict[str, Player] = {}
        self.campaigns: dict[str, Campaign] = {MAIN_CAMPAIGN: Campaign(MAIN_CAMPAIGN, "My campaign")}
        self.campaign = MAIN_CAMPAIGN
        # Named voices the open campaign uses (and this session's pending ones). The rest (benched
        # people's, other campaigns' characters) are parked with their voiceprints, out of the matcher.
        self.voices: dict[str, Voice] = {}
        self.parked: dict[str, Voice] = {}
        self._parked_vecs: dict[str, np.ndarray] = {}
        self.messages: dict[str, Message] = {}
        self.status = "idle"
        self.source_desc: dict | None = None
        self.live: LiveSession | None = None
        self.buffer = AudioBuffer()  # current session's audio (for t and voice samples)
        self.audio_monitor = True  # live level + quality while a mic session runs; the UI can switch it off
        self.refine_now = threading.Event()
        self.train_job: TrainJob | None = None
        self.ready = threading.Event()
        self.load_error: str | None = None
        self.embedder = None
        self.matcher: VoiceMatcher | None = None
        self.cleanup_llm = MemoryLLM(model=config.LLM_MODEL, timeout=60)
        self.llm_ok = False
        self._next_label = 1
        self._persisted_vecs: dict[str, np.ndarray] = {}  # voiceprints from state.json until the matcher exists
        self.vault = None  # campaign.Vault (set by the server): its glossary corrects new lines
        self._glossary: tuple = ((), 0.0, None, [], [])  # (signature, checked, fixer, names, entries)
        self._spell: tuple = (None, None)  # (key, Speller): rebuilt when the glossary or Setup changes
        self.cleanup_q: queue.Queue = queue.Queue()  # lines with names only the LLM can settle
        self._learner = cleanup.Learner()
        self._mic_test: tuple | None = None  # (source, meter, timer) while Setup's mic test runs
        self._load_state()

    # ---------------------------------------------------------------- startup

    def start_background(self) -> None:
        loops = (
            (self._load_models, "load-models"),
            (self._llm_health_loop, "llm"),
            (self._refine_loop, "refine"),
            (self._cleanup_loop, "cleanup"),
        )
        for target, name in loops:
            threading.Thread(target=target, name=name, daemon=True).start()

    def _load_models(self) -> None:
        if os.environ.get("HUGGING_FACE_TOKEN") and not os.environ.get("HF_TOKEN"):
            os.environ["HF_TOKEN"] = os.environ["HUGGING_FACE_TOKEN"]
        t0 = time.perf_counter()
        try:
            import torch
            from pyannote.audio import Pipeline
            from silero_vad import load_silero_vad

            pipeline = Pipeline.from_pretrained(config.DIARIZER)
            pipeline.to(torch.device("mps"))
            self.embedder = pipeline._embedding
            load_silero_vad()  # warm the download/cache; each session loads its own instance
            asr.warm_up()
        except Exception as e:
            log.exception("model load failed")
            self.load_error = (
                f"Models failed to load ({type(e).__name__}: {e}). Check HF_TOKEN has access to {config.DIARIZER}, then restart."
            )
            self.bus.emit("error", {"message": self.load_error})
            self._emit_session()
            return
        with self.lock:
            self.matcher = VoiceMatcher(self.embedder)
            for vid, vec in self._persisted_vecs.items():
                if vid in self.voices and self.voices[vid].status == "named":
                    self.matcher.add(vid, vec)
            self._persisted_vecs.clear()
        self.ready.set()
        self.llm_ok = self.cleanup_llm.healthy()
        log.info(
            "models ready in %.1fs; llm %s at %s ok=%s",
            time.perf_counter() - t0,
            self.cleanup_llm.model,
            self.cleanup_llm.url,
            self.llm_ok,
        )
        self._emit_session()

    def _wait_ready(self, sess: LiveSession | None = None) -> bool:
        """Block until models are loaded; False if loading failed or the session was abandoned."""
        while not self.ready.wait(0.5):
            if self.load_error or (sess is not None and sess.abandoned):
                return False
        return True

    def _require_models(self) -> None:
        if self.load_error:
            raise ValueError(self.load_error)

    def shutdown(self) -> None:
        with self._start_lock:
            if self.live is not None:
                self.live.abandon()
        with self.lock:
            job = self.train_job
        if job:
            job.cancel()
        self._save_state()
        self.save_transcript(force=True)

    # ------------------------------------------------------------ transcripts

    # ----------------------------------------------------------- audio quality

    def mic_test(self, device: str | None, seconds: float = 20.0) -> dict:
        """Listen to the mic for a while without a session: just the level meter and quality
        read-out (nothing is recorded or transcribed), for tuning mic settings."""
        with self._start_lock:
            if self.status != "idle" or self.train_job is not None:
                raise ValueError("Stop the session (or training) first; its own meter shows the mic.")
        self.stop_mic_test()
        frames: queue.Queue = queue.Queue()
        meter = levels.LevelMeter(self.bus.emit, source="test")
        src = MicSource(device)
        try:
            src.start(frames.put, lambda: None)
        except Exception as e:
            src.stop()
            raise ValueError(f"Couldn't open the microphone: {e}") from e
        timer = threading.Timer(seconds, self.stop_mic_test)
        timer.daemon = True
        self._mic_test = (src, meter, timer, frames)

        def drain() -> None:  # the meter's maths stays off the audio callback
            while self._mic_test is not None and self._mic_test[1] is meter:
                try:
                    meter.feed(frames.get(timeout=0.5))
                except queue.Empty:
                    continue

        threading.Thread(target=drain, name="mic-test", daemon=True).start()
        timer.start()
        out = {"state": "running", "device": device, "seconds": seconds}
        self.bus.emit("mic.test", out)
        return out

    def stop_mic_test(self) -> dict:
        test, self._mic_test = self._mic_test, None
        if test is not None:
            src, meter, timer, _ = test
            timer.cancel()
            src.stop()
            meter.stop()
            self.bus.emit("mic.test", {"state": "stopped"})
        return {"state": "stopped"}

    def session_quality(self, sid: str) -> dict:
        """The quality report of a recorded session (cached next to it until the audio changes)."""
        folder = self.sessions_dir() / sid
        path, end = session_audio(sid)
        st = path.stat()
        key = [str(path), st.st_mtime_ns, st.st_size, end]
        cache = folder / "quality.json"
        try:
            cached = json.loads(cache.read_text())
            if cached.get("key") == key:
                return cached["report"]
        except (OSError, ValueError):
            pass
        record = json.loads((folder / "transcript.json").read_text())
        out = levels.report(load_audio(path, 0.0, end), record["messages"])
        src = record["session"].get("source") or {}
        out["mic"] = src.get("device") or src.get("name") or src.get("kind")
        tmp = folder / ".quality.json.tmp"
        tmp.write_text(json.dumps({"key": key, "report": out}))
        tmp.replace(cache)
        return out

    def _quality_quietly(self, sid: str) -> None:
        try:
            self.session_quality(sid)
        except Exception:
            log.exception("couldn't measure the recording's quality")

    def known_names(self) -> list[tuple[str, str, str]]:
        """(name, type, description) for the campaign glossary: Setup's characters and the names
        given to voices in the voices panel."""
        with self.lock:
            out = [(p.character, "pc", f"Played by {p.name}.") for p in self.players.values() if p.character and not p.is_gm]
            for v in self.voices.values():
                if v.status == "named" and v.character and v.player_id in self.players:
                    out.append((v.character, "npc", f"Voiced by {self.players[v.player_id].name}."))
        return out

    def sessions_dir(self) -> Path:
        return config.DATA_DIR / "sessions"

    def transcript_record(self) -> dict | None:
        """The current (or just finished) session as a transcript record; None before any session."""
        with self.lock:
            sess = self.live
            if sess is None:
                return None
            meta = {
                "id": sess.id,
                "started_at": sess.started_at,
                "source": sess.desc,
                "duration_s": round(sess.buffer.n / SR, 1),
                "campaign": sess.campaign,
            }
            return transcript.build(
                meta,
                [p.to_json() for p in self.players.values()],
                [v.to_json() for v in self.voices.values()],
                [m.to_json() for m in self.messages.values()],
            )

    def _transcript_signature(self) -> tuple:
        """Changes whenever the saved transcript would: lines, speakers, names."""
        return (
            self.live.id if self.live else None,
            tuple((m.id, m.voice_id) for m in self.messages.values()),
            tuple((p.name, p.character, p.is_gm) for p in self.players.values()),
            tuple((v.id, v.player_id, v.character, v.label) for v in self.voices.values()),
        )

    def save_transcript(self, force: bool = False) -> Path | None:
        """Write data/sessions/<id>/transcript.{json,md} + meta.json (atomic). Skips if nothing changed."""
        with self._transcript_lock:
            with self.lock:
                # A mic session is kept even if nobody spoke: its recording is worth replaying.
                if self.live is None or not (self.messages or self.live.desc["kind"] == "mic"):
                    return None
                sig = self._transcript_signature()
                if not force and sig == self._saved_signature:
                    return None
                record = self.transcript_record()
            folder = self.sessions_dir() / record["session"]["id"]
            if (folder / "transcript.orig.json").exists():
                # Improve / Fix spellings rewrote it after the session ended: theirs is newer than
                # what's in memory, so the live copy stops saving over it.
                self._saved_signature = sig
                return None
            folder.mkdir(parents=True, exist_ok=True)
            files = {
                "transcript.json": json.dumps(record, ensure_ascii=False, indent=1),
                "transcript.md": transcript.markdown(record),
                "meta.json": json.dumps(record["session"]),
            }
            for name, body in files.items():
                tmp = folder / f".{name}.tmp"
                tmp.write_text(body, encoding="utf-8")
                tmp.replace(folder / name)
            self._saved_signature = sig
            return folder

    def session_campaign(self, sid: str) -> str | None:
        """The campaign a saved session belongs to (MAIN for those from before campaigns); None if
        it has no readable meta.json."""
        if not SESSION_ID.fullmatch(sid):
            return None
        try:
            return json.loads((self.sessions_dir() / sid / "meta.json").read_text()).get("campaign", MAIN_CAMPAIGN)
        except (OSError, ValueError, AttributeError):
            return None

    def campaign_sessions(self) -> list[Path]:
        """Folders of the open campaign's saved sessions."""
        return [
            m.parent for m in self.sessions_dir().glob("*/meta.json") if self.session_campaign(m.parent.name) == self.campaign
        ]

    def list_sessions(self) -> list[dict]:
        out = []
        for folder in self.campaign_sessions():
            meta = folder / "meta.json"
            try:
                s = json.loads(meta.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            s["audio"] = (meta.parent / "audio.wav").is_file()
            s["restorable"] = (meta.parent / "transcript.orig.json").is_file()  # improved; original kept
            try:  # the verdict, if the recording has been measured
                s["quality"] = json.loads((meta.parent / "quality.json").read_text())["report"]["verdict"]["kind"]
            except (OSError, ValueError, KeyError, TypeError):
                s["quality"] = None
            try:
                session_audio(s["id"])
                s["replayable"] = True
            except KeyError:
                s["replayable"] = False
            out.append(s)
        return sorted(out, key=lambda s: s.get("started_at", 0), reverse=True)

    def merge_sessions(self, ids: list[str]) -> dict:
        """Append one saved session onto an earlier one and delete the later folder.

        The earlier session keeps its id and start time. The later session's lines are shifted
        to follow it, and the two recordings are concatenated when both exist. A recording on
        only one of them is refused: the merged audio would no longer match the transcript.
        """
        if len(ids) != 2 or len(set(ids)) != 2:
            raise ValueError("Pick two different saved sessions.")
        for sid in ids:
            if not SESSION_ID.fullmatch(sid):
                raise KeyError(sid)
        with self.lock:
            if self.live is not None and self.live.id in ids:
                raise ValueError("Stop the live session before merging it.")
            campaign = self.campaign
        records = []
        for sid in ids:
            if self.session_campaign(sid) != campaign:
                raise ValueError("That session belongs to another campaign; open it first.")
            folder = self.sessions_dir() / sid
            try:
                record = json.loads((folder / "transcript.json").read_text())
            except (OSError, json.JSONDecodeError) as e:
                raise KeyError(sid) from e
            records.append((folder, record))
        records.sort(key=lambda item: item[1]["session"].get("started_at", 0))
        (keep_dir, keep), (drop_dir, drop) = records
        keep_audio, drop_audio = keep_dir / "audio.wav", drop_dir / "audio.wav"
        if keep_audio.is_file() != drop_audio.is_file():
            raise ValueError("Merge sessions that both have a recording, or neither does.")
        offset = _audio_seconds(keep_audio) if keep_audio.is_file() else float(keep["session"].get("duration_s") or 0)
        seen = {m["id"] for m in keep["messages"]}
        shifted = []
        for m in drop["messages"]:
            mid = m["id"]
            if mid in seen:
                mid = f"{mid}-merged"
            seen.add(mid)
            shifted.append(m | {"id": mid, "t_start": m["t_start"] + offset, "t_end": m["t_end"] + offset})
        messages = sorted(keep["messages"] + shifted, key=lambda m: m["t_start"])
        # The earlier session wins when a player or voice id is in both.
        players = _first_by_id(keep.get("players", []), drop.get("players", []))
        voices = _first_by_id(keep.get("voices", []), drop.get("voices", []))
        added = _audio_seconds(drop_audio) if drop_audio.is_file() else float(drop["session"].get("duration_s") or 0)
        session = dict(keep["session"])
        session["duration_s"] = round(offset + added, 1)
        session["lines"] = len(messages)
        merged_from = list(session.get("merged_from") or [])
        merged_from.append(drop["session"]["id"])
        session["merged_from"] = merged_from
        record = keep | {"session": session, "players": players, "voices": voices, "messages": messages}
        if keep_audio.is_file():
            _concat_wav(keep_audio, drop_audio)
        for name in ("quality.json", "transcript.orig.json"):
            (keep_dir / name).unlink(missing_ok=True)
        files = {
            "transcript.json": json.dumps(record, ensure_ascii=False, indent=1),
            "transcript.md": transcript.markdown(record),
            "meta.json": json.dumps(session),
        }
        for name, body in files.items():
            tmp = keep_dir / f".{name}.tmp"
            tmp.write_text(body, encoding="utf-8")
            tmp.replace(keep_dir / name)
        shutil.rmtree(drop_dir)
        return next(s for s in self.list_sessions() if s["id"] == keep["session"]["id"])

    def session_file(self, sid: str, fmt: str) -> Path:
        """A saved session's transcript.{md,json} or its own recording (fmt "wav")."""
        if not SESSION_ID.fullmatch(sid) or fmt not in ("md", "json", "wav"):
            raise KeyError(sid)
        path = self.sessions_dir() / sid / ("audio.wav" if fmt == "wav" else f"transcript.{fmt}")
        if not path.is_file():
            raise KeyError(sid)
        return path

    def _snapshot_setup(self, sess: LiveSession, phase: str) -> None:
        """sessions/<id>/setup/<phase>.json (state.json's shape: players + voiceprints) plus the
        voices' training clips, so `troubadour-sim` can re-run the session against exactly this setup.
        "start" is what the session began with; "end" adds the voices named during play."""
        folder = self.sessions_dir() / sess.id / "setup"
        try:
            data = self._state_data()
            (folder / "voices").mkdir(parents=True, exist_ok=True)
            for v in data["voices"]:
                wav = self._voice_audio_path(v["id"])
                if wav.is_file():
                    shutil.copyfile(wav, folder / "voices" / wav.name)
            tmp = folder / f".{phase}.json.tmp"
            tmp.write_text(json.dumps(data))
            tmp.replace(folder / f"{phase}.json")
        except OSError:
            log.exception("couldn't snapshot the %s setup", phase)

    # ----------------------------------------------------------- persistence

    def _state_path(self) -> Path:
        return config.DATA_DIR / "state.json"

    def _voice_audio_path(self, vid: str) -> Path:
        return config.DATA_DIR / "voices" / f"{vid}.wav"

    def _load_state(self) -> None:
        path = self._state_path()
        if not path.exists():
            return
        data = json.loads(path.read_text())
        if data.get("version") != STATE_VERSION:
            backup = path.with_name("state.v1.json")  # the pre-campaign file, kept as it was
            if not backup.exists():
                shutil.copyfile(path, backup)
            data = migrate_state(data)
        people = {p["id"]: Player(**{k: p[k] for k in PERSON_KEYS if k in p}) for p in data["players"]}

        def seats(entries: list[dict]) -> dict[str, dict]:
            return {
                s["player_id"]: {"character": s["character"], "is_gm": s["is_gm"]} for s in entries if s["player_id"] in people
            }

        self.campaigns = {
            c["id"]: Campaign(c["id"], c["name"], seats(c["roster"]), seats(c.get("left", []))) for c in data["campaigns"]
        }
        if not self.campaigns:  # a hand-edited file: keep everyone, in a fresh main campaign
            self.campaigns = {MAIN_CAMPAIGN: Campaign(MAIN_CAMPAIGN, "My campaign")}
        self.campaign = data["campaign"] if data["campaign"] in self.campaigns else next(iter(self.campaigns))
        for pid, seat in self.campaigns[self.campaign].roster.items():
            p = people.pop(pid)
            p.character, p.is_gm = seat["character"], seat["is_gm"]
            self.players[pid] = p
        self.campaigns[self.campaign].roster = {}
        self.bench = people
        for v in data["voices"]:
            voice = Voice(
                id=v["id"],
                label=v["label"],
                status="named",
                player_id=v["player_id"],
                character=v["character"],
                campaign=v.get("campaign"),
                seconds=v["seconds"],
                trained=v.get("trained", False),
            )
            wav = self._voice_audio_path(voice.id)
            if wav.exists():
                voice.sample_audio = load_audio(wav)
            vec = np.array(v["vec"], np.float64)
            if self._in_campaign(voice):
                self.voices[voice.id], self._persisted_vecs[voice.id] = voice, vec
            else:
                self.parked[voice.id], self._parked_vecs[voice.id] = voice, vec
        self._next_label = data["next_label"]
        # Recordings whose voice is gone (unassigned/deleted while offline, or older builds).
        for wav in (config.DATA_DIR / "voices").glob("*.wav"):
            if wav.stem not in self.voices and wav.stem not in self.parked:
                wav.unlink(missing_ok=True)

    def _in_campaign(self, v: Voice) -> bool:
        """A named voice the open campaign uses: a seated player's own voice, or a character of theirs
        named in this campaign."""
        return v.player_id in self.players and v.campaign in (None, self.campaign)

    def _voiceprint(self, vid: str) -> np.ndarray | None:
        if self.matcher and vid in self.matcher.names:
            return self.matcher.vector(vid)
        if vid in self._parked_vecs:
            return self._parked_vecs[vid]
        return self._persisted_vecs.get(vid)  # models not loaded yet

    def _state_data(self) -> dict:
        """The library, campaigns and named voiceprints (parked ones too), as stored in state.json."""
        with self.lock:
            voices = []
            for v in (*self.voices.values(), *self.parked.values()):
                vec = self._voiceprint(v.id) if v.status == "named" else None
                if vec is None:
                    continue
                voices.append(
                    {
                        "id": v.id,
                        "label": v.label,
                        "player_id": v.player_id,
                        "character": v.character,
                        "campaign": v.campaign,
                        "seconds": v.seconds,
                        "trained": v.trained,
                        "vec": vec.tolist(),
                    }
                )
            seated = {p.id: {"character": p.character, "is_gm": p.is_gm} for p in self.players.values()}
            campaigns = [
                {
                    "id": c.id,
                    "name": c.name,
                    "roster": [
                        {"player_id": pid, **seat} for pid, seat in (seated if c.id == self.campaign else c.roster).items()
                    ],
                    "left": [{"player_id": pid, **seat} for pid, seat in c.left.items()],
                }
                for c in self.campaigns.values()
            ]
            return {
                "version": STATE_VERSION,
                "campaign": self.campaign,
                "campaigns": campaigns,
                "players": [p.person_json() for p in (*self.players.values(), *self.bench.values())],
                "voices": voices,
                "next_label": self._next_label,
            }

    def _save_state(self) -> None:
        """Atomically write players + named voiceprints. Safe from any thread."""
        with self._save_lock:
            data = self._state_data()
            config.DATA_DIR.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile("w", dir=config.DATA_DIR, suffix=".tmp", delete=False) as f:
                json.dump(data, f)
            Path(f.name).replace(self._state_path())

    # -------------------------------------------------------------- snapshot

    def memory_stats(self) -> dict:
        """Where memory goes, for soak tests (GET /api/debug/memory)."""
        import gc
        import sys

        import torch

        out = {k: round(v, 1) for k, v in asr.memory().items()}
        if torch.backends.mps.is_available():
            out["torch_mps_allocated_mb"] = round(torch.mps.current_allocated_memory() / 2**20, 1)
            out["torch_mps_driver_mb"] = round(torch.mps.driver_allocated_memory() / 2**20, 1)
        with self.lock:
            out |= {
                "py_blocks": sys.getallocatedblocks(),
                "py_objects": len(gc.get_objects()),
                "audio_buffer_mb": round(self.buffer._data.nbytes / 2**20, 1),
                "messages": len(self.messages),
                "voices": len(self.voices),
            }
        return out

    def session_json(self) -> dict:
        job = self.train_job
        return {
            "status": self.status,
            "source": self.source_desc,
            "t": round(self.buffer.n / SR, 2),
            "llm": {"url": self.cleanup_llm.url, "model": self.cleanup_llm.model, "ok": self.llm_ok},
            "audio_monitor": self.audio_monitor,
            # Speech models (Whisper, VAD, voices) load once at start-up: ~20 s, or fail for good.
            "asr": {
                "model": settings.current()["whisper"],
                "state": "error" if self.load_error else "ready" if self.ready.is_set() else "loading",
            },
            "training": {"player_id": job.player_id} if job else None,
            "error": self.load_error,
        }

    def snapshot(self) -> dict:
        with self.lock:
            return {
                "session": self.session_json(),
                **self.campaigns_json(),
                "players": [p.to_json() for p in self.players.values()],
                "library": self.library_json(),
                "voices": [v.to_json() for v in self.voices.values() if v.visible],
                "messages": [m.to_json() for m in self.messages.values()],
            }

    def campaigns_json(self) -> dict:
        with self.lock:
            return {"campaign": self.campaign, "campaigns": [c.to_json() for c in self.campaigns.values()]}

    def library_json(self) -> list[dict]:
        """People who aren't playing in the open campaign (their voiceprints are kept)."""
        with self.lock:
            return [p.person_json() for p in self.bench.values()]

    def _emit_session(self) -> None:
        self.bus.emit("session.updated", self.session_json())

    def set_audio_monitor(self, on: bool) -> None:
        """The live level + quality read-out costs a little work on every frame; the UI can
        switch it off. Applies to the running session at once and to the next ones."""
        stopped = None
        with self.lock:
            self.audio_monitor = on
            sess = self.live
            if sess is not None and self.status != "idle":
                if on and sess.meter is None:
                    sess.meter = levels.LevelMeter(self.bus.emit)
                elif not on:
                    stopped, sess.meter = sess.meter, None
        if stopped is not None:
            stopped.stop()  # the meter reads "off"
        self._emit_session()

    def _emit_patches(self, patches: list[dict]) -> None:
        if patches:
            self.bus.emit("messages.updated", patches)

    # --------------------------------------------------------------- players

    def add_player(self, name: str, character: str = "", avatar: str | None = None, is_gm: bool = False) -> dict:
        with self.lock:
            # Colors are per person: avoid everyone's, so seating someone later doesn't clash.
            people = [*self.players.values(), *self.bench.values()]
            used = {p.color for p in people}
            color = next((c for c in PALETTE if c not in used), PALETTE[len(people) % len(PALETTE)])
            p = Player(id=short_id("p"), name=name, character=character, avatar=avatar, color=color, is_gm=is_gm)
            self.players[p.id] = p
            out = p.to_json()
        self._save_state()
        self.bus.emit("player.upserted", out)
        return out

    def update_player(self, pid: str, fields: dict) -> dict:
        """fields: any of name, character, avatar (None clears), is_gm, color — already validated."""
        with self.lock:
            p = self.players[pid]
            for key in ("name", "character", "avatar", "is_gm", "color"):
                if key in fields and (fields[key] is not None or key == "avatar"):
                    setattr(p, key, fields[key])
            out = p.to_json()
        self._save_state()
        self.bus.emit("player.upserted", out)
        return out

    def remove_player(self, pid: str) -> None:
        """Delete someone from the library: every campaign, their voiceprints and Train recordings."""
        with self.lock:
            seated = pid in self.players
            if seated:
                owned = [v.id for v in self.voices.values() if v.player_id == pid]
                for vid in owned:
                    self._unassign(vid)  # their voices go back to pending; recordings are deleted
                del self.players[pid]
            elif pid in self.bench:
                del self.bench[pid]
            else:
                raise KeyError(pid)
            for v in [v for v in self.parked.values() if v.player_id == pid]:
                del self.parked[v.id]
                self._parked_vecs.pop(v.id, None)
                self._voice_audio_path(v.id).unlink(missing_ok=True)
            for c in self.campaigns.values():
                c.roster.pop(pid, None)
                c.left.pop(pid, None)
        self._save_state()
        if seated:
            self.bus.emit("player.removed", {"id": pid})
        else:
            self.bus.emit("library.updated", self.library_json())

    def seat_player(self, pid: str, character: str | None = None, is_gm: bool | None = None) -> dict:
        """Bring someone from the library into the open campaign; their voiceprints come along.
        Unset fields come from their seat here before they left, else no character, and GM if
        they're the first at the table."""
        with self.lock:
            if pid in self.players:
                raise ValueError("They're already in this campaign.")
            p = self.bench.pop(pid)
            before = self.campaigns[self.campaign].left.pop(pid, None) or {"character": "", "is_gm": not self.players}
            p.character = before["character"] if character is None else character
            p.is_gm = before["is_gm"] if is_gm is None else is_gm
            self.players[pid] = p
            self._sync_voices()
            out = p.to_json()
        self._save_state()
        self.bus.emit("player.upserted", out)
        self.bus.emit("library.updated", self.library_json())
        return out

    def bench_player(self, pid: str) -> None:
        """Take someone out of the open campaign, back to the library: their voiceprints are kept.
        Only between sessions; the finished session is saved and cleared first, so its transcript
        keeps their lines."""
        with self._start_lock:
            with self.lock:
                if pid not in self.players:
                    raise KeyError(pid)
                self._require_idle("removing a player")
            self._close_finished()
            with self.lock:
                p = self.players.pop(pid)
                self.campaigns[self.campaign].left[pid] = {"character": p.character, "is_gm": p.is_gm}
                p.character, p.is_gm = "", False
                self.bench[pid] = p
                self._sync_voices()
                snapshot = self.snapshot()
        self._save_state()
        self.bus.emit("snapshot", snapshot)

    # ------------------------------------------------------------- campaigns

    def vault_dir(self, cid: str | None = None) -> Path:
        """A campaign's notes (default: the open one): MAIN's is config.vault_dir() (TROUBADOUR_VAULT or
        data/campaign), later ones live in data/campaigns/<id>."""
        cid = cid or self.campaign
        if cid == MAIN_CAMPAIGN:
            return config.vault_dir()
        return config.DATA_DIR / "campaigns" / cid

    def create_campaign(self, name: str) -> dict:
        with self.lock:
            c = Campaign(short_id("c"), name)
            self.campaigns[c.id] = c
        self._save_state()
        self.bus.emit("campaigns.updated", self.campaigns_json())
        return c.to_json()

    def adopt_campaign(self, cid: str, name: str) -> dict:
        """The campaign with this id, created (as an empty one called `name`) when this Mac doesn't have it,
        so a campaign restored from Troubadour Cloud shows in the picker and keeps backing up to its own
        vault. The id names a folder, so only the shape the service accepts gets through."""
        with self.lock:
            existing = self.campaigns.get(cid)
            if existing:
                return existing.to_json()
            if not CAMPAIGN_ID.fullmatch(cid):
                raise ValueError("That campaign has an id Troubadour can't use.")
            c = self.campaigns[cid] = Campaign(cid, name)
        self._save_state()
        self.bus.emit("campaigns.updated", self.campaigns_json())
        return c.to_json()

    def rename_campaign(self, cid: str, name: str) -> dict:
        with self.lock:
            c = self.campaigns[cid]
            c.name = name
        self._save_state()
        self.bus.emit("campaigns.updated", self.campaigns_json())
        return c.to_json()

    def open_campaign(self, cid: str) -> dict:
        """Switch campaigns between sessions: seat its players, swap in its voices, its notes and
        its History. The finished session is saved and cleared first."""
        with self._start_lock:
            with self.lock:
                if cid not in self.campaigns:
                    raise KeyError(cid)
                if cid == self.campaign:
                    return self.campaigns_json()
                self._require_idle("switching campaigns")
            self._close_finished()
            with self.lock:
                self.campaigns[self.campaign].roster = {
                    p.id: {"character": p.character, "is_gm": p.is_gm} for p in self.players.values()
                }
                for p in self.players.values():
                    p.character, p.is_gm = "", False
                    self.bench[p.id] = p
                self.players = {}
                for pid, seat in self.campaigns[cid].roster.items():
                    if pid in self.bench:
                        p = self.bench.pop(pid)
                        p.character, p.is_gm = seat["character"], seat["is_gm"]
                        self.players[pid] = p
                self.campaigns[cid].roster = {}
                self.campaign = cid
                self._sync_voices()
                # Spellings and names belong to a campaign's notes; rebuild them from the new vault.
                self._glossary, self._spell, self._learner = ((), 0.0, None, [], []), (None, None), cleanup.Learner()
                snapshot = self.snapshot()
        self._save_state()
        self.bus.emit("snapshot", snapshot)
        return self.campaigns_json()

    def _require_idle(self, doing: str) -> None:
        if self.status != "idle":
            raise ValueError(f"Stop the session before {doing}.")
        if self.train_job is not None:
            raise ValueError(f"Finish or cancel voice training before {doing}.")

    def _close_finished(self) -> None:
        """Save the finished session one last time, then clear it from the live view, so later
        roster or campaign changes can't rewrite its transcript (caller holds _start_lock; idle).
        Its last relabel and spelling fixes land first (bounded wait)."""
        if self.live is not None and self.ready.is_set():
            self._refine()
            deadline = time.monotonic() + SETTLE_S
            while self.cleanup_q.unfinished_tasks and time.monotonic() < deadline:
                time.sleep(0.1)
        self.save_transcript(force=True)
        with self.lock:
            self.live, self.source_desc, self.buffer = None, None, AudioBuffer()
            self.messages.clear()
            for vid in [v.id for v in self.voices.values() if v.status == "pending"]:
                del self.voices[vid]
            for v in self.voices.values():
                v.spans = []

    def _sync_voices(self) -> None:
        """Park the named voices the open campaign doesn't use (out of the matcher, voiceprint kept)
        and bring back the ones it does (lock held)."""
        for v in [v for v in self.voices.values() if v.status == "named" and not self._in_campaign(v)]:
            vec = self.matcher.remove(v.id) if self.matcher else self._persisted_vecs.pop(v.id, None)
            del self.voices[v.id]
            self.parked[v.id] = v
            if vec is not None:
                self._parked_vecs[v.id] = vec
            self.bus.emit("voice.removed", {"id": v.id, "merged_into": None})
        for v in [v for v in self.parked.values() if self._in_campaign(v)]:
            del self.parked[v.id]
            vec = self._parked_vecs.pop(v.id, None)
            if vec is not None and self.matcher:
                self.matcher.add(v.id, vec)
            elif vec is not None:
                self._persisted_vecs[v.id] = vec  # models not loaded yet
            self.voices[v.id] = v
            self.bus.emit("voice.upserted", v.to_json())
        self._refresh_suggestions()

    # ---------------------------------------------------------------- voices

    def _surface(self, v: Voice) -> None:
        v.label = f"Voice {self._next_label}"
        self._next_label += 1

    def _update_suggestion(self, v: Voice) -> None:
        if v.status != "pending" or v.sum is None or not self.matcher or not self.matcher.names:
            v.suggested_player_id = v.suggested_score = None
            return
        scores = self.matcher.scores(normalize(v.sum))
        best = int(np.argmax(scores))
        target = self.voices.get(self.matcher.names[best])
        if float(scores[best]) >= SUGGEST_MIN and target and target.player_id:
            v.suggested_player_id, v.suggested_score = target.player_id, float(scores[best])
        else:
            v.suggested_player_id = v.suggested_score = None

    def _refresh_suggestions(self) -> None:
        for other in self.voices.values():
            if other.status == "pending" and other.visible:
                self._update_suggestion(other)
                self.bus.emit("voice.upserted", other.to_json())

    def assign_voice(self, vid: str, player_id: str, character: str | None = None) -> dict:
        character = (character or "").strip() or None
        with self.lock:
            v = self.voices[vid]
            if player_id not in self.players:
                raise KeyError(player_id)
            if v.status == "named":  # re-point an already named voice
                v.player_id, v.character = player_id, character
                v.campaign = self.campaign if character else None
                self.bus.emit("voice.upserted", v.to_json())
                result = {"voice": v.to_json(), "merged_into": None}
            else:
                if self.matcher is None:
                    raise ValueError("Models are still loading; try again in a few seconds.")
                vec = normalize(v.sum)
                target = next(
                    (
                        t
                        for t in self.voices.values()
                        if t.status == "named" and t.player_id == player_id and t.character == character
                    ),
                    None,
                )
                if target:
                    self.matcher.add(target.id, vec)  # same player + character → merge
                    target.seconds += v.seconds
                    target.spans += v.spans
                    del self.voices[vid]
                    patches = []
                    for m in self.messages.values():
                        if m.voice_id == vid:
                            m.voice_id = target.id
                            patches.append({"id": m.id, "voice_id": target.id})
                    self.bus.emit("voice.removed", {"id": vid, "merged_into": target.id})
                    self.bus.emit("voice.upserted", target.to_json())
                    self._emit_patches(patches)
                    result = {"voice": target.to_json(), "merged_into": target.id}
                else:
                    self.matcher.add(vid, vec)
                    v.status, v.player_id, v.character, v.sum = "named", player_id, character, None
                    v.campaign = self.campaign if character else None  # a character is this campaign's
                    v.suggested_player_id = v.suggested_score = None
                    if v.label is None:
                        self._surface(v)
                    self.bus.emit("voice.upserted", v.to_json())
                    result = {"voice": v.to_json(), "merged_into": None}
            self._refresh_suggestions()
        self._save_state()
        self.refine_now.set()
        return result

    def assign_message(self, mid: str, player_id: str, character: str | None = None) -> dict:
        """The user says who spoke one line: it moves to that person's (or character's) voice at
        once and stays there (refine never relabels it), and its voiceprint teaches that voice,
        like one confident span. Moving it again takes the lesson back first."""
        character = (character or "").strip() or None
        with self.lock:
            m = self.messages[mid]
            if player_id not in self.players:
                raise KeyError(player_id)
            if self.matcher is None:
                raise ValueError("Models are still loading; try again in a few seconds.")
            target = next(
                (
                    v
                    for v in self.voices.values()
                    if v.status == "named" and v.player_id == player_id and v.character == character
                ),
                None,
            )
            if target is None:
                target = Voice(id=short_id("v"), label=None, status="named", player_id=player_id, character=character)
                target.campaign = self.campaign if character else None  # a character is this campaign's
                self._surface(target)
                self.voices[target.id] = target
            if m.taught and m.vec is not None:
                self.matcher.forget(m.voice_id, m.vec)
            if m.vec is not None:
                self.matcher.learn(target.id, m.vec)
            if m.voice_id != target.id:
                target.seconds += m.t_end - m.t_start
                target.spans.append((m.t_start, m.t_end))
            m.voice_id, m.confidence, m.provisional = target.id, 1.0, False
            m.pinned, m.taught = True, m.vec is not None
            self.bus.emit("voice.upserted", target.to_json())
            self._emit_patches([{"id": m.id, "voice_id": target.id, "confidence": 1.0, "provisional": False}])
            self._refresh_suggestions()
            result = {"message": m.to_json(), "voice": target.to_json()}
        self._save_state()
        self.refine_now.set()  # the lesson may move other lines too
        return result

    def unassign_voice(self, vid: str) -> dict:
        with self.lock:
            out = self._unassign(vid)
        self._save_state()
        return out

    def _unassign(self, vid: str) -> dict:
        """Back to pending (lock held). A Train recording stays with the person, not the voice: delete it."""
        v = self.voices[vid]
        if v.status == "named":
            owner = self.players.get(v.player_id)
            vec = self.matcher.remove(vid) if self.matcher else self._persisted_vecs.pop(vid, None)
            v.status, v.player_id, v.character, v.campaign = "pending", None, None, None
            for m in self.messages.values():
                if m.voice_id == vid:  # its print is gone: the lines it was taught by are free again
                    m.pinned = m.taught = False
            v.sum = (vec if vec is not None else np.zeros(256)) * max(v.seconds, 1.0)
            if v.trained:
                v.trained, v.sample_audio = False, None
                self._voice_audio_path(vid).unlink(missing_ok=True)
            if owner is not None:
                owner.trained = any(
                    o.trained and o.player_id == owner.id and o.character is None
                    for o in self.voices.values()
                    if o.status == "named"
                )
                self.bus.emit("player.upserted", owner.to_json())
            if v.label is None:
                self._surface(v)
            self._update_suggestion(v)
        self.bus.emit("voice.upserted", v.to_json())
        return v.to_json()

    def voice_sample(self, vid: str) -> bytes:
        with self.lock:
            v = self.voices[vid]
            if v.sample_audio is not None:
                return wav_bytes(v.sample_audio[: int(SAMPLE_MAX_S * SR)])
            audio = self.buffer.view()
            chunks, total = [], 0.0
            # Longest spans first: they carry the clearest voice.
            for s, e in sorted(v.spans, key=lambda x: x[0] - x[1]):
                if total >= SAMPLE_MAX_S:
                    break
                chunks += [audio[int(s * SR) : int(e * SR)], np.zeros(int(0.25 * SR), np.float32)]
                total += e - s
        if not chunks:
            raise KeyError(vid)
        return wav_bytes(np.concatenate(chunks))

    # --------------------------------------------------------------- session

    def start(self, source: dict) -> dict:
        """source: {"kind": "file", "name", "speed"} | {"kind": "session", "id", "speed"} (replay a
        saved session's audio) | {"kind": "mic", "device"} (validated by the server). A file
        source may also carry "end" (seconds; troubadour-sim only, the API never passes it)."""
        self._require_models()
        kind = source["kind"]
        if kind in ("file", "session"):
            key = "name" if kind == "file" else "id"
            try:
                if kind == "file":
                    path, end = sample_path(source[key]), source.get("end")
                else:
                    path, end = session_audio(source[key])
                src = FileSource(path, float(source.get("speed") or 1.0), end=end)
            except KeyError:
                raise
            except Exception as e:
                raise ValueError(f"Couldn't read {source[key]}: {e}") from e
            desc = {"kind": kind, key: source[key], "speed": src.speed}
        else:
            src = MicSource(source.get("device"))
            desc = {"kind": "mic", "device": source.get("device"), "device_name": src.device_name()}
        self.stop_mic_test()  # a session needs the mic; its own meter takes over
        with self._start_lock:
            sess = LiveSession(src, desc, self.campaign)
            if kind == "mic" and config.RECORD_AUDIO:
                self._attach_recorder(sess, src)  # before start(): the recording begins at t=0 too
            # Open the source first: if the mic is missing or busy, nothing has changed yet.
            try:
                src.start(sess.frames_q.put, lambda: sess.frames_q.put(STOP))
            except Exception as e:
                src.stop()
                if sess.recorder is not None:
                    sess.recorder.close()
                    shutil.rmtree(sess.recorder.path.parent, ignore_errors=True)
                raise ValueError(f"Couldn't open {desc['kind']}: {e}") from e
            self.save_transcript(force=True)  # the old transcript is about to be cleared
            with self.lock:
                if self.live is not None:
                    self.live.abandon()  # its threads exit without touching shared state
                self.messages.clear()
                for vid in [v.id for v in self.voices.values() if v.status == "pending"]:
                    del self.voices[vid]
                for v in self.voices.values():
                    v.spans = []  # spans pointed into the previous session's audio
                self.live, self.buffer, self.source_desc, self.status = sess, sess.buffer, desc, "running"
                snapshot = self.snapshot()
            sess.awake = keep_awake()  # a 4-hour session must not stop when the Mac idles to sleep
            self._snapshot_setup(sess, "start")
            sess.meter = levels.LevelMeter(self.bus.emit) if self.audio_monitor else None
            threading.Thread(target=self._segment_loop, args=(sess,), name="segmenter", daemon=True).start()
            threading.Thread(target=self._worker_loop, args=(sess,), name="worker", daemon=True).start()
        self.bus.emit("snapshot", snapshot)  # clients drop the old transcript
        return snapshot["session"]

    def _attach_recorder(self, sess: LiveSession, src: MicSource) -> None:
        """Full-quality recording: the mic's own rate (usually 48 kHz), not the pipeline's 16 kHz."""
        try:
            rate = src.native_rate()
        except Exception:
            return  # no such device: start() is about to fail with the real message

        def failed(error: str) -> None:  # from the writer thread, mid-session
            log.error("session recording stopped: %s", error)
            self.bus.emit("error", {"message": f"The audio recording stopped (the transcript still saves): {error}"})

        try:
            sess.recorder = Recorder(self.sessions_dir() / sess.id / "audio.wav", rate, on_error=failed)
        except OSError as e:
            log.exception("can't record session audio")
            self.bus.emit("error", {"message": f"Not recording audio (the transcript still saves): {e}"})
            return
        src.tap = sess.recorder.write

    def pause(self) -> dict:
        """Hold the mic or replay. The session, transcript, and recording stay open."""
        with self._start_lock, self.lock:
            sess = self.live
            if self.status != "running" or sess is None:
                return self.session_json()
            sess.paused = True
            self.status = "paused"
            sess.source.pause()
            out = self.session_json()
        self.bus.emit("session.updated", out)
        return out

    def resume(self) -> dict:
        with self._start_lock, self.lock:
            sess = self.live
            if self.status != "paused" or sess is None:
                return self.session_json()
            sess.source.resume()
            sess.paused = False
            self.status = "running"
            out = self.session_json()
        self.bus.emit("session.updated", out)
        return out

    def stop(self) -> dict:
        """Stop listening; queued utterances still finish, then status goes idle."""
        with self._start_lock, self.lock:
            sess = self.live
            if self.status not in ("running", "paused") or sess is None:
                return self.session_json()
            self.status = "stopping"
            sess.paused = False
            sess.source.stop()
            sess.frames_q.put(STOP)
            out = self.session_json()
        self.bus.emit("session.updated", out)
        return out

    def _finish(self, sess: LiveSession) -> None:
        with self.lock:
            if sess is not self.live:
                return
        # Save everything before announcing "idle": a Replay clicked the moment the UI says the
        # session ended must see the final meta.json (its duration caps the replay) and recording.
        sess.release()
        self.save_transcript(force=True)
        self._save_state()  # keep voiceprints adapted during the session
        self._snapshot_setup(sess, "end")
        if sess.desc["kind"] == "mic":
            threading.Thread(target=self._quality_quietly, args=(sess.id,), name="quality", daemon=True).start()
        with self.lock:
            if sess is not self.live:  # a new session started meanwhile; it saved this one first
                return
            self.status = "idle"
        self._emit_session()
        self.refine_now.set()

    # ------------------------------------------------------------- segmenter

    def _segment_loop(self, sess: LiveSession) -> None:
        if not self._wait_ready(sess):
            # Models failed to load (the error toast is already out) or the session was replaced:
            # stop the source and let the worker end the session instead of leaving it "running".
            sess.source.stop()
            sess.utt_q.put(STOP)
            return
        try:
            self._segment(sess)
        except Exception as e:
            log.exception("segmenter failed")
            self.bus.emit("error", {"message": f"Listening stopped: {type(e).__name__}: {e}"})
            with self.lock:
                if sess is self.live and self.status == "running":
                    self.status = "stopping"
            sess.source.stop()
            sess.utt_q.put(STOP)  # worker finishes what it has, then the session goes idle

    def _segment(self, sess: LiveSession) -> None:
        import torch
        from silero_vad import load_silero_vad

        # Own model per session: Silero is stateful, and an abandoned session may still be draining.
        vad = load_silero_vad()
        seg = Segmenter(SR, SEGMENT)
        buffer, frames_q, utt_q = sess.buffer, sess.frames_q, sess.utt_q
        was_speaking = False
        next_active = 0
        active_voice: str | None = None
        next_session_emit = 0.0
        last_frame, stalled = time.monotonic(), False
        while True:
            try:
                frame = frames_q.get(timeout=0.5)
            except queue.Empty:
                if sess.abandoned:
                    return
                if sess.paused:
                    last_frame = time.monotonic()
                    continue
                # A mic that stops delivering (unplugged, Bluetooth drop, device reset) would
                # otherwise leave a silent "running" session. File replays are paced by us.
                if sess.desc["kind"] == "mic" and not stalled and time.monotonic() - last_frame > MIC_STALL_S:
                    stalled = True
                    log.warning("no audio from the microphone for %.0fs", MIC_STALL_S)
                    self.bus.emit(
                        "error", {"message": f"No audio from the microphone for {MIC_STALL_S:.0f}s. Is it still plugged in?"}
                    )
                continue
            last_frame = time.monotonic()
            if stalled:
                stalled = False
                log.info("microphone audio is back")
            if sess.abandoned:
                return
            if frame is STOP:
                for start, end in seg.flush():
                    utt_q.put((start / SR, end / SR))
                utt_q.put(STOP)
                return
            buffer.append(frame)
            meter = sess.meter  # read once: the UI can switch the monitor off mid-frame
            if meter is not None:
                meter.feed(frame)
            job = self.train_job
            if job is not None and job.from_session:
                job.feed(frame)
            now = buffer.n
            with torch.no_grad():
                prob = vad(torch.from_numpy(frame), SR).item()
            for start, end in seg.feed(frame, prob):
                utt_q.put((start / SR, end / SR))
            if seg.start is not None and not was_speaking:
                next_active = seg.start + int(0.8 * SR)
            was_speaking = seg.start is not None
            if seg.start is None and active_voice is not None:
                active_voice = None
                self.bus.emit("speaker.active", {"voice_id": None, "confidence": 0.0})
            if seg.start is not None and now >= next_active:
                next_active = now + int(ACTIVE_EVERY_S * SR)
                vid, score = self._quick_speaker(buffer, max(seg.start, now - int(ACTIVE_WINDOW_S * SR)), now)
                if vid is not None:
                    active_voice = vid
                    self.bus.emit("speaker.active", {"voice_id": vid, "confidence": round(score, 3)})
            if now / SR >= next_session_emit:
                next_session_emit = now / SR + 5.0
                self._emit_session()

    def _embed(self, audio: np.ndarray, start: float, end: float) -> np.ndarray | None:
        with self.embed_lock:
            return self.matcher.embed(audio, start, end)

    def _quick_speaker(self, buffer: AudioBuffer, start: int, end: int) -> tuple[str | None, float]:
        """Best guess for the box lights: named voices, else a visible pending voice. No adaptation."""
        vec = self._embed(buffer.view(), start / SR, end / SR)
        if vec is None:
            return None, 0.0
        with self.lock:
            if self.matcher.names:
                scores = self.matcher.scores(vec)
                best = int(np.argmax(scores))
                if scores[best] >= self.matcher.unknown_below:
                    return self.matcher.names[best], float(scores[best])
            pending = [(float(normalize(v.sum) @ vec), v.id) for v in self.voices.values() if v.status == "pending" and v.visible]
        if pending:
            score, vid = max(pending)
            if score >= JOIN:
                return vid, score
        return None, 0.0

    # ---------------------------------------------------------------- worker

    def _worker_loop(self, sess: LiveSession) -> None:
        # No wait for the models here: utterances only arrive once the segmenter has them.
        warned = False
        while True:
            item = sess.utt_q.get()
            if sess.abandoned:
                return
            if item is STOP:
                self._finish(sess)
                return
            try:
                self._process_utterance(sess, *item)
            except Exception as e:
                log.exception("utterance failed")
                if not warned:  # once per session: the UI should know lines are being lost
                    warned = True
                    self.bus.emit("error", {"message": f"Transcription failed: {type(e).__name__}: {e}"})

    def _glossary_state(self) -> tuple[Corrector | None, list[str]]:
        """(text fixer, names for Whisper) from the vault, rebuilt when a note changes (checked
        every few seconds). A vault that can't be read never stops transcription."""
        if self.vault is None:
            return None, []
        signature, checked, corrector, names, entries = self._glossary
        now = time.monotonic()
        if corrector is not None and now - checked < GLOSSARY_CHECK_S:
            return corrector, names
        try:
            fresh = self.vault.signature()
            if corrector is None or fresh != signature:
                entries = self.vault.glossary()
                corrector = Corrector(entries, english())
                order = {"pc": 0, "npc": 1}
                names = [e.name for e in sorted(entries, key=lambda e: order.get(e.type, 2))]
        except OSError:
            log.exception("couldn't read the campaign glossary")
            fresh = signature
        self._glossary = (fresh, now, corrector, names, entries)
        return corrector, names

    def _ignored_signature(self) -> tuple:
        try:
            st = self.vault.ignored_path.stat() if self.vault is not None else None
            return (st.st_mtime_ns, st.st_size) if st else ()
        except Exception:  # an odd or broken vault never stops transcription
            return ()

    def speller(self) -> cleanup.Speller | None:
        """Known spellings for the cleanup: confirmed glossary entries, the names Setup and the
        voices panel know (even before they're in the vault), and the D&D vocabulary."""
        if not config.CLEANUP:
            return None
        self._glossary_state()
        signature, entries = self._glossary[0], self._glossary[4]
        with self.lock:
            known = tuple(self.known_names())
        key = (signature, known, self._ignored_signature())
        if self._spell[0] != key:
            have = {e.name.casefold() for e in entries}
            extra = [Entity(name, kind, description=about) for name, kind, about in known if name.casefold() not in have]
            keep = []
            if self.vault is not None:
                try:  # the user's calls: ignored words, and names still waiting for review
                    keep = [
                        *self.vault.ignored(),
                        *(n for e in self.vault.entities() if e.status == "suggested" for n in e.names()),
                    ]
                except Exception:  # an odd or broken vault never stops transcription
                    keep = []
            self._spell = (key, cleanup.Speller([*entries, *extra], keep=keep))
        return self._spell[1]

    def whisper_prompt(self, vault_names: list[str], history: list[str], seconds: float) -> str:
        """The Whisper prompt for a `seconds` long clip after `history` (the lines so far): the
        table's names (Setup, voices panel), glossary names said lately, the rest of the glossary,
        and the last lines, fitted to Whisper's prompt window. A clip shorter than CONTEXT_MIN_S
        gets no lines: on a blip of noise they tempt Whisper into a whole invented sentence.
        asr.transcribe drops the prompt if it gets echoed."""
        with self.lock:
            table = [n for n, _, _ in self.known_names()] + [p.name for p in self.players.values()]
        nicknames = [a for e in self._glossary[4] for a in e.aliases]  # entries as of the last glossary check
        names = asr.prompt_names(table, vault_names, history[-PROMPT_MENTION_LINES:], nicknames)[:PROMPT_NAMES]
        lines = history[-config.ASR_CONTEXT_LINES :] if seconds >= CONTEXT_MIN_S else []
        return asr.build_prompt(names, lines)

    def _whisper_prompt(self, vault_names: list[str], seconds: float) -> str | None:
        if not config.ASR_PROMPT:
            return None
        with self.lock:
            history = [m.text for m in list(self.messages.values())[-PROMPT_MENTION_LINES:]]
        return self.whisper_prompt(vault_names, history, seconds)

    def _process_utterance(self, sess: LiveSession, t0: float, t1: float) -> None:
        audio = sess.buffer.view()
        corrector, vault_names = self._glossary_state()
        clip = audio[int(t0 * SR) : int(t1 * SR)]
        prompt = self._whisper_prompt(vault_names, t1 - t0)
        text = asr.transcribe(clip, prompt) if prompt else asr.transcribe(clip)
        if not text or transcript.filler_only(text):  # "Um...": nothing was said
            return
        if corrector is not None:
            text = corrector(text)
        fixes: list[dict] = []
        unsure = False
        if (speller := self.speller()) is not None:
            sure = [(c.start, c.end, c.options[0]) for c in speller.find(text) if c.sure]
            if sure:
                text, fixes = cleanup.apply(text, [], sure)
            unsure = any(not c.sure for c in speller.find(text))
        vec = self._embed(audio, t0, t1)
        with self.lock:
            if sess is not self.live:  # a newer session started while we transcribed
                return
            voice_id, confidence, provisional = self._identify(vec, t0, t1)
            m = Message(
                id=short_id("m"),  # unique across sessions: late LLM results can't hit a new message
                voice_id=voice_id,
                text=text,
                t_start=t0,
                t_end=t1,
                confidence=confidence,
                provisional=provisional,
                fixes=fixes,
                level_db=levels.clip_db(clip),
                vec=vec,
            )
            self.messages[m.id] = m
            out = m.to_json()
            prev_id = next(reversed(list(self.messages)[:-1]), None)
        self.bus.emit("message.created", out)
        if prev_id is not None:
            self._separate_from_next(audio, prev_id, t0)
        if unsure:
            self.cleanup_q.put(m.id)

    def _separate_from_next(self, audio: np.ndarray, prev_id: str, next_start: float) -> None:
        """The previous line's voiceprint was taken before we knew this line would follow.
        Rescore it on audio that stops where this line starts, so its tail isn't this speaker."""
        with self.lock:
            prev = self.messages.get(prev_id)
            if prev is None or prev.pinned or self.matcher is None:
                return
            if next_start - prev.t_end > self.matcher.pad_to:
                return
            end = min(prev.t_end, next_start)
            # The cutter keeps ~200 ms after the last speech frame; that is where a quick reply sits.
            if next_start - prev.t_end < 0.5 and end - prev.t_start > 0.7:
                end -= 0.2
            start = prev.t_start
            if end - start < 0.5:
                return
        vec = self._embed(audio, start, end)
        if vec is None:
            return
        with self.lock:
            prev = self.messages.get(prev_id)
            if prev is None or prev.pinned or not self.matcher.names:
                return
            scores = self.matcher.scores(vec)
            best = int(np.argmax(scores))
            score = float(scores[best])
            names = self.matcher.names
            current = float(scores[names.index(prev.voice_id)]) if prev.voice_id in names else -1.0
            # A near tie stays: the same person, heard a little differently, is not a new speaker.
            if score < self.matcher.unknown_below or score - current < 0.1:
                return
            pick = self._character_tiebreak(scores, names[best], prev.voice_id)
            if pick == prev.voice_id:
                return
            prev.vec = vec
            dur = prev.t_end - prev.t_start
            old = self.voices.get(prev.voice_id)
            if old is not None:
                old.seconds = max(0.0, old.seconds - dur)
            new = self.voices.get(pick)
            if new is not None:
                new.seconds += dur
                new.spans.append((prev.t_start, prev.t_end))
            prev.voice_id = pick
            prev.confidence = score
            prev.provisional = score < PROVISIONAL_BELOW
            patch = {
                "id": prev.id,
                "voice_id": pick,
                "confidence": round(score, 3),
                "provisional": prev.provisional,
            }
        self._emit_patches([patch])

    # ----------------------------------------------------------- spelling cleanup

    def _cleanup_loop(self) -> None:
        """Settles names only context can decide ("Dallara": Elara or Delara?) a few seconds
        behind the transcript, in small batches."""
        while True:
            batch = [self.cleanup_q.get()]
            deadline = time.monotonic() + CLEANUP_GATHER_S
            while len(batch) < CLEANUP_BATCH and (left := deadline - time.monotonic()) > 0:
                try:
                    batch.append(self.cleanup_q.get(timeout=left))
                except queue.Empty:
                    break
            try:
                self._cleanup(batch)
            except Exception:  # the LLM is down or confused: the line keeps Whisper's spelling
                log.exception("spelling cleanup failed")
            finally:
                for _ in batch:
                    self.cleanup_q.task_done()  # a campaign switch waits for these

    def _cleanup(self, batch: list[str]) -> None:
        speller = self.speller()
        if speller is None:
            return
        with self.lock:
            order = list(self.messages)
            targets = []
            for mid in batch:
                m = self.messages.get(mid)
                cands = [c for c in speller.find(m.text) if not c.sure] if m else []
                if cands:
                    targets.append((m.id, self._speaker_name(m), m.text, cands))
            if not targets:
                return
            first = order.index(targets[0][0])
            asked = {t[0] for t in targets}
            context = [
                (self.messages[j].id, self._speaker_name(self.messages[j]), self.messages[j].text)
                for j in order[max(0, first - CLEANUP_CONTEXT) : first]
                if j not in asked
            ]
        picked = cleanup.ask(self.cleanup_llm.json, speller, context, targets)
        patches, learned = [], []
        with self.lock:
            for mid, _, text, cands in targets:
                m = self.messages.get(mid)
                if m is None or m.text != text:  # changed meanwhile: the answer no longer fits
                    continue
                edits = [(c.start, c.end, picked[(mid, c.start)]) for c in cands if (mid, c.start) in picked]
                if not edits:
                    continue
                learned += [(text[s:e], name) for s, e, name in edits]
                m.text, m.fixes = cleanup.apply(m.text, m.fixes, edits)
                patches.append({"id": m.id, "text": m.text, "fixes": m.fixes})
        self._emit_patches(patches)
        self.learn(learned, speller)

    def learn(self, learned: list[tuple[str, str]], speller: cleanup.Speller, learner: cleanup.Learner | None = None) -> None:
        """A spelling the LLM keeps resolving the same way becomes a heard-as suggestion."""
        if self.vault is None:
            return
        changed = False
        for heard, name in learned:
            if not (learner or self._learner).note(heard, name) or name not in speller.glossary:
                continue
            try:
                e = self.vault.get(name)
                taken = {h.casefold() for h in (*e.heard_as_suggested, *e.heard_as_rejected)}
                if self.vault.find(heard) is not None or heard.casefold() in taken:
                    continue
                self.vault.update(e.name, heard_as_suggested=[*e.heard_as_suggested, heard])
                changed = True
            except (KeyError, ValueError, OSError):
                continue
        if changed:
            self.bus.emit("campaign.updated", {})

    def _identify(self, vec: np.ndarray | None, t0: float, t1: float) -> tuple[str | None, float, bool]:
        """(voice_id, confidence, provisional); lock held. Option B discovery when nothing matches."""
        duration = t1 - t0
        if vec is None:  # too short to embed: lean on the previous speaker, flagged
            last = next(reversed(self.messages.values()), None)
            return (last.voice_id if last else None), 0.2, True
        name, score, unknown = self.matcher.classify(vec, duration)
        if not unknown:
            name = self._character_tiebreak(self.matcher.scores(vec), name, self._last_voice_of(name))
            v = self.voices.get(name)
            if v:
                v.seconds += duration
                v.spans.append((t0, t1))
            return name, score, score < PROVISIONAL_BELOW
        pending = [(float(normalize(v.sum) @ vec), v) for v in self.voices.values() if v.status == "pending"]
        best_score, group = max(pending, key=lambda x: x[0], default=(0.0, None))
        if group is None or best_score < JOIN:
            group = Voice(id=short_id("v"), label=None, sum=np.zeros_like(vec))
            self.voices[group.id] = group
            best_score = 0.0
        group.sum = group.sum + vec * duration
        group.seconds += duration
        group.spans.append((t0, t1))
        if group.label is None and group.seconds >= PANEL_S:
            self._surface(group)
        if group.visible:
            self._update_suggestion(group)
            self.bus.emit("voice.upserted", group.to_json())
        return group.id, best_score, True

    def _last_voice_of(self, voice_id: str) -> str | None:
        """The voice used for this voice's player in their most recent message."""
        owner = self.voices[voice_id].player_id if voice_id in self.voices else None
        if owner is None:
            return None
        for m in reversed(self.messages.values()):
            v = self.voices.get(m.voice_id) if m.voice_id else None
            if v and v.player_id == owner:
                return v.id
        return None

    def _character_tiebreak(self, scores: np.ndarray, best: str, previous: str | None) -> str:
        """Pick among ONE player's voiceprints (e.g. normal + a character voice named in the panel).

        The speaker embedding barely separates one person's voices, so switching
        away from the player's previous voice needs CHARACTER_MARGIN; near-ties
        keep the previous one (else their normal voice). The player is right either way.
        """
        owner = self.voices[best].player_id if best in self.voices else None
        if owner is None:
            return best
        names = self.matcher.names
        candidates = [n for n in names if n in self.voices and self.voices[n].player_id == owner]
        if len(candidates) < 2:
            return best
        rivals = [float(scores[names.index(n)]) for n in candidates if n != best]
        if float(scores[names.index(best)]) - max(rivals) >= CHARACTER_MARGIN:
            return best
        if previous in candidates:
            return previous
        normal = next((n for n in candidates if self.voices[n].character is None), None)
        return normal or best

    # ---------------------------------------------------------------- refine

    def _refine_loop(self) -> None:
        while True:
            self.refine_now.wait(REFINE_EVERY_S)
            self.refine_now.clear()
            if not self.ready.is_set():
                continue
            try:
                self._refine()
                self.save_transcript()  # autosave: a crash or power loss costs at most REFINE_EVERY_S
            except Exception:
                log.exception("refine failed")

    def _refine(self) -> None:
        """Re-score past messages with the matured voiceprints (benchmark: +14 pts for option B),
        then tidy pending voices. All message changes go out as one messages.updated batch."""
        patches: dict[str, dict] = {}
        with self.lock:
            if self.matcher.names:
                self._relabel(patches)
            self._tidy_pending(patches)
        self._emit_patches([{"id": mid, **p} for mid, p in patches.items()])

    def _relabel(self, patches: dict[str, dict]) -> None:
        for m in self.messages.values():
            if m.vec is None or m.pinned:
                continue
            scores = self.matcher.scores(m.vec)
            best = int(np.argmax(scores))
            score = float(scores[best])
            patch = {}
            if score >= self.matcher.unknown_below:
                pick = self._character_tiebreak(scores, self.matcher.names[best], m.voice_id)
                if pick != m.voice_id:
                    patch["voice_id"] = m.voice_id = pick
            current = self.voices.get(m.voice_id)
            if current and current.status == "named" and m.voice_id in self.matcher.names:
                own = float(scores[self.matcher.names.index(m.voice_id)])
                provisional = own < PROVISIONAL_BELOW
                if abs(own - m.confidence) > 0.02:
                    m.confidence = own
                    patch["confidence"] = round(own, 3)
                if provisional != m.provisional:
                    patch["provisional"] = m.provisional = provisional
            if patch:
                patches.setdefault(m.id, {}).update(patch)

    def _tidy_pending(self, patches: dict[str, dict]) -> None:
        """Keep the voices panel short over a long session (a 16 h soak grew 132 pending voices).

        - A pending voice whose lines have all moved to named voices disappears.
        - Pending voices whose voiceprints drifted together (≥ JOIN) merge into the biggest.
        """
        owned: dict[str, int] = {}
        for m in self.messages.values():
            if m.voice_id:
                owned[m.voice_id] = owned.get(m.voice_id, 0) + 1
        pending = sorted((v for v in self.voices.values() if v.status == "pending"), key=lambda v: -v.seconds)
        kept: list[Voice] = []
        changed: set[str] = set()
        for v in pending:
            if not owned.get(v.id):
                self._drop_pending(v, None)
                continue
            target = next((k for k in kept if float(normalize(k.sum) @ normalize(v.sum)) >= JOIN), None)
            if target is None:
                kept.append(v)
                continue
            target.sum = target.sum + v.sum
            target.seconds += v.seconds
            target.spans += v.spans
            for m in self.messages.values():
                if m.voice_id == v.id:
                    m.voice_id = target.id
                    patches.setdefault(m.id, {})["voice_id"] = target.id
            self._drop_pending(v, target.id)
            changed.add(target.id)
        for vid in changed:
            target = self.voices[vid]
            if target.label is None and target.seconds >= PANEL_S:
                self._surface(target)
            if target.visible:
                self._update_suggestion(target)
                self.bus.emit("voice.upserted", target.to_json())

    def _drop_pending(self, v: Voice, merged_into: str | None) -> None:
        del self.voices[v.id]
        if v.visible:
            self.bus.emit("voice.removed", {"id": v.id, "merged_into": merged_into})

    # ------------------------------------------------------------------- llm

    def _speaker_name(self, m: Message) -> str:
        v = self.voices.get(m.voice_id) if m.voice_id else None
        if v and v.player_id and v.player_id in self.players:
            p = self.players[v.player_id]
            who = v.character or p.character
            return f"{p.name}{' (GM)' if p.is_gm else ''}{f' as {who}' if who else ''}"
        return v.label if v and v.label else "Unknown"

    def _set_llm_ok(self, ok: bool) -> None:
        if ok != self.llm_ok:
            self.llm_ok = ok
            self._emit_session()

    def recheck_llm(self) -> dict:
        """Probe LM Studio now, instead of waiting for the periodic health loop."""
        try:
            ok = self.cleanup_llm.healthy()
        except Exception as e:
            log.warning("llm health check failed: %s", e)
            ok = False
        self._set_llm_ok(ok)
        return {"url": self.cleanup_llm.url, "model": self.cleanup_llm.model, "ok": self.llm_ok}

    def _llm_health_loop(self) -> None:
        while True:
            try:
                self._set_llm_ok(self.cleanup_llm.healthy())
            except Exception as e:
                log.warning("llm health check failed: %s", e)
                self._set_llm_ok(False)
            time.sleep(LLM_HEALTH_EVERY_S)

    # ----------------------------------------------------------------- train

    def train(self, pid: str, seconds: float = 20.0) -> None:
        self._require_models()
        # _start_lock: a campaign switch or bench (which check for training under it) can't
        # slip in between and leave this job training someone who's no longer seated.
        with self._start_lock, self.lock:
            if pid not in self.players:
                raise KeyError(pid)
            if self.train_job is not None:
                raise ValueError("Already training someone; finish or cancel that first.")
            job = TrainJob(pid, max(5.0, min(float(seconds), 60.0)), from_session=self.status == "running")
            self.train_job = job
        self._emit_session()
        threading.Thread(target=self._train_run, args=(job,), name="train", daemon=True).start()

    def cancel_training(self) -> None:
        with self.lock:
            job = self.train_job
        if job is not None:
            job.cancel()

    def _train_status(self, job: TrainJob, state: str, message: str | None = None) -> None:
        recent = job.frames[-8:]
        level = float(min(1.0, np.sqrt(np.mean(np.concatenate(recent) ** 2)) * 8)) if recent else 0.0
        self.bus.emit(
            "train.status",
            {
                "player_id": job.player_id,
                "state": state,
                "seconds_left": round(max(0.0, job.seconds - job.captured_s), 1) if state == "recording" else 0,
                "level": round(level, 3) if state == "recording" else 0,
                "message": message,
            },
        )

    def _train_run(self, job: TrainJob) -> None:
        own_mic = None
        try:
            if not self._wait_ready():
                raise RuntimeError(self.load_error or "models unavailable")
            if not job.from_session:  # no session running: open the default mic just for this
                own_mic = MicSource()
                own_mic.start(job.feed, lambda: None)
            deadline = time.monotonic() + job.seconds * 3 + 5
            while not job.done.wait(0.25):
                self._train_status(job, "recording")
                if job.from_session and self.status == "paused":
                    deadline += 0.25  # wall time spent paused is not "no audio arrived"
                    continue
                if job.from_session and self.status != "running":
                    raise RuntimeError("The session stopped before training finished.")
                if time.monotonic() > deadline:
                    raise RuntimeError("No audio arrived; is the mic working?")
            if own_mic:
                own_mic.stop()
                own_mic = None
            if job.cancelled:
                self._train_status(job, "cancelled")
                return
            self._train_status(job, "processing")
            message = self._finish_training(job)
            self._train_status(job, "done", message)
        except Exception as e:
            log.warning("training failed: %s", e)
            self._train_status(job, "error", str(e))
        finally:
            if own_mic:
                own_mic.stop()
            with self.lock:
                if self.train_job is job:
                    self.train_job = None
            self._emit_session()

    def _finish_training(self, job: TrainJob) -> str:
        import torch
        from silero_vad import get_speech_timestamps, load_silero_vad

        audio = np.concatenate(job.frames)
        # Separate VAD instance: the session's model is stateful and owned by the segmenter.
        stamps = get_speech_timestamps(torch.from_numpy(audio), load_silero_vad(), sampling_rate=SR)
        spans = [(s["start"] / SR, s["end"] / SR) for s in stamps]
        speech = sum(e - s for s, e in spans)
        if speech < TRAIN_MIN_SPEECH_S:
            raise ValueError(f"Only heard {speech:.0f}s of speech. Try again, a bit louder or closer.")
        with self.embed_lock:
            vec = centroid(self.embedder, audio, spans)
        if vec is None:
            raise ValueError("Couldn't get a voiceprint from that recording.")
        with self.lock:
            p = self.players.get(job.player_id)
            if p is None:
                raise ValueError("That player was deleted.")
            target = next(
                (v for v in self.voices.values() if v.status == "named" and v.player_id == p.id and v.character is None),
                None,
            )
            if target is None:
                target = Voice(id=short_id("v"), label=None, status="named", player_id=p.id)
                self._surface(target)
                self.voices[target.id] = target
            self.matcher.add(target.id, vec)  # merges if the voice already exists
            target.sample_audio = audio
            target.sample_rev += 1
            target.seconds += speech
            target.trained = p.trained = True
            self.bus.emit("voice.upserted", target.to_json())
            self.bus.emit("player.upserted", p.to_json())
            self._refresh_suggestions()
            path = self._voice_audio_path(target.id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(wav_bytes(audio))  # voice samples survive restarts
        self._save_state()
        self.refine_now.set()
        return f"Heard {speech:.0f}s of speech."
