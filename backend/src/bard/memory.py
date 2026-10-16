"""Campaign memory jobs: the local LLM reads pasted notes and played sessions into the vault.

- import_notes: pasted session notes → Notes/<title>.md (verbatim) + entities and facts.
  The notes are the user's own, so what they name is added as confirmed.
- write_up: a saved session's transcript → Sessions/<date> <title>.md (summary, highlights,
  events with [[links]]) + its transcript in Sessions/Transcripts/, plus *suggested* facts,
  entities and heard-as spellings for review in the Campaign tab.
- spelling_suggestions: no LLM; transcript words that look like misspellings of a glossary
  name ("Dilara" for Elara), and frequent unknown names worth adding.

Jobs run one at a time on a worker thread and report progress as `campaign.job` events;
every change to the vault is followed by `campaign.updated`. Model output is untrusted:
names, types and lengths are validated before anything is written.
"""

import collections
import difflib
import json
import logging
import queue
import re
import threading
import time
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from . import config, dnd_terms, transcript
from .campaign import TYPES, Entity, Vault, add_unique, everyday, norm, safe_name, write_atomic

log = logging.getLogger("bard")

CHUNK_CHARS = 6000  # notes per extraction call; small enough for a fast, careful answer
# Per transcript part, whatever the model returns. The last part gets more room: capped at 3 like the
# rest, a finale (crowned, allied, handed a sword) came out as "the kobolds thanked the party".
PART_FACTS, PART_NAMES, PART_EVENTS, FINAL_PART_EVENTS = 5, 8, 3, 8
STORY_MAX = 20  # "What happened" beats on a session page
MAX_SUGGESTED_FACTS = 30  # per write-up: more than anyone will review
# Facts about a passing state, not a lasting truth ("Magenta has one hit point").
FLEETING = re.compile(
    r"\b(hit points?|hp|currently|right now|at the moment|is (standing|sitting|walking|holding|filling)|this turn)\b", re.I
)
TRANSCRIPT_CHUNK_CHARS = 12000  # ~15 min of table talk per write-up call
MAX_ITEMS = 40
NAME_MAX, TEXT_MAX = 80, 300
STORY_TEXT_MAX = 600  # a retold beat can merge a whole scene; 300 cut a finale off mid-sentence


# ------------------------------------------------------------------ LLM


class MemoryLLM:
    """OpenAI-compatible chat with a JSON schema (LM Studio supports structured output)."""

    def __init__(self, url: str = config.LLM_URL, model: str = config.MEMORY_LLM_MODEL, timeout: float = 600):
        self.url = url.rstrip("/")
        self.model = model
        self.client = httpx.Client(timeout=timeout)

    def healthy(self) -> bool:
        """The server answers and has this model."""
        try:
            r = self.client.get(f"{self.url}/models", timeout=3)
            data = r.json().get("data", []) if r.status_code == 200 else []
            return any(isinstance(m, dict) and m.get("id") == self.model for m in data)
        except Exception:  # down, wrong service on the port, non-JSON reply: all mean "not usable"
            return False

    def json(self, system: str, user: str, schema: dict, max_tokens: int = 4000) -> dict:
        r = self.client.post(
            f"{self.url}/chat/completions",
            json={
                "model": self.model,
                "temperature": 0,
                "max_tokens": max_tokens,
                "reasoning_effort": config.MEMORY_REASONING,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                "response_format": {"type": "json_schema", "json_schema": {"name": "out", "strict": True, "schema": schema}},
            },
        )
        if r.is_error:
            raise RuntimeError(f"LM Studio couldn't answer ({r.status_code}): {_server_error(r)}")
        content = r.json()["choices"][0]["message"]["content"] or ""
        match = re.search(r"\{.*\}", content, re.S)
        try:
            out = json.loads(match.group(0)) if match else {}
        except json.JSONDecodeError:
            out = {}
        return out if isinstance(out, dict) else {}


def _server_error(r: httpx.Response) -> str:
    """LM Studio's reason, short: its last line ("The model has crashed…"), not a 400 with a docs link."""
    try:
        err = r.json().get("error")
    except ValueError:
        err = r.text
    if isinstance(err, dict):
        err = err.get("message")
    lines = [line.strip() for line in str(err or "").splitlines() if line.strip()]
    return (lines[-1] if lines else "no details")[:200]


