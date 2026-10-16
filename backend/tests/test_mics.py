"""Mic details: catalog + the user's corrections + measurements → a ✓/✗/? checklist."""

import json

import pytest
from fastapi.testclient import TestClient

from bard import config, mics, server


def by_id(checks):
    return {c["id"]: c["ok"] for c in checks}


@pytest.mark.parametrize(
    ("name", "model"),
    [
        ("Yeti GX", "Logitech G Yeti GX"),
        ("Yeti Stereo Microphone", "Blue Yeti"),
        ("Yeti Nano", "Blue Yeti Nano"),
        ("MacBook Pro Microphone", "MacBook built-in mics"),
    ],
)
def test_catalog(name, model):
    assert mics.specs(name)["model"] == model


def test_yeti_gx_gets_little_xs():
    usb = {"name": "Yeti GX", "connection": "USB", "rate": 48000}
    checks = by_id(mics.checks(usb, mics.specs("Yeti GX"), []))
    assert checks == {"around": False, "distance": False, "wired": True, "rate": True}


def test_a_switchable_condenser_passes():
    checks = mics.checks({"connection": "USB", "rate": 48000}, mics.specs("Yeti Stereo Microphone"), [])
    assert by_id(checks)["around"] is True
    assert "set it to omni" in checks[0]["detail"]
    assert by_id(checks)["distance"] is True


def test_unknown_and_wireless():
    checks = by_id(mics.checks({"connection": "Bluetooth", "rate": 16000}, mics.specs("Mystery Mic 3000"), []))
    assert checks == {"around": None, "distance": None, "wired": False, "rate": False}


def test_measurements_add_a_check(dirs):
    measured = [{"session": "x", "started_at": 2, "verdict": "microphone", "reasons": ["Omar is quiet"]}]
    checks = mics.checks({"connection": "USB", "rate": 48000}, mics.specs("Yeti GX"), measured)
    assert checks[-1]["id"] == "measured"
    assert checks[-1]["ok"] is False


def test_corrections_win_and_measurements_are_found(dirs):
    mics.set_override("Mystery Mic 3000", {"capsule": "condenser", "patterns": ["omni"]})
    assert mics.specs("Mystery Mic 3000") == {"source": "you", "capsule": "condenser", "patterns": ["omni"]}
    folder = config.DATA_DIR / "sessions" / "2026-10-08_193401-eebf"
    folder.mkdir(parents=True)
    (folder / "meta.json").write_text(
        json.dumps({"id": folder.name, "started_at": 5, "source": {"kind": "mic", "device": None, "device_name": "Yeti GX"}})
    )
    (folder / "quality.json").write_text(json.dumps({"report": {"verdict": {"kind": "settings", "reasons": ["raise the gain"]}}}))
    assert mics.measured(config.DATA_DIR / "sessions", "Yeti GX")[0]["verdict"] == "settings"  # "the default mic" still counts


def test_api(dirs, monkeypatch):
    monkeypatch.setattr(
        mics,
        "system_devices",
        lambda: [{"name": "Yeti GX", "maker": "Logitech", "connection": "USB", "rate": 48000, "default": True}],
    )
    client = TestClient(server.app, base_url="http://127.0.0.1:8765")
    (gx,) = client.get("/api/mics").json()
    assert gx["specs"]["patterns"] == ["supercardioid"]
    assert by_id(gx["checks"])["around"] is False
    r = client.patch("/api/mics/Yeti GX", json={"patterns": ["omni"]})
    assert by_id(r.json()["checks"])["around"] is True
    assert client.patch("/api/mics/Yeti GX", json={"patterns": ["round"]}).status_code == 422


def test_system_devices_are_read_again_when_an_input_appears(monkeypatch):
    inputs = [{"name": "MacBook Pro Microphone", "default": True}]
    runs = []

    class Done:
        stdout = json.dumps({"SPAudioDataType": [{"_items": []}]})

    monkeypatch.setattr(mics, "_CACHE", (0.0, [], []))
    monkeypatch.setattr(mics, "list_mics", lambda: inputs)
    monkeypatch.setattr(mics.subprocess, "run", lambda *a, **k: runs.append(1) or Done())
    mics.system_devices()
    mics.system_devices()
    assert len(runs) == 1  # cached while nothing changes
    inputs.append({"name": "Yeti Stereo Microphone", "default": False})
    mics.system_devices()
    assert len(runs) == 2
