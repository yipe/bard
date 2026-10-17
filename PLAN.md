# Bard — live TTRPG transcription (tech demo)

Goal: check feasibility of near-real-time, speaker-labeled transcription of an
in-person D&D 5e table (one shared mic). Game-mechanics highlighting (regex + LLM tags)
was built, then removed on 2026-10-16 at the owner's request: transcript and audio quality only.
Everything runs locally on one Apple Silicon Mac (M5 Max, 128 GB).

## Run

1. Local LLM: LM Studio server with `google/gemma-4-26b-a4b-qat` loaded
   (`lms server start && lms load google/gemma-4-26b-a4b-qat --yes`; it serves on
   port 1235 here). Override with `BARD_LLM_URL` / `BARD_LLM_MODEL`.
2. `yarn setup` (once: `uv sync` + `pnpm install`), then `yarn build` after UI changes.
3. `HF_TOKEN=… yarn start` → http://127.0.0.1:8765. Needs a Hugging Face
   token with access to `pyannote/speaker-diarization-community-1`
   (`HUGGING_FACE_TOKEN` is also accepted).

Sample files for replay live in `backend/samples/eval/` (built by
`spike/dataset.py`). Players, campaigns and named/trained voiceprints persist in
`backend/data/state.json`. UI dev server: `yarn dev:web` (proxies to 8765).

## Develop

Root `package.json` scripts (yarn) drive both halves; the backend uses uv,
the web app pnpm.

| Command | Does |
|---|---|
| `yarn test` | backend pytest (no models/GPU: a fake speaker embedder makes sine tones act as voices) + web vitest |
| `yarn lint` | ruff (backend) + oxlint (web) |
| `yarn format` / `yarn format:check` | ruff format + import sort / prettier |
| `yarn typecheck` | `tsc -b` for the web app |
| `yarn check` | everything above + production build; run before committing |
| `yarn check:live` | `spike/harden_check.py` against the running server: validation, Host/Origin checks, session lifecycle, train/cancel, merges; only touches its own `zz-check-*` players |

Benchmark scripts need the extra group: `cd backend && uv run --group spike spike/eval.py --tag NAME`.

Stress test (separate server, own data dir, nothing audible): build a long WAV
in `backend/samples/soak/`, start
`BARD_PORT=8766 BARD_DATA=/tmp/bard-soak BARD_SAMPLES=$PWD/samples/soak BARD_MAX_REPLAY_SPEED=8 caffeinate -i uv run bard`,
then run `spike/ui_probe.py` (muted headless Chrome watching the UI over the
DevTools protocol) and `spike/soak.py` (starts the replay, samples memory/lag,
names voices every 10 min). Both write CSVs to `samples/soak/`.

Layout: `backend/src/bard/` engine (`engine.py` threads + state, `voices.py`
matcher, `asr.py`, `audio.py` sources, `server.py`
FastAPI). `web/src/` UI (contract in `lib/types.ts`). `backend/spike/`
benchmark scripts plus `harden_check.py`, a non-destructive regression check
against a running server (only touches its own `zz-check-*` players).

Safety: binds 127.0.0.1, no auth by design. Host and Origin checks stop other
websites in the same browser from calling the API or reading the WebSocket.
Inputs are bounded (names, avatars as small `data:image` URLs, colors, speed,
train length); replay files must live in `samples/eval/`. State writes are
atomic; Train recordings live in `backend/data/voices/` and are deleted with
their voice.

Long sessions: a session holds a `caffeinate -i` assertion so the Mac doesn't
idle-sleep mid-game (closing the lid still sleeps it). If a mic delivers no
frames for `MIC_STALL_S` (5 s), the UI gets an error toast once. Session audio
costs ~230 MB/hour of RAM.

## Pipeline

All stages publish events over one WebSocket; the UI applies patches as better
information arrives.

| # | Stage | Tech | Target latency |
|---|---|---|---|
| 1 | Capture + VAD | `sounddevice` at the mic's rate → 16 kHz mono, Silero VAD → utterances (`bard/segment.py`: 400 ms silence ends a line, 200 ms padding, 12 s cap) | — |
| 2 | Speaker ID | `VoiceMatcher` (`bard/voices.py`): embed each utterance (padded to ≥ 1.5 s), nearest known voiceprint; best score < 0.40 → unknown, grouped into a pending "Voice N"; confident ≥ 3 s matches adapt the voiceprint | 0.5–1 s |
| 3 | ASR | `mlx-whisper` large-v3-turbo; prompt: the table's names, glossary names said lately, the rest of the glossary (≤ 20 names) and the last lines, fitted to Whisper's 223-token prompt window; glossary fix-up of names afterwards | 1.5–4 s |
| 4 | Refine | re-score past utterances against current (matured) voiceprints and relabel; later also re-run ASR over larger windows. pyannote is *not* used for identity (benchmark: it lowers accuracy) | background |

Each session has its own audio buffer, queues and threads; starting a new one
abandons the old (its threads exit without side effects). Whisper calls are
serialized, and all torch-MPS work (speaker embeddings) shares one lock:
concurrent MPS use aborts the process.

### Voices

