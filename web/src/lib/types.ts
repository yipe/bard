/** A person in the library. `character` and `is_gm` are their seat in the open campaign. */
export interface Player {
  id: string
  name: string
  character: string
  avatar: string | null /* data URL */
  color: string /* hex like #f59e0b */
  trained: boolean
  is_gm: boolean
}
/** Someone in the library who isn't playing in the open campaign (voice training kept). */
export type LibraryPlayer = Omit<Player, "character" | "is_gm">
/** A campaign: its own players' characters, notes (vault) and saved sessions. */
export interface CampaignInfo {
  id: string
  name: string
}
export interface Campaigns {
  campaign: string /* id of the open campaign */
  campaigns: CampaignInfo[]
}
export interface Voice {
  id: string
  label: string /* "Voice 3" */
  status: "pending" | "named"
  player_id: string | null
  character: string | null /* optional PC/NPC name for this voice */
  seconds: number /* speech heard so far */
  sample_url: string | null /* wav */
  suggested_player_id: string | null
  suggested_score: number | null /* 0-1, for pending voices */
  trained: boolean /* voiceprint came from a Train recording */
}
export interface Message {
  id: string
  voice_id: string | null
  text: string
  t_start: number
  t_end: number /* session seconds */
  confidence: number /* 0-1 */
  provisional: boolean
  /** Words the spelling cleanup replaced (UTF-16 offsets into the current `text`). */
  fixes: Fix[]
  /** Loudness of the line (dBFS); null when unknown. */
  level_db: number | null
}
/** A span of `Message.text` the spelling cleanup replaced; `heard` is what speech-to-text wrote. */
export interface Fix {
  start: number
  end: number /* UTF-16, end exclusive */
  heard: string
}
export type Source =
  | { kind: "mic"; device: string | null }
  | { kind: "file"; name: string; speed: number }
  | { kind: "session"; id: string /* saved session being replayed */; speed: number }
export interface Session {
  status: "idle" | "running" | "paused" | "stopping"
  source: Source | null
  t: number /* session seconds at send time */
  llm: { url: string; model: string; ok: boolean }
  asr: {
    model: string
    state: "loading" | "ready" | "error"
  } /* Whisper + voice models, loaded at start-up */
  training: { player_id: string } | null /* who is being trained right now */
  /** Live level + quality read-out on (PUT /api/audio/monitor); off saves the per-frame work. */
  audio_monitor: boolean
  error: string | null /* e.g. models failed to load; blocks Start and Train */
}
export interface SavedSession {
  id: string
  started_at: number /* epoch seconds */
  source: Source | null
  duration_s: number
  lines: number
  audio: boolean /* has its own mic recording at /api/sessions/{id}/audio.wav */
  replayable: boolean /* its recording, or the sample file it replayed, still exists */
  restorable: boolean /* improved; the original transcript can be restored */
  /** The recording's verdict, once measured (GET /api/sessions/{id}/quality); null = not yet. */
  quality: QualityVerdict | null
  /** Ids of later sessions already appended onto this one. */
  merged_from?: string[]
}
/** Set on a saved record once "Improve transcript" re-transcribed it. */
export interface ImprovedInfo {
  at: number /* epoch seconds */
  model: string
  changed: number /* lines whose text changed */
}
/** Set on a saved record once Retranscribe reassigned speakers. The words are unchanged. */
export interface RetranscribedInfo {
  at: number
  changed: number
  lines: number
  /** Lines whose old label was hearing the person who spoke next. */
  heard_next: number
  /** Lines whose own audio now matches a different enrolled voice. */
  rematch: number
  backup: string
}
/** Set on a saved record once "Fix spellings" ran over it. */
export interface SpellingInfo {
  at: number /* epoch seconds */
  fixed: number /* spellings fixed */
}
export type MessagePatch = { id: string } & Partial<Omit<Message, "id">>
export interface Snapshot extends Campaigns {
  session: Session
  players: Player[]
  library: LibraryPlayer[]
  voices: Voice[]
  messages: Message[]
}
export interface SourceList {
  files: { name: string; duration: number; audio_url: string }[]
  mics: { name: string; default: boolean }[]
}
export type ServerEvent =
  | { type: "snapshot"; data: Snapshot }
  | { type: "session.updated"; data: Session }
  | { type: "player.upserted"; data: Player }
  | { type: "player.removed"; data: { id: string } }
  | { type: "voice.upserted"; data: Voice }
  | { type: "voice.removed"; data: { id: string; merged_into: string | null } }
  | { type: "speaker.active"; data: { voice_id: string | null; confidence: number } } // null = nobody speaking; ≤ 4/s
  | { type: "message.created"; data: Message }
  // Batch; unknown ids ignored. `fixes`, when present, replaces the old list and fits the patch's
  // `text` (or the current text if the patch has none).
  | { type: "messages.updated"; data: MessagePatch[] }
  | {
      type: "train.status"
      data: {
        player_id: string
        state: "recording" | "processing" | "done" | "error" | "cancelled"
        seconds_left: number
        level: number /* 0-1 */
        message: string | null
      }
    }
  | { type: "error"; data: { message: string } }
  | { type: "campaign.job"; data: CampaignJob }
  | { type: "campaign.answer"; data: CampaignAnswer }
  | { type: "audio.level"; data: AudioLevel } // ~10/s while a mic is open
  | { type: "audio.quality"; data: AudioQuality } // every 2 s, over the last ~15 s
  | { type: "mic.test"; data: { state: "running" | "stopped" } }
  | { type: "campaign.updated"; data: Record<string, never> } // refetch GET /api/campaign
  | { type: "campaigns.updated"; data: Campaigns } // created or renamed; switching sends a snapshot
  | { type: "library.updated"; data: LibraryPlayer[] }
  | { type: "cloud.updated"; data: CloudStatus }

