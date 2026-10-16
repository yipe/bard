"""Render a short D&D scene with macOS `say` voices.

Writes <out>.wav (16 kHz mono) and <out>.truth.json ([{start, end, speaker, text}]).
Speaker labels are the ground truth for spike/run.py.
"""

import json
import random
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

SR = 16000

# speaker label -> macOS voice. "GM:orc" is the GM doing an NPC voice; with TTS
# it is a different synthetic voice, so it only exercises the mapping flow.
VOICES = {
    "GM": "Daniel",
    "GM:orc": "Grandpa (English (UK))",
    "Alice": "Samantha",
    "Bob": "Fred",
    "Cara": "Flo (English (US))",
}

SCRIPT = [
    ("GM", "The cave mouth opens into a torch-lit chamber. Three orcs look up from a cooking fire."),
    ("GM:orc", "Who dares enter the den of Grukk? Turn back, little ones, or be eaten."),
    ("Alice", "I'm not turning back. Thessaly draws her rapier and steps forward."),
    ("GM", "Roll initiative, everyone."),
    ("Bob", "I got a fourteen."),
    ("Cara", "Nineteen for Wren."),
    ("Alice", "Twelve."),
    ("GM", "Wren, you're up first. The nearest orc is about thirty feet away."),
    ("Cara", "I cast fire bolt at the closest orc. That's seventeen to hit."),
    ("GM", "Seventeen hits. The orc has AC thirteen. Roll damage."),
    ("Cara", "Nine fire damage."),
    ("GM", "The flames catch its hide armor. It's badly burned but still standing."),
    ("GM:orc", "You will pay for that, witch!"),
    ("GM", "Grukk swings his greataxe at Bram. Does a twenty one hit?"),
    ("Bob", "Yeah, my AC is eighteen. That hits."),
    ("GM", "You take twelve slashing damage."),
    ("Bob", "Ouch. Bram is down to fifteen hit points."),
    ("GM", "Thessaly, your turn."),
    ("Alice", "I attack the burned orc with my rapier. Natural twenty!"),
    ("GM", "A critical hit. Roll it."),
    ("Alice", "Twenty two piercing damage, with sneak attack."),
    ("GM", "Your blade slips between its ribs. The orc dies before it hits the ground."),
    ("Bob", "Nice one. I use second wind and heal eight hit points."),
]


def render(voice: str, text: str, tmp: Path) -> np.ndarray:
    aiff = tmp / "line.aiff"
    subprocess.run(["say", "-v", voice, "-o", str(aiff), text], check=True)
    pcm = subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-i", str(aiff), "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"],
        check=True,
        capture_output=True,
    ).stdout
    return np.frombuffer(pcm, np.float32)


def main(out: Path) -> None:
    rng = random.Random(0)
    chunks, truth, t = [], [], 0.0
    with tempfile.TemporaryDirectory() as d:
        for speaker, text in SCRIPT:
            gap = np.zeros(int(SR * rng.uniform(0.25, 0.8)), np.float32)
            chunks.append(gap)
            t += len(gap) / SR
            audio = render(VOICES[speaker], text, Path(d))
            chunks.append(audio)
            truth.append({"start": round(t, 3), "end": round(t + len(audio) / SR, 3), "speaker": speaker, "text": text})
            t += len(audio) / SR
    audio = np.concatenate(chunks)
    # Light room noise so VAD/diarization don't see perfect digital silence.
    audio = audio + np.random.default_rng(0).normal(0, 0.002, len(audio)).astype(np.float32)
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-y",
            "-f",
            "f32le",
            "-ar",
            str(SR),
            "-ac",
            "1",
            "-i",
            "-",
            str(out.with_suffix(".wav")),
        ],
        input=audio.tobytes(),
        check=True,
    )
    out.with_suffix(".truth.json").write_text(json.dumps(truth, indent=1))
    print(f"{out.with_suffix('.wav')}: {len(audio) / SR:.1f}s, {len(truth)} lines, {len(VOICES)} voices")


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else "samples/scene"))