Speaker ID detects **voices**, not players. A voice is a cluster of speech
embeddings (centroid + a few sample clips). Each voice maps to a player and,
optionally, a character:

- Player's normal voice → player, their PC.
- Player doing a character voice → same player, that PC.
- GM's narration → GM; each NPC voice → GM + NPC name (e.g. "Morvath").

Messages store `voice_id`; the displayed speaker comes from the mapping, so
remapping a voice relabels every past message from it.

**Default: no setup recording (option B).** The session starts with no known
voices. Unmatched utterances are grouped as they arrive; once a group holds
~3 s of speech it appears in the voices panel as "Voice N" and the user taps a
name. Naming a second group with an existing name merges it. The refine pass
then relabels earlier messages with the matured voiceprints.

**Optional: Train.** Any player can tap Train (setup or mid-session) and talk
for 15–30 s, any words, nobody else speaking. That creates or strengthens their
voiceprint and marks them trained. Most useful for quiet players, who may
never accumulate enough speech to surface in the panel.

**One line, by hand.** Clicking a line's speaker name offers every player (and the characters
someone already voices; a typed character goes with the pick). `POST /api/messages/{id}/speaker`
moves that line at once and pins it: refine never relabels it. Its voiceprint teaches the
chosen voice like one confident span (`VoiceMatcher.learn`, weight 1; naming a voice in the
panel counts 5×), so the next lines in that voice follow; moving the line again takes the
lesson back first (`forget`), and a voice that was only that line goes away again.

**Character voices: name them, don't train them.** We tried training a
separate character voiceprint plus a style model (WavLM; `spike/style.py`).
On a real normal/character pair the speaker embedding got 65%, WavLM 80% on
held-out 1–2 s windows, and live it still couldn't tell them apart, so it was
removed. Instead, a character voice that is really distinct to the speaker
model scores below 0.40 against the player's normal voiceprint, surfaces as
its own "Voice N", and gets named with a character in the panel. Among one
player's voiceprints a line only switches if one wins by `CHARACTER_MARGIN`
(0.08), so near-identical ones don't flicker. Once a player has a character
voice, their normal-voice lines show no character (out of character).

Benchmark: voiceprints put a person's NPC voices with their normal voice
(the GM is identified as the GM 96%+ even in the NPC-heavy roleplay clip). Good
for "who is talking"; telling *which* NPC needs sub-clustering within one
speaker's segments (pyannote did split off the jeweler voice) or text cues.

### Events

Authoritative shapes: `web/src/lib/types.ts`. The WebSocket sends `snapshot`
first (and after every session start, campaign switch or player benching); the client
applies events idempotently.
A client that falls behind is disconnected and resyncs from a fresh snapshot.

- `session.updated`: status, source, `t` (audio seconds), LLM health, `asr` (Whisper model and
  `loading`/`ready`/`error`), `training` (who is being trained), `error` (e.g. models failed to load)
- `player.upserted` / `player.removed`; `library.updated` (people not in the open campaign);
  `campaigns.updated` (created / renamed; switching sends a `snapshot`)
- `voice.upserted` / `voice.removed` (`merged_into` on merges)
- `speaker.active`: `{voice_id, confidence}` lights the mapped player's box
- `message.created`; `messages.updated`: one batch of patches (refine
  relabels, merges, spelling fixes)
- `train.status`: recording / processing / done / error / cancelled
- `error`: model, worker (once per session), recording and mic-stall failures, shown as a toast

## UI (Vite + React + TS + Tailwind v4 + shadcn/ui)

1. **Setup**: add players (name, character, avatar or initials), GM included.
   Each player card has an optional **Train** button: record 15–30 s of any
   speech from their seat, shows "Trained" when done. Never required.
2. **Session**: player grid (box glows via `data-speaking`), chat feed below
   (avatar, name, text; virtualized with react-virtuoso, so ~40 rows in the DOM
   at any length), consecutive lines of one speaker grouped under one name,
   messages update in place when relabeled. Untrained players' cards keep the
   Train button. Header has **Transcript (.md)** / **Data (.json)** downloads. Mic
   sessions show an **Audio quality** box on the right (meter + read-out); closing it
   switches the server's monitor off (`PUT /api/audio/monitor`, `session.audio_monitor`)
   so no level/quality work runs per frame, until it is shown again (resets on restart).
3. **Voices panel**: pending "Voice N" cards with play-sample buttons; tap a
   player (and optionally a character / NPC name) to name one. Suggest the
   closest existing voice so a repeat is a one-tap confirm (merge). Expect
   ~1–2 taps per person per session.
