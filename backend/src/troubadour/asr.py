"""Speech-to-text for one utterance (mlx-whisper).

Calls are serialized: an abandoned session's worker can still be mid-utterance
when the next session's worker starts, and mlx-whisper isn't meant to run twice
at once.
"""

import functools
import re
import threading
import unicodedata

import numpy as np

from .config import SR
from .settings import current as model_settings

_lock = threading.Lock()

# Whisper's stock outputs for noise / silence (YouTube outros and subtitle credits).
HALLUCINATIONS = {
    "",
    "you",
    "thank you",
    "thanks for watching",
    "thank you for watching",
    "bye",
    "we'll be right back",
    "we'll see you next time",
    "see you next time",
    "subtitles by the amara.org community",
}
MAX_WORDS_PER_S = 8.0  # people top out near 6.6 (99th percentile of a real session)

# Whisper reads at most n_ctx/2 - 1 prompt tokens and silently drops the *start* of a longer
# prompt: the names. A big glossary plus two long lines overflowed 46% of one real session's prompts.
PROMPT_TOKENS = 223
CONTEXT_TOKENS = 60  # of those, for the last lines (tuned in PLAN.md, "Whisper prompt")
PROMPT_HEAD, PROMPT_TAIL = "A D&D session.", "Initiative, perception check, saving throw."


@functools.cache
def _tokenizer():
    from mlx_whisper.tokenizer import get_tokenizer

    return get_tokenizer(multilingual=True, num_languages=100)  # large-v3 and turbo


def prompt_tokens(text: str) -> int:
    """Prompt length the way Whisper counts it (mlx_whisper encodes " " + the stripped prompt)."""
    return len(_tokenizer().encode(" " + text.strip()))


def build_prompt(names: list[str], lines: list[str], context_tokens: int = CONTEXT_TOKENS) -> str:
    """'A D&D session. <names>. Initiative, … <the last lines>' that fits Whisper's prompt window:
    the lines keep their last context_tokens tokens, and names go in, in the given order, while
    there's room."""
    words = " ".join(lines).split()
    while words and prompt_tokens(" ".join(words)) > context_tokens:
        words = words[max(1, len(words) // 8) :]  # whole words off the front, oldest first
    context = " ".join(words)

    def build(k: int) -> str:
        head = f"{PROMPT_HEAD} {', '.join(names[:k])}." if k else PROMPT_HEAD
        return " ".join(p for p in (head, PROMPT_TAIL, context) if p)

    lo, hi = 0, len(names)  # the most names that fit (more names, more tokens)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        lo, hi = (mid, hi) if prompt_tokens(build(mid)) <= PROMPT_TOKENS else (lo, mid - 1)
    return build(lo)


def prompt_names(first: list[str], glossary: list[str], recent: list[str], nicknames: list[str] | None = None) -> list[str]:
    """The order names earn a place in the prompt: `first` (Setup's characters and players, the
    voices panel), then names said in `recent` lines (latest first), then the rest of the glossary.
    A nickname (a glossary alias) said lately goes in as said: "Phisho", not only "Phishondra"."""
    forms = {n.casefold(): n for n in (*glossary, *(nicknames or [])) if n}
    said: list[str] = []
    if forms:  # longest first: "Ten Proof Babadabroose" wins over "Ten Proof"
        alternatives = "|".join(re.escape(k) for k in sorted(forms, key=len, reverse=True))
        spoken = re.compile(rf"(?<!\w)(?:{alternatives})(?!\w)")
        for line in reversed(recent):
            said += [forms[m.group(0)] for m in spoken.finditer(line.casefold())]
    return list(dict.fromkeys(n for n in (*first, *said, *glossary) if n))


def _run(audio: np.ndarray, prompt: str | None, model: str | None = None) -> str:
    import mlx_whisper

    with _lock:
        result = mlx_whisper.transcribe(
            audio,
            path_or_hf_repo=model or model_settings()["whisper"],
            language="en",
            verbose=None,
            # Benchmarked: these stop repetition loops on trailing noise.
            condition_on_previous_text=False,
            word_timestamps=True,
            hallucination_silence_threshold=1.0,
            initial_prompt=prompt,
        )
    # Language is forced to English and every name the table uses is in Latin script: letters from
    # another script mean the segment is Whisper making things up ("образbak Waters factual").
    segments = [
        s
        for s in result["segments"]
        if not (s.get("no_speech_prob", 0) > 0.6 and s.get("avg_logprob", 0) < -1.0) and not _foreign(s["text"])
    ]
    text = " ".join(s["text"].strip() for s in segments).strip()
    return "" if text.lower().strip(" .!?,") in HALLUCINATIONS else text


def _foreign(text: str) -> bool:
    """Any letter outside the Latin script (accented Latin letters are fine)."""
    return any(ch.isalpha() and not unicodedata.name(ch, "").startswith("LATIN") for ch in text)


def too_fast(text: str, audio: np.ndarray) -> bool:
    """More words than anyone fits in the clip: invented ("We'll be right back." in 0.3 s)."""
    return len(text.split()) > MAX_WORDS_PER_S * len(audio) / SR


def _words(text: str) -> list[str]:
    return [w for w in (t.strip(".,!?;:\"'()-").lower() for t in text.split()) if w]


def suspicious(text: str, prompt: str) -> bool:
    """A prompted result that reads the prompt back (5 of its words in a row) or loops."""
    words = _words(text)
    if any(len(w) > 25 for w in words):  # "epipopeopeopeo…"
        return True
    if len(words) >= 8 and len(set(words)) / len(words) < 0.3:
        return True
    p = _words(prompt)
    grams = {tuple(p[i : i + 5]) for i in range(len(p) - 4)}
    if any(tuple(words[i : i + 5]) in grams for i in range(len(words) - 4)):
        return True
    # A short line that is mostly the prompt's names ("Auggie, Kessia, Magenta, Nora." from noise).
    return len(words) >= 3 and sum(w in set(p) for w in words) / len(words) >= 0.75


def transcribe(audio: np.ndarray, prompt: str | None = None, model: str | None = None) -> str:
    """One utterance. A prompt (glossary names) helps names, but on a quiet clip Whisper can read
    it back as speech, fall into a loop or invent a sentence; such a result is redone without the
    prompt, and a line still spoken impossibly fast is dropped.
    model: another Whisper (e.g. large-v3-mlx for an offline pass); default the live one."""
    text = _run(audio, prompt, model)
    if prompt and text and (suspicious(text, prompt) or too_fast(text, audio)):
        text = _run(audio, None, model)
    return "" if too_fast(text, audio) else text


# MLX's buffer cache defaults to ~all of RAM; Whisper sees a new input length every
# utterance, so an unbounded cache creeps up over a long session. 1 GB keeps reuse.
MLX_CACHE_LIMIT = 1 << 30


def warm_up() -> None:
    import mlx.core as mx

    mx.set_cache_limit(MLX_CACHE_LIMIT)
    transcribe(np.zeros(16000, np.float32))


def memory() -> dict:
    import mlx.core as mx

    return {"mlx_active_mb": mx.get_active_memory() / 2**20, "mlx_cache_mb": mx.get_cache_memory() / 2**20}
