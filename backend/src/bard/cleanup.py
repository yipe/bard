"""Spelling cleanup behind the transcriber: names and D&D terms, spelled the way the campaign
spells them.

1. Code, instantly, on every line (`Speller.find`): words and 2-3 word phrases that look or
   sound like a known spelling (glossary names, aliases, heard-as spellings, a built-in D&D
   vocabulary). A sure match is fixed right away: an exact heard-as spelling, a phrase that is
   the term with different spacing ("thunder wave"), or a near-identical spelling with only
   one candidate ("Augie" → "Auggie").
2. The local LLM, a few seconds behind (`ask`): ambiguous words ("Dallara": Elara or Delara?)
   with the lines around them. It may only answer one of the candidate names or "keep".

Every fix keeps what Whisper heard (`fixes`: [{start, end, heard}] in UTF-16 offsets of the
new text), so the UI can show it and a person can see what changed. Annotations move with the
text. Everyday English words are never candidates, so table talk is left alone.
"""

import difflib
import os
import re
from collections import Counter
from dataclasses import dataclass, field

from . import dnd_terms
from .campaign import Entity, english, everyday, norm
from .memory import looks_like, skeleton
from .transcript import utf16_offset, utf16_to_py

SPELLED = re.compile(r"(?:[A-Za-z]-){2,}[A-Za-z]")  # "B-U-T-T-E": letters spelled out, not a word


def key(phrase: str) -> str:
    """Comparison key for phrases: case and punctuation fold, but "the" stays a word."""
    return re.sub(r"[^a-z0-9]+", " ", phrase.casefold()).strip()


VOCAB_RATIO = 0.8  # official D&D names must be this close (the campaign's own: looks_like)
PHRASE_RATIO = 0.78  # "hideous laughing" ~ "Hideous Laughter" (0.81); each word is checked too
SURE_RATIO = 0.88  # "Augie"/"Auggie" (0.91) is fixed by code; "Magento"/"Magenta" (0.86) asks the LLM
_WORD = re.compile(r"[A-Za-z][A-Za-z'-]*")


@dataclass
class Candidate:
    start: int  # Python indices into the line
    end: int
    heard: str
    options: list[str]  # canonical names it may be
    sure: bool = False