4. **History**: every saved session, read-only (transcript, talk time, audio quality
   report, audio with click-a-timestamp-to-seek). URLs: `/history/<id>`. Every
   session autosaves to `data/sessions/<id>/transcript.{md,json}` + `meta.json`
   (each refine tick, on stop/new session, on shutdown). API:
   `GET /api/transcript.{md,json}`, `/api/sessions`, `/api/sessions/{id}/transcript.{md,json}`.
   - **Improve transcript**: `POST /api/sessions/{id}/improve` re-transcribes every
     line with the glossary as it is now (spellings accepted since, imported notes)
     plus the previous lines; speakers and line boundaries stay; a line heard as
     nothing keeps its text. Waits while a live session runs. The original stays as
     `transcript.orig.json` (`POST …/restore` puts it back).
   - **Recording**: mic sessions also write `audio.wav` at the mic's own rate
     (48 kHz 16-bit, ~345 MB/h; `BARD_RECORD=0` turns it off). The mic opens
     at its native rate; a soxr stream feeds the pipeline 16 kHz. Written on a
     background thread, header patched each second (crash-safe). A disk error
     stops the recording, not the session, and is toasted as it happens.
   - **Setup snapshot**: `setup/{start,end}.json` (players + voiceprints, state.json
     shape) + `setup/voices/*.wav` (training clips).
   - **Replay**: `{"kind": "session", "id", "speed"}` source; capped at what the
     session heard (`duration_s`), so a stopped replay of a long sample stays short.
     `GET /api/sessions/{id}/audio.wav` downloads the recording.
   - **bard-sim**: `uv run bard-sim <id> [--setup start|end|current] [--speed 4]`
     re-runs a session headless in a throwaway data dir and writes
     `sims/<run>/transcript.*` + `compare.json` (speaker agreement with unnamed
     voices aligned by overlap, per-minute text similarity).
5. **Campaign**: the glossary and memory (see Campaign memory below): search and
   filter, an editor per entry (aliases, heard-as spellings, facts, review of
   suggestions, merge, delete to `.trash`), import pasted notes, spelling
   suggestions mined from transcripts, campaign-wide facts, and a **Write up for
   campaign** button on each History session.

## Campaigns and the player library

People are a library shared by every campaign; a campaign seats some of them, each with
a campaign-specific character and GM flag, and has its own vault and saved sessions.

- `state.json` v2: `players` (the library: name, avatar, color, trained), `campaigns`
  (`{id, name, roster, left}`: seats `{player_id, character, is_gm}`; `left` remembers the
  seats of people taken out, so adding them back restores their character), `campaign` (the open one),
  `voices` (each named voiceprint, with `campaign`: null for a person's own voice, which
  goes with them everywhere, or the campaign an NPC/character voice was named in).
  A pre-campaign file migrates into campaign `main` on load; the original is kept as
  `state.v1.json`. Nothing moves on disk.
- `main` keeps `config.vault_dir()` and every session saved before campaigns; later
  campaigns get `data/campaigns/<id>/`. Sessions record `campaign` in `meta.json`;
  History, replays and spelling suggestions list only the open campaign's.
