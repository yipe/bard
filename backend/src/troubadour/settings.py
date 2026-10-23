"""Model choices saved on this machine. Environment defaults apply until the account menu changes them."""

import json
from urllib.parse import urlparse

from . import config

WHISPER_TURBO = "mlx-community/whisper-large-v3-turbo"
WHISPER_LARGE = "mlx-community/whisper-large-v3-mlx"
SPEAKER_MODEL = "pyannote/speaker-diarization-community-1"

# Shown in the menu. A saved id that is not in this list is still kept.
WHISPER_OPTIONS = (
    {"id": WHISPER_TURBO, "label": "Turbo (faster, live)"},
    {"id": WHISPER_LARGE, "label": "Large (slower)"},
)

_KEYS = ("whisper", "whisper_improve", "llm", "memory")


def path():
    return config.DATA_DIR / "settings.json"


def defaults() -> dict:
    return {
        "whisper": config.WHISPER_MODEL,
        "whisper_improve": config.WHISPER_IMPROVE_MODEL,
        "llm": config.LLM_MODEL,
        "memory": config.MEMORY_LLM_MODEL,
        "llm_url": config.LLM_URL,
        "api_key": "",
    }


def normalize_llm_url(value: str) -> str:
    """A port, host:port, or full URL. A bare port means this machine. The path defaults to /v1."""
    text = value.strip()
    if not text or len(text) > 300:
        raise ValueError("Address must be a port, host:port, or URL")
    if text.isdigit():
        text = f"http://127.0.0.1:{text}/v1"
    elif "://" not in text:
        text = "http://" + text
    parsed = urlparse(text)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("Address must be an http or https URL")
    if parsed.username or parsed.password:
        raise ValueError("Put an API key in the key field, not in the address")
    path = parsed.path.rstrip("/")
    if path in ("", "/"):
        path = "/v1"
    return f"{parsed.scheme}://{parsed.netloc}{path}"


def _read() -> dict:
    try:
        raw = json.loads(path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def saved() -> dict:
    raw = _read()
    return {k: raw[k] for k in _KEYS if isinstance(raw.get(k), str) and raw[k].strip()}


def known_llms() -> list[str]:
    """Model ids this Mac has used, so the menu still lists them when LM Studio is offline."""
    raw = _read().get("known_llms")
    remembered = [m.strip() for m in raw if isinstance(m, str) and m.strip()] if isinstance(raw, list) else []
    chosen = saved()
    seeds = [config.LLM_MODEL, config.MEMORY_LLM_MODEL, chosen.get("llm", ""), chosen.get("memory", "")]
    out: list[str] = []
    for model in (*remembered, *seeds):
        if model and model not in out:
            out.append(model)
    return out


def current() -> dict:
    raw = _read()
    chosen = {**defaults(), **saved()}
    url = raw.get("llm_url")
    if isinstance(url, str) and url.strip():
        chosen["llm_url"] = url.strip()
    key = raw.get("api_key")
    if isinstance(key, str):
        chosen["api_key"] = key
    return chosen


def update(patch: dict) -> dict:
    raw = _read()
    nxt = {k: raw[k] for k in (*_KEYS, "llm_url", "api_key", "known_llms") if k in raw}
    for key in _KEYS:
        value = patch.get(key)
        if value is None:
            continue
        if not isinstance(value, str) or not value.strip() or len(value) > 200:
            raise ValueError(f"{key} must be a model id")
        nxt[key] = value.strip()
    if "llm_url" in patch and patch["llm_url"] is not None:
        nxt["llm_url"] = normalize_llm_url(patch["llm_url"])
    if "api_key" in patch and patch["api_key"] is not None:
        key = patch["api_key"]
        if not isinstance(key, str) or len(key) > 400:
            raise ValueError("API key is too long")
        nxt["api_key"] = key.strip()
    known = known_llms()
    for model in (nxt.get("llm"), nxt.get("memory")):
        if model and model not in known:
            known.append(model)
    nxt["known_llms"] = known
    path().parent.mkdir(parents=True, exist_ok=True)
    path().write_text(json.dumps(nxt, indent=2) + "\n", encoding="utf-8")
    return current()
