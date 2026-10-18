# Backend agent guide (Python 3, uv, FastAPI)

Run from `backend/`: `uv run troubadour` (server), `uv run pytest -q`, `uv run ruff check src tests spike`,
`uv run ruff format src tests spike`. Research scripts: `uv run --group spike spike/<script>.py`.

## Modules (`src/troubadour/`)

| Module | Role |
|---|---|
| `engine.py` | The live engine. Threads: segmenter (VAD → utterances), worker (Whisper → message), LLM health check, refine loop, spelling cleanup, training. Owns the player library, campaigns (seats, parked voices), voices, messages, sessions, autosave. |
| `server.py` | HTTP + WebSocket API, Host/Origin checks, static `web/dist`. Contract mirrored in `web/src/lib/types.ts`. |
| `audio.py` | `MicSource` (native rate → soxr 16 kHz), `FileSource` (paced replay), `Recorder` (48 kHz WAV, background writer). |
| `asr.py` | mlx-whisper; prompt with glossary names + last lines; `suspicious()` drops echoed/looping prompted output. |
| `voices.py` | Speaker embeddings and the voice matcher. |
| `transcript.py` | Saved record shape (`build`), Markdown export, UTF-16 helpers. |
| `campaign.py` | Obsidian vault: `Vault`, entity notes (frontmatter + Facts sections), `Corrector`, `everyday()`. |
| `obsidian.py` | Finds the campaign folder in Obsidian's vault registry, so links name the vault by id. |
| `memory.py` | Campaign jobs (note import, session write-up), job runner, spelling suggestions, `looks_like`. |
| `ask.py` | Ask: questions answered from the campaign's transcripts and notes (LLM plan → BM25 search → LLM answer with citations). |
| `cleanup.py` | Spelling cleanup: `Speller.find` (code), `ask` (constrained LLM), `apply` (edits + offsets), `clean_record`. |
| `improve.py` | Post-session jobs: re-transcribe (`run`), `fix_spellings`, restore. |
| `dnd_terms.py` | D&D vocabulary: committed SRD list + an optional local list in `data/dnd_terms.json`. |
| `cloud.py` | Opt-in Troubadour Cloud backup: OAuth PKCE sign-in, vault scan, plan → signed uploads → commit. Only network code besides the local LLM. |
| `sim.py` | `troubadour-sim`: headless re-run of a saved session in a throwaway data dir, with a comparison. |
| `config.py` | Env-driven settings (`TROUBADOUR_*`). Paths like `vault_dir()` resolve per call so tests can redirect them. |

## Invariants (break these and something subtle breaks)

- **Session isolation:** work for an abandoned session must never write shared state. Check
  `sess is self.live` under `self.lock` before mutating; message ids are unique across sessions.
- **Locks:** `engine.lock` (RLock) guards players/voices/messages; never call Whisper, the LLM or
  disk-heavy work while holding it. `asr._lock` serializes Whisper; `embed_lock` serializes torch MPS.
- **A broken vault, a down LLM, or a full disk never stops transcription.** Catch, log, carry on.
- **Patches, not replacements**, for anything a UI may click quickly (`Vault.review`, ignore list).
- **Never lose words:** improve keeps a line's old text if Whisper now hears nothing; every spelling
  fix records what was heard (`fixes`); the first rewrite of a saved transcript backs up
  `transcript.orig.json`.
- **Vault notes are the user's:** Troubadour owns only the documented frontmatter keys, the description,
  `## Facts` and `## Suggested facts`. Keep other keys/tags/sections verbatim; never rewrite a note
  whose frontmatter doesn't parse (`read_only`); deletes go to `.trash/`.
- **Spelling cleanup must not touch everyday speech:** dictionary words and their forms, case-only
  changes to D&D terms, spelled-out letters and aliases are never fixed. Add a test for any new rule.

## Testing

- No models, no network: Whisper is stubbed (`troubadour.asr.transcribe`), the embedder is a fake,
  `engine.cleanup_llm.json` raises unless a test patches it, and the generated D&D list is hidden
  (`hermetic_dnd_terms`). Keep it that way.
- Use the fixtures in `tests/conftest.py` (`dirs`, `engine`, `tone`, `speak`, `write_wav`).
- Fake LLMs return scripted JSON (`tests/test_memory.py::FakeLLM`); include adversarial outputs
  (junk names, invented spellings) when testing anything an LLM feeds.
- Piping pytest through `tail` hides its exit code: check `${PIPESTATUS[0]}` or don't pipe.
- Real-data experiments go in `spike/` and read `data/` read-only; results go in `samples/` (ignored).

## Adding an API endpoint

1. Validate input with pydantic models in `server.py` (length caps on every string and list).
2. Map errors with `_call` (KeyError → 404, ValueError → 400).
3. Mutations of the vault call `memory.changed()` (regenerates `Glossary.md`, emits `campaign.updated`).
4. Update `web/src/lib/types.ts` + `api.ts`, and the API list in `PLAN.md`.
