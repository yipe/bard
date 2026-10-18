"""Can we tell "unenrolled voice" from "enrolled voice"? (feeds the voices panel)

Leave-one-out: for each clip and each enrolled speaker S, hide S's voiceprint.
S's segments are then "unknown"; everyone else's are "known". A good top-1
cosine threshold flags unknown segments without flagging known ones.

Usage: uv run spike/unknown.py
"""

import glob
import json
import sys
from collections import Counter
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import eval as ev  # noqa: E402
import run  # noqa: E402

from troubadour import voices  # noqa: E402

MIN_S = 1.5


def main() -> None:
    known, unknown = [], []  # top-1 cosine scores
    per_speaker: dict[str, Counter] = {}
    embedder = ev.get_pipeline()._embedding
    for truth_path in sorted(glob.glob(str(ev.EVAL / "*.truth.json"))):
        clip = Path(truth_path).name[: -len(".truth.json")]
        truth = json.loads(Path(truth_path).read_text())
        audio = run.load_audio(ev.EVAL / f"{clip}.wav")
        segments = json.loads(Path(glob.glob(str(ev.CACHE / f"{clip}.asr.*.json"))[0]).read_text())["value"]
        enrolled = ev.enrolled_voices(clip)
        names = list(enrolled)
        matrix = np.stack([enrolled[n] for n in names])
        for r in ev.word_runs(segments, []):
            if r["end"] - r["start"] < MIN_S:
                continue
            actual = Counter()
            for w in r["words"]:
                a = ev.true_speakers_at(truth, (w["start"] + w["end"]) / 2)
                if len(a) == 1 and "*" not in a:
                    actual[a.pop()] += 1
            if not actual or len(actual) > 1 or next(iter(actual)) not in enrolled:
                continue
            spk = next(iter(actual))
            v = voices.embed_span(embedder, audio, r["start"], r["end"])
            if v is None:
                continue
            scores = matrix @ v
            per_speaker.setdefault(f"{clip}:{spk}", Counter())[names[int(np.argmax(scores))] == spk] += 1
            own = names.index(spk)
            known.append(float(scores[own]) if np.argmax(scores) == own else None)
            unknown.append(float(np.delete(scores, own).max()))
    known = [k for k in known if k is not None]  # correctly identified known segments
    print(f"known (correct) segments: n={len(known)} median={np.median(known):.3f}")
    print(f"unknown segments:         n={len(unknown)} median={np.median(unknown):.3f}")
    for t in (0.3, 0.35, 0.4, 0.45, 0.5, 0.55, 0.6):
        flagged_unknown = np.mean(np.array(unknown) < t)
        flagged_known = np.mean(np.array(known) < t)
        print(f"threshold {t:.2f}: unknown caught {flagged_unknown:5.1%}   known wrongly flagged {flagged_known:5.1%}")
    print("\nper-speaker accuracy on segments >= 1.5 s (worst first):")
    rows = sorted(per_speaker.items(), key=lambda kv: kv[1][True] / sum(kv[1].values()))
    for key, c in rows[:12]:
        print(f"  {key:<24} {c[True] / sum(c.values()):6.1%}  (n={sum(c.values())})")


if __name__ == "__main__":
    main()
