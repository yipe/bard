"""Runtime settings. Everything runs on this machine; override with env vars."""

import os
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]
REPO = BACKEND.parent

HOST = os.environ.get("BARD_HOST", "127.0.0.1")
PORT = int(os.environ.get("BARD_PORT", "8765"))

# Local OpenAI-compatible LLM for live spelling cleanup (LM Studio default port here is 1235).
LLM_URL = os.environ.get("BARD_LLM_URL", "http://127.0.0.1:1235/v1")
LLM_MODEL = os.environ.get("BARD_LLM_MODEL", "google/gemma-4-26b-a4b-qat")
# Campaign memory (reading notes, writing up sessions) can use a bigger local model than the
# live spelling cleanup; LM Studio loads it on first use if it isn't loaded yet.
MEMORY_LLM_MODEL = os.environ.get("BARD_MEMORY_MODEL", LLM_MODEL)
# "none" suits gemma (it otherwise spends the budget thinking); gpt-oss wants "low"/"medium"/"high".
MEMORY_REASONING = os.environ.get("BARD_MEMORY_REASONING", "none")

WHISPER_MODEL = os.environ.get("BARD_WHISPER", "mlx-community/whisper-large-v3-turbo")
# Prompt Whisper with the campaign's names and the last few lines (measured on a real 2.6 h
# session: word errors 12.9% → 11.0%, names spelled right 31% → 86%, same speed). BARD_ASR_PROMPT=0: off.
ASR_PROMPT = os.environ.get("BARD_ASR_PROMPT", "1") != "0"
ASR_CONTEXT_LINES = 2
# Spelling cleanup behind the transcriber (names, D&D terms; see cleanup.py). BARD_CLEANUP=0: off.
CLEANUP = os.environ.get("BARD_CLEANUP", "1") != "0"
DIARIZER = "pyannote/speaker-diarization-community-1"  # only its speaker-embedding model is used

DATA_DIR = Path(os.environ.get("BARD_DATA", BACKEND / "data"))
SAMPLES_DIR = Path(os.environ.get("BARD_SAMPLES", BACKEND / "samples" / "eval"))
WEB_DIST = REPO / "web" / "dist"
# Bard Cloud (optional backup + web view of the campaign vault). Nothing is sent until the user connects.
CLOUD_URL = os.environ.get("BARD_CLOUD_URL", "https://cloud.5eapps.com")


def vault_dir() -> Path:
    """The campaign wiki: an Obsidian vault of markdown notes. BARD_VAULT points it at an existing
    vault (or a folder in one). Resolved per call, so it follows DATA_DIR (tests use a tmp dir)."""
    env = os.environ.get("BARD_VAULT")
    return Path(env) if env else DATA_DIR / "campaign"


SR = 16000

# File replay speed cap. Stress tests raise it (e.g. BARD_MAX_REPLAY_SPEED=8); 4× is plenty for demos.
MAX_REPLAY_SPEED = float(os.environ.get("BARD_MAX_REPLAY_SPEED", "4"))
# Keep each mic session's audio at the mic's own rate (data/sessions/<id>/audio.wav; 48 kHz is
# ~345 MB/hour) so it can be listened to, replayed, or re-run with `bard-sim`. BARD_RECORD=0: off.
RECORD_AUDIO = os.environ.get("BARD_RECORD", "1") != "0"