def _obj(props: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": props, "required": required or list(props), "additionalProperties": False}


STR = {"type": "string"}
STRS = {"type": "array", "items": STR}
KIND = {"type": "string", "enum": list(TYPES)}

NOTES_SCHEMA = _obj(
    {
        "entities": {
            "type": "array",
            "items": _obj({"name": STR, "type": KIND, "aliases": STRS, "description": STR, "facts": STRS}),
        },
        "campaign_facts": STRS,
    }
)

NOTES_SYSTEM = """You build the glossary for a Dungeons & Dragons campaign from the players' or GM's notes.
List every NAMED thing in the notes: player characters (pc), NPCs (npc), places, factions, items,
creatures, spells, and campaign-specific terms (term). Skip generic words (the party, a goblin, initiative).
For each: the exact spelling used in the notes; other names the notes use for it (aliases); one short
sentence saying what it is; and up to 5 short facts the notes state about it. Never guess or invent.
If a name matches one in the existing glossary, use the glossary's spelling.
campaign_facts: up to 5 facts about the campaign as a whole (where the party is, the main quest).
Answer with JSON only."""

WRITEUP_SCHEMA = _obj(
    {
        "events": {"type": "array", "items": _obj({"at": STR, "text": STR})},
        "facts": {"type": "array", "items": _obj({"about": STR, "text": STR})},
        "new_names": {"type": "array", "items": _obj({"name": STR, "type": KIND, "description": STR})},
        "spellings": {"type": "array", "items": _obj({"heard": STR, "means": STR})},
    }
)

WRITEUP_SYSTEM = """You keep the campaign wiki for a Dungeons & Dragons group. You get part of a session's
speech-to-text transcript (it has errors, especially in names; each line starts with its time) and the
campaign glossary.
events: the story beats of this part (about 15 minutes of play): 1-3, one per scene, in order. A beat says
what changed. Most important: revelations, the moments the party (or the GM) uncovers a secret, a twist or
a threat, said plainly ("Kessia realized the water had been poisoned.", not "noticed it smelled strange").
Then outcomes: what the party gained or lost (a title, allies, enemies, a gift or item (name it), a
reward, a promise, a debt), said specifically ("The kobolds made Thoronn their king, swore to be the
party's allies and gave him a ceremonial sword.", not "thanked the party"). Then: discoveries, decisions,
who the party met and what they learned, a deal, a betrayal, a death, or a fight told as what it meant
and how it ended. Never play-by-play: no single attacks, hits or misses, rolls, initiative, damage,
movement, positioning, spells cast or rules talk.
Bad: "Oola's attack failed." "Phishondra moved 30 feet." "The party rolled initiative." "Thoronn cast
Moonbeam, dealing damage."
Good: "Skeletons wrapped in fire ambushed the party; they fought them off in the fog, and Thoronn's
Moonbeam finished the last one."
Each beat is one or two plain past-tense sentences, like a player recapping last session for a friend:
specific and easy to read, not dramatic (no "terrifying", "harrowing", "grotesque" or other flourishes),
naming who did it. Skip out-of-game chatter. at = the time of the line where the scene starts, copied
exactly from the transcript.
facts: up to 5 lasting things learned about someone or something, that will still be true next session,
each a full sentence naming its subject ("Magenta is afraid of heights."); about = that name as the
glossary spells it, or as the transcript does if it's new. Never passing states (hit points, where
someone is standing, what they're doing right now), rules, or trivia ("is a player character").
new_names: named NPCs, places, items, factions or creatures that matter in this part and are NOT in the
glossary (and are not a misspelling of a glossary name), with a one-sentence description. Up to 10.
spellings: when the transcript clearly misspells a glossary name, the misspelled word exactly as it appears
(heard) and the glossary name it means (means). Only clear cases.
Players joke, plan, speculate and talk about real life; only what actually happens in the game counts,
usually what the GM narrates or confirms ("They assassinate her." as a joke is not an event).
Never invent anything that isn't in the transcript. Answer with JSON only."""

SESSION_END = (
    "This part is the end of the session: use up to 8 beats here, and give each thing the party came away "
    "with its own beat: a title, an alliance, a gift or item (named), a promise. The last beat says how it ended."
)

SUMMARY_SCHEMA = _obj({"title": STR, "summary": STR, "highlights": STRS})

SUMMARY_SYSTEM = """You write the session page of a Dungeons & Dragons campaign wiki from a list of story
beats. title: 2-5 words naming the session (no date). summary: one paragraph, at most 120 words, past
tense, plain and matter-of-fact (no flourishes), ending with how the session ended and what the party came
away with (titles, alliances, gifts and items by name, promises).
highlights: the 3-8 moments the players will want to remember: revelations and twists first (secrets
uncovered, a betrayal, a threat discovered), then outcomes (titles, alliances, enemies, named gifts and
rewards), big wins, losses and decisions, and the finale if it changed something. Not routine fights,
travel or shopping.
Use only what the beats say. Answer with JSON only."""

STORY_SCHEMA = _obj({"story": {"type": "array", "items": _obj({"at": STR, "text": STR})}})

# Its own call: asked alongside the title and summary, the model echoed the first beats instead.
STORY_SYSTEM = """You edit the "What happened" section of a Dungeons & Dragons campaign wiki. You get the session's
story beats in order, each starting with its time. Retell the session as 6-12 beats for someone who wasn't
there: merge everything about one scene or one fight into a single beat, keep what changed the story
(revelations and twists above all, then outcomes such as titles, allies and enemies, discoveries,
decisions, who the party met, what they won or lost, transformations, deaths, how fights ended), and drop
the play-by-play: spells cast, attacks, saving throws, positioning, tactics. When you merge beats, keep
every concrete gain or loss in them by name: titles, alliances, gifts and items, promises, deaths. Give
the ending up to 3 beats of its own, one for each thing the party came away with (a title, an alliance, a
gift), rather than cramming it into one.
Each beat is one or two plain past-tense sentences, like a player recapping last session for a friend:
clear and specific, a little colour is fine but not dramatic, no flourishes ("terrifying", "harrowing",
"grotesque"). at = the time of the first beat it covers, copied exactly. Use only what the beats say.
Answer with JSON only."""


# ------------------------------------------------------------------ validation


def _text(value, limit: int = TEXT_MAX) -> str:
    return re.sub(r"\s+", " ", value).strip()[:limit] if isinstance(value, str) else ""


def _story(value, events: list[tuple[str, str]], ending: set[str]) -> list[tuple[str, str]]:
    """The retold "What happened" as (time, text). A time must be one of the beats' (else the previous
    one's: the model can't invent a moment). It must be short and reach the session's ending (`ending`:
    the last part's beat times); otherwise (empty, or it echoed the beats and ran out before the end) the
    beats themselves."""
    times = {at for at, _ in events}
    out: list[tuple[str, str]] = []
    for beat in value if isinstance(value, list) else []:
        if isinstance(beat, dict) and (text := _text(beat.get("text"), STORY_TEXT_MAX)):
            at = beat.get("at")
            out.append((at if at in times else out[-1][0] if out else events[0][0], text))
    return out if 0 < len(out) <= STORY_MAX and out[-1][0] in ending else events


_SMALL = {"of", "the", "and", "de", "du", "da", "von", "van", "a", "an", "in", "on", "to"}


def _name(value) -> str:
    """A plausible proper name, or "": short; not a list ("Willie, Ollie, and Wally"); not a
    description ("The rooster", "the party"): its words are capitalised, small words aside."""
    name = _text(value, NAME_MAX).strip(" .,:;\"'")
    if not name or not norm(name) or "," in name or ";" in name:
        return ""
    words = name.split()
    if words[0].casefold() == "the" and len(words) > 1:
        words = words[1:]
    big = [w for w in words if w.casefold() not in _SMALL]
    if not big or not big[0][0].isupper() or sum(w[0].isupper() for w in big) * 2 < len(big):
        return ""
    return name


def _kind(value) -> str:
    return value if value in TYPES else "term"


def chunks(text: str, size: int) -> list[str]:
    """Paragraph-aligned pieces of at most ~size chars (a single huge paragraph is split hard)."""
    out, cur = [], ""
    for para in re.split(r"\n\s*\n", text.strip()):
        if len(para) > size:
            if cur:
                out.append(cur)
                cur = ""
            while len(para) > size:
                out.append(para[:size])
                para = para[size:]
        if cur and len(cur) + len(para) + 2 > size:
            out.append(cur)
            cur = ""
        cur = f"{cur}\n\n{para}" if cur else para
    if cur.strip():
        out.append(cur)
    return out


def glossary_brief(vault: Vault, limit: int = 300) -> str:
    rows = []
    for e in vault.entities()[:limit]:
        also = f" (also {', '.join(e.aliases)})" if e.aliases else ""
        rows.append(f"- {e.name} [{e.type}]{also}")
    return "\n".join(rows) or "(empty)"


# ------------------------------------------------------------------ jobs


@dataclass
class Job:
    id: str
    kind: str  # "import" | "writeup" | "improve" | "spelling"
    title: str
    state: str = "queued"  # queued | running | done | error
    done: int = 0
    total: int = 0
    message: str = ""
    created: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    suggested: int = 0
    note: str | None = None  # vault-relative path of the note it wrote

    def to_json(self) -> dict:
        return dict(self.__dict__)


class Memory:
    def __init__(self, vault: Vault, bus, llm: MemoryLLM | None = None, sessions_dir=None, seed=lambda: []):
        self.vault = vault
        self.bus = bus
        self.llm = llm or MemoryLLM()
        self.sessions_dir = sessions_dir or (lambda: config.DATA_DIR / "sessions")
        self.seed = seed  # () -> [(name, type, description)]: names Bard already knows (Setup, voices panel)
        self.jobs: dict[str, Job] = {}
        self._q: queue.Queue = queue.Queue()
        self._lock = threading.Lock()
        threading.Thread(target=self._run, name="memory", daemon=True).start()

    # ---------------------------------------------------------------- job plumbing

    def emit(self, job: Job) -> None:
        self.bus.emit("campaign.job", job.to_json())

    def changed(self) -> None:
        try:
            self.vault.write_index()
        except OSError:
            log.exception("couldn't write Glossary.md")
        self.bus.emit("campaign.updated", {})

    def submit(self, kind: str, title: str, work) -> Job:
        """Queue work(job) on the job thread (one job at a time); progress goes out as campaign.job."""
        job = Job(id=uuid.uuid4().hex[:8], kind=kind, title=title)
        with self._lock:
            self.jobs[job.id] = job
            for old in sorted(self.jobs.values(), key=lambda j: j.id)[:-20]:
                if old.state in ("done", "error"):
                    del self.jobs[old.id]
        self.emit(job)
        self._q.put((job, work))
        return job

    def _run(self) -> None:
        while True:
            job, work = self._q.get()
            job.state = "running"
            self.emit(job)
            try:
                work(job)
                job.state = "done"
            except Exception as e:
                log.exception("campaign job %s failed", job.kind)
                job.state, job.message = "error", f"{type(e).__name__}: {e}"
            self.emit(job)
            self.changed()

    def list_jobs(self) -> list[dict]:
        with self._lock:
            return [j.to_json() for j in self.jobs.values()]

    def busy(self) -> bool:
        """A job is queued or running (they write the open campaign's notes and sessions)."""
        with self._lock:
            return any(j.state in ("queued", "running") for j in self.jobs.values())

    # ---------------------------------------------------------------- seeding

    def seed_known(self) -> list[str]:
        """Names Bard already knows become entries: Setup's characters (pc) and the character or
        NPC names given to voices in the voices panel (npc). The most certain names there are."""
        added = []
        for name, kind, description in self.seed():
            name = (name or "").strip()
            if not name or norm(name) in ("dm", "gm") or self.vault.find(name):
                continue
            try:
                self.vault.create(name, kind, description=description)
                added.append(name)
            except ValueError:
                continue
        if added:
            self.changed()
        return added

    # ---------------------------------------------------------------- import notes

    def import_notes(self, title: str, text: str) -> Job:
        title = safe_name(title or time.strftime("Notes %Y-%m-%d"))
        text = text.strip()
        if not text:
            raise ValueError("Paste some notes first.")
        return self.submit("import", title, lambda job: self._import(job, title, text))

    def _import(self, job: Job, title: str, text: str) -> None:
        self.seed_known()
        stamp = time.strftime("%Y-%m-%d")
        path = self.vault.write_note(
            "Notes", title, f"---\ntype: notes\nimported: {stamp}\ntags: [bard/notes]\n---\n# {title}\n\n{text}\n"
        )
        note = path.stem
        job.note = str(path.relative_to(self.vault.root))
        parts = chunks(text, CHUNK_CHARS)
        job.total = len(parts)
        self.emit(job)
        for part in parts:
            out = self.llm.json(NOTES_SYSTEM, f"Existing glossary:\n{glossary_brief(self.vault)}\n\nNotes:\n{part}", NOTES_SCHEMA)
            self._merge_notes(job, out, note)
            job.done += 1
            job.message = f"Read part {job.done} of {job.total}"
            self.emit(job)
            self.bus.emit("campaign.updated", {})
        job.message = f"{len(job.created)} new, {len(job.updated)} updated"

    def _merge_notes(self, job: Job, out: dict, note: str) -> None:
        source = f" ([[{note}]])"
        items = out.get("entities") if isinstance(out.get("entities"), list) else []
        for item in items[:MAX_ITEMS]:
            if not isinstance(item, dict) or not (name := _name(item.get("name"))):
                continue
            aliases = [a for a in (_name(a) for a in item.get("aliases") or [] if isinstance(a, str)) if a]
            facts = [f + source for f in (_text(f) for f in item.get("facts") or [] if isinstance(f, str)) if f][:5]
            description = _text(item.get("description"))
            existing = self.vault.find(name) or next((e for a in aliases if (e := self.vault.find(a))), None)
            # An alias that already names a *different* entry would make two notes claim one name.
            aliases = [a for a in aliases if (o := self.vault.find(a)) is None or (existing and o.path == existing.path)]
            if existing is None:
                try:
                    e = self.vault.create(name, _kind(item.get("type")), aliases=aliases, description=description, facts=facts)
                except ValueError:
                    continue
                job.created.append(e.name)
            else:
                # The user's own notes outrank a write-up's guess: a suggested entry they mention is
                # confirmed, and their description replaces the AI's.
                guessed = existing.status == "suggested"
                self.vault.update(
                    existing.name,
                    aliases=[
                        *existing.aliases,
                        *aliases,
                        *([name] if norm(name) not in {norm(n) for n in existing.names()} else []),
                    ],
                    facts=add_unique(existing.facts, facts),
                    description=(description or existing.description) if guessed else (existing.description or description),
                    status="confirmed",
                )
                if existing.name not in job.created and existing.name not in job.updated:
                    job.updated.append(existing.name)
        for fact in (out.get("campaign_facts") or [])[:5]:
            if text := _text(fact):
                self.vault.add_campaign_fact(text + source)

    # ---------------------------------------------------------------- session write-up

    def write_up(self, sid: str) -> Job:
        path = self.sessions_dir() / sid / "transcript.json"
        if not path.is_file():
            raise KeyError(sid)
        record = json.loads(path.read_text())
        return self.submit("writeup", sid, lambda job: self._write_up(job, record))

    def _write_up(self, job: Job, record: dict) -> None:
        self.seed_known()
        s = record["session"]
        lines = [
            f"{transcript.clock(m['t_start'])} {m['speaker']}{' as ' + m['character'] if m.get('character') else ''}: {m['text']}"
            for m in record["messages"]
        ]
        if not lines:
            raise ValueError("That session has no lines.")
        body = "\n".join(lines)
        heard_text = norm(body)
        parts = chunks(body.replace("\n", "\n\n"), TRANSCRIPT_CHUNK_CHARS)
        job.total = len(parts) + 2
        self.emit(job)
        events: list[tuple[str, str]] = []  # (clock of the part's first line, event)
        ending: set[str] = set()  # the last part's beat times: a retelling must get that far
        facts: list[tuple[str, str]] = []
        new_names: dict[str, tuple[str, str]] = {}
        spellings: list[tuple[str, str]] = []
        hints = misspelling_hints(body, self.vault.entities())
        for i, part in enumerate(parts):
            first = part.split(" ", 1)[0]
            clocks = set(re.findall(r"^(\d+:\d\d(?::\d\d)?) ", part, re.M))
            prompt = f"Glossary:\n{glossary_brief(self.vault)}\n\n"
            if hints:
                prompt += f"Likely misspellings of glossary names in this transcript:\n{hints}\n\n"
            last = i == len(parts) - 1
            if last:
                prompt += f"{SESSION_END}\n\n"
            out = self.llm.json(WRITEUP_SYSTEM, prompt + f"Transcript:\n{part}", WRITEUP_SCHEMA)
            part_events = [ev for ev in out.get("events") or [] if isinstance(ev, dict) and _text(ev.get("text"))]
            for ev in part_events[: FINAL_PART_EVENTS if last else PART_EVENTS]:
                at = ev.get("at") if ev.get("at") in clocks else first  # only a time this part really has
                events.append((at, _text(ev.get("text"))))
                if last:
                    ending.add(at)
            part_facts = [
                (about, text)
                for f in out.get("facts") or []
                if isinstance(f, dict)
                and (about := _name(f.get("about")))
                and (text := _text(f.get("text")))
                and not FLEETING.search(text)
            ]
            facts += part_facts[:PART_FACTS]
            part_names = [n for n in out.get("new_names") or [] if isinstance(n, dict) and _name(n.get("name"))]
            for n in part_names[:PART_NAMES]:
                name = _name(n.get("name"))
                new_names.setdefault(norm(name), (name, _kind(n.get("type")), _text(n.get("description"))))
            for sp in out.get("spellings") or []:
                if isinstance(sp, dict) and (heard := _name(sp.get("heard"))) and (means := _name(sp.get("means"))):
                    spellings.append((heard, means))
            job.done += 1
            job.message = f"Read part {job.done} of {len(parts)}"
            self.emit(job)

        beats = "\n".join(f"- {at} {e}" for at, e in events)
        story = _story(self.llm.json(STORY_SYSTEM, beats, STORY_SCHEMA).get("story"), events, ending) if events else []
        job.done += 1
        job.message = "Told the story"
        self.emit(job)
        summary = self.llm.json(SUMMARY_SYSTEM, "\n".join(f"- {e}" for _, e in events) or "- (nothing happened)", SUMMARY_SCHEMA)
        title = _text(summary.get("title"), 60) or "Session"
        date = time.strftime("%Y-%m-%d", time.localtime(s["started_at"]))
        sid = s["id"]
        existing = self._session_note(sid)
        # A re-run keeps the page's name: facts elsewhere link to it.
        note_title = existing.stem if existing else safe_name(f"{date} {title}")

        # Suggestions first, so the session page can link the new names. A "new" name that sounds
        # like a known one is a misspelling, not a new character ("Dallara" is Elara); among new
        # names, later spellings of an earlier one fold into it ("Willy Olly Wally").
        kept: list[str] = []
        for name, kind, description in new_names.values():
            known = self.vault.find(name) or next(
                (e for e in self.vault.entities() if any(looks_like(name, n) for n in e.names())), None
            )
            if known is not None:
                if not self.vault.find(name):
                    spellings.append((name, known.name))
                continue
            twin = next(
                (k for k in kept if looks_like(name, k) or difflib.SequenceMatcher(None, norm(name), norm(k)).ratio() >= 0.85),
                None,
            )
            if twin is not None:
                spellings.append((name, twin))
                continue
            try:
                self.vault.create(name, kind, status="suggested", description=description)
            except ValueError:
                continue
            kept.append(name)
            job.created.append(name)
            job.suggested += 1
        for about, text in facts[:MAX_SUGGESTED_FACTS]:
            e = self.vault.find(about)
            if e is not None and not e.read_only:
                before = len(e.suggested_facts) + len(e.facts)
                e = self.vault.add_fact(e.name, f"{text} ([[{note_title}]])", suggested=True)
                if len(e.suggested_facts) + len(e.facts) > before:
                    job.suggested += 1
                    if e.name not in job.created and e.name not in job.updated:
                        job.updated.append(e.name)
        for heard, means in spellings:
            e = self.vault.find(means)
            # Only words that really occur in this transcript, sound like the name they're said to
            # mean ("Elkari" is El-Khari, not Elara), and aren't already a known name.
            if e is None or e.read_only or norm(heard) not in heard_text or self.vault.find(heard) is not None:
                continue
            if not any(looks_like(heard, n) for n in e.names()):
                continue
            if norm(heard) in {norm(h) for h in (*e.heard_as, *e.heard_as_suggested, *e.heard_as_rejected)}:
                continue
            self.vault.update(e.name, heard_as_suggested=[*e.heard_as_suggested, heard])
            job.suggested += 1

        linked = [link_names(t, self.vault) for _, t in story]
        highlights = [
            link_names(h, self.vault) for h in (_text(x) for x in summary.get("highlights") or [] if isinstance(x, str)) if h
        ]
        mentioned = sorted({e.name for e in self.vault.entities() if any(f"[[{e.name}]]" in x for x in [*linked, *highlights])})
        tdir = self.vault.root / "Sessions" / "Transcripts"
        tdir.mkdir(parents=True, exist_ok=True)
        (tdir / f"{sid}.md").write_text(transcript.markdown(record), encoding="utf-8")
        page = [
            f"---\ntype: session\nsession_id: {sid}\ndate: {date}\nduration: {transcript.clock(s.get('duration_s', 0))}\ntags: [bard/session]\n---",
            f"# {note_title}",
            "",
            link_names(_text(summary.get("summary"), 2000), self.vault),
            "",
            "## Highlights",
            *(f"- {h}" for h in highlights),
            "",
            "## What happened",
            *(f"- {when} {e}" for (when, _), e in zip(story, linked, strict=True)),
            "",
            "## Mentioned",
            ", ".join(f"[[{n}]]" for n in mentioned) or "(nobody from the glossary)",
            "",
            "## Transcript",
            f"[[Transcripts/{sid}|Full transcript]] · {s.get('lines', len(lines))} lines",
            "",
        ]
        path = existing or self.vault.root / "Sessions" / f"{note_title}.md"
        write_atomic(path, "\n".join(page))
        job.note = str(path.relative_to(self.vault.root))
        job.done = job.total
        job.message = f"Wrote {path.stem}; {job.suggested} suggestions to review"

    def _session_note(self, sid: str) -> Path | None:
        """A session's existing write-up (re-running one replaces it instead of adding another)."""
        for path in (self.vault.root / "Sessions").glob("*.md"):
            try:
                head = path.read_text(encoding="utf-8")[:400]
            except OSError:
                continue
            if f"session_id: {sid}\n" in head:
                return path
        return None

    def written_up(self) -> dict[str, str]:
        """session id → vault-relative path of its write-up."""
        out = {}
        for path in (self.vault.root / "Sessions").glob("*.md"):
            try:
                m = re.search(r"^session_id: (\S+)$", path.read_text(encoding="utf-8")[:400], re.M)
            except OSError:
                continue
            if m:
                out[m.group(1)] = str(path.relative_to(self.vault.root))
        return out


def link_names(text: str, vault: Vault, entities: list[Entity] | None = None) -> str:
    """Turn the first mention of each glossary name (or alias) into an Obsidian [[link]]:
    [[Elara]] when the text spells it that way, [[Elara|Ellie]] for an alias."""
    for e in sorted(entities if entities is not None else vault.entities(), key=lambda e: -len(e.name)):
        if f"[[{e.name}]]" in text or f"[[{e.name}|" in text:
            continue  # already linked once
        hits = []
        for n in e.names():
            if m := re.search(rf"(?<![\[\w|]){re.escape(n)}(?![\w\]|])", text, re.I):
                hits.append(m)
        if not hits:
            continue
        first = min(hits, key=lambda m: (m.start(), -len(m.group(0))))  # earliest; longest name there
        said = first.group(0)
        link = f"[[{e.name}]]" if said == e.name else f"[[{e.name}|{said}]]"
        text = text[: first.start()] + link + text[first.end() :]
    return text


# ------------------------------------------------------------------ spelling suggestions (no LLM)


FILLERS = frozenset(
    [
        "okay",
        "ok",
        "yeah",
        "yep",
        "yup",
        "nope",
        "nah",
        "hmm",
        "hm",
        "mm",
        "mmm",
        "uh",
        "um",
        "huh",
        "oh",
        "ooh",
        "ah",
        "aha",
        "wow",
        "oops",
        "yay",
        "hey",
        "gotcha",
        "alright",
        "mm-hmm",
        "uh-huh",
        "uh-oh",
        "lol",
        "whoa",
        "bye-bye",
    ]
)


@dataclass
class Word:
    spelling: str  # its most common capitalised form
    count: int = 0
    cap_mid: int = 0  # capitalised away from a sentence start
    low_mid: int = 0
    example: str = ""


def ordinary(word: str) -> bool:
    """An everyday word (or a form of one, or a filler): never a name candidate."""
    return word.casefold() in FILLERS or everyday(word)


def scan_words(texts) -> dict[str, Word]:
    """Words of 3+ letters by lowercase key, with how often they're capitalised mid-sentence:
    names are, ordinary words aren't (a sentence start says nothing either way)."""
    out: dict[str, Word] = {}
    caps: dict[str, collections.Counter] = {}
    for text in texts:
        for m in re.finditer(r"[A-Za-z][A-Za-z'-]{2,}", text):
            word = m.group(0).removesuffix("'s").strip("'-")
            if len(word) < 3:
                continue
            key = word.casefold()
            w = out.setdefault(key, Word(spelling=word, example=text[:160]))
            w.count += 1
            before = text[: m.start()].rstrip()
            if before and before[-1] not in '.!?"':
                if word[0].isupper():
                    w.cap_mid += 1
                else:
                    w.low_mid += 1
            if word[0].isupper():
                caps.setdefault(key, collections.Counter())[word] += 1
    for key, c in caps.items():
        out[key].spelling = c.most_common(1)[0][0]
    return out


def name_like(w: Word) -> bool:
    if ordinary(w.spelling):
        return False
    mid = w.cap_mid + w.low_mid
    return mid == 0 or w.cap_mid / mid >= 0.6


def misspelling_hints(text: str, entities: list[Entity]) -> str:
    """ "- Dallara, Dilara → Elara" lines: words in this text that look like a glossary name."""
    words = [w for w in scan_words(text.splitlines()).values() if name_like(w)]
    rows = []
    for e in entities:
        names = {norm(n) for n in e.names()}
        heard = {norm(h) for h in e.heard_as}
        seen = [
            w
            for w in sorted(words, key=lambda w: -w.count)
            if norm(w.spelling) not in names and (norm(w.spelling) in heard or any(looks_like(w.spelling, n) for n in e.names()))
        ]
        if seen:
            rows.append(f"- {', '.join(w.spelling for w in seen[:8])} → {e.name}")
    return "\n".join(rows)


def skeleton(word: str) -> str:
    """A crude sound-alike key: c/k, z/s, ph/f, y/i merged, vowels and doubled letters dropped."""
    w = norm(word).replace(" ", "").replace("ph", "f")
    w = w.translate(str.maketrans("czy", "ksi"))
    w = re.sub(r"(.)\1+", r"\1", w)
    return w[:1] + re.sub(r"[aeiou]", "", w[1:])


def looks_like(heard: str, name: str) -> bool:
    a, b = norm(heard).replace(" ", ""), norm(name).replace(" ", "")
    if a == b or len(a) < 3 or len(b) < 3:
        return False
    if difflib.SequenceMatcher(None, a, b).ratio() >= 0.75:
        return True
    if abs(len(a) - len(b)) > 3 or len(a) < 4:  # "SLR" isn't Elara
        return False
    ka, kb = skeleton(a), skeleton(b)
    # Same sounds ("Cassia"/"Kessia"), or same sounds after a different first letter ("Dilara"/"Elara").
    return (len(kb) >= 2 and ka == kb) or (len(kb) >= 3 and ka[1:] == kb[1:])


def spelling_suggestions(vault: Vault, sessions: Iterable[Path], min_count: int = 2) -> dict:
    """{"misheard": [{entity, heard, count, example}], "unknown": [{heard, count, example}]} from the
    transcripts in the given session folders."""
    texts = []
    for folder in sessions:
        try:
            texts += [m.get("text", "") for m in json.loads((folder / "transcript.json").read_text()).get("messages", [])]
        except (OSError, json.JSONDecodeError, AttributeError):
            continue
    words = [w for w in scan_words(texts).values() if w.count >= min_count and name_like(w)]
    entities = vault.entities()
    known = {norm(n) for e in entities for n in (*e.names(), *e.heard_as)}
    handled = {norm(n) for e in entities for n in (*e.heard_as_suggested, *e.heard_as_rejected)}
    handled |= {norm(w) for w in vault.ignored()}
    misheard, matched = [], set()
    for e in entities:
        if e.read_only:
            continue
        for w in words:
            key = norm(w.spelling)
            if key in known or key in handled or key in matched:
                continue
            if any(looks_like(w.spelling, name) for name in e.names()):
                misheard.append({"entity": e.name, "heard": w.spelling, "count": w.count, "example": w.example})
                matched.add(key)
    vocabulary = {norm(t) for t, _ in dnd_terms.terms()}  # known already: the cleanup spells these
    unknown = [
        {"heard": w.spelling, "count": w.count, "example": w.example}
        for w in sorted(words, key=lambda w: -w.count)
        if w.spelling[0].isupper()
        and not w.spelling.isupper()  # "NPC", "MLM": acronyms, not names
        and norm(w.spelling) not in known | handled | matched | vocabulary
    ][:50]
    misheard.sort(key=lambda s: -s["count"])
    return {"misheard": misheard[:100], "unknown": unknown}
