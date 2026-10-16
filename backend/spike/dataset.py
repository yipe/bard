"""Build the eval set in samples/eval/: <clip>.wav (16 kHz mono) + <clip>.truth.json.

truth.json: [{start, end, speaker, text}] in clip time. speaker "*" marks
multi-speaker cues ("ALL:", "LAURA AND TRAVIS:"); scoring skips them.

Sources (in samples/src/):
- Critical Role: <youtube id>.m4a + <youtube id>.en.vtt (official captions,
  speaker named at each change). One combat-dense window per episode, plus a
  roleplay window from C2E58.
- AMI: <meeting>.wav (Array1-01, single distant mic) + <meeting>.rttm
  (pyannote AMI-diarization-setup, only_words). Whole meeting, no text.

Each clip also gets <clip>.enroll.json: {source, speakers: {name: [[start,
end], ...]}}, ~30 s of isolated speech per speaker from outside the clip (CR:
elsewhere in the episode; AMI: the sibling meeting with the same people).
enroll15.json / enroll60.json are the same with ~15 s / ~60 s.
"""

import json
import re
import subprocess
from pathlib import Path

SRC = Path("samples/src")
OUT = Path("samples/eval")
CLIP_S = 600

CR_EPISODES = {
    "7Z610fSoifk": "c2e58",
    "P8UVu-kGusg": "c2e44",
    "eS88k3VxT_I": "c3e114",
    "9QXZ4LfSi84": "vm52",
}
# Fixed extra windows: (episode id, start seconds, label).
CR_EXTRA = [("7Z610fSoifk", 5400, "c2e58_rp")]
# eval meeting -> sibling meeting (same participants) used for enrollment
AMI_MEETINGS = {"ES2004a": "ES2004b", "IS1009a": "IS1009b", "TS3003a": "TS3003b"}

LABEL = re.compile(r"^([A-Z][A-Z'\-]+(?:(?:,? AND |, )[A-Z][A-Z'\-]+)*):\s*")
TYPOS = {"TARVIS": "TRAVIS", "MARIASHA": "MARISHA"}
NON_SPEECH = re.compile(r"\([^)]*\)|\[[^\]]*\]|♪")
MECHANICS = re.compile(r"damage|to hit|\bAC\b|initiative|attack|saving throw|\broll", re.I)


def ts(s: str) -> float:
    h, m, rest = s.split(":")
    return int(h) * 3600 + int(m) * 60 + float(rest)


def parse_vtt(path: Path) -> list[dict]:
    """Caption cues split into single-speaker utterances."""
    utterances, speaker = [], None
    for block in path.read_text().split("\n\n"):
        lines = [line for line in block.strip().splitlines() if line.strip()]
        timing = next((i for i, line in enumerate(lines) if "-->" in line), None)
        if timing is None:
            continue
        start, end = (ts(x.split()[0]) for x in lines[timing].split("-->"))
        parts = []  # (speaker, text) within this cue
        for line in lines[timing + 1 :]:
            m = LABEL.match(line)
            if m:
                names = re.split(r",? AND |, ", m.group(1))
                names = [TYPOS.get(n, n) for n in names]
                speaker = names[0] if len(names) == 1 and names[0] != "ALL" else "*"
                line = line[m.end() :]
            text = NON_SPEECH.sub("", line).strip()
            if text and speaker:
                if parts and parts[-1][0] == speaker:
                    parts[-1] = (speaker, parts[-1][1] + " " + text)
                else:
                    parts.append((speaker, text))
        # Split cue time across speakers in proportion to text length.
        total = sum(len(t) for _, t in parts) or 1
        t = start
        for spk, text in parts:
            dur = (end - start) * len(text) / total
            utterances.append({"start": t, "end": t + dur, "speaker": spk, "text": text})
            t += dur
    return utterances


def window(utterances: list[dict], start: float, length: float) -> list[dict]:
    out = []
    for u in utterances:
        if u["end"] > start and u["start"] < start + length:
            out.append({**u, "start": round(max(0.0, u["start"] - start), 3), "end": round(min(length, u["end"] - start), 3)})
    return out


def densest_window(utterances: list[dict]) -> float:
    """Start of the CLIP_S window with most mechanics talk and no long silences (breaks)."""
    best, best_score = 0.0, -1
    last = utterances[-1]["end"]
    for start in range(1200, int(last - CLIP_S), 60):
        clip = window(utterances, start, CLIP_S)
        times = [0.0] + [u["start"] for u in clip] + [CLIP_S]
        if max(b - a for a, b in zip(times, times[1:])) > 45:
            continue
        score = sum(bool(MECHANICS.search(u["text"])) for u in clip)
        if score > best_score:
            best, best_score = float(start), score
    return best


