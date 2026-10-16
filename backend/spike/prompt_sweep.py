"""Whisper prompt with a big glossary: the old first-40-names prompt vs bard.asr.build_prompt.

    uv run --group spike spike/prompt_sweep.py --data DIR --samples DIR --extra-vault DIR

The adjudicated session (asr_compare gold) had a 5-name glossary, which always fit. To see a
big campaign, its glossary is padded with another campaign's names (--extra-vault; none of
them are said in this session), in the order the engine uses (PCs, NPCs, the rest). Each gold
line is transcribed once per config (bard.asr.transcribe, turbo) on its live span; the lines
before it are the live transcript's, except the last two: transcribed under the config itself.
"""

import argparse
import json
import random
import re
import statistics
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "src"))
sys.path.insert(0, str(Path(__file__).parent))

from asr_score import NAMES, wer  # noqa: E402

from bard import asr  # noqa: E402
from bard.audio import load_audio  # noqa: E402
from bard.campaign import Vault  # noqa: E402

SR = 16000
VAULT = {"pc": ["Auggie", "Elara", "Kessia"], "npc": ["Delara", "Magenta"]}


def table_names(record: dict) -> list[str]:
    """What Setup and the voices panel knew: characters first, then the people."""
    chars = [p["character"] for p in record["players"] if p.get("character") and not p.get("is_gm")]
    chars += [v["character"] for v in record.get("voices", []) if v.get("character")]
    return list(dict.fromkeys(chars + [p["name"] for p in record["players"]]))


def old_prompt(names: list[str], history: list[str]) -> str:
    """The live prompt before build_prompt (Whisper itself then keeps only its last 223 tokens)."""
    base = f"A D&D session. {', '.join(names[:40])}. Initiative, perception check, saving throw."
    return " ".join([base, *(t[:200] for t in history[-2:])])


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--data", type=Path, required=True)
    p.add_argument("--samples", type=Path, required=True)
    p.add_argument("--extra-vault", type=Path, required=True)
    p.add_argument("--session", default="2026-10-08_193401-eebf")
    p.add_argument("--out", type=Path, default=Path("/tmp/bard_prompt.json"))
    args = p.parse_args()
    sdir = args.data / "sessions" / args.session
    record = json.loads((sdir / "transcript.orig.json").read_text())
    table = table_names(record)

    extra = Vault(args.extra_vault).glossary()
    by_type = {"pc": [], "npc": [], "other": []}
    for e in extra:
        by_type[e.type if e.type in ("pc", "npc") else "other"].append(e.name)
    rng = random.Random(0)
    npcs = VAULT["npc"] + by_type["npc"]
    rng.shuffle(npcs)  # Delara and Magenta land somewhere among the other NPCs
    vault = VAULT["pc"] + by_type["pc"] + npcs + by_type["other"]
    small = list(dict.fromkeys(table + VAULT["pc"] + VAULT["npc"]))
    big = list(dict.fromkeys(table + vault))
    print(f"glossary: {len(small)} names, padded to {len(big)}")

    msgs = sorted(record["messages"], key=lambda m: m["t_start"])
    index = {m["id"]: i for i, m in enumerate(msgs)}
    gold = [json.loads(line) for line in (args.samples / "asr_compare" / args.session / "gold.jsonl").open()]
    audio = load_audio(sdir / "audio.wav")
    history_of = lambda gid: [m["text"] for m in msgs[: index[gid]]]  # noqa: E731

    def new(names, context_tokens, limit):
        def make(h):
            ordered = asr.prompt_names(table, names, h[-100:])[:limit]
            return asr.build_prompt(ordered, h[-2:], context_tokens)

        return make

    configs = {f"new, ctx {c}, names {k}": new(vault, c, k) for c in (0, 60) for k in (12, 20)}
    results, hyps = {}, {}
    for label, make in configs.items():
        errs, hits, total, tokens = [], 0, 0, []
        for g in gold:
            i = index[g["id"]]
            m = msgs[i]
            # The two lines before as this config would have heard them (live, a line's prompt
            # holds the lines Whisper itself just wrote, spelled by the same prompt).
            h = history_of(g["id"])
            for j in (i - 2, i - 1):
                if j >= 0:
                    prev = msgs[j]
                    clip = audio[int(prev["t_start"] * SR) : int(prev["t_end"] * SR)]
                    h[j] = asr.transcribe(clip, make(h[:j])) or h[j]
            pr = make(h)
            tokens.append(asr.prompt_tokens(pr))
            hyp = asr.transcribe(audio[int(m["t_start"] * SR) : int(m["t_end"] * SR)], pr)
            hyps.setdefault(label, []).append({"id": g["id"], "gold": g["text"], "hyp": hyp, "prompt": pr})
            errs.append(wer(g["text"], hyp))
            for n in NAMES:
                k = len(re.findall(rf"\b{n}\b", g["text"]))
                total += k
                hits += min(k, len(re.findall(rf"\b{n}\b", hyp)))
        results[label] = {
            "wer": statistics.mean(errs),
            "names": hits / total,
            "over_window": sum(t > 223 for t in tokens) / len(tokens),
        }
        r = results[label]
        print(
            f"{label:28} WER {r['wer']:6.1%}  names {r['names']:6.1%}  prompts over 223 tokens {r['over_window']:5.0%}",
            flush=True,
        )
    args.out.write_text(json.dumps({"scores": results, "lines": hyps}, indent=1))


if __name__ == "__main__":
    main()
