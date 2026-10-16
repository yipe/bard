"""Score the asr_compare configs against an adjudicated gold sample.

    uv run --group spike spike/asr_score.py SESSION sample   # writes adjudicate.md (150 lines to judge)
    uv run --group spike spike/asr_score.py SESSION score    # needs gold.jsonl {id, text}

The gold text is a best guess at what was said, made by reading every config's hypothesis in
context (nobody listened). It favours whichever config is right most often, so read the scores
as a ranking, not absolute accuracy.
"""

import json
import random
import re
import sys
from itertools import combinations
from pathlib import Path

import jiwer

BACKEND = Path(__file__).resolve().parents[1]
NAMES = ["Elara", "Delara", "Auggie", "Kessia", "Magenta"]  # Delara: an NPC, not Elara (the study first conflated them)
NUM = {
    w: str(i)
    for i, w in enumerate(
        "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty".split()
    )
}


def normalize(text: str) -> str:
    t = text.lower().replace("-", " ").replace("alright", "all right")
    t = re.sub(r"[^a-z0-9' ]+", " ", t)
    words = [NUM.get(w, w) for w in t.split()]
    words = [w for w in words if w not in ("uh", "um", "hmm", "mm", "uh-huh")]
    return " ".join(words)


def load(out: Path) -> dict[str, dict[str, str]]:
    configs = {}
    for path in sorted(out.glob("*.jsonl")):
        if path.stem == "gold":
            continue
        configs[path.stem] = {r["id"]: r["text"] for r in map(json.loads, path.open())}
    return configs


def wer(ref: str, hyp: str) -> float:
    r, h = normalize(ref), normalize(hyp)
    if not r:
        return 0.0 if not h else 1.0
    return jiwer.wer(r, h)


def sample(session: str, out: Path) -> None:
    configs = load(out)
    record = json.loads((BACKEND / "data" / "sessions" / session / "transcript.json").read_text())
    msgs = sorted(record["messages"], key=lambda m: m["t_start"])
    idx = {m["id"]: i for i, m in enumerate(msgs)}
    ids = [m["id"] for m in msgs if any(c.get(m["id"]) for c in configs.values())]
    rng = random.Random(7)

    def disagreement(mid):
        hyps = [normalize(c.get(mid, "")) for c in configs.values()]
        pairs = [jiwer.wer(a, b) if a else (0.0 if not b else 1.0) for a, b in combinations(hyps, 2) if a or b]
        return sum(pairs) / len(pairs) if pairs else 0.0

    variants = re.compile(
        r"\b("
        + "|".join(["el+ara", "d[ai]l+ara", "de?l[ao]ra", "lara", "aug+ie", "argy", "k[ae]s+ia", "cassia", "magent[ao]"])
        + r")\b",
        re.I,
    )
    named = [i for i in ids if any(variants.search(c.get(i, "")) for c in configs.values())]
    saved = out / "sample.json"
    if saved.exists():  # same lines every time, so configs that finish later join the same gold
        pick = json.loads(saved.read_text())
    else:
        pick = []
        for pool, n in ((named, 40), (sorted(ids, key=disagreement, reverse=True)[:300], 50), (ids, 60)):
            pool = [i for i in pool if i not in pick]
            pick += rng.sample(pool, min(n, len(pool)))
    pick.sort(key=idx.get)
    lines = ["# Adjudicate: write what was most likely said\n"]
    for mid in pick:
        i = idx[mid]
        m = msgs[i]
        prev = msgs[i - 1]["text"] if i else ""
        nxt = msgs[i + 1]["text"] if i + 1 < len(msgs) else ""
        groups: dict[str, list[str]] = {}
        for name, c in configs.items():
            groups.setdefault(c.get(mid, "").strip(), []).append(name)
        lines.append(f"## {mid} {m['t_start']:.0f}s {m['speaker']}\n< {prev[:140]}\n> {nxt[:140]}")
        lines += [f"  {','.join(v):18} | {k}" for k, v in sorted(groups.items(), key=lambda kv: -len(kv[1]))]
        lines.append("")
    (out / "adjudicate.md").write_text("\n".join(lines))
    (out / "sample.json").write_text(json.dumps(pick))
    print(f"{len(pick)} lines → {out / 'adjudicate.md'}")


def score(out: Path) -> None:
    configs = load(out)
    gold = {r["id"]: r["text"] for r in map(json.loads, (out / "gold.jsonl").open())}
    print(f"{len(gold)} gold lines")
    rows = []
    for name, c in configs.items():
        errs = [wer(g, c.get(mid, "")) for mid, g in gold.items()]
        # Name accuracy: each glossary name in the gold must appear spelled right.
        hits = total = 0
        for mid, g in gold.items():
            for n in NAMES:
                k = len(re.findall(rf"\b{n}\b", g))
                total += k
                hits += min(k, len(re.findall(rf"\b{n}\b", c.get(mid, ""))))
        empties = sum(1 for mid, g in gold.items() if normalize(g) and not normalize(c.get(mid, "")))
        rows.append((name, sum(errs) / len(errs), hits / total if total else float("nan"), empties, total))
    print(f"{'config':6} {'WER':>6} {'names':>7} {'lost':>5}")
    for name, w, n, e, t in sorted(rows, key=lambda r: r[1]):
        print(f"{name:6} {w:6.1%} {n:7.1%} {e:5d}   (names in gold: {t})")
    json.dump(
        [dict(zip(("config", "wer", "names", "lost", "name_total"), r, strict=True)) for r in rows],
        (out / "scores.json").open("w"),
        indent=1,
    )


if __name__ == "__main__":
    session, what = sys.argv[1], sys.argv[2]
    out = BACKEND / "samples" / "asr_compare" / session
    sample(session, out) if what == "sample" else score(out)