- The engine holds only the open campaign: `players` (seated), `voices` (seated people's
  own voices + this campaign's characters + pending). Everyone else waits on `bench`, their
  voices `parked` outside the matcher with their voiceprints and Train clips kept.
- Switching (`POST /api/campaigns/{id}/open`) and removing a player from a campaign
  (`POST /api/players/{id}/bench`) happen between sessions only (not while training or a
  campaign job runs): the finished session gets its last relabel and waits (≤ `SETTLE_S`)
  for queued LLM tags and spelling fixes, is saved one last time and cleared, so roster
  changes can't rewrite its transcript; a fresh `snapshot` follows.
- Jobs (write-up, improve, spelling, import) use the open campaign's notes: the server refuses
  another campaign's session, and `campaign_gate` keeps a job from being queued mid-switch.
- The live engine never saves over a transcript that Improve / Fix spellings rewrote
  (`transcript.orig.json` exists): theirs is newer than its in-memory copy.
- API: `POST /api/campaigns {name}`, `PATCH /api/campaigns/{id} {name}`,
  `POST /api/campaigns/{id}/open`, `POST /api/players/{id}/seat {character, is_gm}`,
  `POST /api/players/{id}/bench`, `DELETE /api/players/{id}` (delete from the library:
  every campaign, voiceprints and clips). Events: `campaigns.updated`, `library.updated`;
  `snapshot` carries `campaign`, `campaigns` and `library`.
- UI: campaign picker in the header (New campaign…, rename); Setup lists the library under
  "From your other campaigns" (Add, or delete for good); a card's remove button benches.

## Campaign memory (Obsidian vault)

`Engine.vault_dir()`: the open campaign's notes. For `main`, `config.vault_dir()`:
`data/campaign`, or `BARD_VAULT` (e.g. a folder in an existing
vault). Plain markdown, so people, Obsidian and agents can all read and edit it; Bard
re-reads notes when their file changes (edits in Obsidian count).

- One note per entity in `People/ Places/ Factions/ Items/ Lore/` (file name = name).
  Frontmatter Bard owns: `type`, `aliases`, `heard_as` (ASR misspellings it corrects),
  `heard_as_suggested` / `heard_as_rejected`, `status` (confirmed | suggested), `tags`.
  Body: description, `## Facts`, `## Suggested facts`. Other keys, tags and sections are
  kept verbatim; a note whose frontmatter doesn't parse is read-only, never rewritten.
- `Sessions/<date> <title>.md` (summary, highlights, timestamped events with [[links]])
  + `Sessions/Transcripts/<id>.md`; `Notes/` (pasted notes, verbatim); `Facts.md`;
  `Glossary.md` (generated index); `README.md` (written once, explains the layout).
- **Import notes** (`POST /api/campaign/import`): chunks of ~6 k chars → the memory LLM
  (JSON schema) → entities + facts merged in (by name, alias or heard-as). The user's
  notes confirm suggested entries and replace their AI descriptions.
- **Write up** (`POST /api/campaign/writeup/{id}`): ~15 min transcript parts → events
  (with the time of their line), lasting facts, new names, misspellings → session page +
  *suggestions* to review. Guards, all added after reading real output: hints listing
  transcript words that look like known names (so "Dallara" isn't a new NPC); a "new"
  name that sounds like a known one becomes a spelling suggestion; duplicate new names
  fold together; descriptions and lists aren't names; at most 5 facts and 8 names per
  part and 30 facts per write-up; fleeting facts (hit points, "currently") dropped;
  table jokes aren't events; a misspelling must occur in the transcript.
- **Spelling suggestions** (no LLM): transcript words that look or sound like a name
  (sequence ratio, or a crude sound key: Cassia/Kessia, Dilara/Elara), and frequent
  unknown names. A word counts as name-like only if it's capitalised mid-sentence and
  isn't an everyday word, filler or contraction.
- Review decisions go through `POST …/entities/{name}/review` (changes, not list
  replacement: rapid clicks can't undo each other); the UI also serializes its writes.
- Model: `BARD_MEMORY_MODEL` (default the loaded gemma; `BARD_MEMORY_REASONING` for
  gpt-oss). On the 2.6 h session gemma wrote it up in ~5 min; gpt-oss-120b took 19 min
  and proposed 3× more, noisier, suggestions.
- **Live use**: new lines go through the glossary corrector (heard-as → name, name
  casing unless the name is an English word), and Whisper is prompted with the
  table's names (Setup, voices panel, confirmed entries) plus the last two lines.
- **Ask** (`ask.py`, `POST /api/campaign/ask {question}` → `campaign.answer` events; the open
  campaign's answers come with `GET /api/campaign`, in memory only): questions like "who did we
  suspect has the diamond?" answered from the open campaign's transcripts and vault notes. One LLM
  call plans (search words with synonyms: diamond → gem, jewel; the glossary entries asked about),
  code searches (BM25 over 8-line transcript windows, speech only so "Theo as Elara" isn't about
  Elara, plus every note; an entry is searched under its aliases and heard-as spellings and its
  note always goes in), and a second call answers from ≤ 14k chars of excerpts, told who's a
  player, the GM, a PC or an NPC, and to keep suspicions apart from facts. It may only cite the
  excerpts it got. Runs on its own thread beside the write-up queue (it never writes). On the
  real 2.6 h campaign: ~2 s per question with gemma. Gemma closes its JSON string early at an
  inner double quote, so it's told to quote with single quotes.

## Bard Cloud (opt-in backup)

The one thing Bard sends off the Mac, and only after the user connects: a copy of the open
campaign's vault, kept as snapshots by a separate service (Bard Cloud: Next.js on Vercel, Clerk,
Neon, private Vercel Blob) that shows it read-only in a browser. Bard works the same without it.

- `cloud.py`. Sign-in: OAuth 2 authorization code + PKCE (S256) with a loopback redirect to
  `http://127.0.0.1:8765/api/cloud/callback` (fixed: another `BARD_PORT` can't sign in); the
  service says where to sign in (`GET /api/v1/client`), so Bard only knows `BARD_CLOUD_URL`. The
  `state` is single-use and expires in 10 min. Tokens (refresh included) in `data/cloud.json`,
  mode 600; a refused refresh disconnects.
- Backup (a `campaign.job`, kind `backup`, on the memory job thread, so never mid-write-up): list
  the vault (no hidden files or folders, no symlinks, `Sessions/Transcripts/` only if the campaign
  opts in; paths NFC), sha256 each file, `plan` (the service lists what it lacks) → PUT those to
  signed URLs → `commit` the list as a snapshot. A file the service would refuse (over 20 MB, a
  path rule, a case-only twin) is left out and listed, not fatal. A lost race (`conflict`), a
  lost upload (`missing_blobs`) or a note edited mid-backup starts over once. Unchanged vault: no
  new snapshot. Hashes are cached by (file, size, mtime). A failure is kept as `last_error` until
  a backup succeeds.
- **Automatic backups** (`AutoBackup`, a thread started with the server; per campaign, on by
  default once connected): every 30 s it fingerprints the open campaign's vault (paths, sizes,
  mtimes; no reading). When that differs from the last backup's and has stayed the same for 2 min
  (a write-up or an Obsidian edit has settled), and the last automatic backup was ≥ 10 min ago,
  and no other job is running, it queues a backup job. So a write-up is backed up a couple of
  minutes after it lands, without any hook into the write-up code.
- **Restore** (a job, kind `restore`): the newest snapshot of any of the account's vaults (this
  campaign's, or one from another Mac) into the open campaign's folder. Each file is checked
  against its sha256 before it's written; a note that differs is moved to
  `.trash/Before restore <time>/` first; files the snapshot doesn't have are left alone (a restore
  never deletes). Paths go through the same rules as backups and must land inside the folder (no
  `..`, no writing through symlinks). On the web, a vault also downloads as a .zip.
- The service's contract: `backend/tests/cloud_contract.schema.json` (copied from the service's
  `contract.schema.json`); a test fails if its limits or error codes drift from `cloud.py`.
- API: `GET /api/cloud` (never calls the service), `POST /api/cloud/connect` → `{authorize_url}`,
  `GET /api/cloud/callback`, `POST /api/cloud/disconnect`, `PUT /api/cloud/options
  {include_transcripts?, auto_backup?}`, `POST /api/cloud/backup` → job, `GET /api/cloud/vaults`,
  `POST /api/cloud/restore {vault_id}` → job. Event: `cloud.updated`.
- UI: Campaign header → **Cloud backup** (connect, automatic on/off, include transcripts, back up
  now, last backup or failure, restore, disconnect).

## Spelling cleanup (behind the transcriber)

D&D vocabulary: Bard ships the SRD 5.1/5.2 names (`srd_terms.json`, ~1.6k spells, creatures,
items, conditions, species, classes and rules terms; CC-BY-4.0, attributed, each name checked
against the SRD PDFs). An optional, larger list in the same shape at `data/dnd_terms.json` is
used on top when present; it's never committed. Official names are held to a stricter match
than the campaign's own: spelled nearly the same (ratio ≥ 0.8, length ≥ 6), no sound-alikes,
phrases only for spells, creatures and items and only word by word ("hideous laughing" →
Hideous Laughter; "far away" doesn't become a two-word rules term). Indexed by first letter
and last word: 0.1 ms a line. They also drop out of "unknown names" suggestions.

`cleanup.py`. Code finds candidates in every line: words and 2–3 word phrases that look or
sound like a known spelling (confirmed glossary names and aliases, accepted heard-as, the
names Setup and the voices panel know, and a built-in D&D vocabulary, `dnd_terms.py`).
- **Sure fixes** happen before the line is shown: an accepted heard-as spelling, the same
  letters with other spacing or case ("thunder wave", "auggie"), or a near-identical spelling
  with one candidate (ratio ≥ 0.88: "Augie" → "Auggie").
- **Ambiguous** words ("Dallara": Elara *or* Delara, two different characters) go to the
  local LLM a few seconds behind, in batches of up to 8 lines with 4 lines of context; it may
  only answer one of the offered names or "keep". The line is patched in place
  (`messages.updated` with text and fixes); a line edited meanwhile is skipped.
- Never touched: everyday words and their forms ("confusing", "poisoned", "teleports"),
  case-only changes to D&D terms ("scrying"), spelled-out letters ("B-U-T-T-E"), aliases.
- Every fix keeps what was heard (`fixes: [{start, end, heard}]`, UTF-16), shown as a dotted
  underline "Heard as …". An answer the LLM gives twice becomes a heard-as *suggestion*, so
  once approved the instant code path handles it.
- After a session: `POST /api/sessions/{id}/spelling` (seconds for 2.6 h: 23 fixes, 19 lines
  asked); Improve transcript runs it too. `BARD_CLEANUP=0` turns it off.
- Spelling suggestions leave out D&D vocabulary, acronyms and fillers; any suggestion can be
  ✕'d (unknown names go to the vault note `Ignored words.md`).

## Audio quality and microphones

- **Live meter** (`levels.LevelMeter`, fed every 16 kHz frame): `audio.level` ~10/s with a zone
  (silent < −50, quiet < −30 grey-blue, good < −12 green, hot < −3 amber, clip red) and
  `audio.quality` every 2 s over the last 15 s: speech level (p90 of 50 ms RMS), background
  (p10), SNR, rumble (% energy < 100 Hz), clipping, graded with hints. **Test mic** on Setup
  (`POST /api/mic/test`) runs the same meter for 20 s without a session; nothing is recorded.
- **Recording report** (`GET /api/sessions/{id}/quality`, cached in `quality.json`, ~4 s for
  2.6 h, made automatically after a mic session): overall numbers, a per-minute timeline,
  loudness per named player (each line stores `level_db`) and a verdict: good / change
  settings (gain, clipping, rumble) / change microphone (players ≥ 10 dB apart: the mic favours
  one side; or a poor SNR at a good gain). The 2026-10-08 Yeti GX session: speech −41 dBFS,
  SNR 17 dB, rumble 70%, players within 4 dB → "change settings": raise gain, high-pass.
- **Mic details** (`GET /api/mics`, `mics.py`): macOS reports name, maker, connection and rate
  only; pickup pattern and capsule come from a small catalog of common mics or the user's
  corrections (`data/mics.json`, `PATCH /api/mics/{name}`), plus measured verdicts. Shown as a
  ✓/✗/? checklist: hears the whole table (omni/stereo/boundary), picks up distant voices (not
  dynamic), wired, ≥ 44.1 kHz, measured well.
- **Mic choice** (Setup) is remembered in the browser (`localStorage` `bard.mic`) and an
  amber note says when that mic isn't connected. Before, starting any session (which leaves
  Setup) reset it to the macOS default: the 2026-10-15 game was tested on a Yeti, then ran
  2.6 h on the MacBook's built-in mic (beamforming toward the screen: the near player 4 dB
  louder, the far ones 7 dB quieter).
