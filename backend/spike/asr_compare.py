"""Re-transcribe a recorded session under several ASR configs, for offline comparison.

    uv run spike/asr_compare.py SESSION_ID [--configs T0,L0,...] [--limit N] [--data DIR] [--out DIR]

Uses the saved transcript's utterance spans (so every config transcribes the same audio the
live pipeline cut) and writes samples/asr_compare/<session>/<config>.jsonl (or --out): one line
per utterance {id, t_start, t_end, text}. Resumable: finished configs are skipped. --data reads
the sessions of another checkout (read-only). Recordings at any sample rate are resampled.

Configs:
  T0 / L0    turbo / large-v3, one utterance at a time, no prompt (T0 = the live pipeline)
  TG / LG    the same with a glossary prompt
  TGC / LGC  per utterance, prompt = glossary + the previous CONTEXT_LINES lines this config
             produced (context without merging audio; TGC is cheap enough to run live)
  LW0 / LWG  large-v3 over ~28 s windows of consecutive utterances packed back to back (more
  TWG        context); words assigned back by timestamp; without / with glossary; TWG = turbo
"""

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "src"))

SR = 16000
TURBO = "mlx-community/whisper-large-v3-turbo"
LARGE = "mlx-community/whisper-large-v3-mlx"
WINDOW_S = 28.0
HALLUCINATIONS = {"", "you", "thank you", "thanks for watching", "thank you for watching", "bye"}

CONFIGS = {
    "T0": (TURBO, "utt", False),
    "L0": (LARGE, "utt", False),
    "LW0": (LARGE, "win", False),
    "LWG": (LARGE, "win", True),
    "TG": (TURBO, "utt", True),
    "LG": (LARGE, "utt", True),
    "TGC": (TURBO, "ctx", True),
    "LGC": (LARGE, "ctx", True),
    "TWG": (TURBO, "win", True),
}


def glossary_prompt(state: dict) -> str:
    """What a user would have before any notes: Setup's names + a few D&D words. Short: a long
    term list is what Whisper echoes back as "speech" on quiet clips."""
    from bard.engine import migrate_state, seated_players

    players = seated_players(state)
    names = [p["character"] for p in players if p["character"] and p["character"] != "DM"]
    names += [v["character"] for v in migrate_state(state)["voices"] if v.get("character")]
    names += [p["name"] for p in players]
    names = list(dict.fromkeys(names))
    return f"A D&D session. {', '.join(names)}. Initiative, perception check, saving throw."


def run(audio: np.ndarray, model: str, prompt: str | None) -> dict:
    import mlx_whisper

    return mlx_whisper.transcribe(
        audio,
        path_or_hf_repo=model,
        language="en",
        verbose=None,
        condition_on_previous_text=False,
        word_timestamps=True,
        hallucination_silence_threshold=1.0,
        initial_prompt=prompt,
    )


def keep(seg: dict) -> bool:
    return not (seg.get("no_speech_prob", 0) > 0.6 and seg.get("avg_logprob", 0) < -1.0)


def clean(text: str) -> str:
    text = text.strip()
    return "" if text.lower().strip(" .!?,") in HALLUCINATIONS else text


def _words(text: str) -> list[str]:
    return [w.strip(".,!?;:\"'()-").lower() for w in text.split() if w.strip(".,!?;:\"'()-")]


def suspicious(text: str, prompt: str | None) -> bool:
    """A prompted result that echoes the prompt (5 prompt words in a row) or loops."""
    words = _words(text)
    if any(len(w) > 25 for w in words):  # "epipopeopeopeo…"
        return True
    if len(words) >= 8 and len(set(words)) / len(words) < 0.3:
        return True
    if prompt:
        p = _words(prompt)
        grams = {tuple(p[i : i + 5]) for i in range(len(p) - 4)}
        if any(tuple(words[i : i + 5]) in grams for i in range(len(words) - 4)):
            return True
    return False


def transcribe(clip: np.ndarray, model: str, prompt: str | None) -> str:
    """With the prompt, but fall back to no prompt when the result looks like an echo or a loop."""
    r = run(clip, model, prompt)
    text = clean(" ".join(s["text"].strip() for s in r["segments"] if keep(s)))
    if prompt and suspicious(text, prompt):
        r = run(clip, model, None)
        text = clean(" ".join(s["text"].strip() for s in r["segments"] if keep(s)))
    return text


