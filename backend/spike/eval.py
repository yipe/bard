"""Score the pipeline on samples/eval/ (built by spike/dataset.py).

Usage: uv run spike/eval.py --tag NAME [knobs...]

Metrics per clip:
- wer      word error rate vs captions (CR only; captions are lightly edited,
           so this is a relative measure, not an absolute one)
- sa_maj   speaker accuracy of words, each cluster mapped to its majority
           true speaker (splits are free: one person's NPC voice may be its
           own cluster, which the voices panel maps back)
- sa_1to1  same, but one-to-one cluster↔speaker mapping (Hungarian); punishes
           both splits and merges
- clusters found vs true speakers with ≥ 10 s of speech
- der      diarization error rate (pyannote.metrics; AMI collar 0, CR 0.5 s
           because caption timing is coarse)

ASR and diarization outputs are cached in samples/cache/, so changing only
assignment knobs reruns in seconds. Each run appends to samples/results.jsonl.
"""

import argparse
import hashlib
import json
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import run  # noqa: E402

from bard import voices  # noqa: E402

EVAL = Path("samples/eval")
CACHE = Path("samples/cache")
RESULTS = Path("samples/results.jsonl")
TRUTH_TOLERANCE_S = 0.2
MIN_TRUE_SPEAKER_S = 10.0

# What a table would type into the setup flow: players and their characters, per show prefix
# (e.g. {"c2": "Name, Name. Character, Character."}). Kept out of the repo with the clips.
PROMPTS_PATH = EVAL / "prompts.json"


def cached(kind: str, key: dict, compute):
    digest = hashlib.sha1(json.dumps(key, sort_keys=True).encode()).hexdigest()[:12]
    path = CACHE / f"{key['clip']}.{kind}.{digest}.json"
    if path.exists():
        return json.loads(path.read_text())
    CACHE.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    value = compute()
    path.write_text(json.dumps({"value": value, "seconds": time.perf_counter() - t0}))
    return json.loads(path.read_text())


def true_speakers_at(truth: list[dict], t: float) -> set[str]:
    return {u["speaker"] for u in truth if u["start"] - TRUTH_TOLERANCE_S <= t <= u["end"] + TRUTH_TOLERANCE_S}


def speaker_accuracy(words: list[dict], labels: list[str], truth: list[dict]) -> dict:
    from scipy.optimize import linear_sum_assignment

    pairs = []
    for w, label in zip(words, labels):
        actual = true_speakers_at(truth, (w["start"] + w["end"]) / 2)
        if len(actual) == 1 and "*" not in actual:
            pairs.append((label, actual.pop()))
    if not pairs:
        return {}
    counts: dict[str, Counter] = defaultdict(Counter)
    for label, actual in pairs:
        counts[label][actual] += 1
    majority = sum(c.most_common(1)[0][1] for c in counts.values())
    clusters = sorted(counts)
    speakers = sorted({a for _, a in pairs})
    matrix = np.array([[counts[c][s] for s in speakers] for c in clusters])
    rows, cols = linear_sum_assignment(-matrix)
    one_to_one = int(matrix[rows, cols].sum())
    # Direct identity accuracy; only meaningful when labels are enrolled names.
    direct = sum(label == actual for label, actual in pairs)
    named = any(label in speakers for label in clusters)
    return {
        "sa_maj": majority / len(pairs),
        "sa_1to1": one_to_one / len(pairs),
        "sa_id": direct / len(pairs) if named else None,
        "scored_words": len(pairs),
    }


def text_metrics(segments: list[dict], truth: list[dict]) -> dict:
    """WER plus num_recall: share of caption numbers ("12 damage", "AC 15")
    that Whisper transcribed exactly. Numbers are what the mechanics pass needs."""
    import jiwer
    from whisper_normalizer.english import EnglishTextNormalizer

    ref = " ".join(u["text"] for u in truth if u["text"])
    if not ref:
        return {"wer": None}
    norm = EnglishTextNormalizer()
    out = jiwer.process_words(norm(ref), norm(" ".join(s["text"] for s in segments)))
    ref_words = out.references[0]
    numbers = sum(w.isdigit() for w in ref_words)
    hits = sum(
        ref_words[i].isdigit()
        for chunk in out.alignments[0]
        if chunk.type == "equal"
        for i in range(chunk.ref_start_idx, chunk.ref_end_idx)
    )
    return {"wer": out.wer, "num_recall": hits / numbers if numbers else None}


