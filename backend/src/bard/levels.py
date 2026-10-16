"""Input level and audio quality, live, from the 16 kHz frames the pipeline hears.

`LevelMeter.feed` takes every frame. About 10 times a second it emits `audio.level`
(loudness for the UI's meter); every couple of seconds `audio.quality`: the numbers that tell
whether the mic is set up well, over the last WINDOW_S seconds, each graded with a hint:

    speech level      p90 of 50 ms RMS: how loud talking is (aim −30…−12 dBFS)
    background noise  p10 of 50 ms RMS: the room between words
    SNR               speech minus noise (≥ 30 dB good, < 20 dB poor)
    rumble            share of energy below 100 Hz: table thumps, HVAC (< 20% good)
    clipping          share of samples at full scale (should be ~0)

Thresholds come from measuring a real session on a dynamic USB mic in the middle of a table.
"""

import time
from collections.abc import Callable

import numpy as np

from .config import SR

WINDOW_S = 15.0
LEVEL_EVERY_S = 0.1
QUALITY_EVERY_S = 2.0
CLIP = 0.99
FLOOR_DB = -90.0
# The meter's zones (dBFS of ~100 ms RMS). The UI colours them: silent dim grey, quiet light
# grey-blue, good green, hot amber, clip red.
ZONES = ((-50.0, "silent"), (-30.0, "quiet"), (-12.0, "good"), (-3.0, "hot"))


def zone(level_db: float, clipping: bool) -> str:
    if clipping:
        return "clip"
    return next((name for top, name in ZONES if level_db < top), "clip")


def db(x: float) -> float:
    return float(max(FLOOR_DB, 20 * np.log10(max(x, 1e-9))))


def grade(value: float, good: Callable[[float], bool], ok: Callable[[float], bool]) -> str:
    return "good" if good(value) else "ok" if ok(value) else "poor"


def quality(audio: np.ndarray) -> dict | None:
    """The read-out for a stretch of 16 kHz audio; None if there's under 2 s of it."""
    win = int(0.05 * SR)
    n = len(audio) // win
    if n < 40:
        return None
    rms = np.sqrt((audio[: n * win].reshape(n, win).astype(np.float64) ** 2).mean(axis=1))
    speech, noise = db(float(np.percentile(rms, 90))), db(float(np.percentile(rms, 10)))
    snr = speech - noise
    spectrum = np.abs(np.fft.rfft(audio.astype(np.float64))) ** 2
    freqs = np.fft.rfftfreq(len(audio), 1 / SR)
    total = float(spectrum.sum()) or 1.0
    rumble = 100 * float(spectrum[freqs < 100].sum()) / total
    clipped = 100 * float(np.mean(np.abs(audio) >= CLIP))
    talking = speech - noise >= 6  # someone spoke in this window, not just room tone
    out = {
        "window_s": round(len(audio) / SR, 1),
        "speech_db": round(speech, 1),
        "noise_db": round(noise, 1),
        "snr_db": round(snr, 1),
        "rumble_pct": round(rumble, 1),
        "clipped_pct": round(clipped, 3),
        "talking": talking,
        "grades": {
            "speech": grade(speech, lambda v: -30 <= v <= -12, lambda v: -40 <= v <= -6) if talking else "ok",
            "noise": grade(noise, lambda v: v <= -60, lambda v: v <= -50),
            "snr": grade(snr, lambda v: v >= 30, lambda v: v >= 20) if talking else "ok",
            "rumble": grade(rumble, lambda v: v < 20, lambda v: v < 50),
            "clipping": grade(clipped, lambda v: v < 0.01, lambda v: v < 0.1),
        },
    }
    out["hints"] = hints(out)
    return out


def hints(q: dict) -> list[str]:
    g, out = q["grades"], []
    if g["clipping"] != "good":
        out.append("Clipping: lower the mic gain, or move the mic away from the table edge.")
    if q["talking"] and q["speech_db"] < -30:
        out.append("Speech is quiet: raise the mic gain until talking peaks around −20 to −12 dBFS.")
    elif q["talking"] and q["speech_db"] > -10:
        out.append("Speech is very loud: lower the gain a little to leave headroom.")
    if g["rumble"] != "good":
        out.append("Lots of low rumble: turn on the mic's high-pass filter (80–100 Hz) and put the mic on something soft.")
    if g["noise"] != "good":
        out.append(
            "The room is noisy between words: turn off fans or music, and turn off the mic's noise gate only if voices cut out."
        )
    if q["talking"] and g["snr"] == "poor":
        out.append("Voices are barely above the background: move the mic closer to the middle of the table.")
    if not q["talking"]:
        out.append("Waiting for someone to talk…")
    return out[:3]


