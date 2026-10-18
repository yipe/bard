"""Ask: answer a question about the campaign from its transcripts and notes, with the local LLM.

1. Plan (LLM): words likely to sit near the answer ("diamond" → gem, jewel) and the glossary
   entries the question is about.
2. Search (code, BM25) over the open campaign's transcripts, cut into short overlapping
   windows, and every note in its vault. An entry the question is about is searched under every
   spelling Troubadour knows (aliases, heard-as: the transcript says "Dilara" where the notes say
   "Elara"), and its note always goes in.
3. Answer (LLM) from those excerpts only, citing them by id. Model output is untrusted: only
   offered excerpts can be cited, and lengths are capped.

One question at a time on its own thread, beside the write-up queue: it reads, never writes.
Progress and the answer arrive as `campaign.answer` events.
"""

import collections
import json
import logging
import math
import queue
import re
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from .campaign import Entity, Vault, norm, split_frontmatter
from .memory import STR, MemoryLLM, _obj, _text, chunks, glossary_brief
from .transcript import clock

log = logging.getLogger("troubadour")

WINDOW, STRIDE = 8, 4  # transcript lines per excerpt window; windows overlap by half
NOTE_CHARS = 1500  # notes are searched in paragraph-aligned pieces of about this size
EXCERPT_CHARS = 14000  # what the answer call reads, like a write-up part plus the glossary
MAX_EXCERPTS, MAX_ENTRIES, MAX_CITED = 24, 5, 8
MAX_TERMS, TERM_MAX = 12, 40
QUESTION_WEIGHT, TERM_WEIGHT, NAME_WEIGHT = 1.0, 0.6, 1.5
ANSWER_MAX = 1500
KEPT_ANSWERS = 20
SKIP_NOTES = {"glossary.md", "readme.md", "ignored words.md"}  # generated, or about Troubadour itself
NOT_FOUND = "I couldn't find anything about that in the transcripts or notes."

TYPE_LABELS = {
    "pc": "player character",
    "npc": "NPC",
    "place": "place",
    "faction": "faction",
    "item": "item",
    "creature": "creature",
    "spell": "spell",
    "term": "term",
}

# Function words, question words and table filler: they'd match everywhere.
STOP = frozenset(
    [
        "a",
        "about",
        "after",
        "again",
        "all",
        "also",
        "am",
        "an",
        "and",
        "any",
        "are",
        "as",
        "at",
        "be",
        "been",
        "before",
        "being",
        "but",
        "by",
        "can",
        "could",
        "did",
        "do",
        "does",
        "doing",
        "done",
        "down",
        "for",
        "from",
        "get",
        "go",
        "going",
        "got",
        "had",
        "has",
        "have",
        "he",
        "her",
        "here",
        "him",
        "his",
        "how",
        "i",
        "if",
        "in",
        "into",
        "is",
        "it",
        "its",
        "just",
        "know",
        "like",
        "me",
        "my",
        "no",
        "not",
        "now",
        "of",
        "oh",
        "ok",
        "okay",
        "on",
        "one",
        "or",
        "our",
        "out",
        "over",
        "really",
        "right",
        "said",
        "say",
        "so",
        "some",
        "tell",
        "than",
        "that",
        "the",
        "their",
        "them",
        "then",
        "there",
        "these",
        "they",
        "thing",
        "think",
        "this",
        "those",
        "to",
        "told",
        "uh",
        "um",
        "up",
        "us",
        "very",
        "was",
        "we",
        "well",
        "were",
        "what",
        "when",
        "where",
        "which",
        "who",
        "whom",
        "why",
        "will",
        "with",
        "would",
        "yeah",
        "yes",
        "you",
        "your",
    ]
)

PLAN_SCHEMA = _obj({"terms": {"type": "array", "items": STR}, "entries": {"type": "array", "items": STR}})

