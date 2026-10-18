"""D&D 5e vocabulary the spelling cleanup knows besides the campaign glossary.

Only names speech-to-text tends to mangle are worth listing: multi-syllable spell and
creature names, conditions, classes. Ordinary English words ("Shield", "Light") are left out
on purpose: the cleanup must never "fix" everyday speech.
"""

import json
from pathlib import Path

from . import config

SRD_PATH = Path(__file__).with_name("srd_terms.json")


def builtin() -> list[tuple[str, str]]:
    """The SRD's names (CC-BY-4.0, shipped with Troubadour; see srd_terms.json for attribution)."""
    try:
        return [(n, k) for n, k in json.loads(SRD_PATH.read_text())["terms"]]
    except (OSError, ValueError, KeyError):
        return []


def generated_path() -> Path:
    """An optional, larger local list in the same shape as srd_terms.json
    (`{"terms": [[name, type], ...]}`). It lives in data/ and is never committed."""
    return config.DATA_DIR / "dnd_terms.json"


_CACHE: tuple[float, list[tuple[str, str]]] | None = None


def terms() -> list[tuple[str, str]]:
    """(name, type) pairs in the glossary's types: the local list if there is one, plus the
    built-in names."""
    global _CACHE
    path = generated_path()
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return builtin()
    if _CACHE is None or _CACHE[0] != mtime:
        try:
            data = json.loads(path.read_text())
            loaded = [(n, k) for n, k in data["terms"] if isinstance(n, str) and isinstance(k, str)]
        except (OSError, ValueError, KeyError, TypeError):
            return builtin()
        seen = {n.casefold() for n, _ in loaded}
        _CACHE = (mtime, loaded + [(n, k) for n, k in builtin() if n.casefold() not in seen])
    return _CACHE[1]
