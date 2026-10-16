"""Spike: offline transcription + diarization of one audio file.

Usage: uv run spike/run.py AUDIO [--speakers N] [--prompt "names..."]

Prints a speaker-labeled transcript, timings (RTF = processing time / audio
length; < 1 is faster than real time), and, if AUDIO's .truth.json exists,
word-level speaker accuracy.
"""

import argparse
import json
import subprocess
import time
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

SR = 16000
WHISPER = "mlx-community/whisper-large-v3-turbo"
DIARIZER = "pyannote/speaker-diarization-community-1"


def load_audio(path: Path, start: float = 0.0, end: float | None = None) -> np.ndarray:
    span = ["-ss", str(start)] + (["-t", str(end - start)] if end is not None else [])
    pcm = subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", *span, "-i", str(path), "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"],
        check=True,
        capture_output=True,
    ).stdout
    return np.frombuffer(pcm, np.float32).copy()


def timed(label: str, fn, *args, **kwargs):
    t0 = time.perf_counter()
    result = fn(*args, **kwargs)
    elapsed = time.perf_counter() - t0
    print(f"[time] {label}: {elapsed:.2f}s")
    return result, elapsed


ASR_DEFAULTS = {
    "path_or_hf_repo": WHISPER,
    "word_timestamps": True,
    "language": "en",
    # Without these, Whisper loops on trailing noise ("…hit points" ×5).
    "condition_on_previous_text": False,
    "hallucination_silence_threshold": 1.0,
}


def transcribe(audio: np.ndarray, prompt: str | None, **options) -> list[dict]:
    """Whisper segments, each with word timestamps under "words"."""
    import mlx_whisper

    result = mlx_whisper.transcribe(audio, initial_prompt=prompt, **{**ASR_DEFAULTS, **options})
    return result["segments"]


def load_diarizer():
    import torch
    from pyannote.audio import Pipeline

    pipeline = Pipeline.from_pretrained(DIARIZER)
    pipeline.to(torch.device("mps"))
    return pipeline


def diarize(pipeline, audio: np.ndarray, num_speakers: int | None = None, **kwargs) -> list[tuple[float, float, str]]:
    """kwargs pass through to the pipeline (min_speakers, max_speakers)."""
    import torch

    if num_speakers:
        kwargs["num_speakers"] = num_speakers
    out = pipeline({"waveform": torch.from_numpy(audio)[None], "sample_rate": SR}, **kwargs)
    # Exclusive = at most one speaker at a time, which is what word assignment needs.
    return [(turn.start, turn.end, spk) for turn, _, spk in out.exclusive_speaker_diarization.itertracks(yield_label=True)]


# A Whisper segment keeps a single speaker unless a second speaker covers at
# least this many seconds of it. Whisper's boundaries absorb surrounding
# silence, so per-word labels flip at turn edges ("You / will pay for that").
MIN_SPLIT_S = 1.0


def speaker_overlaps(turns: list[tuple[float, float, str]], start: float, end: float) -> list[tuple[str, float]]:
    totals: Counter = Counter()
    for t0, t1, spk in turns:
        totals[spk] += max(0.0, min(end, t1) - max(start, t0))
    return totals.most_common()


def speaker_for(turns: list[tuple[float, float, str]], start: float, end: float) -> str:
    ranked = speaker_overlaps(turns, start, end)
    if ranked and ranked[0][1] > 0:
        return ranked[0][0]
    mid = (start + end) / 2
    return min(turns, key=lambda turn: min(abs(turn[0] - mid), abs(turn[1] - mid)))[2]


def label_words(segments: list[dict], turns: list[tuple[float, float, str]]) -> tuple[list[dict], list[str]]:
    words, labels = [], []
    for seg in segments:
        seg_words = seg.get("words") or []
        ranked = speaker_overlaps(turns, seg["start"], seg["end"])
        split = len(ranked) > 1 and ranked[1][1] >= MIN_SPLIT_S
        for w in seg_words:
            labels.append(speaker_for(turns, w["start"], w["end"]) if split else speaker_for(turns, seg["start"], seg["end"]))
        words.extend(seg_words)
    return words, labels


def print_transcript(words: list[dict], labels: list[str]) -> None:
    line, current, start = [], None, 0.0
    for word, label in zip(words, labels):
        if label != current and line:
            print(f"{start:6.1f}  {current:>11}: {''.join(line).strip()}")
            line = []
        if not line:
            start = word["start"]
        current = label
        line.append(word["word"])
    if line:
        print(f"{start:6.1f}  {current:>11}: {''.join(line).strip()}")


def score(words: list[dict], labels: list[str], truth: list[dict]) -> None:
    """Word-level speaker accuracy, each cluster mapped to its majority true speaker."""

    def true_speaker(t: float) -> str:
        return min(truth, key=lambda u: 0 if u["start"] <= t <= u["end"] else min(abs(u["start"] - t), abs(u["end"] - t)))[
            "speaker"
        ]

    by_cluster: dict[str, Counter] = defaultdict(Counter)
    pairs = []
    for word, label in zip(words, labels):
        actual = true_speaker((word["start"] + word["end"]) / 2)
        by_cluster[label][actual] += 1
        pairs.append((label, actual))
    mapping = {cluster: counts.most_common(1)[0][0] for cluster, counts in by_cluster.items()}
    correct = sum(mapping[label] == actual for label, actual in pairs)
    true_speakers = {u["speaker"] for u in truth}
    print(f"\n[score] clusters={len(by_cluster)} true_speakers={len(true_speakers)}")
    for cluster, counts in sorted(by_cluster.items()):
        print(f"  {cluster} -> {dict(counts.most_common())}")
    unclaimed = true_speakers - set(mapping.values())
    if unclaimed:
        print(f"  merged away (no cluster of their own): {sorted(unclaimed)}")
    print(f"[score] word speaker accuracy: {correct}/{len(pairs)} = {correct / len(pairs):.1%}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("audio", type=Path)
    ap.add_argument("--speakers", type=int, help="known speaker count (default: auto)")
    ap.add_argument("--prompt", help="Whisper prompt, e.g. player and character names")
    args = ap.parse_args()

    audio = load_audio(args.audio)
    duration = len(audio) / SR
    print(f"[audio] {args.audio}: {duration:.1f}s")

    # Warm up / load models separately so run times reflect steady state.
    timed("whisper load", transcribe, np.zeros(SR, np.float32), None)
    pipeline, _ = timed("diarizer load", load_diarizer)

    segments, t_asr = timed("transcribe", transcribe, audio, args.prompt)
    turns, t_dia = timed("diarize", diarize, pipeline, audio, args.speakers)
    print(f"[rtf] asr={t_asr / duration:.3f} diarize={t_dia / duration:.3f} (lower is better; 0.1 = 10x real time)")

    words, labels = label_words(segments, turns)
    print()
    print_transcript(words, labels)

    truth_path = args.audio.with_suffix(".truth.json")
    if truth_path.exists():
        score(words, labels, json.loads(truth_path.read_text()))


if __name__ == "__main__":
    main()
