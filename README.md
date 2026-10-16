# Bard

Live transcription for an in-person D&D table, from one shared microphone. Bard shows who is
speaking and what they said as the game happens, and keeps a campaign wiki (plain Markdown, an
Obsidian vault) that grows every session.

**Everything runs locally** on one Apple Silicon Mac: speech-to-text, speaker recognition and the
LLM. No audio or text leaves the machine.

## What it does

- **Live transcript** with speaker labels. Whisper (`mlx-whisper`, large-v3-turbo) transcribes;
  voiceprints from pyannote's speaker-embedding model tell the voices apart.
- **No setup recording needed.** Unknown voices show up as "Voice 1", "Voice 2"…; tap a name once
  and every past and future line from that voice is relabeled. Optional 15–30 s training helps
  quiet players.
- **Character voices.** A distinct NPC voice can be named as its own character (GM + NPC name).
- **Names spelled right.** Whisper is prompted with the campaign's names, and a spelling cleanup
  fixes misheard names and D&D terms, asking the local LLM only when a word is ambiguous. Every
  fix keeps what was heard.
- **Campaign wiki.** Import your notes, then write up each session: events, facts, new names and
  suggested spellings, all for you to review. Notes stay editable in Obsidian.
- **History.** Every session autosaves its transcript, recording and setup, with Markdown/JSON
  export, an audio-quality report and an "Improve transcript" pass.

## Requirements

- A Mac with Apple Silicon (mlx-whisper and torch on MPS) and `ffmpeg` (`brew install ffmpeg`).
- [uv](https://docs.astral.sh/uv/) (Python 3.12 is installed for you), Node 24 with Corepack
  (`corepack enable` gives Yarn 4 and pnpm).
- [LM Studio](https://lmstudio.ai/) serving an OpenAI-compatible API (default
  `http://127.0.0.1:1235/v1`, model `google/gemma-4-26b-a4b-qat`). Transcription works without it;
  spelling cleanup and the campaign write-ups need it.
- A [Hugging Face token](https://huggingface.co/settings/tokens) with access to
  [`pyannote/speaker-diarization-community-1`](https://huggingface.co/pyannote/speaker-diarization-community-1)
  (accept its terms once). Models download on first start.

## Quick start

```sh
yarn setup                         # uv sync + pnpm install
yarn build                         # build the web UI
lms server start --port 1235 && lms load google/gemma-4-26b-a4b-qat --yes   # or use the LM Studio app
HF_TOKEN=hf_… yarn start           # → http://127.0.0.1:8765
```

Then: add the players on **Setup**, pick the microphone, start a session. An omnidirectional
or boundary mic in the middle of the table works best; Bard's mic check tells you if yours isn't.

## Configuration

Environment variables (see `backend/src/bard/config.py`):

| Variable | Default | |
|---|---|---|
| `HF_TOKEN` | — | Hugging Face token (`HUGGING_FACE_TOKEN` also works) |
| `BARD_HOST` / `BARD_PORT` | `127.0.0.1` / `8765` | where the app listens |
| `BARD_DATA` | `backend/data` | players, voiceprints, sessions, recordings |
| `BARD_VAULT` | `$BARD_DATA/campaign` | campaign wiki; point it at a folder in an existing Obsidian vault |
| `BARD_LLM_URL` / `BARD_LLM_MODEL` | LM Studio, gemma | LLM for spelling cleanup |
| `BARD_MEMORY_MODEL` / `BARD_MEMORY_REASONING` | same model / `none` | LLM for notes and write-ups |
| `BARD_WHISPER` | `mlx-community/whisper-large-v3-turbo` | Whisper model |
| `BARD_RECORD=0`, `BARD_CLEANUP=0`, `BARD_ASR_PROMPT=0` | on | turn off recording, spelling cleanup, name prompting |

## Privacy and safety

- Bard binds to `127.0.0.1` with no auth, by design; Host and Origin checks stop other websites in
  your browser from reaching the API.
- Everything Bard stores lives in `backend/data/` (git-ignored): voiceprints and recordings of the
  people at your table. Ask before you record people.

## Development

```sh
yarn check        # format check, lint, typecheck, tests, build: run before every commit
yarn test         # backend pytest + web vitest (no models, GPU or network needed)
yarn dev:web      # Vite dev server, proxies the API to :8765
yarn format
```

| Path | |
|---|---|
| `backend/src/bard/` | Python engine and FastAPI server |
| `backend/tests/` | pytest |
| `backend/spike/` | benchmarks and research scripts (`uv run --group spike …`) |
| `web/src/` | React 19, Vite, Tailwind v4, shadcn/ui |

Design notes, measurements and the reasoning behind the thresholds are in [PLAN.md](PLAN.md).
Contributor/agent guides: [AGENTS.md](AGENTS.md), [backend/AGENTS.md](backend/AGENTS.md),
[web/AGENTS.md](web/AGENTS.md).

## License

[MIT](LICENSE).

The built-in D&D vocabulary (`backend/src/bard/srd_terms.json`) holds names from the System
Reference Document 5.1 and 5.2 by Wizards of the Coast LLC, available at
<https://www.dndbeyond.com/srd>, licensed under
[CC-BY-4.0](https://creativecommons.org/licenses/by/4.0/legalcode). Bard is unofficial and not
affiliated with or endorsed by Wizards of the Coast.
