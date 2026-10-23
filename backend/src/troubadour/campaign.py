"""Campaign memory, stored as an Obsidian vault: plain markdown that people, Obsidian, and
agents can all read and edit. Troubadour re-reads it from disk, so edits made in Obsidian count.

Layout (config.VAULT_DIR, default data/campaign):

    People/ Places/ Factions/ Items/ Lore/   one note per entity; the file name is its name
    Sessions/                                one write-up per played session
    Sessions/Transcripts/                    the full transcript of each written-up session
    Notes/                                   pasted session notes, kept verbatim
    Facts.md                                 campaign-wide facts
    Glossary.md                              generated index of every entity (regenerated)

An entity note:

    ---
    type: npc
    aliases: [Maggie]              # other names; Obsidian uses them in link search
    heard_as: [Magento]            # how speech-to-text misspells it; Troubadour corrects these
    heard_as_suggested: [Majenta]  # proposed by Troubadour, waiting for review
    status: confirmed              # "suggested" = proposed by a session write-up
    tags: [troubadour/npc]
    ---
    # Magenta
    Short description.

    ## Facts
    - Runs the Rusty Flagon in [[Dallon]]. ([[2026-10-08 Session]])

    ## Suggested facts
    - Owes Kessia a favour. ([[2026-10-15 Session]])

Troubadour owns the frontmatter keys above, the description and those two sections. Other
frontmatter keys, a person's own tags, and other sections are kept as written. A note whose
frontmatter doesn't parse is shown read-only and never rewritten.
"""

import difflib
import re
import shutil
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import yaml

TYPES = {
    "pc": "People",
    "npc": "People",
    "place": "Places",
    "faction": "Factions",
    "item": "Items",
    "creature": "Lore",
    "spell": "Lore",
    "term": "Lore",
}
FOLDERS = sorted(set(TYPES.values()))
# Tags the app adds itself; "bard/" is the pre-rename prefix, so old notes don't keep it as a user tag.
OWN_TAG_PREFIXES = ("troubadour/", "bard/")
OWNED_KEYS = ("type", "aliases", "heard_as", "heard_as_suggested", "heard_as_rejected", "status", "tags")
FACTS, SUGGESTED = "Facts", "Suggested facts"
STATUSES = ("confirmed", "suggested")
_BAD_NAME = re.compile(r'[\\/:*?"<>|#^\[\]\n\r\t]')
_DICT: frozenset[str] | None = None


# Everyday words the 1934 dictionary in /usr/share/dict doesn't have.
MODERN = frozenset(
    [
        "okay",
        "ok",
        "gonna",
        "wanna",
        "gotta",
        "kinda",
        "sorta",
        "yeah",
        "yep",
        "nope",
        "cool",
        "awesome",
        "fuck",
        "fucking",
        "fucked",
        "shit",
        "shitty",
        "damn",
        "dude",
        "guys",
        "email",
        "online",
        "internet",
        "google",
        "phone",
        "laptop",
        "video",
        "app",
        "apps",
        "text",
        "texting",
        "cringe",
        "vibe",
        "vibes",
        "weird",
        "lol",
        "omg",
        "anyway",
        "anyways",
        "alright",
        "tonight",
        "tomorrow",
        "selfie",
        "snack",
        "snacks",
        "pizza",
    ]
)


def everyday(word: str, dictionary: frozenset[str] | None = None) -> bool:
    """An everyday English word or a form of one: "purified", "healthier", "stronger", "poisoned"."""
    d = english() if dictionary is None else dictionary
    w = word.casefold().strip("'-")
    if not w or "'" in w or w in MODERN:
        return True
    forms = {w}
    for suffix in ("s", "es", "ed", "d", "ing", "ly", "er", "est"):
        if w.endswith(suffix) and len(w) > len(suffix) + 2:
            base = w[: -len(suffix)]
            forms |= {base, base + "e"}
            if len(base) > 2 and base[-1] == base[-2]:
                forms.add(base[:-1])  # bigger → big, running → run
    for suffix in ("ies", "ied", "ier", "iest", "ily"):
        if w.endswith(suffix):
            forms.add(w[: -len(suffix)] + "y")  # purified → purify, healthier → healthy
    return any(f in d for f in forms)