- **Start-time check**: Start session on a mic whose checklist has a red item (✗) asks first:
  "Hey, you aren't using an omnidirectional mic" (the `around` check) or "Hey, something in
  this mic's checklist is red", with the red items; Start anyway / Pick another mic. If the
  checklist can't be read, the session just starts.

## Transcription study (real 2.6 h session, Yeti GX)

Correction: the table has two similar names, Elara (a PC) and Delara (an NPC Theo plays);
the study first counted both as Elara. Two gold lines were fixed and two ambiguous ones dropped
(92 lines); the ranking didn't change (TGC 10.6% WER, 91% names).

`spike/asr_compare.py` re-transcribed all 2,020 lines under 9 configs;
`spike/asr_score.py` scores them against 94 lines adjudicated by reading every
hypothesis in context (58 of 150 sampled lines were noise nobody could call; skipped).
The gold favours glossary spellings, so read it as a ranking.

| Config | WER | Names right | Notes |
|---|---|---|---|
| TGC turbo, names + last 2 lines prompt | **11.0%** | 86% | **live now**; same speed as T0 |
| TG turbo, names prompt | 11.6% | 89% | |
| LG large-v3, names prompt | 12.6% | **92%** | 3× slower |
| T0 turbo, no prompt (old live) | 12.9% | 31% | |
| L0 large-v3, no prompt | 13.3% | 31% | 3× slower, no better |
| LGC large-v3, names + last 2 lines | 14.3% | 89% | context hurts large-v3 |
| TWG / LWG / LW0 packed ~28 s windows | 21–31% | 50–89% | words slide across line boundaries |
| The session as recorded live | 13.0% | 28% | |
| …after **Improve transcript** | **10.7%** | 86% | 1,311 of 2,020 lines changed |

