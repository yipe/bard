"""Session transcripts: a JSON record (everything) and a readable Markdown file.

Pure functions over the same JSON shapes the API sends (Player/Voice/Message
.to_json()), so the server, the autosave, and tests all share one formatter.
Speaker naming follows the UI (web/src/lib/speaker.ts).
"""

import re
import time

VERSION = 1

# Hesitation sounds, not words. A line of nothing else ("Um...", "Uh, hmm.") is dropped; inside a
# real line they stay. "Mm-hmm" / "uh-huh" are answers, and "oh"/"ah" reactions: kept.
FILLERS = frozenset({"um", "umm", "uhm", "uh", "uhh", "er", "erm", "hm", "hmm", "mm", "mmm"})


def filler_only(text: str) -> bool:
    words = [w.strip("-'") for w in re.findall(r"[a-z'-]+", text.lower())]
    words = [w for w in words if w]
    return bool(words) and all(w in FILLERS for w in words)


def without_fillers(record: dict) -> dict:
    """A saved record minus filler-only lines (records saved before they were dropped have them)."""
    messages = [m for m in record["messages"] if not filler_only(m["text"])]
    if len(messages) == len(record["messages"]):
        return record
    return record | {"messages": messages, "session": record["session"] | {"lines": len(messages)}}


def utf16_to_py(text: str, units: int) -> int:
    count = 0
    for i, ch in enumerate(text):
        if count >= units:
            return i
        count += 2 if ord(ch) > 0xFFFF else 1
    return len(text)


def utf16_offset(text: str, index: int) -> int:
    """Python str index → UTF-16 code-unit offset (what JavaScript string indices use)."""
    return len(text[:index].encode("utf-16-le")) // 2


def clock(seconds: float) -> str:
    s = int(seconds)
    h, m, s = s // 3600, s % 3600 // 60, s % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def speaker(voice_id: str | None, voices: dict, players: dict) -> dict:
    """{name, player_id, character} for a message's voice, as the UI shows it."""
    v = voices.get(voice_id) if voice_id else None
    p = players.get(v["player_id"]) if v and v.get("player_id") else None
    if p is None:
        return {"name": v["label"] if v else "Unknown", "player_id": None, "character": None}
    has_character_voice = any(
        o.get("character") and o.get("player_id") == p["id"] for o in voices.values() if o["status"] == "named"
    )
    character = v.get("character") or (None if has_character_voice else p.get("character") or None)
    return {"name": p["name"], "player_id": p["id"], "character": character, "gm": p.get("is_gm", False)}


def build(meta: dict, players: list[dict], voices: list[dict], messages: list[dict]) -> dict:
    """The JSON record. meta: {id, started_at (epoch s), source, duration_s}."""
    by_player = {p["id"]: p for p in players}
    by_voice = {v["id"]: v for v in voices}
    out_messages = []
    for m in sorted(messages, key=lambda m: m["t_start"]):
        if filler_only(m["text"]):
            continue
        who = speaker(m["voice_id"], by_voice, by_player)
        text = m["text"]
        out_messages.append(
            {
                "id": m["id"],
                "t_start": m["t_start"],
                "t_end": m["t_end"],
                "speaker": who["name"],
                "player_id": who["player_id"],
                "character": who["character"],
                "voice_id": m["voice_id"],
                "provisional": m["provisional"],
                "confidence": m["confidence"],
                "text": text,
                "fixes": m.get("fixes", []),
                "level_db": m.get("level_db"),
            }
        )
    return {
        "version": VERSION,
        "session": meta | {"lines": len(out_messages)},
        "players": [{k: p.get(k) for k in ("id", "name", "character", "is_gm", "color")} for p in players],
        "voices": [
            {k: v[k] for k in ("id", "label", "player_id", "character", "trained")} for v in voices if v["status"] == "named"
        ],
        "messages": out_messages,
    }


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("*", "\\*").replace("_", "\\_").replace("`", "\\`")


def _who(m: dict, gm_ids: set[str]) -> str:
    name = m["speaker"] + (" (GM)" if m["player_id"] in gm_ids else "")
    if m["character"]:
        name += f" as {m['character']}"
    return name + (" (?)" if m["provisional"] else "")


def _source(src: dict | None) -> str:
    if not src:
        return "unknown"
    if src["kind"] == "file":
        return f"{src['name']} (replay, {src.get('speed', 1):g}×)"
    if src["kind"] == "session":
        return f"replay of session {src['id']} ({src.get('speed', 1):g}×)"
    return f"microphone ({src.get('device') or 'default'})"


def markdown(record: dict) -> str:
    s = record["session"]
    gm_ids = {p["id"] for p in record["players"] if p.get("is_gm")}
    started = time.strftime("%Y-%m-%d %H:%M", time.localtime(s["started_at"]))
    roster = ", ".join(
        p["name"] + (" (GM)" if p["is_gm"] else "") + (f" as {p['character']}" if p["character"] else "")
        for p in record["players"]
    )
    lines = [
        f"# Bard session · {started}",
        "",
        f"- Source: {_source(s.get('source'))}",
        f"- Length: {clock(s.get('duration_s', 0))} · {s['lines']} lines",
        f"- Players: {roster or 'none'}",
        "",
        "---",
        "",
    ]
    for m in record["messages"]:
        lines += [f"**{clock(m['t_start'])} · {_escape(_who(m, gm_ids))}:** {_escape(m['text'])}", ""]
    return "\n".join(lines)
