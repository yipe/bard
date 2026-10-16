"""Try the campaign-memory jobs with a real local model, into a scratch vault.

    uv run spike/memory_trial.py writeup SESSION_ID --vault /tmp/bard-vault [--model M]
    uv run spike/memory_trial.py import NOTES.md --vault /tmp/bard-vault [--model M]

Never touches data/campaign. Prints the job result and timing.
"""

import argparse
import json
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "src"))

from bard import config  # noqa: E402
from bard.campaign import Vault  # noqa: E402
from bard.engine import Bus, migrate_state, seated_players  # noqa: E402
from bard.memory import Memory, MemoryLLM  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("what", choices=("writeup", "import"))
    p.add_argument("target")
    p.add_argument("--vault", required=True)
    p.add_argument("--model", default=config.MEMORY_LLM_MODEL)
    args = p.parse_args()
    vault_root = Path(args.vault)
    assert config.DATA_DIR / "campaign" != vault_root, "use a scratch vault"
    state = migrate_state(json.loads((config.DATA_DIR / "state.json").read_text()))
    players = {p["id"]: p for p in seated_players(state)}
    known = [(p["character"], "pc", f"Played by {p['name']}.") for p in players.values() if p["character"] and not p["is_gm"]]
    known += [
        (v["character"], "npc", f"Voiced by {players[v['player_id']]['name']}.")
        for v in state["voices"]
        if v.get("character") and v["player_id"] in players
    ]
    m = Memory(Vault(vault_root), Bus(), llm=MemoryLLM(model=args.model), seed=lambda: known)
    t0 = time.monotonic()
    if args.what == "writeup":
        job = m.write_up(args.target)
    else:
        path = Path(args.target)
        job = m.import_notes(path.stem, path.read_text())
    last = None
    while job.state in ("queued", "running"):
        if job.message != last:
            print(f"[{time.monotonic() - t0:5.0f}s] {job.message}", flush=True)
            last = job.message
        time.sleep(1)
    print(json.dumps(job.to_json(), indent=1))
    print(f"{args.model}: {time.monotonic() - t0:.0f}s")


if __name__ == "__main__":
    main()