/* ---- Troubadour Cloud (opt-in backup of the open campaign's vault) ---- */
export interface CloudBackup {
  at: number /* epoch s */
  snapshot: string
  files: number
  bytes: number
  uploaded: number /* files new or changed in that backup */
  unchanged: boolean
  skipped: string[] /* "path: why", at most 50 */
  quota: { used_bytes: number; limit_bytes: number; after_bytes: number }
  fingerprint: string
}
export interface CloudStatus {
  url: string
  connected: boolean
  campaign: string /* the campaign the settings below belong to */
  include_transcripts: boolean
  auto_backup: boolean /* back up by itself a couple of minutes after notes change */
  last_backup: CloudBackup | null
  last_error: { at: number; message: string } | null /* the last backup failed; cleared by a good one */
}
/** Who is signed in to Troubadour Cloud (GET /api/cloud/account). `avatar` is a data: URL. */
export interface CloudAccount {
  email: string | null
  name: string | null
  avatar: string | null
}

/** A campaign in Troubadour Cloud (backed up from any Mac), to restore from. */
export interface CloudVault {
  id: string
  name: string
  campaign_id: string
  head_snapshot: string | null
  created_at: string
}

/* ---- Campaign memory (an Obsidian vault of notes) ---- */
export type EntityType = "pc" | "npc" | "place" | "faction" | "item" | "creature" | "spell" | "term"
export interface Entity {
  name: string
  type: EntityType
  aliases: string[]
  heard_as: string[] /* how speech-to-text misspells it; fixed in new transcripts */
  heard_as_suggested: string[]
  heard_as_rejected: string[]
  status: "confirmed" | "suggested"
  description: string
  facts: string[] /* may contain Obsidian links: [[Target]] or [[Target|label]] */
  suggested_facts: string[]
  path: string | null /* vault-relative, e.g. "People/Elara.md" */
  read_only: boolean
  sessions?: number /* how many played sessions mention this name; missing on older payloads */
}
/** A source an answer cites: a transcript excerpt, a glossary entry or another vault note. */
export interface AnswerSource {
  id: string /* "S1"…, as the model cited it */
  kind: "transcript" | "entry" | "note"
  label: string /* "2026-10-08 The Desert Road · 01:15", or a vault-relative path */
  session: string | null /* transcript: saved session id */
  t: number | null /* transcript: seconds into the session */
  path: string | null /* entry or note: vault-relative */
  entry: string | null /* entry: its name */
  excerpt: string
}
/** A question about the campaign, answered by the local LLM from transcripts and notes. */
export interface CampaignAnswer {
  id: string
  question: string
  state: "queued" | "searching" | "thinking" | "done" | "error"
  answer: string
  found: boolean /* false: the sources didn't answer it */
  sources: AnswerSource[]
  message: string /* error */
  created: number /* epoch s */
}
export interface CampaignJob {
  id: string
  kind: "import" | "writeup" | "facts" | "improve" | "spelling" | "retranscribe" | "backup" | "restore"
  title: string /* notes title, or the session id for a write-up / improve */
  state: "queued" | "running" | "done" | "error"
  done: number
  total: number
  message: string
  created: string[]
  updated: string[]
  suggested: number
  note: string | null /* vault-relative path it wrote */
}
/** The campaign folder in Obsidian's vault registry: the vault's id and the folder inside it ("" = the vault). */
export interface ObsidianVault {
  id: string
  folder: string
}
export interface Campaign {
  vault: string /* absolute path of the vault folder */
  obsidian: ObsidianVault | null /* null: Obsidian doesn't know the folder (not opened as a vault) */
  types: EntityType[]
  entities: Entity[]
  facts: string[] /* campaign-wide */
  jobs: CampaignJob[]
  answers: CampaignAnswer[] /* the open campaign's, oldest first */
  written_up: Record<string, string> /* session id -> vault-relative path of its session page */
  ignored: string[] /* words never suggested again (vault note "Ignored words.md") */
}
export interface CampaignSuggestions {
  misheard: { entity: string; heard: string; count: number; example: string }[]
  unknown: { heard: string; count: number; example: string }[]
}

