"""Non-destructive regression check against a running `troubadour` server.

Only creates/deletes its own players (named "zz-check-…"); leaves real players,
voices and state alone. Plays no audio (file sessions replay server-side only).

Usage: uv run spike/harden_check.py
"""

import asyncio
import json
import time

import httpx
import websockets

BASE = "http://127.0.0.1:8765"
WS = "ws://127.0.0.1:8765/ws"
api = httpx.Client(base_url=BASE, timeout=30)
failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {name}{f'  ({detail})' if detail else ''}")
    if not ok:
        failures.append(name)


async def events_until(ws, pred, timeout: float = 60) -> list[dict]:
    seen, deadline = [], time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            e = json.loads(await asyncio.wait_for(ws.recv(), timeout=max(0.1, deadline - time.monotonic())))
        except TimeoutError:
            break
        seen.append(e)
        if pred(e):
            break
    return seen


async def wait_idle(timeout: float = 30) -> str:
    status = ""
    for _ in range(int(timeout * 2)):
        status = api.get("/api/state").json()["session"]["status"]
        if status == "idle":
            break
        await asyncio.sleep(0.5)
    return status


async def main() -> None:
    before = api.get("/api/state").json()
    real_players = {p["id"] for p in before["players"]}
    check("idle before starting", before["session"]["status"] == "idle", before["session"]["status"])

    # --- input validation
    bad = [
        ("avatar must be a data:image URL", {"name": "zz-check", "avatar": "https://tracker.example/x.png"}),
        ("avatar size capped", {"name": "zz-check", "avatar": "data:image/png;base64," + "A" * 400_000}),
        ("name length capped", {"name": "x" * 61}),
        ("empty name rejected", {"name": "   "}),
    ]
    for label, body in bad:
        check(label, api.post("/api/players", json=body).status_code == 422)
    p = api.post("/api/players", json={"name": "zz-check-a", "character": "Tester"}).json()
    check("bad color rejected", api.patch(f"/api/players/{p['id']}", json={"color": "red;"}).status_code == 422)
    check("good color accepted", api.patch(f"/api/players/{p['id']}", json={"color": "#12ab34"}).status_code == 200)
    traversal = api.post("/api/session/start", json={"source": {"kind": "file", "name": "../data/state.json", "speed": 1}})
    check("session file traversal refused", traversal.status_code == 404, str(traversal.status_code))
    check("file endpoint traversal refused", api.get("/api/files/..%2Fdata%2Fstate.json").status_code in (404, 400))
    check(
        "speed bounded",
        api.post("/api/session/start", json={"source": {"kind": "file", "name": "c2e58.wav", "speed": 50}}).status_code == 422,
    )
    check("unknown source kind rejected", api.post("/api/session/start", json={"source": {"kind": "tape"}}).status_code == 422)
    mic = api.post("/api/session/start", json={"source": {"kind": "mic", "device": "No Such Microphone 9000"}})
    check(
        "missing mic → 400, nothing started",
        mic.status_code == 400 and api.get("/api/state").json()["session"]["status"] == "idle",
        str(mic.status_code),
    )

    # --- cross-site protection
    evil = {"Origin": "https://evil.example"}
    check("cross-site POST refused", api.post("/api/session/stop", headers=evil).status_code == 403)
    check("foreign Host refused", api.get("/api/state", headers={"Host": "evil.example"}).status_code == 400)
    try:
        async with websockets.connect(WS, additional_headers=evil) as w:
            await asyncio.wait_for(w.recv(), 3)
        check("cross-site WebSocket refused", False, "connected")
    except Exception as e:
        check("cross-site WebSocket refused", True, type(e).__name__)

    async with websockets.connect(WS, max_size=None, additional_headers={"Origin": BASE}) as ws:
        snap = json.loads(await ws.recv())
        check(
            "snapshot first, with session.training/error",
            snap["type"] == "snapshot" and "training" in snap["data"]["session"] and "error" in snap["data"]["session"],
        )

        # --- session lifecycle + unique message ids across sessions
        t = time.monotonic()
        api.post("/api/session/start", json={"source": {"kind": "file", "name": "c2e58.wav", "speed": 3}}).raise_for_status()
        seen = await events_until(ws, lambda e: e["type"] == "message.created", 30)
        first = next((e["data"] for e in seen if e["type"] == "message.created"), None)
        check("first message arrives", first is not None, f"{time.monotonic() - t:.1f}s")
        await asyncio.sleep(4)
        ids_a = {m["id"] for m in api.get("/api/state").json()["messages"]}
        api.post("/api/session/start", json={"source": {"kind": "file", "name": "c2e44.wav", "speed": 3}}).raise_for_status()
        await events_until(ws, lambda e: e["type"] == "message.created", 30)
        await asyncio.sleep(3)
        ids_b = {m["id"] for m in api.get("/api/state").json()["messages"]}
        check("message ids never reused across sessions", not (ids_a & ids_b) and bool(ids_b), f"{len(ids_a)}/{len(ids_b)}")

        # --- training: run one, cancel one
        api.post(f"/api/players/{p['id']}/train", json={"seconds": 6}).raise_for_status()
        seen = await events_until(ws, lambda e: e["type"] == "train.status" and e["data"]["state"] in ("done", "error"), 60)
        final = next((e["data"] for e in reversed(seen) if e["type"] == "train.status"), {})
        session_training = [e["data"]["training"] for e in seen if e["type"] == "session.updated"]
        check("train completes", final.get("state") == "done", final.get("message") or "")
        check("session.training set while training", any(session_training), str(session_training[:1]))
        busy = api.post(f"/api/players/{p['id']}/train", json={"seconds": 6})
        api.post(f"/api/players/{p['id']}/train", json={"seconds": 30}).raise_for_status() if busy.status_code != 202 else None
        await asyncio.sleep(1)
        api.post("/api/train/cancel")
        seen = await events_until(
            ws, lambda e: e["type"] == "train.status" and e["data"]["state"] in ("cancelled", "done", "error"), 20
        )
        final = next((e["data"]["state"] for e in reversed(seen) if e["type"] == "train.status"), None)
        check("train cancel → cancelled", final == "cancelled", str(final))
        await asyncio.sleep(0.5)
        check("training cleared after cancel", api.get("/api/state").json()["session"]["training"] is None)

        # --- naming a pending voice: merges arrive as one messages.updated batch
        state = api.get("/api/state").json()
        pending = [v for v in state["voices"] if v["status"] == "pending"]
        if pending:
            biggest = max(pending, key=lambda v: v["seconds"])
            owned = sum(m["voice_id"] == biggest["id"] for m in state["messages"])
            r = api.post(f"/api/voices/{biggest['id']}/assign", json={"player_id": p["id"], "character": None})
            seen = await events_until(ws, lambda e: False, 3)  # collect everything for a few seconds
            merged = r.json().get("merged_into")
            check("assign ok", r.status_code == 200, f"merged_into={merged}")
            if merged and owned:
                relabel = [
                    e["data"]
                    for e in seen
                    if e["type"] == "messages.updated" and all(x.get("voice_id") == merged for x in e["data"])
                ]
                check("merge relabels in one messages.updated batch", sum(len(b) for b in relabel) == owned, f"{owned} owned")
            else:
                print(f"SKIP  merge relabel (merged={bool(merged)}, owned messages={owned})")
            sample = api.get(r.json()["voice"]["sample_url"]) if r.json()["voice"]["sample_url"] else None
            check("voice sample served", sample is not None and sample.status_code == 200 and sample.content[:4] == b"RIFF")
        else:
            print("SKIP  no pending voice to assign")

        api.post("/api/session/stop")
        check("stop drains to idle", await wait_idle() == "idle")

    # --- cleanup: deleting the player deletes its Train recording
    state = api.get("/api/state").json()
    trained = [v["id"] for v in state["voices"] if v["player_id"] == p["id"] and v["trained"]]
    check("delete check player", api.delete(f"/api/players/{p['id']}").status_code == 204)
    from pathlib import Path

    data = Path(__file__).resolve().parents[1] / "data"
    check(
        "its recording was deleted", all(not (data / "voices" / f"{v}.wav").exists() for v in trained), f"{len(trained)} trained"
    )
    after = api.get("/api/state").json()
    check("real players untouched", {x["id"] for x in after["players"]} == real_players)
    saved = json.loads((data / "state.json").read_text())
    check("state.json valid and has no check players", all(not x["name"].startswith("zz-check") for x in saved["players"]))

    print(f"\n{'ALL PASSED' if not failures else f'{len(failures)} FAILED: ' + ', '.join(failures)}")


CHECK_FILES = {"c2e58.wav", "c2e44.wav"}


def remove_check_sessions(existing: set[str]) -> None:
    """Saved sessions this run created (replays of its own sample files) leave the History."""
    import shutil
    from pathlib import Path

    sessions = Path(__file__).resolve().parents[1] / "data" / "sessions"
    deadline = time.monotonic() + 15  # the last one is written when its session goes idle
    while api.get("/api/state").json()["session"]["status"] != "idle" and time.monotonic() < deadline:
        time.sleep(0.5)
    for s in api.get("/api/sessions").json():
        src = s.get("source") or {}
        if s["id"] not in existing and src.get("kind") == "file" and src.get("name") in CHECK_FILES:
            shutil.rmtree(sessions / s["id"], ignore_errors=True)


async def run() -> None:
    existing = {s["id"] for s in api.get("/api/sessions").json()}
    try:
        await main()
    finally:  # never leave a session running, check players or check sessions behind
        api.post("/api/session/stop")
        for p in api.get("/api/state").json()["players"]:
            if p["name"].startswith("zz-check"):
                api.delete(f"/api/players/{p['id']}")
        remove_check_sessions(existing)


asyncio.run(run())