def per_utterance(audio, msgs, model, prompt):
    for m in msgs:
        yield m, transcribe(audio[int(m["t_start"] * SR) : int(m["t_end"] * SR)], model, prompt)


CONTEXT_LINES = 2


def with_context(audio, msgs, model, prompt):
    recent: list[str] = []
    for m in msgs:
        clip = audio[int(m["t_start"] * SR) : int(m["t_end"] * SR)]
        text = transcribe(clip, model, " ".join([prompt, *recent]))
        if text:
            recent = [*recent, text][-CONTEXT_LINES:]
        yield m, text


GAP_S = 0.8  # silence between packed clips: a clear turn boundary, still too short for Whisper to skip


def windows(msgs):
    """Consecutive utterances packed into windows of at most WINDOW_S seconds of audio."""
    group: list[dict] = []
    total = 0.0
    for m in msgs:
        d = m["t_end"] - m["t_start"] + GAP_S
        if group and total + d > WINDOW_S:
            yield group
            group, total = [], 0.0
        group.append(m)
        total += d
    if group:
        yield group


def per_window(audio, msgs, model, prompt):
    """Clips packed back to back (no long real silences, which make Whisper skip short turns);
    each word goes back to the clip its midpoint falls in, else the nearest clip."""
    gap = np.zeros(int(GAP_S * SR), np.float32)
    for group in windows(msgs):
        parts, spans, at = [], [], 0.0
        for m in group:
            clip = audio[int(m["t_start"] * SR) : int(m["t_end"] * SR)]
            parts += [clip, gap]
            spans.append((at, at + len(clip) / SR))
            at += (len(clip) + len(gap)) / SR
        window = np.concatenate(parts)
        r = run(window, model, prompt)
        if prompt and suspicious(" ".join(s["text"] for s in r["segments"]), prompt):
            r = run(window, model, None)
        texts: list[list[str]] = [[] for _ in group]
        for seg in r["segments"]:
            if not keep(seg):
                continue
            for w in seg.get("words", []):
                mid = (w["start"] + w["end"]) / 2
                i = min(
                    range(len(group)),
                    key=lambda k: 0 if spans[k][0] <= mid <= spans[k][1] else min(abs(mid - spans[k][0]), abs(mid - spans[k][1])),
                )
                texts[i].append(w["word"])
        for m, words in zip(group, texts):
            yield m, clean("".join(words))


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("session")
    p.add_argument("--configs", default=",".join(CONFIGS))
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--data", type=Path, default=BACKEND / "data")
    p.add_argument("--out", type=Path)
    args = p.parse_args()
    from bard.audio import load_audio

    sdir = args.data / "sessions" / args.session
    record = json.loads((sdir / "transcript.json").read_text())
    setup = sdir / "setup" / "end.json"
    state = json.loads((setup if setup.exists() else args.data / "state.json").read_text())
    prompt = glossary_prompt(state)
    msgs = sorted(record["messages"], key=lambda m: m["t_start"])[: args.limit or None]
    audio = load_audio(sdir / "audio.wav")  # any rate (mic sessions record at the mic's own) → 16 kHz
    out = args.out or BACKEND / "samples" / "asr_compare" / args.session
    out.mkdir(parents=True, exist_ok=True)
    (out / "prompt.txt").write_text(prompt)
    print("prompt:", prompt, flush=True)

    for name in args.configs.split(","):
        path = out / f"{name}.jsonl"
        if path.exists() and sum(1 for _ in path.open()) >= len(msgs):
            print(f"{name}: done already", flush=True)
            continue
        model, mode, use_prompt = CONFIGS[name]
        t0 = time.monotonic()
        rows = []
        gen = {"utt": per_utterance, "ctx": with_context, "win": per_window}[mode](
            audio, msgs, model, prompt if use_prompt else None
        )
        for i, (m, text) in enumerate(gen):
            rows.append({"id": m["id"], "t_start": m["t_start"], "t_end": m["t_end"], "text": text})
            if i % 200 == 0:
                print(f"{name}: {i}/{len(msgs)} ({time.monotonic() - t0:.0f}s)", flush=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text("".join(json.dumps(r) + "\n" for r in rows))
        tmp.replace(path)
        print(f"{name}: {len(rows)} lines in {time.monotonic() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