export type TrainStatus = Extract<ServerEvent, { type: "train.status" }>["data"]

/** Saved session record from `GET /api/sessions/{id}/transcript.json`. Historical: speakers are precomputed. */
export interface RecordPlayer {
  id: string
  name: string
  character: string
  is_gm: boolean
  color?: string /* absent in records saved before colors were stored */
}
export interface RecordMessage {
  id: string
  t_start: number
  t_end: number
  speaker: string /* display name at save time, e.g. "Nora" or "Voice 38" */
  player_id: string | null /* null for unnamed voices */
  character: string | null
  voice_id: string | null
  provisional: boolean
  confidence: number
  text: string
  /** Records saved before mechanics were removed also carry `annotations`; ignored. */
  fixes?: Fix[] /* absent in records saved before the spelling cleanup */
  level_db?: number | null
}
export interface SessionRecord {
  version: number
  session: Omit<SavedSession, "audio" | "replayable" | "restorable" | "quality"> & {
    improved?: ImprovedInfo
    spelling?: SpellingInfo
    retranscribed?: RetranscribedInfo
  }
  players: RecordPlayer[]
  voices: {
    id: string
    label: string
    player_id: string | null
    character: string | null
    trained: boolean
  }[]
  messages: RecordMessage[]
}

/* ---- Audio: live level, quality read-outs, recording reports, mic details ---- */
export type AudioSource = "session" | "test"
export type LevelZone = "silent" | "quiet" | "good" | "hot" | "clip" | "off"
export interface AudioLevel {
  source: AudioSource
  db: number | null /* ~100 ms RMS, dBFS; null when the mic stopped */
  peak_db: number | null
  clipping: boolean
  zone: LevelZone
}
export type Grade = "good" | "ok" | "poor"
export interface QualityGrades {
  speech: Grade
  noise: Grade
  snr: Grade
  rumble: Grade
  clipping: Grade
}
/** The five numbers that say whether a mic is set up well. */
export interface QualityNumbers {
  speech_db: number
  noise_db: number
  snr_db: number
  rumble_pct: number
  clipped_pct: number
  grades: QualityGrades
}
export interface AudioQuality extends QualityNumbers {
  source: AudioSource
  window_s: number
  talking: boolean
  hints: string[]
}
export type QualityVerdict = "good" | "settings" | "microphone"
export interface RecordingQuality {
  mic: string | null
  overall: QualityNumbers
  /** One point per minute. */
  timeline: {
    t: number
    speech_db: number
    noise_db: number
    snr_db: number
    rumble_pct: number
    clipped_pct: number
  }[]
  /** Named players, loudest first. */
  speakers: { speaker: string; level_db: number; lines: number }[]
  spread_db: number
  verdict: { kind: QualityVerdict | "unknown"; reasons: string[] }
}
export type MicPattern =
  "omni" | "cardioid" | "supercardioid" | "hypercardioid" | "figure-8" | "stereo" | "boundary" | "beamforming"
export type MicCapsule = "dynamic" | "condenser" | "array"
export interface MicSpecs {
  source: "catalog" | "you" | "unknown"
  model?: string
  capsule?: MicCapsule
  patterns?: MicPattern[]
  bits?: number
  max_rate?: number
  wireless?: boolean
  notes?: string
}
export interface MicCheck {
  id: "around" | "distance" | "wired" | "rate" | "measured"
  label: string
  ok: boolean | null /* null = can't tell */
  detail: string
}
export interface MicInfo {
  name: string
  maker: string | null
  connection: "built-in" | "USB" | "Bluetooth" | "virtual" | "aggregate" | "other"
  rate: number | null
  default: boolean
  specs: MicSpecs
  measured: { session: string; started_at: number; verdict: QualityVerdict | "unknown"; reasons: string[] }[]
  checks: MicCheck[]
}
export type MicSpecsPatch = Partial<Pick<MicSpecs, "model" | "capsule" | "patterns" | "wireless" | "notes">>
