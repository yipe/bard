"""UI stress probe: a muted headless Chrome watching the Bard UI over the DevTools protocol.

Usage: uv run spike/ui_probe.py --server http://127.0.0.1:8766 --minutes 140

Waits until a session is running, opens the UI (lands on the Session view) and
every --sample-every seconds records to samples/soak/ui.csv:
  heap_mb      JS heap in use
  nodes        DOM nodes
  rows         rendered <li> rows (chat + side panels)
  busy_pct     main-thread busy share since the last sample (Performance.TaskDuration)
  gap_max_ms   worst gap between animation frames (a frozen UI shows up here)
  long_ms      total long-task time since the last sample
  connected    the header still says "Connected"
When the session goes idle it reloads the page, times how long the full
transcript takes to render, and saves a screenshot. No audio: Chrome is muted.
"""

import argparse
import asyncio
import base64
import csv
import itertools
import json
import subprocess
import tempfile
import time
from pathlib import Path

import httpx
import websockets

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
OUT = Path(__file__).resolve().parents[1] / "samples" / "soak"

PROBE_JS = """
window.__probe = {gaps: [], longMs: 0};
(() => {
  let last = performance.now();
  const tick = (t) => {
    window.__probe.gaps.push(t - last); last = t;
    if (window.__probe.gaps.length > 20000) window.__probe.gaps.shift();
    requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
  try {
    new PerformanceObserver((list) => {
      for (const e of list.getEntries()) window.__probe.longMs += e.duration;
    }).observe({type: "longtask", buffered: true});
  } catch (e) {}
})();
"""

SAMPLE_JS = """
(() => {
  const p = window.__probe || {gaps: [], longMs: 0};
  const out = {
    rows: document.getElementsByTagName("li").length,
    connected: document.body.innerText.includes("Connected"),
    gapMax: p.gaps.length ? Math.max(...p.gaps) : 0,
    longMs: p.longMs,
    view: document.body.innerText.includes("Stop") ? "session" : "other",
  };
  p.gaps = []; p.longMs = 0;
  return JSON.stringify(out);
})()
"""


class CDP:
    def __init__(self, ws):
        self.ws, self.ids, self.pending, self.errors = ws, itertools.count(1), {}, []

    async def pump(self):
        async for raw in self.ws:
            msg = json.loads(raw)
            if "id" in msg and msg["id"] in self.pending:
                self.pending.pop(msg["id"]).set_result(msg)
            elif msg.get("method") == "Runtime.exceptionThrown":
                self.errors.append(msg["params"]["exceptionDetails"].get("text", "exception"))
            elif msg.get("method") == "Runtime.consoleAPICalled" and msg["params"]["type"] == "error":
                self.errors.append(" ".join(str(a.get("value", a.get("description", ""))) for a in msg["params"]["args"])[:300])
            elif msg.get("method") == "Log.entryAdded" and msg["params"]["entry"]["level"] == "error":
                entry = msg["params"]["entry"]
                if "/api/files/" not in entry.get("url", ""):  # audio range requests get cancelled on seek: noise
                    self.errors.append(entry["text"][:300])

    async def call(self, method, **params):
        i = next(self.ids)
        fut = asyncio.get_running_loop().create_future()
        self.pending[i] = fut
        await self.ws.send(json.dumps({"id": i, "method": method, "params": params}))
        msg = await asyncio.wait_for(fut, 120)
        if "error" in msg:
            raise RuntimeError(f"{method}: {msg['error']}")
        return msg.get("result", {})

    async def eval(self, expr):
        r = await self.call("Runtime.evaluate", expression=expr, returnByValue=True, awaitPromise=True)
        return r.get("result", {}).get("value")

    async def metrics(self):
        return {m["name"]: m["value"] for m in (await self.call("Performance.getMetrics"))["metrics"]}