class LevelMeter:
    """Feed it 16 kHz frames; it emits `audio.level` and `audio.quality` through `emit`."""

    def __init__(self, emit: Callable[[str, dict], None], source: str = "session"):
        self.emit = emit
        self.source = source  # "session" or "test": the UI shows them in different places
        self.ring = np.zeros(int(WINDOW_S * SR), np.float32)
        self.filled = 0
        self.at = 0
        self._sum_sq = 0.0
        self._peak = 0.0
        self._count = 0
        self._next_level = 0.0
        self._next_quality = time.monotonic() + QUALITY_EVERY_S

    def feed(self, frame: np.ndarray) -> None:
        n = len(frame)
        end = self.at + n
        if end <= len(self.ring):
            self.ring[self.at : end] = frame
        else:
            k = len(self.ring) - self.at
            self.ring[self.at :] = frame[:k]
            self.ring[: n - k] = frame[k:]
        self.at = end % len(self.ring)
        self.filled = min(len(self.ring), self.filled + n)
        self._sum_sq += float(np.dot(frame, frame))
        self._peak = max(self._peak, float(np.max(np.abs(frame))) if n else 0.0)
        self._count += n
        now = time.monotonic()
        if now >= self._next_level and self._count:
            self._next_level = now + LEVEL_EVERY_S
            level, clipping = db((self._sum_sq / self._count) ** 0.5), self._peak >= CLIP
            self.emit(
                "audio.level",
                {
                    "source": self.source,
                    "db": round(level, 1),
                    "peak_db": round(db(self._peak), 1),
                    "clipping": clipping,
                    "zone": zone(level, clipping),
                },
            )
            self._sum_sq, self._peak, self._count = 0.0, 0.0, 0
        if now >= self._next_quality:
            self._next_quality = now + QUALITY_EVERY_S
            q = quality(self.recent())
            if q is not None:
                self.emit("audio.quality", {"source": self.source, **q})

    def recent(self) -> np.ndarray:
        if self.filled < len(self.ring):
            return self.ring[: self.filled].copy()
        return np.concatenate([self.ring[self.at :], self.ring[: self.at]])

    def stop(self) -> None:
        self.emit("audio.level", {"source": self.source, "db": None, "peak_db": None, "clipping": False, "zone": "off"})


# ------------------------------------------------------------------ a recorded session

SPREAD_POOR_DB = 10.0  # loudest vs quietest regular speaker: the mic hears the table unevenly


def clip_db(audio: np.ndarray) -> float:
    return round(db(float(np.sqrt(np.mean(audio.astype(np.float64) ** 2)))) if len(audio) else FLOOR_DB, 1)


def report(audio: np.ndarray, messages: list[dict], minute_s: float = 60.0) -> dict:
    """Quality of a whole recording: overall numbers, a per-minute timeline, how loud each
    speaker comes through, and a verdict: keep / change settings / change microphone."""
    step = int(minute_s * SR)
    timeline = []
    for at in range(0, len(audio), step):
        q = quality(audio[at : at + step])
        if q is not None:
            timeline.append(
                {"t": round(at / SR), **{k: q[k] for k in ("speech_db", "noise_db", "snr_db", "rumble_pct", "clipped_pct")}}
            )
    if not timeline:
        return {
            "overall": None,
            "timeline": [],
            "speakers": [],
            "verdict": {"kind": "unknown", "reasons": ["Too little audio to judge."]},
        }

    def median(key: str) -> float:
        return round(float(np.median([t[key] for t in timeline])), 1)

    overall = {k: median(k) for k in ("speech_db", "noise_db", "snr_db", "rumble_pct")}
    overall["clipped_pct"] = round(float(np.mean([t["clipped_pct"] for t in timeline])), 3)
    overall["grades"] = {
        "speech": grade(overall["speech_db"], lambda v: -30 <= v <= -12, lambda v: -40 <= v <= -6),
        "noise": grade(overall["noise_db"], lambda v: v <= -60, lambda v: v <= -50),
        "snr": grade(overall["snr_db"], lambda v: v >= 30, lambda v: v >= 20),
        "rumble": grade(overall["rumble_pct"], lambda v: v < 20, lambda v: v < 50),
        "clipping": grade(overall["clipped_pct"], lambda v: v < 0.01, lambda v: v < 0.1),
    }

    # How loud each speaker arrives: the median of their lines' loudness.
    levels: dict[str, list[float]] = {}
    for m in messages:
        level = m.get("level_db")
        if level is None:
            level = clip_db(audio[int(m["t_start"] * SR) : int(m["t_end"] * SR)])
        if m.get("player_id") and level > FLOOR_DB:  # named players only: "Voice 47" is nobody yet
            levels.setdefault(m["speaker"], []).append(level)
    speakers = sorted(
        ({"speaker": s, "level_db": round(float(np.median(v)), 1), "lines": len(v)} for s, v in levels.items() if len(v) >= 10),
        key=lambda s: -s["level_db"],
    )
    spread = round(speakers[0]["level_db"] - speakers[-1]["level_db"], 1) if len(speakers) >= 2 else 0.0
    return {
        "overall": overall,
        "timeline": timeline,
        "speakers": speakers,
        "spread_db": spread,
        "verdict": verdict(overall, speakers, spread),
    }


def verdict(overall: dict, speakers: list[dict], spread: float) -> dict:
    """ "good", "settings" (fixable on this mic) or "microphone" (this mic can't hear the table well)."""
    g = overall["grades"]
    settings, mic = [], []
    if g["clipping"] != "good":
        settings.append("It clips: lower the gain.")
    if overall["speech_db"] < -30:
        settings.append(f"Speech averages {overall['speech_db']:.0f} dBFS: raise the gain toward −20.")
    if g["rumble"] != "good":
        settings.append(
            f"{overall['rumble_pct']:.0f}% of the sound is rumble below 100 Hz: turn on the high-pass filter and put the mic on something soft."
        )
    if spread >= SPREAD_POOR_DB:
        quiet = speakers[-1]["speaker"]
        mic.append(
            f"{quiet} comes through {spread:.0f} dB quieter than {speakers[0]['speaker']}: the mic favours one side of the table. An omnidirectional or boundary mic in the middle hears everyone evenly."
        )
    if g["snr"] == "poor" and overall["speech_db"] >= -30:
        mic.append(
            f"Voices are only {overall['snr_db']:.0f} dB above the background even at a good gain: the mic is picking up more room than voices."
        )
    kind = "microphone" if mic else "settings" if settings else "good"
    return {"kind": kind, "reasons": [*mic, *settings] or ["Levels, noise and balance between speakers all look good."]}