PLAN_SYSTEM = """You help search the transcripts and notes of a Dungeons & Dragons campaign for the answer to a
question. List:
- terms: up to 10 single words or short phrases likely to appear where the answer is: the question's key
  words, their synonyms and related words (diamond → gem, jewel, stone; stole → took, thief).
- entries: the glossary names the question is about, exactly as the glossary spells them; none if it names
  nobody in the glossary.
Answer with JSON only."""

ANSWER_SYSTEM = """You answer questions about a Dungeons & Dragons campaign for the people who play it, using
only the excerpts you are given: notes from the campaign wiki and parts of session transcripts.

How to read them:
- The players are real people; each plays a player character (PC). The GM plays everyone else: the
  non-player characters (NPCs). "We" in a question means the players and their characters.
- Transcripts are automatic speech-to-text of the table: names can be misspelled, lines can be cut short
  or given to the wrong speaker, and there is table talk and jokes that aren't part of the story.
  "Name as Character:" is a player speaking as their character.
- Notes are the group's wiki. Unconfirmed facts are suggestions nobody has checked yet.
- Keep apart what happened or was confirmed, what someone suspected or guessed, and what's still unknown.
  Say who suspected something and in which session when the excerpts show it.

Answer in a few plain sentences, as someone who was at the table; call the excerpts "the notes" or "the
transcripts". Quote with single quotes, never double quotes. If they don't answer the question, say so
and set found to false.
List the ids of the excerpts you used in sources. Never invent anything that isn't in the excerpts.
Answer with JSON only."""


@dataclass(eq=False)  # identity: two windows can read the same
class Passage:
    kind: str  # "transcript" | "note" | "entry"
    text: str
    label: str
    words: str | None = None  # what's searched, if not the text: a transcript's speech without speakers
    session: str | None = None
    t: float | None = None
    path: str | None = None  # vault-relative
    entry: str | None = None
    lines: list[tuple[float, str, str]] | None = None  # transcript: (t, line, speech), shared by its windows
    span: tuple[int, int] = (0, 0)  # transcript: lines[span[0]:span[1]]
    order: tuple = ()
    counts: collections.Counter = field(default_factory=collections.Counter)

    def source(self, sid: str) -> dict:
        return {
            "id": sid,
            "kind": self.kind,
            "label": self.label,
            "session": self.session,
            "t": self.t,
            "path": self.path,
            "entry": self.entry,
            "excerpt": self.text[:400],
        }


@dataclass
class Answer:
    id: str
    question: str
    campaign: str | None = None
    state: str = "queued"  # queued | searching | thinking | done | error
    answer: str = ""
    found: bool = False
    sources: list[dict] = field(default_factory=list)
    message: str = ""
    created: float = field(default_factory=time.time)

    def to_json(self) -> dict:
        return dict(self.__dict__)


# ------------------------------------------------------------------ search


_WORD = re.compile(r"[a-z0-9]+(?:'[a-z]+)?")


def tokens(text: str) -> list[str]:
    """Search words: lowercase, possessives and plurals folded ("Elara's" → elara, "gems" → gem)."""
    out = []
    for w in _WORD.findall(text.casefold().replace("\u2019", "'")):
        w = w.removesuffix("'s").replace("'", "")
        if w in STOP:
            continue
        if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
            w = w[:-1]
        if w not in STOP:
            out.append(w)
    return out


def rank(passages: list[Passage], weights: dict[str, float], k1: float = 1.2, b: float = 0.75) -> list[tuple[float, Passage]]:
    """BM25, best first; only passages that share a word with the query."""
    if not passages or not weights:
        return []
    avg = sum(sum(p.counts.values()) for p in passages) / len(passages) or 1
    df = {w: sum(1 for p in passages if w in p.counts) for w in weights}
    out = []
    for p in passages:
        length = sum(p.counts.values())
        score = 0.0
        for w, weight in weights.items():
            tf = p.counts.get(w, 0)
            if tf:
                idf = math.log(1 + (len(passages) - df[w] + 0.5) / (df[w] + 0.5))
                score += weight * idf * tf * (k1 + 1) / (tf + k1 * (1 - b + b * length / avg))
        if score > 0:
            out.append((score, p))
    return sorted(out, key=lambda sp: -sp[0])