async def main(args) -> None:
    api = httpx.Client(base_url=args.server, timeout=60)
    print("waiting for a running session…", flush=True)
    while api.get("/api/state").json()["session"]["status"] != "running":
        await asyncio.sleep(2)

    profile = tempfile.mkdtemp(prefix="bard-ui-probe-")
    chrome = subprocess.Popen(
        [CHROME, "--headless=new", "--mute-audio", f"--remote-debugging-port={args.cdp_port}", f"--user-data-dir={profile}",
         "--no-first-run", "--no-default-browser-check", "--enable-precise-memory-info", "--window-size=1440,1000", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )  # fmt: skip
    try:
        for _ in range(60):
            try:
                targets = httpx.get(f"http://127.0.0.1:{args.cdp_port}/json/list").json()
                page = next(t for t in targets if t["type"] == "page")
                break
            except (httpx.HTTPError, StopIteration):
                await asyncio.sleep(0.5)
        async with websockets.connect(page["webSocketDebuggerUrl"], max_size=None) as ws:
            cdp = CDP(ws)
            pump = asyncio.create_task(cdp.pump())
            for domain in ("Page", "Runtime", "Log", "Performance"):
                await cdp.call(f"{domain}.enable")
            await cdp.call("Page.addScriptToEvaluateOnNewDocument", source=PROBE_JS)
            await cdp.call("Emulation.setDeviceMetricsOverride", width=1440, height=1000, deviceScaleFactor=1, mobile=False)
            await cdp.call("Page.navigate", url=args.server + "/")
            await asyncio.sleep(3)

            OUT.mkdir(parents=True, exist_ok=True)
            t0 = time.monotonic()
            last_task = (await cdp.metrics()).get("TaskDuration", 0.0)
            last_t = t0
            rows = []
            with (OUT / "ui.csv").open("w", newline="") as f:
                w = csv.writer(f)
                cols = [
                    "wall_s",
                    "messages",
                    "heap_mb",
                    "nodes",
                    "rows",
                    "busy_pct",
                    "gap_max_ms",
                    "long_ms",
                    "connected",
                    "view",
                    "errors",
                ]
                w.writerow(cols)
                while time.monotonic() - t0 < args.minutes * 60:
                    await asyncio.sleep(args.sample_every)
                    state = api.get("/api/state").json()
                    m = await cdp.metrics()
                    s = json.loads(await cdp.eval(SAMPLE_JS))
                    now = time.monotonic()
                    busy = (m.get("TaskDuration", 0) - last_task) / (now - last_t) * 100
                    last_task, last_t = m.get("TaskDuration", 0), now
                    row = [round(now - t0), len(state["messages"]), round(m.get("JSHeapUsedSize", 0) / 1e6), int(m.get("Nodes", 0)),
                           s["rows"], round(busy, 1), round(s["gapMax"]), round(s["longMs"]), s["connected"], s["view"], len(cdp.errors)]  # fmt: skip
                    w.writerow(row)
                    f.flush()
                    rows.append(row)
                    print(" ".join(f"{c}={v}" for c, v in zip(cols, row, strict=True)), flush=True)
                    if state["session"]["status"] == "idle" and row[1] > 0:
                        break

            # Fresh load with the whole transcript: how long until it's on screen?
            expected = len(api.get("/api/state").json()["messages"])
            t = time.monotonic()
            await cdp.call("Page.reload", ignoreCache=True)
            rendered, connected = 0, False
            while time.monotonic() - t < 180:
                await asyncio.sleep(0.25)
                try:
                    # The session is idle now, so the app opens on Setup: switch to the transcript.
                    await cdp.eval(
                        "[...document.querySelectorAll('button')].find(b => b.textContent.trim() === 'Session')?.click()"
                    )
                    s = json.loads(await cdp.eval(SAMPLE_JS))
                except Exception:
                    continue
                rendered, connected = s["rows"], s["connected"]
                if connected and s["view"] == "session" and rendered >= min(expected, 20):
                    break
            load_s = time.monotonic() - t
            await asyncio.sleep(5)
            m = await cdp.metrics()
            shot = await cdp.call("Page.captureScreenshot", format="png")
            (OUT / "ui_end.png").write_bytes(base64.b64decode(shot["data"]))
            print("\n=== UI probe summary ===")
            print(f"samples {len(rows)}, messages at end {expected}")
            print(
                f"heap MB start {rows[0][2]} max {max(r[2] for r in rows)} end {rows[-1][2]}; nodes max {max(r[3] for r in rows)}"
            )
            print(f"main-thread busy % last 5 samples {[r[5] for r in rows[-5:]]}; worst frame gap {max(r[6] for r in rows)} ms")
            print(f"disconnected samples: {sum(not r[8] for r in rows)}")
            print(
                f"reload with {expected} messages: connected={connected} in {load_s:.1f}s; heap {m.get('JSHeapUsedSize', 0) / 1e6:.0f} MB, nodes {int(m.get('Nodes', 0))}"
            )
            print(f"console errors: {cdp.errors[:10] or 'none'}")
            pump.cancel()
    finally:
        chrome.terminate()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", default="http://127.0.0.1:8766")
    ap.add_argument("--minutes", type=float, default=140)
    ap.add_argument("--sample-every", type=float, default=30)
    ap.add_argument("--cdp-port", type=int, default=9333)
    asyncio.run(main(ap.parse_args()))