The names prompt is the win (names ~30% → 86–92%); the last lines add a little for turbo.
A bigger model and longer windows don't pay here. Pasting notes or accepting spellings
grows the glossary, which the prompt and the corrector both use.

Prompting needs a guard: on quiet clips Whisper reads the prompt back ("armor class,
d20, cantrip…") or loops; such a result is redone without the prompt (`asr.suspicious`).

## Whisper prompt (big glossaries)

Whisper reads at most n_ctx/2 − 1 = **223 prompt tokens** and drops the *start* of a longer
prompt (mlx-whisper: `prompt_tokens[-(n_ctx // 2 - 1):]`), which is where the names were. On
2026-10-15 (glossary of 56 names) the first-40-names prompt alone was 188 tokens and **46% of
the session's prompts overflowed**. Now `asr.build_prompt` keeps the last lines to 60 tokens
and adds names, in `asr.prompt_names` order, only while the whole prompt fits; the engine
caps the list at `PROMPT_NAMES` = 20: the table's names (Setup, voices panel), glossary names
said in the last 100 lines (latest first; a glossary nickname said goes in as said, e.g.
"Phisho"), then the rest of the glossary. Clips under 1 s (`CONTEXT_MIN_S`) get the names but
not the last lines: on a blip of noise the lines tempt Whisper into a whole sentence.

`spike/prompt_sweep.py`: the 2026-10-08 gold (94 lines, glossary of 5) with its glossary
padded to 65 names from another campaign (none said in the session); per-line WER, turbo.

| Prompt | WER | Names right |
|---|---|---|
| old, the real 5-name glossary | 11.8% | 76% |
| old, padded to 65 names (5% of prompts overflowed) | 18.6% | 76% |
| all names that fit, no lines | 14.2% | 91% |
| ≤ 12 names, no lines | 11.8% | 91% |
| ≤ 20 names, no lines | 12.6% | 91% |
| ≤ 12 names + 60 tokens of the config's own last lines | 9.4% | 85% |
| **≤ 20 names + 60 tokens of its own last lines (live now)** | **9.3%** | 82% |

A long name list costs words everywhere, overflow or not: cap it. The names lost with the
lines are mostly casing ("magenta", "delara"), which the corrector fixes. Context lines taken
from the old live transcript instead (misspelled names) gave 11.1–11.4% WER and 76–82% names.

Tonight's 2026-10-15 session (laptop mic), 76 lines where configs disagree, adjudicated blind
(variants unlabeled and shuffled; 24 of 100 sampled were too noisy to call):

| Config | Mean WER | Pooled WER | Names right |
|---|---|---|---|
| as recorded live (old prompt, overflowing) | 19.8% | 11.5% | 2 / 14 |
| **live now** (`build_prompt`, own last lines) | **15.3%** | **9.8%** | 7 / 14 |
| TG turbo, 9 table names, no lines | 9.9% | 9.1% | 9 / 14 |
| TGC turbo, 9 table names + 2 own lines, untrimmed | 14.3% | 9.8% | 7 / 14 |
| T0 turbo, no prompt | 11.1% | 9.2% | |
| L0 large-v3, no prompt | 11.0% | 10.6% | |
| LG large-v3, 9 table names | 16.6% | 13.7% | 9 / 14 |

Lines vs no lines is a wash here (22 lines better, 20 worse); the losses are mostly sub-second
noise clips that the last lines tempt into a sentence (3 of 5). **large-v3 isn't better on a
weaker mic either**, not even on the quietest players' lines (48 of the 76: L0 8.0% vs T0
8.7%, TG 9.8%), costs ~3.7× the time, and with a prompt writes "Subtitles by the Amara.org
community" on noise (17 lines this session; turbo never did).

