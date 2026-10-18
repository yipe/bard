"""Utterance cutting: per-frame speech probabilities in, utterance spans out.

The state machine is Silero's VADIterator (threshold with a 0.15 hysteresis, a silence wait
before an utterance ends, padding on both sides), plus a forced cut at max_s and padding that
never reaches back into the previous utterance. No model in here: the engine runs Silero and
feeds the probability; spike/segment_sweep.py replays cached probabilities through the same
code to tune the values (PLAN.md, "Utterance cutting").
"""

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class SegmentConfig:
    threshold: float = 0.5  # Silero speech probability; speech ends below threshold - 0.15
    silence_ms: int = 400  # silence that ends an utterance
    pad_ms: int = 200  # audio kept before the first and after the last speech frame
    max_s: float = 12.0  # force a cut in long monologues so text keeps flowing
    min_s: float = 0.3  # shorter utterances are dropped


class Segmenter:
    """Feed every frame (any fixed size) with its speech probability; get finished spans back as
    (start, end) sample indices counted from the first frame. `start` is the open utterance's
    start, or None between utterances."""

    def __init__(self, sr: int, cfg: SegmentConfig):
        self.sr, self.cfg = sr, cfg
        self.n = 0  # samples fed
        self.start: int | None = None
        self._temp_end = 0  # where the current run of silence began (Silero's temp_end)
        self._last_end = 0  # padding never reaches back into the previous utterance

    def feed(self, frame: np.ndarray, prob: float) -> list[tuple[int, int]]:
        cfg, w = self.cfg, len(frame)
        self.n += w
        pad = int(self.sr * cfg.pad_ms / 1000)
        out: list[tuple[int, int]] = []
        if prob >= cfg.threshold:
            self._temp_end = 0
            if self.start is None:
                self.start = max(self._last_end, self.n - pad - w)
        elif prob < cfg.threshold - 0.15 and self.start is not None:
            if not self._temp_end:
                self._temp_end = self.n
            if self.n - self._temp_end >= self.sr * cfg.silence_ms / 1000:
                self._last_end = min(self._temp_end + pad - w, self.n)
                self._emit(out, self.start, self._last_end)
                self.start, self._temp_end = None, 0
                return out
        if self.start is not None and self.n - self.start >= cfg.max_s * self.sr:
            self._emit(out, self.start, self.n)
            self.start = self.n
        return out

    def flush(self) -> list[tuple[int, int]]:
        """The open utterance, cut at the last frame (the source ended)."""
        out: list[tuple[int, int]] = []
        if self.start is not None:
            self._emit(out, self.start, self.n)
        self.start, self._temp_end = None, 0
        return out

    def _emit(self, out: list[tuple[int, int]], start: int, end: int) -> None:
        if end - start >= self.cfg.min_s * self.sr:
            out.append((start, end))
