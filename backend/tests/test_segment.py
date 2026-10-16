import numpy as np
import pytest
import torch
from silero_vad import VADIterator

from bard.segment import SegmentConfig, Segmenter

SR, W = 16000, 512


class FakeVad:
    """Silero's interface, answering from a script of probabilities."""

    def __init__(self, probs):
        self.probs = iter(probs)

    def reset_states(self):
        pass

    def __call__(self, x, sr):
        return torch.tensor(next(self.probs))


def silero_spans(probs, cfg: SegmentConfig) -> list[tuple[int, int]]:
    """What the engine cut before Segmenter: VADIterator events, a hard cut at max_s, min_s."""
    vad = VADIterator(FakeVad(probs), cfg.threshold, SR, cfg.silence_ms, cfg.pad_ms)
    spans, start, n = [], None, 0
    for _ in probs:
        n += W
        event = vad(torch.zeros(W))
        if event and "start" in event:
            start = min(int(event["start"]), n)
        if event and "end" in event and start is not None:
            spans.append((start, min(int(event["end"]), n)))
            start = None
        if start is not None and n - start >= cfg.max_s * SR:
            spans.append((start, n))
            start = n
    if start is not None:
        spans.append((start, n))
    return [(s, e) for s, e in spans if e - s >= cfg.min_s * SR]


def run(probs, cfg: SegmentConfig) -> list[tuple[int, int]]:
    seg = Segmenter(SR, cfg)
    out = [span for p in probs for span in seg.feed(np.zeros(W, np.float32), p)]
    return out + seg.flush()


@pytest.mark.parametrize("seed", range(5))
@pytest.mark.parametrize(
    "cfg",
    [SegmentConfig(pad_ms=80), SegmentConfig(), SegmentConfig(silence_ms=250, pad_ms=100, max_s=4.0)],
    ids=["before (80 ms padding)", "now", "short"],
)
def test_cutting_matches_silero(seed, cfg):
    rng = np.random.default_rng(seed)
    # Talk and pauses of random lengths, probabilities noisy around the threshold.
    probs = []
    while len(probs) < 3000:
        speech = rng.random() < 0.6
        probs += list(np.clip(rng.normal(0.8 if speech else 0.2, 0.2, rng.integers(3, 200)), 0, 1))
    assert run(probs, cfg) == silero_spans(probs, cfg)


def test_long_speech_is_cut_at_max_s():
    spans = run([0.9] * 300, SegmentConfig(max_s=4.0))
    assert spans[:2] == [(0, 125 * W), (125 * W, 250 * W)]  # the first frame boundary at or past 4 s
    assert spans[2] == (250 * W, 300 * W)


def test_padding_is_kept_around_speech():
    probs = [0.0] * 50 + [0.9] * 50 + [0.0] * 50
    (span,) = run(probs, SegmentConfig())
    pad = int(SR * 0.2)
    assert span == (50 * W - pad, 100 * W + pad)


def test_padding_never_overlaps_the_previous_utterance():
    probs = [0.9] * 50 + [0.0] * 14 + [0.9] * 50 + [0.0] * 50  # a 0.45 s pause, 0.3 s padding
    first, second = run(probs, SegmentConfig(pad_ms=300))
    assert first[1] <= second[0]
