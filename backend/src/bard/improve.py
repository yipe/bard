"""Improve a saved transcript: re-transcribe every line with what the campaign knows now.

After a session the glossary usually knows more than it did live (spellings accepted from
the Campaign tab, names from imported notes). Each line's audio goes through Whisper again
with that glossary in the prompt plus the lines before it, then through the heard-as
corrector. Speakers, times and line boundaries stay as they were.

The first improvement backs the original up as transcript.orig.json. Lines Whisper now hears
as nothing keep their old text: an improvement never deletes what was said.
"""

import json
import time
from collections.abc import Callable
from pathlib import Path

import numpy as np

from . import asr, cleanup, config, transcript
from .audio import load_audio
from .campaign import Corrector
from .engine import Engine, session_audio

SR = config.SR


def improve_record(
    record: dict,
    audio: np.ndarray,
    transcribe: Callable[[np.ndarray, str | None], str],
    corrector: Corrector | None,
    prompt_after: Callable[[list[str], float], str] | None,
    progress: Callable[[int, int], None] = lambda done, total: None,
    pause: Callable[[], None] = lambda: None,
) -> tuple[dict, int]:
    """(new record, lines changed). Pure apart from the callbacks; the record isn't modified.
    prompt_after(the lines so far, the line's seconds) gives its Whisper prompt; None: no prompt."""
    messages = sorted(record["messages"], key=lambda m: m["t_start"])
    history: list[str] = []
    changed = 0
    out = []
    for i, m in enumerate(messages):
        pause()
        clip = audio[int(m["t_start"] * SR) : int(m["t_end"] * SR)]
        prompt = prompt_after(history, m["t_end"] - m["t_start"]) if prompt_after else None
        text = transcribe(clip, prompt) if len(clip) else ""
        if text and corrector is not None:
            text = corrector(text)
        if not text or transcript.filler_only(text):
            text = m["text"]  # never lose a line (filler-only lines are hidden)
        if text != m["text"]:
            changed += 1
        # Records saved before mechanics were removed still carry "annotations": dropped here.
        out.append({k: v for k, v in m.items() if k != "annotations"} | {"text": text})
        history.append(text)
        progress(i + 1, len(messages))
    new = dict(record)
    new["messages"] = out
    new["session"] = {k: v for k, v in record["session"].items() if k != "mechanics"}
    return new, changed


def run(job, engine: Engine, sid: str, emit: Callable[[], None], model: str | None = None) -> None:
    """The job: improve saved session `sid` in place (backup first)."""
    folder = engine.sessions_dir() / sid
    record = json.loads((folder / "transcript.json").read_text())
    path, end = session_audio(sid)
    audio = load_audio(path, 0.0, end)
    corrector, names = engine._glossary_state()
    job.total = len(record["messages"])
    emit()
    last = [0.0]

    def progress(done: int, total: int) -> None:
        job.done = done
        if time.monotonic() - last[0] > 1 or done == total:
            last[0] = time.monotonic()
            job.message = f"Line {done} of {total}"
            emit()

    def pause() -> None:
        # A live session needs Whisper more than this does: wait until it's over.
        while engine.status != "idle":
            job.message = "Paused while a session runs"
            emit()
            time.sleep(2)

    new, changed = improve_record(
        record,
        audio,
        lambda clip, prompt: asr.transcribe(clip, prompt, model=model),
        corrector,
        (lambda history, seconds: engine.whisper_prompt(names, history, seconds)) if config.ASR_PROMPT else None,
        progress,
        pause,
    )
    if (speller := engine.speller()) is not None:
        job.message = "Fixing spellings"
        emit()
        new, _, decided = cleanup.clean_record(new, speller, engine.cleanup_llm.json)
        engine.learn(decided, speller, cleanup.Learner())
    new["session"] = new["session"] | {
        "improved": {"at": time.time(), "model": model or config.WHISPER_MODEL, "changed": changed}
    }
    save(folder, new)
    job.message = f"{changed} of {len(new['messages'])} lines changed"


def save(folder: Path, record: dict) -> None:
    """Write a saved session's transcript files, backing the original up the first time."""
    backup = folder / "transcript.orig.json"
    if not backup.exists():
        backup.write_text((folder / "transcript.json").read_text())
    for name, body in (
        ("transcript.json", json.dumps(record, ensure_ascii=False, indent=1)),
        ("transcript.md", transcript.markdown(record)),
        ("meta.json", json.dumps(record["session"])),
    ):
        tmp = folder / f".{name}.tmp"
        tmp.write_text(body, encoding="utf-8")
        tmp.replace(folder / name)


def fix_spellings(job, engine: Engine, sid: str, emit: Callable[[], None]) -> None:
    """The job: the spelling cleanup over a saved session (no re-listening; minutes, not hours)."""
    folder = engine.sessions_dir() / sid
    record = json.loads((folder / "transcript.json").read_text())
    speller = engine.speller()
    if speller is None:
        raise ValueError("Spelling cleanup is turned off (BARD_CLEANUP=0).")

    def progress(done: int, total: int) -> None:
        job.done, job.total = done, total
        job.message = f"Asking about {done} of {total} lines"
        emit()

    new, fixed, decided = cleanup.clean_record(record, speller, engine.cleanup_llm.json, progress=progress)
    engine.learn(decided, speller, cleanup.Learner())
    new["session"] = new["session"] | {"spelling": {"at": time.time(), "fixed": fixed}}
    save(folder, new)
    job.message = f"{fixed} spellings fixed"


def restore(folder: Path) -> bool:
    """Put the original transcript back; False if it was never improved."""
    backup = folder / "transcript.orig.json"
    if not backup.exists():
        return False
    record = json.loads(backup.read_text())
    (folder / "transcript.json").write_text(backup.read_text())
    (folder / "transcript.md").write_text(transcript.markdown(record), encoding="utf-8")
    (folder / "meta.json").write_text(json.dumps(record["session"]))
    backup.unlink()
    return True
