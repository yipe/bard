import numpy as np
import pytest
from conftest import SR, FakeEmbedder, tone

from bard.voices import VoiceMatcher, centroid, embed_span, normalize


@pytest.fixture
def emb():
    return FakeEmbedder()


def vec(emb, freq, seconds=2.0):
    a = tone(freq, seconds)
    return embed_span(emb, a, 0, seconds)


def test_embed_span_too_short_is_none(emb):
    assert embed_span(emb, tone(200, 1), 0, 0.3) is None


def test_same_tone_same_voice_different_tone_different(emb):
    a, a2, b = vec(emb, 200), vec(emb, 200, 3.0), vec(emb, 800)
    assert a @ a2 > 0.95
    assert a @ b < 0.2


def test_centroid_is_unit_and_ignores_too_short_spans(emb):
    audio = tone(200, 4)
    c = centroid(emb, audio, [(0, 2), (2, 2.1)])
    assert c is not None
    assert np.isclose(np.linalg.norm(c), 1.0)
    assert centroid(emb, audio, [(0, 0.1)]) is None


def test_starts_empty_and_reports_unknown(emb):
    m = VoiceMatcher(emb)
    assert m.classify(vec(emb, 200), 2.0) == (None, 0.0, True)


def test_classify_known_and_unknown(emb):
    m = VoiceMatcher(emb, {"alice": vec(emb, 200), "bob": vec(emb, 800)})
    name, score, unknown = m.classify(vec(emb, 200, 3.0), 3.0)
    assert (name, unknown) == ("alice", False)
    assert score > 0.9
    assert m.classify(vec(emb, 3000), 3.0)[2] is True  # nobody we know


def test_add_same_name_merges(emb):
    m = VoiceMatcher(emb, {"alice": vec(emb, 200)})
    m.add("alice", vec(emb, 220))
    assert m.names == ["alice"]
    assert np.isclose(np.linalg.norm(m.vector("alice")), 1.0)


def test_remove_returns_voiceprint(emb):
    m = VoiceMatcher(emb, {"alice": vec(emb, 200), "bob": vec(emb, 800)})
    v = m.remove("alice")
    assert m.names == ["bob"]
    assert v is not None
    assert m.remove("nobody") is None


def test_observe_adapts_only_confident_long_spans(emb):
    alice, bob = vec(emb, 200), vec(emb, 800)
    m = VoiceMatcher(emb, {"alice": alice, "bob": bob})
    near_alice = normalize(alice + 0.4 * normalize(np.random.default_rng(0).random(256)))
    before = m.vector("alice").copy()
    m.observe("alice", near_alice, duration=1.0)  # too short to trust
    assert np.allclose(m.vector("alice"), before)
    m.observe("alice", near_alice, duration=4.0)
    assert not np.allclose(m.vector("alice"), before)
    assert np.allclose(m.vector("bob"), bob)  # only the matched voice moves


def test_observe_with_single_voice_uses_unknown_threshold(emb):
    # Regression: with one voice there is no runner-up; this used to raise IndexError.
    m = VoiceMatcher(emb, {"alice": vec(emb, 200)})
    m.observe("alice", vec(emb, 200), duration=4.0)


def test_embed_pads_short_spans(emb):
    m = VoiceMatcher(emb, pad_to=1.5)
    audio = tone(200, 3)
    assert embed_span(emb, audio, 1.0, 1.4) is None  # raw 0.4 s: too short
    assert m.embed(audio, 1.0, 1.4) is not None  # padded to 1.5 s around its center


def test_normalize():
    assert np.isclose(np.linalg.norm(normalize(np.array([3.0, 4.0]))), 1.0)
    assert SR == 16000