def english() -> frozenset[str]:
    """Lowercase English words (macOS ships /usr/share/dict/words); empty if unavailable."""
    global _DICT
    if _DICT is None:
        try:
            _DICT = frozenset(Path("/usr/share/dict/words").read_text().lower().split())
        except OSError:
            _DICT = frozenset()
    return _DICT


def norm(text: str) -> str:
    """Comparison key: case-, punctuation- and leading-"the"-insensitive."""
    key = re.sub(r"[^a-z0-9]+", " ", str(text).casefold()).strip()
    return key[4:] if key.startswith("the ") else key


def safe_name(name: str) -> str:
    """A note title that is a valid file name wherever Obsidian runs; ValueError if empty."""
    clean = re.sub(r"\s+", " ", _BAD_NAME.sub("-", str(name))).strip().lstrip(".").strip()[:120]
    if not norm(clean):
        raise ValueError("A name is required.")
    return clean


def unique(items) -> list[str]:
    """Non-empty strings, first spelling wins among ones that norm() the same."""
    out, seen = [], set()
    for item in items:
        text = str(item).strip()
        if text and norm(text) and norm(text) not in seen:
            seen.add(norm(text))
            out.append(text)
    return out


_SOURCE = re.compile(r"\s*\(\[\[[^\]]*\]\]\)\s*$")  # a trailing "([[Session 12]])"


def same_fact(a: str, b: str) -> bool:
    """Equal or nearly so, ignoring where each came from."""
    x, y = norm(_SOURCE.sub("", a)), norm(_SOURCE.sub("", b))
    return x == y or difflib.SequenceMatcher(None, x, y).ratio() > 0.9


def add_unique(existing: list[str], new: list[str]) -> list[str]:
    out = list(existing)
    for fact in new:
        if str(fact).strip() and not any(same_fact(fact, f) for f in out):
            out.append(str(fact).strip())
    return out


@dataclass
class Entity:
    name: str
    type: str = "term"
    aliases: list[str] = field(default_factory=list)
    heard_as: list[str] = field(default_factory=list)
    heard_as_suggested: list[str] = field(default_factory=list)
    heard_as_rejected: list[str] = field(default_factory=list)  # suggestions turned down: never re-suggested
    status: str = "confirmed"
    description: str = ""
    facts: list[str] = field(default_factory=list)
    suggested_facts: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)  # a person's own tags; Troubadour adds troubadour/<type>
    extra_meta: dict = field(default_factory=dict)  # frontmatter keys Troubadour doesn't own
    extra_sections: list[tuple[str, str]] = field(default_factory=list)  # (heading, body), verbatim
    path: Path | None = None
    read_only: bool = False  # frontmatter didn't parse: show it, never overwrite it

    def names(self) -> list[str]:
        return [self.name, *self.aliases]

    def to_json(self, root: Path | None = None) -> dict:
        path = None
        if self.path is not None:
            path = str(self.path.relative_to(root)) if root else str(self.path)
        return {
            "name": self.name,
            "type": self.type,
            "aliases": self.aliases,
            "heard_as": self.heard_as,
            "heard_as_suggested": self.heard_as_suggested,
            "heard_as_rejected": self.heard_as_rejected,
            "status": self.status,
            "description": self.description,
            "facts": self.facts,
            "suggested_facts": self.suggested_facts,
            "path": path,
            "read_only": self.read_only,
        }


