"""What Troubadour knows about each microphone, and whether it suits the middle of a table.

Three sources, because no API reports a mic's pickup pattern or capsule type:
1. macOS (system_profiler): name, maker, connection (built-in, USB, Bluetooth…), sample rate.
2. A small catalog of common mics (below) for capsule, pickup patterns and switches; a person's
   own corrections in data/mics.json win over it.
3. Measurements: the quality verdicts of sessions recorded with that mic.

`checks` turns that into the ✓ / ✗ / ? list the UI shows.
"""

import json
import re
import subprocess
import time
from pathlib import Path

from . import config
from .audio import list_mics

# (pattern on the device name, specs). Patterns: what the capsule can do; switchable = several.
CATALOG: list[tuple[str, dict]] = [
    (
        r"yeti\s*gx",
        {"model": "Logitech G Yeti GX", "capsule": "dynamic", "patterns": ["supercardioid"], "bits": 24, "max_rate": 96000},
    ),
    (
        r"yeti\s*x\b",
        {
            "model": "Blue Yeti X",
            "capsule": "condenser",
            "patterns": ["cardioid", "omni", "figure-8", "stereo"],
            "bits": 24,
            "max_rate": 48000,
        },
    ),
    (
        r"yeti\s*nano",
        {"model": "Blue Yeti Nano", "capsule": "condenser", "patterns": ["cardioid", "omni"], "bits": 24, "max_rate": 48000},
    ),
    (
        r"yeti",
        {
            "model": "Blue Yeti",
            "capsule": "condenser",
            "patterns": ["cardioid", "omni", "figure-8", "stereo"],
            "bits": 16,
            "max_rate": 48000,
        },
    ),
    (
        r"macbook.*microphone",
        {
            "model": "MacBook built-in mics",
            "capsule": "array",
            "patterns": ["beamforming"],
            "bits": 24,
            "max_rate": 48000,
            "notes": "Three-mic array that steers toward whoever faces the screen.",
        },
    ),
    (r"iphone.*microphone", {"model": "iPhone (Continuity)", "capsule": "array", "patterns": ["beamforming"], "wireless": True}),
    (
        r"airpods",
        {
            "model": "AirPods",
            "capsule": "array",
            "patterns": ["beamforming"],
            "wireless": True,
            "notes": "Built to hear the wearer and cancel everyone else.",
        },
    ),
    (
        r"jabra\s*speak",
        {
            "model": "Jabra Speak",
            "capsule": "condenser",
            "patterns": ["omni"],
            "notes": "A conference speakerphone: made for the middle of a table.",
        },
    ),
    (
        r"quadcast",
        {
            "model": "HyperX QuadCast",
            "capsule": "condenser",
            "patterns": ["cardioid", "omni", "figure-8", "stereo"],
            "bits": 16,
            "max_rate": 48000,
        },
    ),
    (r"shure\s*mv7|\bmv7\b", {"model": "Shure MV7", "capsule": "dynamic", "patterns": ["cardioid"]}),
    (r"podmic", {"model": "Rode PodMic", "capsule": "dynamic", "patterns": ["cardioid"]}),
    (r"nt-?usb", {"model": "Rode NT-USB", "capsule": "condenser", "patterns": ["cardioid"]}),
    (r"q2u", {"model": "Samson Q2U", "capsule": "dynamic", "patterns": ["cardioid"]}),
    (r"at2020", {"model": "Audio-Technica AT2020USB", "capsule": "condenser", "patterns": ["cardioid"]}),
    (r"wave\s*:?\s*3", {"model": "Elgato Wave:3", "capsule": "condenser", "patterns": ["cardioid"]}),
]

AROUND = {"omni", "stereo", "boundary"}  # patterns that hear a whole table
_TRANSPORT = {
    "coreaudio_device_type_builtin": "built-in",
    "coreaudio_device_type_usb": "USB",
    "coreaudio_device_type_bluetooth": "Bluetooth",
    "coreaudio_device_type_bluetooth_le": "Bluetooth",
    "coreaudio_device_type_virtual": "virtual",
    "coreaudio_device_type_aggregate": "aggregate",
}
_CACHE: tuple[float, list[str], list[dict]] = (0.0, [], [])


def system_devices() -> list[dict]:
    """Input devices as macOS describes them. Cached 30 s (system_profiler takes ~0.5 s), but read
    again as soon as an input is plugged in or removed."""
    global _CACHE
    inputs = sorted(m["name"] for m in list_mics())
    if time.monotonic() - _CACHE[0] < 30 and _CACHE[1] == inputs:
        return _CACHE[2]
    try:
        raw = subprocess.run(
            ["system_profiler", "SPAudioDataType", "-json"], capture_output=True, text=True, timeout=10, check=True
        ).stdout
        items = json.loads(raw)["SPAudioDataType"][0].get("_items", [])
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, IndexError):
        items = []
    out = [
        {
            "name": d["_name"],
            "maker": d.get("coreaudio_device_manufacturer"),
            "connection": _TRANSPORT.get(d.get("coreaudio_device_transport", ""), "other"),
            "rate": d.get("coreaudio_device_srate"),
            "default": d.get("coreaudio_default_audio_input_device") == "spaudio_yes",
        }
        for d in items
        if d.get("coreaudio_device_input")
    ]
    _CACHE = (time.monotonic(), inputs, out)
    return out


