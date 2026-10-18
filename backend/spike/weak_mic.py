"""Audio clean-up and junk filters on an adjudicated session (PLAN.md, "Weak mics").

    uv run --with noisereduce spike/weak_mic.py SESSION CAMPAIGN --data DIR --samples DIR raw,norm,hp,...

Each gold line (samples/asr_compare/<session>/gold.jsonl) is transcribed with the live prompt
(engine logic: table names, names said lately, nicknames, the last two lines as this variant
heard them, none for clips under CONTEXT_MIN_S). Whisper's temperature fallback is seeded, so
variants differ only by what they change. Variants combine: hp (100 Hz high-pass), nr
(noisereduce), norm (speech level to -20 dBFS), nofilter (no script / words-per-second filters).
Writes <samples>/asr_compare/<session>/weak_mic.json: {variant: {id: text}}; score with
asr_score's wer().
"""

import argparse
import json
import sys
from pathlib import Path

import mlx.core as mx
import numpy as np
from scipy.signal import butter, sosfilt

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "src"))

from troubadour import asr  # noqa: E402
from troubadour.audio import load_audio  # noqa: E402
from troubadour.campaign import Vault  # noqa: E402
from troubadour.engine import CONTEXT_MIN_S, PROMPT_MENTION_LINES, PROMPT_NAMES  # noqa: E402

SR = 16000
HP = butter(4, 100, "highpass", fs=SR, output="sos")


def level_db(x: np.ndarray) -> float:
    frames = x[: len(x) // 800 * 800].reshape(-1, 800)  # 50 ms
    rms = np.sqrt(np.mean(frames**2, axis=1) + 1e-12) if len(frames) else np.array([1e-6])
    return float(20 * np.log10(np.percentile(rms, 90)))


def clean(x: np.ndarray, how: str) -> np.ndarray:
    if "hp" in how:
        x = sosfilt(HP, x)
    if "nr" in how:
        import noisereduce

        x = noisereduce.reduce_noise(y=x, sr=SR, stationary=False, prop_decrease=0.8)
    if "norm" in how:  # speech level to -20 dBFS, peaks kept under 0.99
        x = x * min(10 ** ((-20 - level_db(x)) / 20), 0.99 / (np.abs(x).max() + 1e-9))
    return np.asarray(x, np.float32)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("session")
    p.add_argument("campaign")
    p.add_argument("variants")
    p.add_argument("--data", type=Path, default=BACKEND / "data")
    p.add_argument("--samples", type=Path, default=BACKEND / "samples")
    args = p.parse_args()

    out_dir = args.samples / "asr_compare" / args.session
    gold_ids = [json.loads(line)["id"] for line in (out_dir / "gold.jsonl").open()]
    sdir = args.data / "sessions" / args.session
    msgs = sorted(json.loads((sdir / "transcript.json").read_text())["messages"], key=lambda m: m["t_start"])
    index = {m["id"]: i for i, m in enumerate(msgs)}
    order = {"pc": 0, "npc": 1}
    entries = Vault(args.data / "campaigns" / args.campaign).glossary()
    vault = [e.name for e in sorted(entries, key=lambda e: order.get(e.type, 2))]
    nicknames = [a for e in entries for a in e.aliases]
    state = json.loads((sdir / "setup" / "end.json").read_text())
    camp = next(c for c in state["campaigns"] if c["id"] == state["campaign"])
    people = {x["id"]: x for x in state["players"]}
    table = [r["character"] for r in camp["roster"] if r["character"] and not r["is_gm"]]
    table += [v["character"] for v in state["voices"] if v.get("status") == "named" and v.get("character")]
    table += [people[r["player_id"]]["name"] for r in camp["roster"]]
    audio = load_audio(sdir / "audio.wav")

    def make(history: list[str], seconds: float) -> str:
        names = asr.prompt_names(table, vault, history[-PROMPT_MENTION_LINES:], nicknames)[:PROMPT_NAMES]
        return asr.build_prompt(names, history[-2:] if seconds >= CONTEXT_MIN_S else [])

    real_run, real_foreign, real_rate = asr._run, asr._foreign, asr.MAX_WORDS_PER_S

    def seeded_run(*a, **k):
        mx.random.seed(0)  # the temperature fallback samples: same clip, same answer
        return real_run(*a, **k)

    asr._run = seeded_run
    results = {}
    for how in args.variants.split(","):
        off = "nofilter" in how
        asr._foreign = (lambda t: False) if off else real_foreign
        asr.MAX_WORDS_PER_S = 1e9 if off else real_rate

        def say(m: dict, history: list[str], how=how) -> str:
            clip = clean(audio[int(m["t_start"] * SR) : int(m["t_end"] * SR)], how)
            return asr.transcribe(clip, make(history, m["t_end"] - m["t_start"]))

        got = {}
        for gid in gold_ids:
            i = index[gid]
            h = [m["text"] for m in msgs[:i]]
            for j in (i - 2, i - 1):  # the two lines before, as this variant would have heard them
                if j >= 0:
                    h[j] = say(msgs[j], h[:j]) or h[j]
            got[gid] = say(msgs[i], h)
        results[how] = got
        print(how, "done", flush=True)
    (out_dir / "weak_mic.json").write_text(json.dumps(results))


if __name__ == "__main__":
    main()