def mention_counts(vault: "Vault", sessions_dir: Path) -> dict[str, int]:
    """How many sessions mention each glossary name, in the transcript or that session's note.

    A name counts once per session, however often it is said. An alias that is an ordinary word
    ("Dead") does not count, or it would rank a one-off character with the party.
    """
    entities = vault.entities()
    needle_owner: dict[str, str] = {}
    for e in entities:
        for n in e.aliases:
            folded = n.casefold()
            if len(folded) < 2 or everyday(folded):
                continue
            needle_owner.setdefault(folded, e.name)
    for e in entities:
        folded = e.name.casefold()
        if len(folded) >= 2:
            needle_owner[folded] = e.name
    if not needle_owner:
        return {e.name: 0 for e in entities}
    rx = re.compile(
        r"(?<![a-z0-9])(?:"
        + "|".join(re.escape(n) for n in sorted(needle_owner, key=len, reverse=True))
        + r")(?![a-z0-9])"
    )
    present: dict[str, set[str]] = {}

    def take(sid: str, path: Path) -> None:
        try:
            text = path.read_text(encoding="utf-8", errors="ignore").casefold()
        except OSError:
            return
        found = present.setdefault(sid, set())
        for hit in rx.findall(text):
            if owner := needle_owner.get(hit):
                found.add(owner)

    if sessions_dir.is_dir():
        for path in sessions_dir.glob("*/transcript.json"):
            take(path.parent.name, path)
    notes = vault.root / "Sessions"
    if notes.is_dir():
        for path in notes.glob("*.md"):
            try:
                head = path.read_text(encoding="utf-8", errors="ignore")[:400]
            except OSError:
                continue
            m = re.search(r"^session_id:\s*(\S+)", head, re.M)
            take(m.group(1) if m else path.stem, path)
    return {e.name: sum(e.name in found for found in present.values()) for e in entities}


# ------------------------------------------------------------------ markdown


def split_frontmatter(text: str) -> tuple[dict | None, str]:
    """(frontmatter, body). None: a frontmatter block exists but isn't a YAML mapping."""
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return None, text
    try:
        meta = yaml.safe_load(text[4:end]) or {}
    except yaml.YAMLError:
        return None, text
    if not isinstance(meta, dict):
        return None, text
    body = text[end + 4 :]
    return meta, body[1:] if body.startswith("\n") else body


def sections(body: str) -> tuple[str, list[tuple[str, str]]]:
    """(preamble, [(heading, text)]) split on level-2 headings."""
    preamble: list[str] = []
    found: list[tuple[str, list[str]]] = []
    for line in body.splitlines():
        if line.startswith("## "):
            found.append((line[3:].strip(), []))
        elif found:
            found[-1][1].append(line)
        else:
            preamble.append(line)
    return "\n".join(preamble), [(h, "\n".join(lines).strip("\n")) for h, lines in found]


def bullets(text: str) -> list[str]:
    """Bullet items. A non-bullet line continues the previous item: nothing a person typed is lost."""
    items: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line[:2] in ("- ", "* ", "+ "):
            items.append(line[2:].strip())
        elif items:
            items[-1] += " " + line
        else:
            items.append(line)
    return [i for i in items if i]


def _strings(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str | int | float):
        return unique([value])
    if isinstance(value, list):
        return unique(v for v in value if isinstance(v, str | int | float))
    return []


def parse(name: str, text: str) -> Entity:
    meta, body = split_frontmatter(text)
    preamble, found = sections(body)
    lines = preamble.splitlines()
    while lines and not lines[0].strip():
        lines = lines[1:]
    if lines and lines[0].startswith("# "):
        lines = lines[1:]
    e = Entity(name=name, description="\n".join(lines).strip())
    for heading, content in found:
        if heading.casefold() == FACTS.casefold():
            e.facts = bullets(content)
        elif heading.casefold() == SUGGESTED.casefold():
            e.suggested_facts = bullets(content)
        else:
            e.extra_sections.append((heading, content))
    if meta is None:
        e.read_only = True
        return e
    kind = str(meta.get("type") or "term").casefold()
    status = str(meta.get("status") or "confirmed").casefold()
    e.type = kind if kind in TYPES else "term"
    e.aliases = [a for a in _strings(meta.get("aliases")) if norm(a) != norm(name)]
    e.heard_as = _strings(meta.get("heard_as"))
    e.heard_as_suggested = [h for h in _strings(meta.get("heard_as_suggested")) if norm(h) not in {norm(x) for x in e.heard_as}]
    e.heard_as_rejected = _strings(meta.get("heard_as_rejected"))
    e.status = status if status in STATUSES else "confirmed"
    e.tags = [t for t in _strings(meta.get("tags")) if not t.startswith(OWN_TAG_PREFIXES)]
    e.extra_meta = {k: v for k, v in meta.items() if k not in OWNED_KEYS}
    return e