@dataclass
class Speller:
    """Known spellings: the campaign's confirmed entries first, then D&D vocabulary."""

    entities: list[Entity]
    dictionary: frozenset[str] = field(default_factory=english)
    dnd: list[tuple[str, str]] = field(default_factory=dnd_terms.terms)
    keep: list[str] = field(default_factory=list)  # never "fix" these: ignored words, suggested entries

    def __post_init__(self) -> None:
        self.canonical: dict[str, str] = {}  # spelling (norm) → the name it stands for
        self.glossary: set[str] = set()  # canonical names from the campaign (vs D&D terms)
        self.about: dict[str, str] = {}  # name → "npc: one-line description" for the LLM
        for e in self.entities:
            if e.status != "confirmed" or e.read_only:
                continue
            self.glossary.add(e.name)
            self.about[e.name] = f"{e.type}: {(e.description.splitlines() or [''])[0][:120]}"
            for n in e.names():  # an alias is a real name: it keeps its own spelling
                self.canonical.setdefault(key(n), n)
                self.about.setdefault(n, f"also called {n}: {self.about[e.name]}" if n != e.name else self.about[e.name])
        self.vocabulary: set[str] = set()  # D&D terms (not names: never just re-cased)
        self.kind: dict[str, str] = {}
        for name, kind in self.dnd:
            if key(name) not in self.canonical:
                self.canonical[key(name)] = name
                self.vocabulary.add(name)
                self.kind[name] = kind
                self.about.setdefault(name, kind)
        self.kept = {key(w) for w in self.keep}
        self.joined = {k.replace(" ", ""): v for k, v in self.canonical.items()}
        self.names = sorted(set(self.canonical.values()))
        # Thousands of official names: look them up by first letter and by sound, not one by one.
        self.by_first: dict[str, set[str]] = {}
        self.by_sound: dict[str, set[str]] = {}
        self.by_last: dict[str, set[str]] = {}  # multi-word official names by their last word
        for n in self.vocabulary:
            if " " in n:
                if self.kind.get(n) in ("spell", "creature", "item"):  # not rules terms or feats
                    self.by_last.setdefault(key(n).split()[-1], set()).add(n)
            elif k := key(n):
                self.by_first.setdefault(k[0], set()).add(n)
                self.by_sound.setdefault(skeleton(n)[1:], set()).add(n)
        self.own = sorted(n for n in self.names if n not in self.vocabulary and " " not in n)
        self.heard = {key(h): e.name for e in self.entities if e.status == "confirmed" for h in e.heard_as}

    def _ordinary(self, word: str) -> bool:
        """An everyday word or a form of one ("confusing", "poisoned", "healthier")."""
        return everyday(word, self.dictionary)

    def _inflected(self, phrase: str, name: str) -> bool:
        """The term used as an English word: "teleports", "poisoned", "hexed"."""
        a, b = key(phrase).replace(" ", ""), key(name).replace(" ", "")
        return a != b and a.startswith(b) and a[len(b) :] in ("s", "es", "ed", "d", "ing")

    def find(self, text: str) -> list[Candidate]:
        tokens = [(m.start(), m.end()) for m in _WORD.finditer(text)]
        out: list[Candidate] = []
        i = 0
        while i < len(tokens):
            for size in (3, 2, 1):
                if i + size > len(tokens):
                    continue
                start, end = tokens[i][0], tokens[i + size - 1][1]
                phrase = text[start:end]
                if SPELLED.fullmatch(phrase):
                    continue
                if size > 1 and not re.fullmatch(r"[A-Za-z'-]+(?: [A-Za-z'-]+)*", phrase):
                    continue  # only plain spaces inside a phrase ("thunder wave", not "wave. Thunder")
                cand = self._check(phrase, size)
                if cand is not None:
                    out.append(Candidate(start, end, phrase, *cand))
                    i += size
                    break
            else:
                i += 1
        return out

    def _check(self, phrase: str, size: int) -> tuple[list[str], bool] | None:
        k = key(phrase)
        if k in self.kept:
            return None
        if k in self.heard:
            return [self.heard[k]], True  # an accepted heard-as spelling
        name = self.canonical.get(k) or self.joined.get(k.replace(" ", ""))
        if name is not None:
            if phrase == name:
                return None
            if key(phrase) == key(name) and (name in self.vocabulary or all(self._ordinary(w) for w in phrase.split())):
                return None  # only the case differs: "saving throw", "scrying", "bard" stay as said
            return [name], True  # same letters, other spacing or case ("thunder wave", "auggie")
        if size > 1:
            options = self._phrase_options(phrase)
            return (options, False) if options else None  # a fuzzy phrase is always confirmed
        if self._ordinary(phrase) or len(phrase) < 4:
            return None
        options = self._word_options(phrase)
        if not options:
            return None
        ratio = difflib.SequenceMatcher(None, key(phrase), key(options[0])).ratio()
        return options, len(options) == 1 and ratio >= SURE_RATIO

    def _word_options(self, word: str) -> list[str]:
        k = key(word).replace(" ", "")
        out = [n for n in self.own if looks_like(word, n) and not self._inflected(word, n)]
        for n in sorted(self.by_first.get(k[:1], set())):
            if len(n) < 6 or self._inflected(word, n):
                continue  # short official names collide with everything
            if difflib.SequenceMatcher(None, k, key(n).replace(" ", "")).ratio() >= VOCAB_RATIO:
                out.append(n)  # official names: spelled nearly the same (sound-alikes are for the campaign's own)
        return out

    def _phrase_options(self, phrase: str) -> list[str]:
        """Official multi-word names with as many words, ending alike, spelled nearly the same."""
        k = key(phrase)
        said = phrase.casefold().split()
        out = []
        for n in sorted(self.by_last.get(k.split()[-1], set())):
            name = n.casefold().split()
            if len(name) != len(said) or key(n) == k or difflib.SequenceMatcher(None, k, key(n)).ratio() < PHRASE_RATIO:
                continue
            # Word by word too: "arkanist magic aura" fits, "far away" isn't "Fare Away".
            if all(_close(a, b) for a, b in zip(said, name, strict=True)):
                out.append(n)
        return out


def _close(a: str, b: str) -> bool:
    """One word of a phrase against one word of a name: alike, or starting the same ("maxim…")."""
    return difflib.SequenceMatcher(None, a, b).ratio() >= 0.7 or len(os.path.commonprefix([a, b])) >= 4


# ------------------------------------------------------------------ edits


def apply(text: str, fixes: list[dict], edits: list[tuple[int, int, str]]) -> tuple[str, list[dict]]:
    """Replace (start, end) Python spans with names. Earlier fixes (UTF-16 offsets, as stored)
    move with the text; each edit adds a fix {start, end, heard}."""
    done = [dict(f, start=utf16_to_py(text, f["start"]), end=utf16_to_py(text, f["end"])) for f in fixes]
    new = text
    for e0, e1, repl in sorted(edits, reverse=True):
        heard = new[e0:e1]
        delta = len(repl) - (e1 - e0)
        new = new[:e0] + repl + new[e1:]
        for f in done:
            if f["start"] >= e1:
                f["start"] += delta
                f["end"] += delta
            elif f["end"] > e0:  # overlaps the edit: grow or shrink with it
                f["start"] = min(f["start"], e0)
                f["end"] = max(f["end"], e1) + delta
        done.append({"start": e0, "end": e0 + len(repl), "heard": heard})
    out = [dict(f, start=utf16_offset(new, f["start"]), end=utf16_offset(new, f["end"])) for f in done]
    return new, sorted(out, key=lambda f: f["start"])


# ------------------------------------------------------------------ LLM