def mentioned(text: str, entities: list[Entity]) -> list[Entity]:
    """Entries a text names under any spelling (name, alias, heard-as)."""
    key = f" {norm(text)} "
    return [e for e in entities if any(n and f" {n} " in key for n in map(norm, (*e.names(), *e.heard_as)))]


def query_weights(question: str, terms: list[str], entries: list[Entity]) -> dict[str, float]:
    weights: dict[str, float] = {}

    def add(words: list[str], weight: float) -> None:
        for w in words:
            weights[w] = max(weights.get(w, 0.0), weight)

    for term in terms:
        add(tokens(term), TERM_WEIGHT)
    add(tokens(question), QUESTION_WEIGHT)
    for e in entries:
        for spelling in (*e.names(), *e.heard_as):
            add(tokens(spelling), NAME_WEIGHT)
    return weights


# ------------------------------------------------------------------ corpus


def session_passages(folder: Path, title: str | None = None) -> tuple[list[Passage], list[dict]]:
    """A saved session as overlapping windows of lines, and its players (for the roster)."""
    record = json.loads((folder / "transcript.json").read_text(encoding="utf-8"))
    started = record.get("session", {}).get("started_at") or 0
    name = title or (time.strftime("%Y-%m-%d", time.localtime(started)) if started else folder.name)
    lines = []
    for m in record.get("messages", []):
        who = m.get("speaker") or "Unknown"
        if m.get("character"):
            who += f" as {m['character']}"
        text = str(m.get("text", ""))
        lines.append((float(m.get("t_start", 0)), f"[{clock(m.get('t_start', 0))}] {who}: {text}", text))
    out = []
    for i in range(0, max(len(lines) - WINDOW + STRIDE, 1), STRIDE):
        window = lines[i : i + WINDOW]
        if not window:
            break
        out.append(
            Passage(
                "transcript",
                "\n".join(line for _, line, _ in window),
                f"{name} · {clock(window[0][0])}",
                words="\n".join(speech for _, _, speech in window),  # "Theo as Elara" isn't about Elara
                session=folder.name,
                t=window[0][0],
                lines=lines,
                span=(i, i + len(window)),
                order=(started, window[0][0]),
            )
        )
    return out, record.get("players", [])


def entry_passage(e: Entity, root: Path) -> Passage:
    """A glossary entry as one passage: what it is, what's known, what's only suggested."""
    parts = [f"{e.name} ({TYPE_LABELS.get(e.type, e.type)})"]
    if e.aliases:
        parts.append(f"Also called {', '.join(e.aliases)}.")
    if e.description:
        parts.append(e.description)
    if e.facts:
        parts.append("Facts:\n" + "\n".join(f"- {f}" for f in e.facts))
    if e.suggested_facts:
        parts.append("Unconfirmed facts:\n" + "\n".join(f"- {f}" for f in e.suggested_facts))
    parts += [f"{heading}:\n{body.strip()}" for heading, body in e.extra_sections if body.strip()]
    path = str(e.path.relative_to(root)) if e.path else None
    return Passage("entry", "\n".join(parts), e.name, path=path, entry=e.name, order=(0, e.name))