def render(e: Entity) -> str:
    meta: dict = {"type": e.type, "aliases": e.aliases, "heard_as": e.heard_as}
    if e.heard_as_suggested:
        meta["heard_as_suggested"] = e.heard_as_suggested
    if e.heard_as_rejected:
        meta["heard_as_rejected"] = e.heard_as_rejected
    meta["status"] = e.status
    meta["tags"] = [f"troubadour/{e.type}", *e.tags]
    meta |= e.extra_meta
    front = yaml.safe_dump(meta, sort_keys=False, allow_unicode=True, default_flow_style=None, width=1000)
    out = [f"---\n{front}---", f"# {e.name}", ""]
    if e.description:
        out += [e.description, ""]
    out += [f"## {FACTS}", *(f"- {f}" for f in e.facts), ""]
    if e.suggested_facts:
        out += [f"## {SUGGESTED}", *(f"- {f}" for f in e.suggested_facts), ""]
    for heading, content in e.extra_sections:
        out += [f"## {heading}", content, ""]
    return "\n".join(out).rstrip() + "\n"


def write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


# ------------------------------------------------------------------ vault


class Vault:
    """Thread-safe access to the vault. Notes are re-read whenever their file changes."""

    def __init__(self, root: Path | Callable[[], Path] | None = None):
        # A folder; a function giving one (the open campaign's), looked up on every use; or None:
        # config.vault_dir(), also looked up on every use.
        self._root = root
        self.lock = threading.RLock()
        self._cache: dict[Path, tuple[tuple[int, int], Entity]] = {}

    @property
    def root(self) -> Path:
        from . import config

        if callable(self._root):
            return self._root()
        return self._root if self._root is not None else config.vault_dir()

    # ---------------------------------------------------------------- reading

    def _read(self, path: Path) -> Entity | None:
        try:
            st = path.stat()
            key = (st.st_mtime_ns, st.st_size)
            hit = self._cache.get(path)
            if hit and hit[0] == key:
                return hit[1]
            e = parse(path.stem, path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            return None
        e.path = path
        self._cache[path] = (key, e)
        return e

    def entities(self) -> list[Entity]:
        with self.lock:
            out = []
            for folder in FOLDERS:
                for path in sorted((self.root / folder).glob("*.md")):
                    e = self._read(path)
                    if e is not None:
                        out.append(e)
            return out

    def get(self, name: str) -> Entity:
        """By name (case-insensitive). KeyError if absent."""
        key = norm(name)
        for e in self.entities():
            if norm(e.name) == key:
                return e
        raise KeyError(name)

    def find(self, term: str) -> Entity | None:
        """By name, alias or heard-as spelling."""
        key = norm(term)
        if not key:
            return None
        for e in self.entities():
            if key in {norm(n) for n in (*e.names(), *e.heard_as)}:
                return e
        return None

    def glossary(self) -> list[Entity]:
        """Confirmed, readable entities: what transcription may rely on."""
        return [e for e in self.entities() if e.status == "confirmed" and not e.read_only]

    def signature(self) -> tuple:
        """Changes whenever any entity note changes (cheap: one stat per note)."""
        out = []
        for folder in FOLDERS:
            for path in sorted((self.root / folder).glob("*.md")):
                try:
                    st = path.stat()
                except OSError:
                    continue
                out.append((str(path), st.st_mtime_ns, st.st_size))
        return tuple(out)

    # ---------------------------------------------------------------- writing

    def _save(self, e: Entity) -> Entity:
        if e.read_only:
            raise ValueError(f"{e.name}.md has frontmatter Troubadour can't read; fix it in Obsidian first.")
        path = self.root / TYPES[e.type] / f"{safe_name(e.name)}.md"
        old = e.path
        write_atomic(path, render(e))
        if old is not None and old != path and old.exists():
            old.unlink()
            self._cache.pop(old, None)
        self._cache.pop(path, None)
        return self._read(path) or e

    def create(self, name: str, kind: str = "term", **fields) -> Entity:
        with self.lock:
            name = safe_name(name)
            if kind not in TYPES:
                raise ValueError(f"Unknown type {kind!r}.")
            if self.find(name) is not None:
                raise ValueError(f"{name} is already in the glossary.")
            e = Entity(name=name, type=kind)
            self._apply(e, fields)
            return self._save(e)

    def update(self, current: str, /, **fields) -> Entity:
        """Any of: name (rename), type, aliases, heard_as, heard_as_suggested, heard_as_rejected, status,
        description, facts, suggested_facts. Each given field replaces the old value."""
        with self.lock:
            e = self.get(current)
            if e.read_only:
                raise ValueError(f"{e.name}.md has frontmatter Troubadour can't read; fix it in Obsidian first.")
            if "name" in fields:
                new = safe_name(fields.pop("name"))
                clash = self.find(new)
                if clash is not None and clash.path != e.path:
                    raise ValueError(f"{new} is already in the glossary.")
                if norm(new) != norm(e.name):
                    fields.setdefault("aliases", e.aliases)
                    fields["aliases"] = [*fields["aliases"], e.name]  # old spelling still resolves
                e.name = new
            self._apply(e, fields)
            return self._save(e)

    @staticmethod
    def _apply(e: Entity, fields: dict) -> None:
        if "type" in fields:
            if fields["type"] not in TYPES:
                raise ValueError(f"Unknown type {fields['type']!r}.")
            e.type = fields["type"]
        if "status" in fields:
            if fields["status"] not in STATUSES:
                raise ValueError(f"Unknown status {fields['status']!r}.")
            e.status = fields["status"]
        for key in ("aliases", "heard_as", "heard_as_suggested", "heard_as_rejected"):
            if key in fields:
                setattr(e, key, unique(fields[key]))
        for key in ("facts", "suggested_facts"):
            if key in fields:
                setattr(e, key, [str(f).strip() for f in fields[key] if str(f).strip()])
        if "description" in fields:
            e.description = str(fields["description"] or "").strip()
        e.aliases = [a for a in e.aliases if norm(a) != norm(e.name)]
        accepted = {norm(h) for h in e.heard_as}  # an accepted spelling isn't waiting for review
        decided = accepted | {norm(h) for h in e.heard_as_rejected}
        e.heard_as_suggested = [h for h in e.heard_as_suggested if norm(h) not in decided]

    def delete(self, name: str) -> None:
        """Moves the note to the vault's .trash (Obsidian's own convention); never destroys it."""
        with self.lock:
            e = self.get(name)
            trash = self.root / ".trash"
            trash.mkdir(parents=True, exist_ok=True)
            dest = trash / e.path.name
            if dest.exists():
                dest = trash / f"{e.path.stem} {time.strftime('%Y%m%d-%H%M%S')}.md"
            shutil.move(str(e.path), dest)
            self._cache.pop(e.path, None)

    def merge(self, name: str, into: str) -> Entity:
        """Fold `name` into `into`: its names become aliases; facts and spellings combine."""
        with self.lock:
            src, dst = self.get(name), self.get(into)
            if src.path == dst.path:
                raise ValueError("Can't merge a note into itself.")
            if src.read_only or dst.read_only:
                raise ValueError("One of these notes has frontmatter Troubadour can't read; fix it in Obsidian first.")
            merged = self.update(
                dst.name,
                aliases=[*dst.aliases, *src.names()],
                heard_as=[*dst.heard_as, *src.heard_as],
                heard_as_suggested=[*dst.heard_as_suggested, *src.heard_as_suggested],
                facts=add_unique(dst.facts, src.facts),
                suggested_facts=add_unique(dst.suggested_facts, src.suggested_facts),
                description=dst.description or src.description,
            )
            self.delete(src.name)
            return merged

    def review(
        self,
        name: str,
        accept_spellings: list[str] = (),
        reject_spellings: list[str] = (),
        accept_facts: list[str] = (),
        reject_facts: list[str] = (),
    ) -> Entity:
        """Apply review decisions as changes, not replacements, so quick clicks can't undo each
        other: accepted spellings join heard_as, rejected ones are remembered; accepted facts
        move from suggested to facts, rejected ones are dropped."""
        with self.lock:
            e = self.get(name)
            done = {norm(f) for f in (*accept_facts, *reject_facts)}
            return self.update(
                e.name,
                heard_as=[*e.heard_as, *accept_spellings],
                heard_as_rejected=[*e.heard_as_rejected, *reject_spellings],
                heard_as_suggested=e.heard_as_suggested,  # _apply drops the decided ones
                facts=add_unique(e.facts, list(accept_facts)),
                suggested_facts=[f for f in e.suggested_facts if norm(f) not in done],
            )

    def add_fact(self, name: str, text: str, suggested: bool = False) -> Entity:
        with self.lock:
            e = self.get(name)
            if any(same_fact(text, f) for f in [*e.facts, *e.suggested_facts]):
                return e
            key = "suggested_facts" if suggested else "facts"
            return self.update(e.name, **{key: [*getattr(e, key), text]})

    # ---------------------------------------------------------------- campaign facts, notes

    @property
    def facts_path(self) -> Path:
        return self.root / "Facts.md"

    def campaign_facts(self) -> list[str]:
        with self.lock:
            try:
                _, body = split_frontmatter(self.facts_path.read_text(encoding="utf-8"))
            except OSError:
                return []
            _, found = sections(body)
            return next((bullets(c) for h, c in found if h.casefold() == FACTS.casefold()), [])

    def set_campaign_facts(self, facts: list[str]) -> list[str]:
        with self.lock:
            facts = [str(f).strip() for f in facts if str(f).strip()]
            write_atomic(
                self.facts_path,
                "---\ntags: [troubadour/facts]\n---\n# Campaign facts\n\nThings that hold across the whole campaign.\n\n"
                f"## {FACTS}\n" + "".join(f"- {f}\n" for f in facts),
            )
            return facts

    def add_campaign_fact(self, text: str) -> list[str]:
        with self.lock:
            return self.set_campaign_facts(add_unique(self.campaign_facts(), [text]))

    @property
    def ignored_path(self) -> Path:
        return self.root / "Ignored words.md"

    def ignored(self) -> list[str]:
        """Words never suggested as names (table banter: "T-Mobile"). Editable in Obsidian."""
        with self.lock:
            try:
                _, body = split_frontmatter(self.ignored_path.read_text(encoding="utf-8"))
            except OSError:
                return []
            _, found = sections(body)
            return unique(next((bullets(c) for h, c in found if h.casefold() == "words"), []))

    def set_ignored(self, words: list[str]) -> list[str]:
        with self.lock:
            words = unique(words)
            write_atomic(
                self.ignored_path,
                "---\ntags: [troubadour/index]\n---\n# Ignored words\n\n"
                "Words Troubadour won't suggest as names. Delete a line to let it suggest that word again.\n\n"
                "## Words\n" + "".join(f"- {w}\n" for w in words),
            )
            return words

    def ignore(self, words: list[str]) -> list[str]:
        with self.lock:
            return self.set_ignored([*self.ignored(), *words])

    def unignore(self, word: str) -> list[str]:
        with self.lock:
            return self.set_ignored([w for w in self.ignored() if norm(w) != norm(word)])

    def write_note(self, folder: str, title: str, body: str, overwrite: bool = False) -> Path:
        """A free-form note (pasted notes, write-ups). Without overwrite, never replaces another."""
        with self.lock:
            base = safe_name(title)
            path = self.root / folder / f"{base}.md"
            n = 2
            while not overwrite and path.exists():
                path = self.root / folder / f"{base} ({n}).md"
                n += 1
            write_atomic(path, body)
            return path

    # ---------------------------------------------------------------- index

    def write_index(self) -> None:
        """Glossary.md: every entity by type, with links, aliases and heard-as spellings.
        Also writes README.md once (never overwritten: it's yours to edit)."""
        with self.lock:
            readme = self.root / "README.md"
            if not readme.exists():
                write_atomic(readme, README)
            by_type: dict[str, list[Entity]] = {}
            for e in self.entities():
                by_type.setdefault(e.type, []).append(e)
            out = [
                "---\ntags: [troubadour/index]\n---",
                "# Glossary",
                "",
                "Generated by Troubadour from the notes in this vault. Edit those notes, not this page.",
                "",
            ]
            titles = {"pc": "Player characters", "npc": "NPCs"}
            for kind in TYPES:
                rows = sorted(by_type.get(kind, []), key=lambda e: e.name.casefold())
                if not rows:
                    continue
                out += [
                    f"## {titles.get(kind, kind.title() + 's')}",
                    "",
                    "| Name | Also | Heard as | About |",
                    "|---|---|---|---|",
                ]
                for e in rows:
                    about = e.description.splitlines()[0] if e.description else ""
                    name = f"[[{e.name}]]" + (" (suggested)" if e.status == "suggested" else "")
                    cells = [name, ", ".join(e.aliases), ", ".join(e.heard_as), about]
                    out.append("| " + " | ".join(c.replace("|", "\\|") for c in cells) + " |")
                out.append("")
            write_atomic(self.root / "Glossary.md", "\n".join(out))


class Corrector:
    """Applies the glossary to freshly transcribed text: each heard-as misspelling becomes the
    entry's name ("Dilara" → "Elara"), and a name or alias in the wrong case gets its spelling
    ("augie" → "Augie"), unless that name is an ordinary English word ("Will", "Hope")."""

    def __init__(self, entities: list[Entity], dictionary: set[str] = frozenset()):
        table: dict[str, str] = {}
        for e in entities:
            for n in e.names():
                if n.casefold() not in dictionary:
                    table.setdefault(n.casefold(), n)
        for e in entities:
            for h in e.heard_as:
                table[h.casefold()] = e.name  # an explicit correction wins over a case fix
        self.table = table
        keys = sorted(table, key=len, reverse=True)
        self.pattern = re.compile(r"(?<![\w'-])(" + "|".join(map(re.escape, keys)) + r")(?![\w-])", re.I) if keys else None

    def __call__(self, text: str) -> str:
        if self.pattern is None or not text:
            return text
        return self.pattern.sub(lambda m: self.table[m.group(0).casefold()], text)


README = """---
tags: [troubadour/index]
---
# Campaign wiki

Kept by [Troubadour](https://github.com/yipe/troubadour), which transcribes the table, and by you. Open this
folder as an Obsidian vault, or read it with any agent: it is plain markdown.

## Layout

- `People/`, `Places/`, `Factions/`, `Items/`, `Lore/`: one note per named thing. The file name is its name.
- `Sessions/`: one page per played session (summary, highlights, what happened), with the full
  transcript in `Sessions/Transcripts/`.
- `Notes/`: session notes pasted into Troubadour, kept word for word.
- `Facts.md`: things that hold across the whole campaign.
- [[Glossary]]: an index of every note, regenerated by Troubadour (edit the notes, not the index).

## A note

```
---
type: npc                  # pc, npc, place, faction, item, creature, spell, term
aliases: [Maggie]          # other names for it
heard_as: [Magento]        # how speech-to-text misspells it; Troubadour corrects these in new transcripts
status: confirmed          # "suggested" = Troubadour proposed it from a session; keep or discard it in Troubadour
---
# Magenta
One or two lines on who or what this is.

## Facts
- One lasting fact per bullet, with where it came from: ([[2026-10-08 The Desert Road]])

## Suggested facts
- Troubadour's proposals from a session, waiting for review in Troubadour's Campaign tab.
```

Troubadour rewrites only the properties above, the description and those two sections. Add any other
properties, tags or `##` sections you like (theories, relationships, art): Troubadour keeps them as written.
Link freely with `[[Name]]`. Deleting a note in Troubadour moves it to `.trash`.
"""