SYSTEM = """You fix how names are spelled in a Dungeons & Dragons table's speech-to-text transcript.
Each question points at one word in a line and lists the names it might be. Using the conversation,
answer which name the speaker meant, or "keep" if it is none of them (an ordinary word, or someone or
something else). Two different characters can have similar names: decide from who is talking and what
is happening. Answer with JSON only."""

SCHEMA = {
    "type": "object",
    "properties": {
        "answers": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"q": {"type": "integer"}, "choice": {"type": "string"}},
                "required": ["q", "choice"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["answers"],
    "additionalProperties": False,
}


def ask(
    llm_json, speller: Speller, context: list[tuple[str, str, str]], targets: list[tuple[str, str, str, list[Candidate]]]
) -> dict[tuple[str, int], str]:
    """targets: (line id, speaker, text, ambiguous candidates). Returns {(line id, start): name}
    for each candidate the model resolved to a name; "keep" and invalid answers are dropped."""
    questions, index = [], {}
    for line_id, _, _text, cands in targets:
        for c in cands:
            index[len(questions)] = (line_id, c)
            questions.append({"q": len(questions), "line": line_id, "word": c.heard, "options": c.options})
    if not questions:
        return {}
    names = sorted({n for q in questions for n in q["options"]})
    glossary = "\n".join(f"- {n} ({speller.about.get(n, '')})" for n in names)
    convo = [f"{speaker}: {text}" for _, speaker, text in context]
    convo += [f"[{line_id}] {speaker}: {text}" for line_id, speaker, text, _ in targets]
    user = f"Names:\n{glossary}\n\nConversation (latest lines have [ids]):\n" + "\n".join(convo)
    user += "\n\nQuestions:\n" + "\n".join(
        f'{q["q"]}. In [{q["line"]}], "{q["word"]}": one of {q["options"]} or "keep"?' for q in questions
    )
    out = llm_json(SYSTEM, user, SCHEMA)
    picked = {}
    for a in out.get("answers") or []:
        if not isinstance(a, dict) or a.get("q") not in index:
            continue
        line_id, c = index[a["q"]]
        choice = a.get("choice")
        if isinstance(choice, str) and choice in c.options:
            picked[(line_id, c.start)] = choice
    return picked


class Learner:
    """Repeated, consistent LLM decisions become heard-as *suggestions* for the glossary (the
    person approves; then the instant code path handles that spelling)."""

    def __init__(self, every: int = 2):
        self.every = every
        self.seen: Counter = Counter()

    def note(self, heard: str, name: str) -> bool:
        """True the moment heard → name has been decided `every` times."""
        self.seen[(norm(heard), name)] += 1
        return self.seen[(norm(heard), name)] == self.every


# ------------------------------------------------------------------ whole records (after a session)


def clean_record(
    record: dict, speller: Speller, llm_json, batch: int = 8, context: int = 4, progress=lambda d, t: None
) -> tuple[dict, int, list[tuple[str, str]]]:
    """(new record, words fixed, [(heard, name)] decided by the LLM) for a saved transcript."""
    # Records saved before mechanics were removed still carry "annotations": dropped here.
    msgs = [
        {k: v for k, v in m.items() if k != "annotations"} | {"fixes": list(m.get("fixes", []))}
        for m in sorted(record["messages"], key=lambda m: m["t_start"])
    ]
    pending: list[int] = []
    fixed = 0
    decided: list[tuple[str, str]] = []

    def edit(i: int, edits: list[tuple[int, int, str]]) -> None:
        nonlocal fixed
        m = msgs[i]
        m["text"], m["fixes"] = apply(m["text"], m["fixes"], edits)
        fixed += len(edits)

    for i, m in enumerate(msgs):
        cands = speller.find(m["text"])
        sure = [(c.start, c.end, c.options[0]) for c in cands if c.sure]
        if sure:
            edit(i, sure)
        if any(not c.sure for c in speller.find(m["text"])):
            pending.append(i)
    for at in range(0, len(pending), batch):
        group = pending[at : at + batch]
        targets = [
            (msgs[i]["id"], msgs[i]["speaker"], msgs[i]["text"], [c for c in speller.find(msgs[i]["text"]) if not c.sure])
            for i in group
        ]
        ctx = [(msgs[j]["id"], msgs[j]["speaker"], msgs[j]["text"]) for j in range(max(0, group[0] - context), group[0])]
        try:
            picked = ask(llm_json, speller, ctx, targets)
        except Exception:  # the LLM is down: the sure fixes still stand
            picked = {}
        for i, (_, _, text, cands) in zip(group, targets, strict=True):
            edits = [(c.start, c.end, picked[(msgs[i]["id"], c.start)]) for c in cands if (msgs[i]["id"], c.start) in picked]
            decided += [(text[s:e], name) for s, e, name in edits]
            if edits:
                edit(i, edits)
        progress(min(at + batch, len(pending)), len(pending))
    session = {k: v for k, v in record["session"].items() if k != "mechanics"}
    return dict(record, session=session, messages=msgs), fixed, decided