def note_passages(vault: Vault) -> list[Passage]:
    """Every note in the vault: entries whole, other notes (session pages, pasted notes, the
    user's own) in pieces. Not Troubadour's generated index, the trash, or transcript copies."""
    root = vault.root
    entities = vault.entities()
    out = [entry_passage(e, root) for e in entities]
    entry_paths = {e.path for e in entities}
    for path in sorted(root.rglob("*.md")) if root.is_dir() else []:
        rel = path.relative_to(root)
        if (
            path in entry_paths
            or any(part.startswith(".") for part in rel.parts)
            or rel.name.casefold() in SKIP_NOTES
            or rel.parts[:2] == ("Sessions", "Transcripts")
        ):
            continue
        try:
            if path.stat().st_size > 2_000_000:
                continue
            _, body = split_frontmatter(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            continue
        # Each bullet its own paragraph, so a long list (a session page's events) splits between items.
        for i, piece in enumerate(chunks(re.sub(r"\n(?=[-*] )", "\n\n", body), NOTE_CHARS)):
            out.append(Passage("note", piece, str(rel.with_suffix("")), path=str(rel), order=(1, str(rel), i)))
    return out


def roster(players_by_session: list[list[dict]], known: list[tuple[str, str, str]]) -> str:
    """Who's who at the table: the latest seat of everyone in the sessions, plus named NPC voices."""
    seats: dict[str, dict] = {}
    for players in players_by_session:  # oldest first: later seats win
        for p in players:
            if isinstance(p, dict) and p.get("name"):
                seats[p.get("id") or p["name"]] = p
    rows = []
    for p in seats.values():
        if p.get("is_gm"):
            rows.append(f"- {p['name']}: the GM")
        elif p.get("character"):
            rows.append(f"- {p['name']}: a player; plays {p['character']} (a PC)")
        else:
            rows.append(f"- {p['name']}: a player")
    rows += [f"- {name}: an NPC ({description})" for name, kind, description in known if kind == "npc"]
    return "\n".join(rows) or "(unknown)"


def merge_windows(chosen: list[Passage]) -> list[Passage]:
    """Overlapping or touching windows of one session become one excerpt."""
    out, by_session = [], collections.defaultdict(list)
    for p in chosen:
        if p.kind == "transcript":
            by_session[p.session].append(p)
        else:
            out.append(p)
    for windows in by_session.values():
        windows.sort(key=lambda p: p.span)
        group = [windows[0]]
        for p in windows[1:]:
            if p.span[0] <= group[-1].span[1]:
                group.append(p)
            else:
                out.append(_joined(group))
                group = [p]
        out.append(_joined(group))
    return out


def _joined(group: list[Passage]) -> Passage:
    first = group[0]
    start, end = first.span[0], max(p.span[1] for p in group)
    text = "\n".join(line for _, line, _ in first.lines[start:end])
    return Passage("transcript", text, first.label, session=first.session, t=first.t, span=(start, end), order=first.order)


def select(ranked: list[tuple[float, Passage]], entries: list[Passage], budget: int = EXCERPT_CHARS) -> list[Passage]:
    """Entries the question is about first, then the best matches, within the budget; notes
    first, then transcripts in the order they were played."""
    chosen, used = [], 0
    for p in [*entries[:MAX_ENTRIES], *(p for _, p in ranked)]:
        if len(chosen) >= MAX_EXCERPTS:
            break
        if p in chosen or used + len(p.text) > budget:
            continue
        chosen.append(p)
        used += len(p.text)
    merged = merge_windows(chosen)
    return sorted(merged, key=lambda p: (p.kind == "transcript", p.order))


# ------------------------------------------------------------------ the job


class Asker:
    def __init__(
        self,
        vault: Vault,
        bus,
        sessions: Callable[[], list[Path]],
        written_up: Callable[[], dict[str, str]] = dict,
        campaign: Callable[[], str | None] = lambda: None,
        seed: Callable[[], list[tuple[str, str, str]]] = list,
        llm: MemoryLLM | None = None,
    ):
        self.vault = vault
        self.bus = bus
        self.sessions = sessions  # folders of the open campaign's sessions
        self.written_up = written_up  # session id → its write-up page (vault-relative)
        self.campaign = campaign
        self.seed = seed  # (name, type, description) Troubadour knows from Setup and the voices panel
        self.llm = llm or MemoryLLM()
        self.answers: dict[str, Answer] = {}
        self._lock = threading.Lock()
        self._q: queue.Queue = queue.Queue()
        threading.Thread(target=self._run, name="ask", daemon=True).start()

    def ask(self, question: str) -> Answer:
        question = re.sub(r"\s+", " ", question).strip()
        if not question:
            raise ValueError("Ask a question first.")
        a = Answer(id=uuid.uuid4().hex[:8], question=question, campaign=self.campaign())
        with self._lock:
            self.answers[a.id] = a
            for old in sorted(self.answers.values(), key=lambda x: x.created)[:-KEPT_ANSWERS]:
                if old.state in ("done", "error"):
                    del self.answers[old.id]
        self._emit(a)
        self._q.put(a)
        return a

    def list(self) -> list[dict]:
        """The open campaign's answers, oldest first."""
        current = self.campaign()
        with self._lock:
            return [a.to_json() for a in sorted(self.answers.values(), key=lambda x: x.created) if a.campaign == current]

    def _emit(self, a: Answer) -> None:
        self.bus.emit("campaign.answer", a.to_json())

    def _run(self) -> None:
        while True:
            a = self._q.get()
            try:
                self._answer(a)
                a.state = "done"
            except httpx.TransportError:
                a.state, a.message = "error", f"Can't reach LM Studio at {self.llm.url}."
            except Exception as e:
                log.exception("ask failed")
                a.state, a.message = "error", str(e) if isinstance(e, RuntimeError | ValueError) else f"{type(e).__name__}: {e}"
            self._emit(a)

    def _answer(self, a: Answer) -> None:
        a.state = "searching"
        self._emit(a)
        if self.campaign() != a.campaign:
            raise ValueError("The campaign changed; ask again.")
        entities = self.vault.entities()
        plan = self.llm.json(PLAN_SYSTEM, f"Glossary:\n{glossary_brief(self.vault)}\n\nQuestion: {a.question}", PLAN_SCHEMA, 600)
        terms = [t for t in (plan.get("terms") or []) if isinstance(t, str) and 0 < len(t.strip()) <= TERM_MAX][:MAX_TERMS]
        named = [e for name in (plan.get("entries") or []) if isinstance(name, str) and (e := self.vault.find(name))]
        about = list({e.name: e for e in [*mentioned(a.question, entities), *named]}.values())

        passages = note_passages(self.vault)
        titles = {sid: Path(rel).stem for sid, rel in self.written_up().items()}
        players = []
        for folder in sorted(self.sessions()):
            try:
                found, seats = session_passages(folder, titles.get(folder.name))
            except (OSError, ValueError, KeyError, TypeError):
                continue  # a session still being written, or unreadable: the rest still answers
            passages += found
            players.append(seats)
        for p in passages:
            p.counts = collections.Counter(tokens(p.words if p.words is not None else p.text))
        names = {e.name for e in about}
        entries = [p for p in passages if p.kind == "entry" and p.entry in names]
        excerpts = select(rank(passages, query_weights(a.question, terms, about)), entries)
        if not excerpts:
            a.answer, a.found = NOT_FOUND, False
            return

        a.state = "thinking"
        self._emit(a)
        ids = [f"S{i}" for i in range(1, len(excerpts) + 1)]
        body = "\n\n".join(f"[{sid}] {p.label}\n{p.text}" for sid, p in zip(ids, excerpts, strict=True))
        user = f"The table:\n{roster(players, self.seed())}\n\nExcerpts:\n\n{body}\n\nQuestion: {a.question}"
        schema = _obj(
            {"answer": STR, "found": {"type": "boolean"}, "sources": {"type": "array", "items": {"type": "string", "enum": ids}}}
        )
        out = self.llm.json(ANSWER_SYSTEM, user, schema, 1500)
        a.answer = _text(out.get("answer"), ANSWER_MAX)
        if not a.answer:
            raise ValueError("The model gave no answer; try asking again.")
        a.found = out.get("found") is True
        by_id = dict(zip(ids, excerpts, strict=True))
        cited = list(dict.fromkeys(s for s in (out.get("sources") or []) if s in by_id))[:MAX_CITED]
        a.sources = [by_id[s].source(s) for s in cited]