def overrides_path() -> Path:
    return config.DATA_DIR / "mics.json"


def overrides() -> dict[str, dict]:
    try:
        data = json.loads(overrides_path().read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def set_override(name: str, fields: dict) -> dict:
    data = overrides()
    data[name] = {k: v for k, v in {**data.get(name, {}), **fields}.items() if v is not None}
    path = overrides_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1))
    tmp.replace(path)
    return data[name]


def specs(name: str) -> dict:
    """Catalog specs for a device name, with the user's corrections on top."""
    found = next((dict(s, source="catalog") for pattern, s in CATALOG if re.search(pattern, name, re.I)), {"source": "unknown"})
    mine = overrides().get(name)
    return {**found, **mine, "source": "you"} if mine else found


def checks(os_info: dict, spec: dict, measured: list[dict]) -> list[dict]:
    """ok: True ✓, False ✗, None ? (not known). Ordered by how much each matters at a table."""
    out = []
    patterns = spec.get("patterns")
    if not patterns:
        out.append(
            {
                "id": "around",
                "label": "Hears the whole table",
                "ok": None,
                "detail": "Pickup pattern unknown: set it in the mic's details.",
            }
        )
    elif AROUND & set(patterns):
        detail = "Switchable: set it to omni." if len(patterns) > 1 else "Omnidirectional."
        out.append({"id": "around", "label": "Hears the whole table", "ok": True, "detail": detail})
    else:
        out.append(
            {
                "id": "around",
                "label": "Hears the whole table",
                "ok": False,
                "detail": f"{patterns[0].capitalize()} only: it favours whoever it points at.",
            }
        )
    capsule = spec.get("capsule")
    out.append(
        {
            "id": "distance",
            "label": "Picks up voices across the table",
            "ok": None if not capsule else capsule != "dynamic",
            "detail": {
                "dynamic": "Dynamic capsule: built for a mouth a few inches away.",
                "condenser": "Condenser capsule: sensitive to distant voices.",
                "array": "Mic array: decent at a distance, but steers toward one direction.",
            }.get(capsule, "Capsule type unknown."),
        }
    )
    wireless = spec.get("wireless") or os_info.get("connection") == "Bluetooth"
    out.append(
        {
            "id": "wired",
            "label": "Steady connection",
            "ok": not wireless,
            "detail": "Wireless: dropouts and lower-quality audio." if wireless else f"{os_info.get('connection') or 'Wired'}.",
        }
    )
    rate = os_info.get("rate") or spec.get("max_rate")
    out.append(
        {
            "id": "rate",
            "label": "Full-quality audio (≥ 44.1 kHz)",
            "ok": None if not rate else rate >= 44100,
            "detail": f"{rate / 1000:g} kHz." if rate else "Sample rate unknown.",
        }
    )
    if measured:
        last = measured[0]
        kind = last["verdict"]
        out.append(
            {
                "id": "measured",
                "label": "Sounded good at your table",
                "ok": True if kind == "good" else False if kind == "microphone" else None,
                "detail": {
                    "good": "Your last recording with it measured well.",
                    "settings": "Your last recording needs setting changes.",
                    "microphone": "Your last recording showed problems settings can't fix.",
                }[kind]
                + f" ({len(measured)} recording{'s' if len(measured) != 1 else ''} measured.)",
            }
        )
    return out


def measured(sessions_dir: Path, name: str) -> list[dict]:
    """Quality verdicts of recordings made with this mic, newest first."""
    out = []
    for meta in sessions_dir.glob("*/meta.json"):
        try:
            m = json.loads(meta.read_text())
            src = m.get("source") or {}
            if src.get("kind") != "mic" or name not in (src.get("device"), src.get("device_name")):
                continue
            q = json.loads((meta.parent / "quality.json").read_text())["report"]
        except (OSError, ValueError, KeyError, TypeError):
            continue
        out.append(
            {
                "session": m["id"],
                "started_at": m.get("started_at", 0),
                "verdict": q["verdict"]["kind"],
                "reasons": q["verdict"]["reasons"],
            }
        )
    return sorted(out, key=lambda r: -r["started_at"])


def describe(sessions_dir: Path) -> list[dict]:
    """Every input device: what macOS says, its specs, measurements, and the checklist."""
    out = []
    for d in system_devices():
        spec = specs(d["name"])
        meas = measured(sessions_dir, d["name"])
        out.append({**d, "specs": spec, "measured": meas, "checks": checks(d, spec, meas)})
    return out