def der(turns: list, truth: list[dict], collar: float) -> float:
    from pyannote.core import Annotation, Segment
    from pyannote.metrics.diarization import DiarizationErrorRate

    ref, hyp = Annotation(), Annotation()
    for i, u in enumerate(truth):
        if u["speaker"] != "*" and u["end"] > u["start"]:
            ref[Segment(u["start"], u["end"]), i] = u["speaker"]
    for i, (start, end, spk) in enumerate(turns):
        hyp[Segment(start, end), i] = spk
    return DiarizationErrorRate(collar=collar)(ref, hyp)


def parse_kv(items: list[str]) -> dict:
    out = {}
    for item in items:
        key, value = item.split("=", 1)
        try:
            out[key] = json.loads(value)
        except json.JSONDecodeError:
            out[key] = value
    return out


_pipeline = None


def get_pipeline():
    global _pipeline
    if _pipeline is None:
        _pipeline = run.load_diarizer()
    return _pipeline


def enrolled_voices(name: str, kind: str = "enroll") -> dict[str, np.ndarray]:
    """Voiceprint per enrolled speaker, from <clip>.<kind>.json spans."""
    spec = json.loads((EVAL / f"{name}.{kind}.json").read_text())

    def compute():
        embedder = get_pipeline()._embedding
        out = {}
        for spk, spans in spec["speakers"].items():
            chunks = [run.load_audio(spec["source"], s, e) for s, e in spans]
            offsets = np.cumsum([0] + [len(c) for c in chunks]) / run.SR
            v = voices.centroid(embedder, np.concatenate(chunks), list(zip(offsets[:-1], offsets[1:])))
            if v is not None:
                out[spk] = v.tolist()
        return out

    value = cached("enroll", {"clip": name, "spans": spec}, compute)["value"]
    return {spk: np.array(v) for spk, v in value.items()}


PANEL_MIN_S = 5.0  # a cluster needs this much speech to show up in the voices panel


def named_cluster_voices(audio: np.ndarray, turns: list, truth: list[dict], minutes: float) -> dict[str, np.ndarray]:
    """Option B: no training; the user names voices in the panel during play.

    Takes pyannote clusters from the first `minutes`, names each after the
    person it mostly contains (stands in for the user's tap), and merges
    clusters given the same name. People silent so far get no voiceprint.
    """
    horizon = minutes * 60
    by_cluster = defaultdict(list)
    for s, e, c in turns:
        if s < horizon and min(e, horizon) - s >= 1.0:
            by_cluster[c].append((s, min(e, horizon)))
    spans_by_speaker = defaultdict(list)
    for spans in by_cluster.values():
        if sum(e - s for s, e in spans) < PANEL_MIN_S:
            continue
        votes = Counter()
        for s, e in spans:
            for u in truth:
                if u["speaker"] != "*":
                    votes[u["speaker"]] += max(0.0, min(e, u["end"]) - max(s, u["start"]))
        if votes:
            spans_by_speaker[votes.most_common(1)[0][0]].extend(spans)
    embedder = get_pipeline()._embedding
    out = {}
    for spk, spans in spans_by_speaker.items():
        v = voices.centroid(embedder, audio, spans)
        if v is not None:
            out[spk] = v
    return out


def word_runs(segments: list[dict], turns: list, split_gap: float = 0.0) -> list[dict]:
    """Consecutive words in one Whisper segment that share a diarization label.

    With no turns, each segment is one run (the live path: no diarization yet).
    split_gap > 0 also starts a new run at any pause between words that long,
    approximating the utterances a live VAD would cut.
    """
    out = []
    for i, seg in enumerate(segments):
        if turns:
            words, labels = run.label_words([seg], turns)
        else:
            words = seg.get("words") or []
            labels = [None] * len(words)
        for w, label in zip(words, labels):
            last = out[-1] if out else None
            paused = split_gap > 0 and last is not None and w["start"] - last["end"] >= split_gap
            if last and last["seg"] == i and last["cluster"] == label and not paused:
                last["words"].append(w)
                last["end"] = w["end"]
            else:
                out.append({"seg": i, "cluster": label, "words": [w], "start": w["start"], "end": w["end"]})
    return out


