"""Watch a bard server's memory by macOS footprint category during a soak test.

Usage: uv run spike/memwatch.py --port 8766 [--every 60]

RSS hides GPU memory, so this samples `footprint -p PID` (phys_footprint,
including IOAccelerator = Metal/MLX/MPS) per category, alongside session audio
time. Writes samples/soak/mem.csv. At the end it fits each category against
hours of audio processed (after a warm-up) and flags anything that keeps growing.

Expected growth, not a leak: the session audio buffer (float32 16 kHz, doubling
capacity: steps in "Malloc Large"). A file replay also holds the whole file in
memory (constant).
"""

import argparse
import csv
import re
import subprocess
import time
from pathlib import Path

import httpx

OUT = Path(__file__).resolve().parents[1] / "samples" / "soak" / "mem.csv"
CATS = {
    "IOAccelerator (graphics)": "gpu",
    "Malloc Large": "malloc_large",
    "Malloc Small": "malloc_small",
    "Malloc Tiny": "malloc_tiny",
}
UNITS = {"B": 1 / 2**20, "KB": 1 / 2**10, "MB": 1, "GB": 2**10}
LINE = re.compile(r"^\s*([\d.]+)\s+(B|KB|MB|GB)\s+\S+\s+\S+\s+\S+\s+\S+\s+\d+\s+(.+?)\s*$")
TOTAL = re.compile(r"Footprint:\s+([\d.]+)\s+(B|KB|MB|GB)")


def pid_of(port: int) -> int:
    return int(subprocess.run(["lsof", "-ti", f"tcp:{port}", "-sTCP:LISTEN"], capture_output=True, text=True).stdout.split()[0])


def footprint(pid: int) -> dict[str, float]:
    out = subprocess.run(["footprint", "-p", str(pid)], capture_output=True, text=True).stdout
    mb = {v: 0.0 for v in CATS.values()}
    total = TOTAL.search(out)
    mb["total"] = float(total.group(1)) * UNITS[total.group(2)] if total else float("nan")
    for line in out.splitlines():
        m = LINE.match(line)
        if m and m.group(3) in CATS:
            mb[CATS[m.group(3)]] = float(m.group(1)) * UNITS[m.group(2)]
    mb["other"] = mb["total"] - sum(mb[v] for v in CATS.values())
    return mb


def slope(xs: list[float], ys: list[float]) -> float:
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    den = sum((x - mx) ** 2 for x in xs)
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True)) / den if den else 0.0


def main(args) -> None:
    api = httpx.Client(base_url=f"http://127.0.0.1:{args.port}", timeout=30)
    pid = pid_of(args.port)
    rows, seen_running, t0 = [], False, time.monotonic()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    debug_cols = [
        "mlx_active_mb",
        "mlx_cache_mb",
        "torch_mps_allocated_mb",
        "torch_mps_driver_mb",
        "py_blocks",
        "py_objects",
        "voices",
    ]
    cols = ["wall_s", "audio_h", "messages", "total", "gpu", "malloc_large", "malloc_small", "malloc_tiny", "other", *debug_cols]
    with OUT.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(cols)
        while time.monotonic() - t0 < args.timeout:
            s = api.get("/api/state").json()
            seen_running |= s["session"]["status"] == "running"
            mb = footprint(pid)
            dbg = api.get("/api/debug/memory").json()  # in-process allocator view (newer servers)
            row = [round(time.monotonic() - t0), round(s["session"]["t"] / 3600, 3), len(s["messages"])]
            row += [round(mb[c]) for c in cols[3:9]] + [round(dbg.get(c, float("nan"))) for c in debug_cols]
            w.writerow(row)
            f.flush()
            rows.append(row)
            print(" ".join(f"{c}={v}" for c, v in zip(cols, row, strict=True)), flush=True)
            if seen_running and s["session"]["status"] == "idle":
                break
            time.sleep(args.every)

    warm = [r for r in rows if r[1] >= args.warmup_h] or rows
    xs = [r[1] for r in warm]
    print(f"\n=== memory by category, MB (fit over {xs[0]:.1f}-{xs[-1]:.1f} h of audio, {len(warm)} samples) ===")
    for i, c in enumerate(cols[3:], start=3):
        ys = [r[i] for r in warm]
        per_h = slope(xs, ys)
        flag = ""
        if c not in ("malloc_large", "total", "py_blocks", "py_objects", "voices", "llm_backlog") and per_h > args.leak_mb_per_h:
            flag = "  <-- grows: possible leak"
        print(f"  {c:<13} start {ys[0]:>7}  end {ys[-1]:>7}  max {max(ys):>7}  trend {per_h:+8.1f} MB per audio-hour{flag}")
    print("  (malloc_large is expected to step up as the session audio buffer doubles: ~230 MB/audio-hour average)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8766)
    ap.add_argument("--every", type=float, default=60)
    ap.add_argument("--timeout", type=float, default=10800)
    ap.add_argument("--warmup-h", type=float, default=1.0, help="ignore the first N audio-hours (caches filling)")
    ap.add_argument("--leak-mb-per-h", type=float, default=50.0)
    main(ap.parse_args())
