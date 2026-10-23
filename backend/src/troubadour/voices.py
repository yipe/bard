"""Voiceprints: embed speech spans, build per-speaker centroids, match spans to them.

Uses the diarization pipeline's own speaker-embedding model (WeSpeaker
ResNet34, 256-d) so enrolled voices and diarization clusters share one space.
All vectors are L2-normalized, so a dot product is cosine similarity.
"""

import numpy as np

SR = 16000
WINDOW_S = 4.0  # long spans are embedded in windows and averaged
MIN_EMBED_S = 0.5  # shorter spans give unreliable embeddings


def normalize(v: np.ndarray) -> np.ndarray:
    return v / (np.linalg.norm(v) + 1e-9)


def embed_span(embedder, audio: np.ndarray, start: float, end: float) -> np.ndarray | None:
    """Unit vector for audio[start:end], or None if too short to trust."""
    import torch

    if end - start < MIN_EMBED_S:
        return None
    n_windows = max(1, round((end - start) / WINDOW_S))
    edges = np.linspace(start, end, n_windows + 1)
    chunks = [audio[int(a * SR) : int(b * SR)] for a, b in zip(edges[:-1], edges[1:], strict=True)]
    length = min(len(c) for c in chunks)
    batch = torch.from_numpy(np.stack([c[:length] for c in chunks]))[:, None]
    vecs = embedder(batch)
    vecs = vecs[~np.isnan(vecs).any(axis=1)]
    if not len(vecs):
        return None
    return normalize(np.mean([normalize(v) for v in vecs], axis=0))


def centroid(embedder, audio: np.ndarray, spans: list[tuple[float, float]]) -> np.ndarray | None:
    """Duration-weighted mean voiceprint over spans."""
    vecs, weights = [], []
    for start, end in spans:
        v = embed_span(embedder, audio, start, end)
        if v is not None:
            vecs.append(v)
            weights.append(end - start)
    if not vecs:
        return None
    return normalize(np.average(vecs, axis=0, weights=weights))


class VoiceMatcher:
    """Match speech spans to enrolled voiceprints. Benchmark winner (see PLAN.md).

    - Spans shorter than pad_to are widened around their center before
      embedding; very short replies otherwise carry too little voice.
    - Confident long matches are folded into that speaker's voiceprint
      (session audio beats setup audio); the enrollment counts as
      `adapt_weight` such segments. Updates use only past audio, so this
      works live.
    - A best score below unknown_below means "probably not an enrolled voice".
    """

    def __init__(
        self,
        embedder,
        enrolled: dict[str, np.ndarray] | None = None,
        pad_to: float = 1.5,
        adapt_weight: float = 5.0,
        adapt_min_s: float = 3.0,
        adapt_margin: float = 0.1,
        unknown_below: float = 0.30,
    ):
        self.embedder = embedder
        self.pad_to = pad_to
        self.adapt_weight = adapt_weight
        self.adapt_min_s = adapt_min_s
        self.adapt_margin = adapt_margin
        self.unknown_below = unknown_below
        # A session may start with no voices (option B: named during play).
        self.names: list[str] = []
        self.matrix = np.zeros((0, embedder.dimension))
        self._sums = np.zeros((0, embedder.dimension))
        for name, vec in (enrolled or {}).items():
            self.add(name, vec)

    def add(self, name: str, vec: np.ndarray) -> None:
        """Known voice from a Train recording or a voice named in the panel.
        Naming a second voice with an existing name merges it into that voice."""
        vec = normalize(vec)
        weight = max(self.adapt_weight, 1.0)
        if name in self.names:
            i = self.names.index(name)
            self._sums[i] += vec * weight
            self.matrix[i] = normalize(self._sums[i])
            return
        self.names.append(name)
        self.matrix = np.vstack([self.matrix, vec])
        self._sums = np.vstack([self._sums, vec * weight])

    def remove(self, name: str) -> np.ndarray | None:
        """Forget a voice (unassigned in the panel). Returns its last voiceprint."""
        if name not in self.names:
            return None
        i = self.names.index(name)
        vec = self.matrix[i].copy()
        self.names.pop(i)
        self.matrix = np.delete(self.matrix, i, axis=0)
        self._sums = np.delete(self._sums, i, axis=0)
        return vec

    def learn(self, name: str, vec: np.ndarray) -> None:
        """One line the user says is `name`'s: it counts like one confident span (a Train
        recording or a voice named in the panel counts adapt_weight times). A new name starts here."""
        vec = normalize(vec)
        if name not in self.names:
            self.names.append(name)
            self.matrix = np.vstack([self.matrix, vec])
            self._sums = np.vstack([self._sums, vec])
            return
        i = self.names.index(name)
        self._sums[i] += vec
        self.matrix[i] = normalize(self._sums[i])

    def forget(self, name: str, vec: np.ndarray) -> None:
        """Undo learn(): the user moved that line to someone else. A voice that was only that line goes."""
        if name not in self.names:
            return
        i = self.names.index(name)
        self._sums[i] -= normalize(vec)
        if np.linalg.norm(self._sums[i]) < 1e-6:
            self.remove(name)
        else:
            self.matrix[i] = normalize(self._sums[i])

    def vector(self, name: str) -> np.ndarray:
        return self.matrix[self.names.index(name)]

    def embed(self, audio: np.ndarray, start: float, end: float) -> np.ndarray | None:
        if end - start < self.pad_to:
            mid = (start + end) / 2
            start, end = max(0.0, mid - self.pad_to / 2), min(len(audio) / SR, mid + self.pad_to / 2)
        return embed_span(self.embedder, audio, start, end)

    def scores(self, vec: np.ndarray) -> np.ndarray:
        """Cosine similarity to each name in self.names (empty if none known)."""
        return self.matrix @ vec

    def observe(self, name: str, vec: np.ndarray, duration: float) -> None:
        """Fold a confidently matched span into name's voiceprint."""
        if not self.adapt_weight or duration < self.adapt_min_s:
            return
        ranked = np.sort(self.scores(vec))
        # With one known voice there is no runner-up; require clearing the unknown threshold instead.
        runner_up = ranked[-2] if len(ranked) > 1 else self.unknown_below
        if ranked[-1] - runner_up < self.adapt_margin:
            return
        i = self.names.index(name)
        self._sums[i] += vec
        self.matrix[i] = normalize(self._sums[i])

    def classify(self, vec: np.ndarray, duration: float) -> tuple[str | None, float, bool]:
        """(best name or None if no voices yet, score, is_unknown). Adapts as a side effect."""
        if not self.names:
            return None, 0.0, True
        scores = self.scores(vec)
        best = int(np.argmax(scores))
        name, score = self.names[best], float(scores[best])
        unknown = score < self.unknown_below
        if not unknown:
            self.observe(name, vec, duration)
        return name, score, unknown