DISCOVER_JOIN = 0.5  # cosine to an unknown group's centroid to join it (same-speaker median ≈ 0.65, different ≈ 0.25)


def assign_discover(audio: np.ndarray, segments: list[dict], truth: list[dict], opts) -> tuple[list, list, int]:
    """Option B, continuous: start with no voices; group unmatched segments as
    they arrive; once a group has opts.panel_s of speech it appears in the
    voices panel and the user names it (simulated: majority true speaker). Its
    past segments are relabeled. A name given twice merges into the existing voice.
    opts.relabel: finally re-score every segment against the matured voiceprints
    (the background refine pass relabeling old messages).
    """
    matcher = voices.VoiceMatcher(get_pipeline()._embedding, pad_to=opts.pad_to, adapt_weight=opts.adapt)
    runs = word_runs(segments, [], opts.split_gap)
    labels_by_run = ["?"] * len(runs)
    vecs: list[np.ndarray | None] = [None] * len(runs)
    groups: list[dict] = []  # {"sum": vec, "runs": [idx], "seconds": float}
    taps = 0  # voice cards the user had to name

    def true_name(run_ids: list[int]) -> str | None:
        votes = Counter()
        for i in run_ids:
            for w in runs[i]["words"]:
                actual = true_speakers_at(truth, (w["start"] + w["end"]) / 2)
                if len(actual) == 1 and "*" not in actual:
                    votes[actual.pop()] += 1
        return votes.most_common(1)[0][0] if votes else None

    def unit(x: np.ndarray) -> np.ndarray:
        return x / np.linalg.norm(x)

    for i, r in enumerate(runs):
        duration = r["end"] - r["start"]
        v = vecs[i] = matcher.embed(audio, r["start"], r["end"])
        if v is None:
            labels_by_run[i] = labels_by_run[i - 1] if i else "?"
            continue
        name, _, unknown = matcher.classify(v, duration)
        if not unknown:
            labels_by_run[i] = name
            continue
        if duration < opts.group_min_s:
            labels_by_run[i] = name if name is not None else (labels_by_run[i - 1] if i else "?")
            continue
        # Unmatched: join the closest unknown group, or start one.
        best = max(groups, key=lambda g: float(unit(g["sum"]) @ v), default=None)
        if best is not None and float(unit(best["sum"]) @ v) >= DISCOVER_JOIN:
            best["sum"] += v * duration
            best["runs"].append(i)
            best["seconds"] += duration
        else:
            best = {"sum": v * duration, "runs": [i], "seconds": duration}
            groups.append(best)
        if best["seconds"] >= opts.panel_s:
            groups = [g for g in groups if g is not best]
            label = true_name(best["runs"])
            if label is None:
                continue
            matcher.add(label, unit(best["sum"]))
            taps += 1
            for k in best["runs"]:
                labels_by_run[k] = label

    if opts.relabel and matcher.names:
        for i, v in enumerate(vecs):
            if v is not None:
                labels_by_run[i] = matcher.names[int(np.argmax(matcher.scores(v)))]

    words, labels = [], []
    for r, label in zip(runs, labels_by_run):
        words.extend(r["words"])
        labels.extend([label] * len(r["words"]))
    return words, labels, taps


