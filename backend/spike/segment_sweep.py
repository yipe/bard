"""Tune utterance cutting (bard.segment) on real audio.

    uv run spike/segment_sweep.py vad      [--data DIR]                 # speaker purity, every grid point
    uv run spike/segment_sweep.py whisper  CONFIG... [--data DIR]       # + word errors, chosen points

Silero's speech probabilities are computed once per recording and cached (--cache); every
setting then replays them through the shipped Segmenter, so a grid point costs seconds.

vad: on the benchmark clips (samples/eval, AMI + Critical Role, with true speaker turns):
  lines, median length, share cut at max_s, purity (speech time of a line's main speaker),
  mixed (lines where a second speaker talks ≥ 1 s), and recall (true speech inside a line).
whisper: each line transcribed like the live path (turbo, bard.asr.build_prompt: the table's
  names + the last 2 lines) with word times. gold = the adjudicated lines of a real session (asr_compare/asr_score), scored on the
  words whose middle falls inside each gold line's span, so any cutting can be scored;
  cr = word errors of whole Critical Role clips against their captions.

Config names: s<silence ms>p<pad ms>m<max s>, e.g. s400p80m12 (before) or s400p200m12 (now).
"""

import argparse
import json
import re
import statistics
import sys
import time
from collections import defaultdict
from itertools import product
from pathlib import Path

import numpy as np

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "src"))
sys.path.insert(0, str(Path(__file__).parent))

from asr_score import NAMES, normalize, wer  # noqa: E402

from bard import asr  # noqa: E402
from bard.segment import SegmentConfig, Segmenter  # noqa: E402

SR, FRAME = 16000, 512
TURBO = "mlx-community/whisper-large-v3-turbo"
GRID = {"silence_ms": (250, 400, 600), "pad_ms": (80, 200, 300), "max_s": (8.0, 12.0, 16.0)}


def name(cfg: SegmentConfig) -> str:
    return f"s{cfg.silence_ms}p{cfg.pad_ms}m{cfg.max_s:g}"


def parse(label: str) -> SegmentConfig:
    s, p, m = re.fullmatch(r"s(\d+)p(\d+)m([\d.]+)", label).groups()
    return SegmentConfig(silence_ms=int(s), pad_ms=int(p), max_s=float(m))


