"""Can a style-sensitive model tell one person's normal voice from their character voice?

The speaker embedding is built to ignore speaking style, so it barely separates
them (held-out margin ≈ 0.04). This compares, on held-out windows:
  - speaker embedding (pyannote WeSpeaker)
  - WavLM-base-plus hidden states, mean-pooled per layer
  - prosody features (pitch percentiles, spectral centroid, loudness spread)
Each voice's model is built from the first half of its clip and tested on 1 s
and 2 s windows from the second half (nearest centroid, cosine / z-scored).

Usage: uv run --with transformers spike/style.py NORMAL.wav CHARACTER.wav
"""

import sys
import warnings

import numpy as np

warnings.filterwarnings("ignore")
SR = 16000


def windows(x: np.ndarray, start_s: float, end_s: float, w: float):
    t = start_s
    while t + w <= end_s + 1e-6:
        yield x[int(t * SR) : int((t + w) * SR)]
        t += w / 2  # 50% overlap


def wavlm_features():
    import torch
    from transformers import AutoFeatureExtractor, WavLMModel

    fe = AutoFeatureExtractor.from_pretrained("microsoft/wavlm-base-plus")
    model = WavLMModel.from_pretrained("microsoft/wavlm-base-plus").eval()

    def feats(chunk: np.ndarray) -> np.ndarray:  # (layers, dim)
        inputs = fe(chunk, sampling_rate=SR, return_tensors="pt")
        with torch.no_grad():
            out = model(**inputs, output_hidden_states=True)
        return torch.stack([h[0].mean(0) for h in out.hidden_states]).numpy()

    return feats


def prosody(chunk: np.ndarray) -> np.ndarray:
    import librosa

    f0, voiced, _ = librosa.pyin(chunk, fmin=60, fmax=600, sr=SR, frame_length=1024)
    f0 = f0[voiced & ~np.isnan(f0)]
    st = 12 * np.log2(f0 / 55) if len(f0) else np.array([0.0])
    centroid = librosa.feature.spectral_centroid(y=chunk, sr=SR)[0]
    rms = librosa.feature.rms(y=chunk)[0] + 1e-6
    return np.array(
        [np.percentile(st, 10), np.percentile(st, 50), np.percentile(st, 90), np.log(np.median(centroid)), np.std(np.log(rms))]
    )


def main(normal_path: str, character_path: str) -> None:
    from pyannote.audio import Pipeline

    from troubadour import voices
    from troubadour.audio import load_audio

    clips = {"normal": load_audio(normal_path), "character": load_audio(character_path)}
    half = {k: len(x) / SR / 2 for k, x in clips.items()}
    emb = Pipeline.from_pretrained("pyannote/speaker-diarization-community-1")._embedding
    wavlm = wavlm_features()

    def unit(v):
        return v / (np.linalg.norm(v, axis=-1, keepdims=True) + 1e-9)

    # Train: features of 1 s windows from the first half of each clip.
    train = {}
    for k, x in clips.items():
        ws = list(windows(x, 0, half[k], 1.0))
        train[k] = {
            "speaker": unit(voices.embed_span(emb, x, 0, half[k])),
            "wavlm": unit(np.mean([unit(wavlm(w)) for w in ws], axis=0)),  # (layers, dim)
            "prosody": np.array([prosody(w) for w in ws]),
        }
    pros_all = np.vstack([train[k]["prosody"] for k in train])
    mu, sd = pros_all.mean(0), pros_all.std(0) + 1e-6
    pros_centroid = {k: ((train[k]["prosody"] - mu) / sd).mean(0) for k in train}

    n_layers = train["normal"]["wavlm"].shape[0]
    results = {"speaker": [], "prosody": []} | {f"wavlm L{i}": [] for i in range(n_layers)}
    for w_s in (1.0, 2.0):
        for true, x in clips.items():
            for chunk in windows(x, half[true], len(x) / SR, w_s):
                v = voices.embed_span(emb, chunk, 0, len(chunk) / SR)
                if v is not None:
                    scores = {k: float(v @ train[k]["speaker"]) for k in train}
                    results["speaker"].append((true, scores))
                f = unit(wavlm(chunk))
                for i in range(n_layers):
                    results[f"wavlm L{i}"].append((true, {k: float(f[i] @ train[k]["wavlm"][i]) for k in train}))
                p = (prosody(chunk) - mu) / sd
                results["prosody"].append((true, {k: -float(np.linalg.norm(p - pros_centroid[k])) for k in train}))

    print(f"held-out windows: {len(results['speaker'])} (1 s and 2 s, second half of each clip)")
    rows = []
    for name, items in results.items():
        correct = sum(max(s, key=s.get) == t for t, s in items)
        margin = np.median([s[t] - s["character" if t == "normal" else "normal"] for t, s in items])
        rows.append((correct / len(items), name, margin))
    for acc, name, margin in sorted(rows, reverse=True)[:8] + [r for r in rows if r[1] in ("speaker", "prosody")]:
        print(f"  {name:<12} accuracy {acc:6.1%}   median margin {margin:+.3f}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