def assign_enrolled(
    name: str, audio: np.ndarray, segments: list[dict], turns: list, truth: list[dict], opts
) -> tuple[list, list]:
    """Label every word with an enrolled speaker name (see --assign)."""
    if opts.voices == "named":
        known = named_cluster_voices(audio, turns, truth, opts.name_minutes)
    else:
        known = enrolled_voices(name, opts.enroll)
    matcher = voices.VoiceMatcher(get_pipeline()._embedding, known, pad_to=opts.pad_to, adapt_weight=opts.adapt)
    names = matcher.names

    runs = word_runs(segments, [] if opts.assign == "seg" else turns, opts.split_gap)
    cluster_scores = {}
    if opts.assign in ("map", "hybrid"):
        for cluster in {r["cluster"] for r in runs}:
            spans = sorted(((s, e) for s, e, c in turns if c == cluster and e - s >= 1.0), key=lambda x: x[0] - x[1])[:40]
            v = voices.centroid(matcher.embedder, audio, spans)
            cluster_scores[cluster] = matcher.scores(v) if v is not None else np.zeros(len(names))

    words, labels, previous = [], [], names[0] if names else "?"
    for r in runs:
        duration = r["end"] - r["start"]
        v = None if opts.assign == "map" else matcher.embed(audio, r["start"], r["end"])
        own = matcher.scores(v) if v is not None else None
        if opts.assign == "map":
            scores = cluster_scores[r["cluster"]]
        elif opts.assign == "seg":
            scores = own
        else:
            prior = cluster_scores[r["cluster"]]
            # Short runs embed poorly: shift trust toward the cluster's voiceprint.
            w = opts.hybrid_w * min(1.0, duration / opts.hybrid_d) if opts.hybrid_d > 0 else opts.hybrid_w
            scores = prior if own is None else w * own + (1 - w) * prior
        label = names[int(np.argmax(scores))] if scores is not None and len(scores) else previous
        if v is not None and label in names:
            matcher.observe(label, v, duration)
        previous = label
        words.extend(r["words"])
        labels.extend([label] * len(r["words"]))
    return words, labels


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", required=True)
    ap.add_argument("--clips", help="comma-separated clip names (default: all)")
    ap.add_argument("--prompt", choices=["none", "cast"], default="none", help="Whisper initial prompt (measured: no gain)")
    ap.add_argument("--asr", nargs="*", default=[], metavar="KEY=VALUE", help="mlx_whisper.transcribe overrides")
    ap.add_argument("--count", choices=["auto", "oracle", "range"], default="auto", help="speaker count given to diarizer")
    ap.add_argument("--diar", nargs="*", default=[], metavar="KEY=VALUE", help="pipeline params, e.g. clustering.threshold=0.5")
    ap.add_argument("--min-split", type=float, default=run.MIN_SPLIT_S)
    ap.add_argument(
        "--assign",
        choices=["diar", "map", "seg", "hybrid"],
        default="seg",
        help="diar: raw clusters; map: clusters→nearest enrolled voice; seg: classify each Whisper "
        "segment against enrolled voices (no diarization, like the live path); hybrid: classify "
        "diarization-split runs, blended with their cluster's voiceprint",
    )
    ap.add_argument("--hybrid-w", type=float, default=0.5, help="hybrid: weight of the run's own embedding")
    ap.add_argument("--hybrid-d", type=float, default=0.0, help="hybrid: scale weight by min(1, duration/D); 0 = off")
    ap.add_argument("--pad-to", type=float, default=1.5, help="widen runs shorter than this (s) before embedding")
    ap.add_argument(
        "--adapt", type=float, default=5.0, help="update voiceprints from confident segments; enrollment weight K (0 = off)"
    )
    ap.add_argument("--enroll", default="enroll", choices=["enroll", "enroll15", "enroll60"], help="enrollment set (30/15/60 s)")
    ap.add_argument("--split-gap", type=float, default=0.0, help="also split runs at word pauses >= this (s); 0 = off")
    ap.add_argument(
        "--voices",
        choices=["trained", "named", "discover"],
        default="trained",
        help="trained: setup recordings (enroll*.json); named: clusters from the first --name-minutes, "
        "named by the user; discover: start with no voices, new voices surface as they talk and get named",
    )
    ap.add_argument("--name-minutes", type=float, default=3.0)
    ap.add_argument("--panel-s", type=float, default=3.0, help="discover: speech a new voice needs before it is shown/named")
    ap.add_argument("--group-min-s", type=float, default=0.0, help="discover: only segments this long can form new voices")
    ap.add_argument(
        "--relabel",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="discover: final pass re-scores all segments with matured voices",
    )
    args = ap.parse_args()

    asr_opts = parse_kv(args.asr)
    diar_params = parse_kv(args.diar)
    run.MIN_SPLIT_S = args.min_split
    names = args.clips.split(",") if args.clips else sorted(p.name[: -len(".truth.json")] for p in EVAL.glob("*.truth.json"))

    rows = {}
    for name in names:
        truth = json.loads((EVAL / f"{name}.truth.json").read_text())
        audio = run.load_audio(EVAL / f"{name}.wav")
        duration = len(audio) / run.SR
        talk = Counter()
        for u in truth:
            talk[u["speaker"]] += u["end"] - u["start"]
        n_true = sum(1 for s, t in talk.items() if s != "*" and t >= MIN_TRUE_SPEAKER_S)

        prompts = json.loads(PROMPTS_PATH.read_text()) if args.prompt == "cast" else {}
        prompt = prompts.get(name[:2])
        asr = cached("asr", {"clip": name, "prompt": prompt, **asr_opts}, lambda: run.transcribe(audio, prompt, **asr_opts))
        segments = asr["value"]

        count_kwargs = {"auto": {}, "oracle": {"num_speakers": n_true}, "range": {"min_speakers": 2, "max_speakers": n_true + 2}}[
            args.count
        ]

        def compute_diar():
            pipeline = get_pipeline()
            params = pipeline.parameters(instantiated=True)
            for key, value in diar_params.items():
                section, field = key.split(".")
                params[section][field] = value
            pipeline.instantiate(params)
            return run.diarize(pipeline, audio, **count_kwargs)

        dia = cached("diar", {"clip": name, "count": count_kwargs, **diar_params}, compute_diar)
        turns = [tuple(t) for t in dia["value"]]

        taps = None
        if args.assign == "diar":
            words, labels = run.label_words(segments, turns)
        elif args.voices == "discover":
            words, labels, taps = assign_discover(audio, segments, truth, args)
        else:
            words, labels = assign_enrolled(name, audio, segments, turns, truth, args)

        row = {
            **text_metrics(segments, truth),
            **speaker_accuracy(words, labels, truth),
            "clusters": len(set(labels)),
            "true": n_true,
            "der": der(turns, truth, collar=0.0 if name.startswith("ami") else 0.5),
            "rtf_asr": asr["seconds"] / duration,
            "rtf_diar": dia["seconds"] / duration,
            "taps": taps,
        }
        rows[name] = row

    def fmt(v, pct=True):
        return "   -  " if v is None else (f"{v:6.1%}" if pct else f"{v:6.3f}")

    print(f"\n{args.tag}")
    keys = ("wer", "num_recall", "sa_maj", "sa_1to1", "sa_id", "der")
    print(
        f"{'clip':<13} {'WER':>6} {'NUM':>6} {'SAmaj':>6} {'SA1:1':>6} {'ID':>6} {'DER':>6} {'clus':>5} {'rtfA':>6} {'rtfD':>6}"
    )
    for name, r in rows.items():
        print(
            f"{name:<13} " + " ".join(fmt(r.get(k)) for k in keys) + " "
            f"{r['clusters']:>2}/{r['true']:<2} {fmt(r['rtf_asr'], False)} {fmt(r['rtf_diar'], False)}"
            + (f"  taps={r['taps']}" if r["taps"] is not None else "")
        )
    summary = {}
    for group, prefix in (("cr", "c"), ("ami", "a")):
        group_rows = [r for n, r in rows.items() if n[0] == prefix]
        if group_rows:
            summary[group] = {
                k: float(np.mean([r[k] for r in group_rows if r.get(k) is not None]))
                for k in keys
                if any(r.get(k) is not None for r in group_rows)
            }
            print(f"{'mean ' + group:<13} " + " ".join(fmt(summary[group].get(k)) for k in keys))
    with RESULTS.open("a") as f:
        f.write(json.dumps({"tag": args.tag, "args": vars(args), "clips": rows, "summary": summary}) + "\n")


if __name__ == "__main__":
    main()