## Utterance cutting

`bard/segment.py` is Silero's VADIterator (proven equal by a test) with the padding as a setting
and padding that never reaches into the previous line. `spike/segment_sweep.py` caches Silero's
probabilities once and replays them through it. Speaker purity on the benchmark clips (AMI + CR,
107 min, true turns); WER on the 2026-10-08 gold by words whose middle falls inside each gold
line (any cutting can be scored), and on whole CR clips against captions.

| Silence / padding / cap | Lines mixing speakers | Speech kept | Gold WER | CR WER |
|---|---|---|---|---|
| 400 ms / 80 ms / 12 s (before) | 25.5% | 81.9% | 12.9% | 21.9% |
| **400 ms / 200 ms / 12 s (now)** | 25.4% | 85.9% | **12.0%** | **21.8%** |
| 400 / 300 / 12 | 26.9% | 88.5% | 21.5%* | 22.0%* |
| 250 / 200 / 12 | 19.9% | 85.8% | 15.3%* | 23.5%* |
| 400 / 200 / 8 | 25.4% | 85.8% | 12.3%* | 22.3%* |

\* with the quiet-point cut below and the old prompt (the 80 and 200 ms rows with it: 12.9/22.1
and 12.0/21.7, so the same ranking).

- Shorter silence (250 ms) separates speakers better (a fifth fewer mixed lines) but shorter
  lines cost Whisper 2–3 points of WER. Not taken; one segment per speaker needs diarization.
- Cutting a 12 s monologue at its quietest moment in the last 3 s instead of at 12 s sharp
  (12% of lines on the clips hit the cap, 14% on 2026-10-15) changed no WER measurably: removed.

## Weak mics (2026-10-15, MacBook built-in mic)

The same 76 blind-adjudicated lines, everything shipped (prompt, nicknames, short-clip rule),
Whisper's temperature fallback seeded so variants differ only by what they change:

| Variant | Mean WER | Pooled WER | Junk on the 5 silent clips |
|---|---|---|---|
| as recorded live | 19.8% | 11.5% | 5 |
| shipped prompt, no junk filters | 14.1% | 9.1% | 2 |
| **+ junk filters (live now)** | **11.4%** | **8.3%** | **0** |
| + loudness normalised to −20 dBFS | 11.4% | 9.6% | 1 |
| + 100 Hz high-pass | 10.9% | 8.9% | 0 |
| + high-pass + normalised | 10.8% | 8.9% | 0 |
| + `noisereduce` + high-pass + normalised | 10.6% | 9.1% | 0 |

On a weak mic Whisper's damage is **junk on noise**, not missed words: 13 live lines held
letters from other scripts ("образbak Waters factual", "COR perfekt달"; one was "…what's
your AC? 18. ож Kobe Thank you."), and the worst single clip was 120 invented words.
`asr._run` now drops any segment with a non-Latin letter (the language is forced to English;
accented Latin is kept), `HALLUCINATIONS` gains the YouTube outros ("we'll be right back",
"see you next time", "subtitles by the amara.org community"), and a result with more than
8 words per second of clip (people top out near 6.6) is redone without the prompt, then
dropped. With 200 ms padding the rate rule flags none of the real fast lines of 2026-10-08.

Cleaning the audio before Whisper doesn't pay (within noise either way; Whisper is trained on
noisy audio), so nothing is filtered. A lower VAD threshold (0.35) finds 54 more clips, 26 s
in 2.6 h, nearly all sub-second and transcribed as junk: the VAD isn't losing quiet players.
What helps a weak mic is the mic (the start-time check), the prompt and the junk filters
above, and fixing speakers by hand (one click per line).

## Milestones

1. **Spike / benchmark**: done (below).
2. **Live backend**: done. Mic or real-time file replay → Silero VAD → Whisper.
3. **Setup + optional Train + voices panel + live speaker lighting**: done.
4. **Chat feed + mechanics** (regex, then local-LLM backfill): done; mechanics removed 2026-10-16.
5. **Background refinement** (re-score past messages, UI relabels): done for
   speaker; re-transcription is the post-session **Improve transcript** job.

Known gaps: one utterance can span a speaker change (VAD cuts on silence only);
the voices panel doesn't split a voice that merged two people.

## Stress test (16.14 h of Critical Role at 8×, UI open)

| | Baseline | After fixes |
|---|---|---|
| Crashes / errors / disconnects | none | none |
| Messages / voices | 9,645 / 298 | 9,637 / 130 |
| UI DOM nodes (max) | 177,647 | 5,063 |
| UI JS heap (max) | 634 MB | 28 MB |
| Worst frame gap | 617 ms | 67 ms |
| Full-history reload (9.6k msgs) | — | ~0.3 s |
| malloc_small per audio-hour | +66 MB | +81 MB (native, not Python) |

