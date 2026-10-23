# Troubadour: agent guide

Troubadour live-transcribes an in-person D&D table from one shared mic: who is speaking, what they
said, and a campaign wiki that grows every session. **Everything runs
locally** on one Mac (Apple Silicon): Python engine + FastAPI in `backend/`, React UI in `web/`,
LLMs in LM Studio. No cloud calls at runtime, ever.

Deeper guides: `backend/AGENTS.md`, `web/AGENTS.md`. Design, measurements and history: `PLAN.md`
(read the relevant section before changing a subsystem; it records why things are the way they are).

## Commands (repo root, yarn 4)

| Task | Command |
|---|---|
| Install everything | `yarn setup` (uv + pnpm) |
| Run the app | `yarn start` → http://127.0.0.1:8765 (serves `web/dist`) |
| API + Vite, with fast refresh | `yarn dev` (`yarn start` and `yarn dev:web`) |
| UI dev server only | `yarn dev:web` (Vite, proxies to :8765) |
| All checks (run before every commit) | `yarn check` = format:check, lint, typecheck, tests, build |
| Format | `yarn format` |
| Live regression check against a running server | `yarn check:live` |

The shell may have `CI=true`: a first `yarn install` needs `--no-immutable`.

## Layout

```
backend/src/troubadour/   engine, server, ASR, voices, audio quality, campaign memory, spelling cleanup
backend/tests/      pytest; no models or network needed
backend/spike/      research and ops scripts (benchmarks, soak tests, harden_check)
backend/data/       USER DATA, git-ignored: state, recordings, sessions, campaign vault
web/src/            React 19 + Vite + Tailwind v4 + shadcn/ui
web/dist/           built UI, served by the backend (git-ignored)
```

## Hard rules

1. **Never touch `backend/data/`** in tests or experiments. It holds real players, voiceprints, hours
   of recordings and the user's Obsidian vault. Tests use tmp dirs (the `dirs`/`engine` fixtures).
   For a scratch server: `TROUBADOUR_PORT=8767 TROUBADOUR_DATA=/tmp/<name> uv run troubadour`.
2. **Never play audio** on the machine. Browsers you drive must be headless with `--mute-audio`.
   Don't start mic sessions; they record the room. Unit tests fake the mic.
3. **Local only.** No hosted APIs in the product. The LLM is LM Studio at `http://127.0.0.1:1235/v1`.
   The one exception is opt-in **Troubadour Cloud** backup (`cloud.py`): nothing is sent until the user
   clicks Connect, it only uploads the vault, and tests never reach it (`httpx.MockTransport`).
4. **Only restart the server (`:8765`) when `GET /api/state` shows `session.status == "idle"`.**
   A real game may be running.
5. **Licensed data and privacy (this repo is public):** only SRD names (CC-BY-4.0, attributed) are
   committed (`backend/src/troubadour/srd_terms.json`); check additions against the SRD text. No other
   D&D product content, no real people's names (tests use made-up players), and don't name where
   a larger local list (`backend/data/dnd_terms.json`) comes from. `backend/local/` is git-ignored
   for local-only tools.
6. `yarn check` must pass before a commit. Small, focused commits; push to `origin main`; never
   force-push without the owner's say-so.

## Conventions

- Prefer boring, explicit code; delete dead code; comments say *why*.
- Every behavior change gets a test that fails without it.
- Any LLM output is untrusted: validate names, types and lengths, and constrain choices before
  writing anything (see `memory.py`, `cleanup.py`).
- Text offsets that cross the API are **UTF-16 code units** (what JS uses); Python works in str
  indices. Convert with `transcript.utf16_offset` / `utf16_to_py`.
- Long work (note import, write-ups, improve, spelling) runs as background jobs reported over the
  WebSocket (`campaign.job`); never block a request on an LLM or Whisper.