def cut(src: Path, dst: Path, start: float = 0.0, length: float | None = None) -> None:
    cmd = ["ffmpeg", "-nostdin", "-v", "error", "-y", "-ss", str(start), "-i", str(src)]
    if length:
        cmd += ["-t", str(length)]
    subprocess.run(cmd + ["-ac", "1", "-ar", "16000", str(dst)], check=True)


# file suffix -> seconds of speech per speaker
ENROLL_TARGETS = {"enroll": 30.0, "enroll15": 15.0, "enroll60": 60.0}
ENROLL_ISOLATION_S = 0.5  # no other speaker this close, to avoid crosstalk in samples


def enrollment(utterances: list[dict], speakers: set[str], exclude: tuple[float, float] | None, target_s: float) -> dict:
    """~target_s of clean single-speaker speech per speaker, spread across the source.

    Simulates the setup flow: each player reads into the mic before play.
    exclude: (start, end) of the eval clip in source time, padded by 60 s.
    """
    utterances = sorted(utterances, key=lambda u: u["start"])
    out = {}
    for spk in sorted(speakers):
        candidates = []
        for i, u in enumerate(utterances):
            if u["speaker"] != spk:
                continue
            # Captions abut (gap 0), so trim a margin instead of requiring silence.
            start, end = u["start"] + ENROLL_ISOLATION_S, u["end"] - ENROLL_ISOLATION_S
            if not 2.0 <= end - start <= 15.0:
                continue
            if exclude and end > exclude[0] - 60 and start < exclude[1] + 60:
                continue
            near = (v for v in utterances[max(0, i - 10) : i + 10] if v["speaker"] != spk)
            if any(v["end"] > start and v["start"] < end for v in near):
                continue
            candidates.append([round(start, 3), round(end, 3)])
        # Take every k-th candidate so samples span the whole source.
        need = target_s / (sum(e - s for s, e in candidates) / len(candidates)) if candidates else 0
        step = max(1, int(len(candidates) / max(need, 1)))
        picked, total = [], 0.0
        for s, e in candidates[::step]:
            if total >= target_s:
                break
            picked.append([s, e])
            total += e - s
        if total >= 8.0:  # too little clean speech: leave them unenrolled, like a late-joining player
            out[spk] = picked
    return out


def write_clip(name: str, src: Path, truth: list[dict], start: float = 0.0, length: float | None = None) -> None:
    cut(src, OUT / f"{name}.wav", start, length)
    (OUT / f"{name}.truth.json").write_text(json.dumps(truth, indent=0))
    speakers = {u["speaker"] for u in truth} - {"*"}
    print(f"{name}: start={start:.0f}s utterances={len(truth)} speakers={len(speakers)}")


def write_enrollment(name: str, src: Path, utterances: list[dict], speakers: set[str], exclude) -> None:
    for suffix, target_s in ENROLL_TARGETS.items():
        spec = enrollment(utterances, speakers, exclude, target_s)
        (OUT / f"{name}.{suffix}.json").write_text(json.dumps({"source": str(src), "speakers": spec}))
        missing = sorted(speakers - set(spec))
        print(f"  {suffix}: {len(spec)}/{len(speakers)} speakers" + (f", missing {missing}" if missing else ""))


def rttm(path: Path) -> list[dict]:
    out = []
    for line in path.read_text().splitlines():
        f = line.split()
        start, dur = float(f[3]), float(f[4])
        out.append({"start": start, "end": round(start + dur, 3), "speaker": f[7], "text": ""})
    return out


def cr_clip(vid: str, label: str, start: float | None) -> None:
    audio = next(SRC.glob(f"{vid}.m4a"), None) or next(SRC.glob(f"{vid}.webm"))
    utterances = parse_vtt(SRC / f"{vid}.en.vtt")
    if start is None:
        start = densest_window(utterances)
    truth = window(utterances, start, CLIP_S)
    write_clip(label, audio, truth, start, CLIP_S)
    speakers = {u["speaker"] for u in truth} - {"*"}
    write_enrollment(label, audio, utterances, speakers, (start, start + CLIP_S))


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for vid, label in CR_EPISODES.items():
        cr_clip(vid, label, None)
    for vid, start, label in CR_EXTRA:
        cr_clip(vid, label, start)
    for meeting, sibling in AMI_MEETINGS.items():
        truth = rttm(SRC / f"{meeting}.rttm")
        write_clip(f"ami_{meeting}", SRC / f"{meeting}.wav", truth)
        speakers = {u["speaker"] for u in truth}
        write_enrollment(f"ami_{meeting}", SRC / f"{sibling}.wav", rttm(SRC / f"{sibling}.rttm"), speakers, None)


if __name__ == "__main__":
    main()