Fixes: virtualized chat, id→index store, top-8 pending voices, MLX cache cap
(1 GB), pending-voice tidy-up in refine, caffeinate per session, mic watchdog,
`/api/debug/memory`. Found after the re-run: the chat mounted at 0 px and stayed
blank until scrolled (now absolutely sized); index.html was cached so rebuilds
didn't show (now `no-cache`, hashed assets immutable). Verdict: 4 h is safe
(~+330 MB native creep; ~9 GB total).

## Benchmark (milestone 1)

Rebuild: `uv run --group spike spike/dataset.py`, then `uv run --group spike spike/eval.py --tag NAME`
(defaults = best config below). Every run appends to `samples/results.jsonl`.

**Data** (`samples/eval/`, 70 min total):

- 5 × 10 min Critical Role clips (C2E44, C2E58 combat + roleplay, C3E114,
  VM E52). Reference = official YouTube captions, which name the speaker at
  every change. Combat windows chosen by density of mechanics talk.
- 3 AMI meetings (ES2004a, IS1009a, TS3003a), **single distant mic**: the
  closest public match to one Yeti in the middle of a table. Reference =
  pyannote AMI-diarization-setup RTTM.
- Enrollment: ~30 s per speaker of isolated speech from *outside* the clip
  (CR: elsewhere in the episode; AMI: the sibling meeting, same people).

**Metric**: word-level speaker identity accuracy (word gets the enrolled name
that was actually speaking). WER is vs lightly edited captions, so treat it as
relative. NUM = share of caption numbers transcribed exactly.

| Config | CR speaker ID | AMI speaker ID | CR WER | CR NUM |
|---|---|---|---|---|
| pyannote clusters (majority-mapped, optimistic) | 81.2% | 95.7% | 17.6% | 93.3% |
| + oracle speaker count | 82.0% | 96.6% | | |
| + clustering threshold 0.4–0.7 | 80.3–81.3% | 95.7–96.2% | | |
| pyannote clusters → nearest enrolled voice | 80.9% | 95.7% | | |
| **each Whisper segment → nearest enrolled voice** | 90.7% | 97.7% | | |
| + pad short segments to 1.5 s | 91.0% | 98.3% | | |
| + adapt voiceprints from confident segments | 91.2% | 98.4% | | |
| + no Whisper prompt (**trained default**) | **91.6%** | **98.4%** | 17.4% | 93.0% |
| whisper large-v3 instead of turbo | 90.0% | 98.0% | 17.1% | 91.3% |

Option B, no training (`--voices discover`; the user's naming taps are
simulated with the true majority speaker; taps are per 10 min clip):

| Config | CR speaker ID | AMI speaker ID | Taps |
|---|---|---|---|
| pyannote clusters from first 3 min, named once (`--voices named`) | 84.2% | 78.1% | — |
| continuous discovery, live labels only | 75.8% | 90.6% | ~5 |
| + refine relabel, voice shown at 5 s | 87.7% | 97.5% | |
| + refine relabel, voice shown at 3 s (**default**) | 89.5% | 98.4% | 4–9 |
| + refine relabel, voice shown at 2 s | 91.0% | 98.7% | 5–18 |

Findings:

- **Enrollment is the lever; diarization clustering is the bottleneck.**
  Classifying segments against enrolled voices beats every pyannote variant;
  blending in pyannote's cluster as a prior *lowers* accuracy (85–90%).
- Accuracy by segment length (CR, single speaker, before padding): < 1 s 57%, 1–2 s 93%,
  2–4 s 99%, 4 s+ 100%. Remaining errors: sub-second replies (~50% are a new
  speaker, ~50% a continuation, so context alone can't fix them) and fast
  crosstalk inside one segment. Splitting at word pauses doesn't help.
- Enrollment length: 15 s ≈ 30 s (91.2%); 60 s 91.8%. Any words work.
- Option B ≈ trained once the refine pass relabels history. Live labels lag
  (~76% CR) until voices mature, so show low-confidence labels as provisional.
  Remaining gap is quiet players who never reach the panel: Train fixes that.
- Unknown voices (leave-one-out, segments ≥ 1.5 s): best score < 0.40 flags
  96.7% of unenrolled speakers, wrongly flags 4.3% of enrolled ones.
- Whisper prompt with names: no measurable gain on WER, numbers, or names.
  large-v3: ~3× slower, no better. Turbo, no prompt.
- Speed: ASR 0.013–0.02 RTF, embeddings negligible. Live latency is bounded by
  utterance end detection, not compute.
- Fixed along the way: Whisper repetition loops on trailing noise
  (`condition_on_previous_text=False`, `hallucination_silence_threshold=1.0`).
- Caveat: CR has a mic per person and AMI is meetings. A 10-minute recording of
  the actual table on the Yeti is still the final go/no-go.

## Known risks

- One shared mic: far-field voices embed worse. AMI (single distant mic) at
  98.4% suggests this is manageable with enrollment.
- Sub-second replies ("Yeah", "12") are ~50% wrong. Mitigation: show them
  with low confidence, let the refine pass revisit with more context.
- Crosstalk, laughter, dice noise → VAD/ASR junk.
- Fantasy names: the Whisper prompt doesn't fix them (Grukk → "drug"). Plan a
  glossary fix-up (fuzzy/phonetic match against setup names) and the LLM pass.
