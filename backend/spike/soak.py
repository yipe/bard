"""Soak test: replay hours of real table audio through a throwaway bard server and watch it.

Run a separate server so your real one (players, voices, open tabs) is untouched:

  BARD_PORT=8766 BARD_DATA=/tmp/bard-soak BARD_SAMPLES=samples/soak uv run bard
  uv run spike/soak.py --port 8766 --file soak_4h.wav --speed 4

Every --sample-every seconds it records server RSS, audio lag, message/voice
counts and event rates to samples/soak/soak.csv. Every --name-every seconds it
names the biggest pending voice (round-robin over soak players) to exercise
merges and refine at scale. At the end it reconnects and times the snapshot.
"""

import argparse
import asyncio
import csv
import json
import subprocess
import time
from collections import Counter
from pathlib import Path

import httpx
import websockets

OUT = Path(__file__).resolve().parents[1] / "samples" / "soak" / "soak.csv"


def server_pid(port: int) -> int:
    out = subprocess.run(["lsof", "-ti", f"tcp:{port}", "-sTCP:LISTEN"], capture_output=True, text=True).stdout.split()
    return int(out[0])


def rss_mb(pid: int) -> float:
    out = subprocess.run(["ps", "-o", "rss=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    return int(out) / 1024 if out else float("nan")


async def main(args) -> None:
    base = f"http://127.0.0.1:{args.port}"
    api = httpx.Client(base_url=base, timeout=60)
    pid = server_pid(args.port)
    players = [api.post("/api/players", json={"name": f"soak-{c}"}).json()["id"] for c in "ABCD"]
    counts: Counter = Counter()
    lags: list[float] = []  # audio seconds between a line ending and its message arriving
    errors: list[str] = []
    t_start = time.monotonic()
    done = asyncio.Event()

    async def listen():
        async with websockets.connect(f"ws://127.0.0.1:{args.port}/ws", max_size=None) as ws:
            await ws.recv()  # snapshot
            api.post(
                "/api/session/start", json={"source": {"kind": "file", "name": args.file, "speed": args.speed}}
            ).raise_for_status()
            async for raw in ws:
                e = json.loads(raw)
                counts[e["type"]] += 1
                if e["type"] == "message.created":
                    audio_now = (time.monotonic() - t_start) * args.speed
                    lags.append(audio_now - e["data"]["t_end"])
                elif e["type"] == "error":
                    errors.append(e["data"]["message"])
                elif e["type"] == "session.updated" and e["data"]["status"] == "idle" and counts["message.created"]:
                    done.set()
                    return

    listener = asyncio.create_task(listen())
    OUT.parent.mkdir(parents=True, exist_ok=True)
    rows, next_name, turn = [], args.name_every, 0
    rss0 = rss_mb(pid)
    with OUT.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "wall_s",
                "audio_s",
                "rss_mb",
                "messages",
                "voices_pending",
                "voices_named",
                "llm_ok",
                "lag_p50",
                "lag_max",
                "events",
                "status",
            ]
        )
        while not done.is_set():
            try:
                await asyncio.wait_for(done.wait(), timeout=args.sample_every)
            except TimeoutError:
                pass
            if listener.done() and listener.exception():
                errors.append(f"websocket: {listener.exception()!r}")
                break
            wall = time.monotonic() - t_start
            s = api.get("/api/state").json()
            window, lags[:] = sorted(lags), []
            voices = s["voices"]
            row = [
                round(wall),
                s["session"]["t"],
                round(rss_mb(pid)),
                len(s["messages"]),
                sum(v["status"] == "pending" for v in voices),
                sum(v["status"] == "named" for v in voices),
                s["session"]["llm"]["ok"],
                round(window[len(window) // 2], 1) if window else "",
                round(window[-1], 1) if window else "",
                sum(counts.values()),
                s["session"]["status"],
            ]
            w.writerow(row)
            f.flush()
            rows.append(row)
            print(
                " ".join(
                    f"{k}={v}"
                    for k, v in zip(
                        ["wall", "audio", "rss", "msgs", "ann", "pend", "named", "llm", "lag50", "lagmax", "ev", "st"],
                        row,
                        strict=True,
                    )
                ),
                flush=True,
            )
            if wall >= next_name:
                next_name += args.name_every
                pending = [v for v in voices if v["status"] == "pending"]
                if pending:
                    biggest = max(pending, key=lambda v: v["seconds"])
                    r = api.post(f"/api/voices/{biggest['id']}/assign", json={"player_id": players[turn % len(players)]})
                    turn += 1
                    print(
                        f"  named {biggest['label']} ({biggest['seconds']}s) -> {r.status_code} merged={r.json().get('merged_into') is not None}",
                        flush=True,
                    )
            if wall > args.timeout:
                errors.append("timeout")
                break

    t = time.monotonic()
    async with websockets.connect(f"ws://127.0.0.1:{args.port}/ws", max_size=None) as ws:
        snap = await ws.recv()
    snap_s = time.monotonic() - t
    final = api.get("/api/state").json()
    print("\n=== soak summary ===")
    print(
        f"wall {rows[-1][0] / 60:.1f} min, audio {final['session']['t'] / 3600:.2f} h at {args.speed}x, status {final['session']['status']}"
    )
    print(f"RSS start {rss0:.0f} MB, max {max(r[2] for r in rows)} MB, end {rows[-1][2]} MB")
    print(f"messages {len(final['messages'])}, voices {len(final['voices'])}")
    lag_maxes = [r[8] for r in rows if r[8] != ""]
    print(
        f"lag (audio s behind) worst window max {max(lag_maxes) if lag_maxes else '-'}; last-hour p50s {[r[7] for r in rows[-6:]]}"
    )
    print(f"snapshot on reconnect: {len(snap) / 1e6:.1f} MB in {snap_s:.2f}s")
    print(f"events: {dict(counts)}")
    print(f"errors: {errors or 'none'}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8766)
    ap.add_argument("--file", default="soak_4h.wav")
    ap.add_argument("--speed", type=float, default=4.0)
    ap.add_argument("--sample-every", type=float, default=30.0)
    ap.add_argument("--name-every", type=float, default=600.0)
    ap.add_argument("--timeout", type=float, default=5400.0)
    asyncio.run(main(ap.parse_args()))
