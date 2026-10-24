"""Runtime settings. Everything runs on this machine; override with env vars."""

import os
from pathlib import Path


def env(name: str, default: str | None = None) -> str | None:
    """TROUBADOUR_<name>, or the pre-rename BARD_<name> (existing setups), or the default."""
    return os.environ.get(f"TROUBADOUR_{name}", os.environ.get(f"BARD_{name}", default))


BACKEND = Path(__file__).resolve().parents[2]
REPO = BACKEND.parent

HOST = env("HOST", "127.0.0.1")
PORT = int(env("PORT", "8765"))

# Local OpenAI-compatible LLM for live spelling cleanup (LM Studio default port here is 1235).
LLM_URL = env("LLM_URL", "http://127.0.0.1:1235/v1")
LLM_MODEL = env("LLM_MODEL", "google/gemma-4-26b-a4b-qat")
# Campaign memory (reading notes, writing up sessions) can use a bigger local model than the
# live spelling cleanup; LM Studio loads it on first use if it isn't loaded yet.
MEMORY_LLM_MODEL = env("MEMORY_MODEL", LLM_MODEL)
# "none" suits gemma (it otherwise spends the budget thinking); gpt-oss wants "low"/"medium"/"high".
MEMORY_REASONING = env("MEMORY_REASONING", "none")

WHISPER_MODEL = env("WHISPER", "mlx-community/whisper-large-v3-turbo")
# Re-listen (Improve transcript) only. Full large-v3, not the live turbo model.
# TROUBADOUR_WHISPER_IMPROVE=mlx-community/whisper-large-v3-turbo keeps both passes the same.
WHISPER_IMPROVE_MODEL = env("WHISPER_IMPROVE", "mlx-community/whisper-large-v3-mlx")
# Prompt Whisper with the campaign's names and the last few lines (measured on a real 2.6 h
# session: word errors 12.9% → 11.0%, names spelled right 31% → 86%, same speed). TROUBADOUR_ASR_PROMPT=0: off.
ASR_PROMPT = env("ASR_PROMPT", "1") != "0"
ASR_CONTEXT_LINES = 2
# Spelling cleanup behind the transcriber (names, D&D terms; see cleanup.py). TROUBADOUR_CLEANUP=0: off.
CLEANUP = env("CLEANUP", "1") != "0"
DIARIZER = "pyannote/speaker-diarization-community-1"  # only its speaker-embedding model is used

# Outside the repo so every checkout/worktree shares one set of players, recordings and vault.
DATA_DIR = Path(env("DATA", str(Path.home() / "troubador")))
SAMPLES_DIR = Path(env("SAMPLES", str(BACKEND / "samples" / "eval")))
WEB_DIST = REPO / "web" / "dist"
# Troubadour Cloud (optional backup + web view of the campaign vault). Nothing is sent until the user connects.
CLOUD_URL = env("CLOUD_URL", "https://cloud.5eapps.com")


def vault_dir() -> Path:
    """The campaign wiki: an Obsidian vault of markdown notes. TROUBADOUR_VAULT points it at an existing
    vault (or a folder in one). Resolved per call, so it follows DATA_DIR (tests use a tmp dir)."""
    vault = env("VAULT")
    return Path(vault) if vault else DATA_DIR / "campaign"


SR = 16000

# File replay speed cap. Stress tests raise it (e.g. TROUBADOUR_MAX_REPLAY_SPEED=8); 4× is plenty for demos.
MAX_REPLAY_SPEED = float(env("MAX_REPLAY_SPEED", "4"))
# Keep each mic session's audio at the mic's own rate (data/sessions/<id>/audio.wav; 48 kHz is
# ~345 MB/hour) so it can be listened to, replayed, or re-run with `troubadour-sim`. TROUBADOUR_RECORD=0: off.
RECORD_AUDIO = env("RECORD", "1") != "0"
