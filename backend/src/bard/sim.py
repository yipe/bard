"""`bard-sim SESSION_ID`: re-run a saved session through the current pipeline, headless.

The session's audio (its recording, or the sample it replayed) plays through the same engine as
the server, against the setup the session had (players, voiceprints, training clips), in a
throwaway data dir, so nothing in data/ changes except the output:

    data/sessions/<id>/sims/<run>/transcript.{json,md}, compare.json

`compare()` reports how the re-run differs from the original: speaker agreement over time and
text similarity.
"""

import argparse
import difflib
import json
import logging
import shutil
import sys
import tempfile
import time
import warnings
from collections import Counter
from pathlib import Path

WINDOW_S = 60  # text is compared per minute: whole-session difflib is quadratic


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x:.1%}"


def _speaker_seconds(record: dict) -> dict[int, str]:
    """Second of audio → who the transcript says was speaking (later lines win overlaps)."""
    out: dict[int, str] = {}
    for m in record["messages"]:
        for s in range(int(m["t_start"]), max(int(m["t_start"]) + 1, int(m["t_end"]))):
            out[s] = m["speaker"]
    return out


def _words_by_window(record: dict) -> dict[int, list[str]]:
    out: dict[int, list[str]] = {}
    for m in record["messages"]:
        words = [w.strip(".,!?;:\"'()").lower() for w in m["text"].split()]
        out.setdefault(int(m["t_start"] // WINDOW_S), []).extend(w for w in words if w)
    return out


def _align_unnamed(a: dict[int, str], b: dict[int, str], named: set[str]) -> dict[str, str]:
    """Rerun label → original label for unnamed voices ("Voice 41" ≡ "Voice 38"): their numbers
    are arbitrary per run, so pair them one-to-one by most shared seconds, greedily."""
    overlap = Counter((a[s], b[s]) for s in a.keys() & b.keys() if b[s] not in named)
    mapping: dict[str, str] = {}
    used: set[str] = set()
    for (orig, new), _ in overlap.most_common():
        if new not in mapping and orig not in used and orig not in named:
            mapping[new] = orig
            used.add(orig)
    return mapping


def compare(original: dict, rerun: dict) -> dict:
    """Both are transcript.json records. Speaker agreement is over seconds where both have a line;
    player names must match exactly, unnamed voices only need the same grouping."""
    a, b = _speaker_seconds(original), _speaker_seconds(rerun)
    both = a.keys() & b.keys()
    named = {p["name"] for r in (original, rerun) for p in r["players"]}
    mapping = _align_unnamed(a, b, named)
    agree = sum(a[s] == mapping.get(b[s], b[s]) for s in both)
    wa, wb = _words_by_window(original), _words_by_window(rerun)
    matched = total = 0
    for w in wa.keys() | wb.keys():
        x, y = wa.get(w, []), wb.get(w, [])
        sm = difflib.SequenceMatcher(None, x, y, autojunk=False)
        matched += sum(block.size for block in sm.get_matching_blocks())
        total += max(len(x), len(y))

    def speakers(r: dict) -> dict[str, int]:
        return dict(Counter(_speaker_seconds(r).values()).most_common())

    return {
        "lines": {"original": len(original["messages"]), "rerun": len(rerun["messages"])},
        "speaker_agreement": round(agree / len(both), 4) if both else None,
        "overlap_s": len(both),
        "text_similarity": round(matched / total, 4) if total else None,
        "speaker_seconds": {"original": speakers(original), "rerun": speakers(rerun)},
    }


def _seed(work: Path, setup: str, session_dir: Path, data_dir: Path) -> None:
    """Fill a throwaway data dir with the chosen setup (state.json + voices/*.wav)."""
    (work / "voices").mkdir(parents=True)
    if setup == "current":
        state, voices = data_dir / "state.json", data_dir / "voices"
    else:
        state, voices = session_dir / "setup" / f"{setup}.json", session_dir / "setup" / "voices"
    if not state.is_file():
        raise SystemExit(f"no {setup} setup for this session ({state} is missing); try --setup current")
    shutil.copyfile(state, work / "state.json")
    for wav in voices.glob("*.wav") if voices.is_dir() else []:
        shutil.copyfile(wav, work / "voices" / wav.name)


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="bard-sim", description=__doc__.split("\n\n")[0])
    p.add_argument("session", help="session id (see GET /api/sessions or data/sessions/)")
    p.add_argument(
        "--setup",
        choices=("start", "end", "current"),
        default="end",
        help="voiceprints to use: as the session started, as it ended (incl. voices named during play; "
        "default), or today's data/state.json",
    )
    p.add_argument("--speed", type=float, default=4.0, help="× real time (default 4; much higher can skip LLM tagging)")
    p.add_argument("--keep", action="store_true", help="keep the throwaway data dir")
    args = p.parse_args(argv)

    warnings.filterwarnings("ignore", category=UserWarning)
    warnings.filterwarnings("ignore", category=FutureWarning)
    for noisy in ("httpx", "httpx2", "huggingface_hub"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

    from . import audio, config, transcript
    from . import engine as eng

    data_dir = config.DATA_DIR
    session_dir = data_dir / "sessions" / args.session
    try:
        wav, end = eng.session_audio(args.session)
    except KeyError:
        raise SystemExit(f"no audio for session {args.session!r} under {data_dir / 'sessions'}") from None
    original_path = session_dir / "transcript.json"
    original = json.loads(original_path.read_text()) if original_path.is_file() else None

    work = Path(tempfile.mkdtemp(prefix="bard-sim-"))
    _seed(work, args.setup, session_dir, data_dir)
    config.DATA_DIR, config.SAMPLES_DIR = work, wav.parent.resolve()  # the engine reads these at call time
    audio.FileSource.MAX_SPEED = max(audio.FileSource.MAX_SPEED, args.speed)

    engine = eng.Engine(eng.Bus())
    campaign = (original or {}).get("session", {}).get("campaign", eng.MAIN_CAMPAIGN)
    if campaign in engine.campaigns:  # --setup current: today's state may have another campaign open
        engine.open_campaign(campaign)
    engine.start_background()
    while not engine.ready.wait(0.5):
        if engine.load_error:
            raise SystemExit(f"models failed to load: {engine.load_error}")
    engine.start({"kind": "file", "name": wav.name, "speed": args.speed, "end": end})
    total = len(engine.live.source.audio) / config.SR
    print(f"re-running {args.session} ({total / 60:.1f} min of audio at {args.speed:g}×, setup={args.setup})")
    t0, next_report = time.monotonic(), 0.0
    while engine.status != "idle":
        time.sleep(1)
        if time.monotonic() >= next_report:
            next_report = time.monotonic() + 30
            print(f"  {engine.buffer.n / config.SR / 60:6.1f} / {total / 60:.1f} min, {len(engine.messages)} lines")
    quiet = 0  # spelling cleanup runs in the background; let it drain (5 s with an empty queue)
    while quiet < 5 and time.monotonic() - t0 < 24 * 3600:
        time.sleep(1)
        quiet = quiet + 1 if engine.cleanup_q.unfinished_tasks == 0 else 0
    engine._refine()  # the final relabel the live refine loop would do

    record = engine.transcript_record()
    run = record["session"]["id"]
    record["session"]["source"] = {"kind": "session", "id": args.session, "speed": args.speed}
    record["session"]["sim"] = {"of": args.session, "setup": args.setup}
    out = session_dir / "sims" / run
    out.mkdir(parents=True, exist_ok=True)
    (out / "transcript.json").write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "transcript.md").write_text(transcript.markdown(record), encoding="utf-8")
    print(f"wrote {out} in {(time.monotonic() - t0) / 60:.1f} min")
    if original is not None:
        result = compare(original, record)
        (out / "compare.json").write_text(json.dumps(result, indent=1))
        print(f"  lines              {result['lines']['original']} → {result['lines']['rerun']}")
        print(f"  speaker agreement  {_pct(result['speaker_agreement'])} over {result['overlap_s']} s")
        print(f"  text similarity    {_pct(result['text_similarity'])}")
    if args.keep:
        print(f"kept {work}")
    else:
        shutil.rmtree(work, ignore_errors=True)
    sys.stdout.flush()


if __name__ == "__main__":
    main()