def audio_and_probs(path: Path, cache: Path) -> tuple[np.ndarray, np.ndarray]:
    import torch
    from silero_vad import load_silero_vad

    from bard.audio import load_audio

    audio = load_audio(path)
    audio = audio[: len(audio) // FRAME * FRAME]
    key = cache / f"{path.parent.name}_{path.stem}.probs.npy"
    if key.exists():
        return audio, np.load(key)
    model, t0 = load_silero_vad(), time.monotonic()
    with torch.no_grad():
        probs = np.array([model(torch.from_numpy(f), SR).item() for f in audio.reshape(-1, FRAME)], np.float32)
    cache.mkdir(parents=True, exist_ok=True)
    np.save(key, probs)
    print(f"  VAD {path.name}: {len(audio) / SR / 60:.0f} min in {time.monotonic() - t0:.0f}s", flush=True)
    return audio, probs


def cut(audio: np.ndarray, probs: np.ndarray, cfg: SegmentConfig) -> list[tuple[float, float]]:
    seg = Segmenter(SR, cfg)
    spans = [s for f, p in zip(audio.reshape(-1, FRAME), probs, strict=True) for s in seg.feed(f, float(p))]
    return [(s / SR, e / SR) for s, e in spans + seg.flush()]


def speaker_metrics(spans: list[tuple[float, float]], truth: list[dict], max_s: float) -> dict:
    main = total = mixed = 0.0
    for s, e in spans:
        by = defaultdict(float)
        for t in truth:
            by[t["speaker"]] += max(0.0, min(e, t["end"]) - max(s, t["start"]))
        talk = sorted(by.values(), reverse=True)
        if talk and talk[0] > 0:
            main, total = main + talk[0], total + sum(talk)
            mixed += len(talk) > 1 and talk[1] >= 1.0
    true_speech = sum(t["end"] - t["start"] for t in truth)
    lens = [e - s for s, e in spans]
    return {
        "lines": len(spans),
        "median_s": statistics.median(lens),
        "at_max": sum(x >= max_s - 0.05 for x in lens) / len(spans),
        "purity": main / total,
        "mixed": mixed / len(spans),
        "recall": total / true_speech,  # overlapping truth turns can push this a little past 100%
    }


def eval_clips(samples: Path, cache: Path) -> list[tuple[str, np.ndarray, np.ndarray, list[dict]]]:
    out = []
    for wav in sorted((samples / "eval").glob("*.wav")):
        truth = json.loads(wav.with_suffix(".truth.json").read_text())
        audio, probs = audio_and_probs(wav, cache)
        out.append((wav.stem, audio, probs, truth))
    return out


def sweep_vad(args) -> None:
    clips = eval_clips(args.samples, args.cache)
    rows = []
    for values in product(*GRID.values()):
        cfg = SegmentConfig(**dict(zip(GRID, values, strict=True)))
        per = [speaker_metrics(cut(a, p, cfg), t, cfg.max_s) for _, a, p, t in clips]
        rows.append((name(cfg), {k: statistics.mean(m[k] for m in per) for k in per[0]}))
    print(f"{'config':16} {'lines':>6} {'median':>7} {'at max':>7} {'purity':>7} {'mixed':>6} {'recall':>7}")
    for label, m in sorted(rows, key=lambda r: -r[1]["purity"]):
        print(
            f"{label:16} {m['lines']:6.0f} {m['median_s']:6.1f}s {m['at_max']:7.1%} {m['purity']:7.1%} "
            f"{m['mixed']:6.1%} {m['recall']:7.1%}"
        )
    (args.out / "vad.json").write_text(json.dumps(dict(rows), indent=1))


class Whisper:
    """The live transcription call (bard.asr) with word times, cached by clip + prompt."""

    def __init__(self, cache: Path):
        self.path = cache / "whisper.json"
        self.memo = json.loads(self.path.read_text()) if self.path.exists() else {}
        self.fresh = 0

    def _run(self, clip: np.ndarray, prompt: str | None) -> tuple[str, list]:
        import mlx_whisper

        r = mlx_whisper.transcribe(
            clip,
            path_or_hf_repo=TURBO,
            language="en",
            verbose=None,
            condition_on_previous_text=False,
            word_timestamps=True,
            hallucination_silence_threshold=1.0,
            initial_prompt=prompt,
        )
        segs = [s for s in r["segments"] if not (s.get("no_speech_prob", 0) > 0.6 and s.get("avg_logprob", 0) < -1.0)]
        text = " ".join(s["text"].strip() for s in segs).strip()
        if text.lower().strip(" .!?,") in asr.HALLUCINATIONS:
            return "", []
        if prompt and asr.suspicious(text, prompt):
            return self._run(clip, None)
        return text, [(w["start"], w["end"], w["word"]) for s in segs for w in s.get("words", [])]

    def __call__(self, key: str, clip: np.ndarray, prompt: str) -> tuple[str, list]:
        k = f"{key}|{prompt}"
        if k not in self.memo:
            self.memo[k] = self._run(clip, prompt)
            self.fresh += 1
            if self.fresh % 200 == 0:
                self.save()
        return self.memo[k]

    def save(self) -> None:
        self.path.write_text(json.dumps(self.memo))


def transcribe(whisper: Whisper, tag: str, audio, spans, names: list[str], wanted=None) -> list[tuple[float, float, str]]:
    """(start, end, word) in session time; like the live path, each line's prompt holds the names
    and the last two lines (bard.asr.build_prompt). wanted(i): only those lines (and the two before each, for the prompt)."""
    keep = (
        set(range(len(spans)))
        if wanted is None
        else {j for i in range(len(spans)) if wanted(i) for j in (i - 2, i - 1, i) if j >= 0}
    )
    recent: list[str] = []
    words = []
    for i in sorted(keep):
        s, e = spans[i]
        if i - 1 not in keep:
            recent = []  # a gap: no earlier line to prompt with (rare, and the same for every config)
        text, ws = whisper(f"{tag}:{s:.3f}:{e:.3f}", audio[int(s * SR) : int(e * SR)], asr.build_prompt(names, recent))
        words += [(s + a, s + b, w) for a, b, w in ws]
        recent = [*recent, text[:200]][-2:]
    return words


def gold_scores(words, gold: list[dict]) -> dict:
    errs, hits, total = [], 0, 0
    for g in gold:
        hyp = "".join(w for a, b, w in words if g["t_start"] <= (a + b) / 2 <= g["t_end"]).strip()
        errs.append(wer(g["text"], hyp))
        for n in NAMES:
            k = len(re.findall(rf"\b{n}\b", g["text"]))
            total += k
            hits += min(k, len(re.findall(rf"\b{n}\b", hyp)))
    return {"gold_wer": statistics.mean(errs), "names": hits / total}


def sweep_whisper(args) -> None:
    import jiwer

    whisper = Whisper(args.cache)
    sdir = args.data / "sessions" / args.session
    record = json.loads((sdir / "transcript.orig.json").read_text())
    spans_by_id = {m["id"]: m for m in record["messages"]}
    gold = [g | spans_by_id[g["id"]] | {"text": g["text"]} for g in map(json.loads, args.gold.open())]
    prompt = (args.gold.parent / "prompt.txt").read_text()  # "A D&D session. <names>. Initiative, …"
    names = prompt.split("A D&D session. ", 1)[1].split(". Initiative", 1)[0].split(", ")
    session_audio, session_probs = audio_and_probs(sdir / "audio.wav", args.cache)
    cr = [(n, a, p, t) for n, a, p, t in eval_clips(args.samples, args.cache) if any(x["text"] for x in t)]
    results = json.loads((args.out / "whisper.json").read_text()) if (args.out / "whisper.json").exists() else {}
    for label in args.configs:
        cfg, t0 = parse(label), time.monotonic()
        spans = cut(session_audio, session_probs, cfg)

        def near_gold(i, spans=spans):
            s, e = spans[i]
            return any(s < g["t_end"] and e > g["t_start"] for g in gold)

        m = gold_scores(transcribe(whisper, args.session, session_audio, spans, names, near_gold), gold)
        refs, hyps = [], []
        for clip, audio, probs, truth in cr:
            words = transcribe(whisper, clip, audio, cut(audio, probs, cfg), [])
            refs.append(normalize(" ".join(t["text"] for t in truth)))
            hyps.append(normalize("".join(w for _, _, w in words)))
        m["cr_wer"] = jiwer.wer(refs, hyps)
        results[label] = m
        whisper.save()
        print(
            f"{label:16} gold WER {m['gold_wer']:6.1%}  names {m['names']:6.1%}  CR WER {m['cr_wer']:6.1%}  ({time.monotonic() - t0:.0f}s)",
            flush=True,
        )
    (args.out / "whisper.json").write_text(json.dumps(results, indent=1))


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("what", choices=("vad", "whisper"))
    p.add_argument("configs", nargs="*")
    p.add_argument("--data", type=Path, default=BACKEND / "data")
    p.add_argument("--session", default="2026-10-08_193401-eebf")
    p.add_argument("--samples", type=Path, default=BACKEND / "samples", help="eval clips, asr_compare gold")
    p.add_argument("--gold", type=Path, help="gold.jsonl (asr_score); default <samples>/asr_compare/<session>/gold.jsonl")
    p.add_argument("--cache", type=Path, help="default <samples>/cache/segment")
    p.add_argument("--out", type=Path, help="default <samples>/segment_sweep")
    args = p.parse_args()
    args.gold = args.gold or args.samples / "asr_compare" / args.session / "gold.jsonl"
    args.cache = args.cache or args.samples / "cache" / "segment"
    args.out = args.out or args.samples / "segment_sweep"
    args.out.mkdir(parents=True, exist_ok=True)
    sweep_vad(args) if args.what == "vad" else sweep_whisper(args)


if __name__ == "__main__":
    main()
