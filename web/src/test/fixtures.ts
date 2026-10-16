import type {
  AudioQuality,
  Campaign,
  CampaignJob,
  Entity,
  Message,
  MicInfo,
  Player,
  RecordMessage,
  SavedSession,
  Session,
  SessionRecord,
  Snapshot,
  Voice,
} from "@/lib/types"

export function player(p: Partial<Player> & { id: string }): Player {
  return { name: p.id, character: "", avatar: null, color: "#f59e0b", trained: false, is_gm: false, ...p }
}

/** The campaign fields every snapshot carries: one campaign, nobody in the library. */
export function snapshotBase(s: Partial<Pick<Snapshot, "campaign" | "campaigns" | "library">> = {}) {
  return { campaign: "main", campaigns: [{ id: "main", name: "My campaign" }], library: [], ...s }
}

export function voice(v: Partial<Voice> & { id: string }): Voice {
  return {
    label: `Voice ${v.id}`,
    status: "pending",
    player_id: null,
    character: null,
    seconds: 0,
    sample_url: null,
    suggested_player_id: null,
    suggested_score: null,
    trained: false,
    ...v,
  }
}

export function message(m: Partial<Message> & { id: string }): Message {
  return {
    voice_id: null,
    text: "",
    t_start: 0,
    t_end: 1,
    confidence: 1,
    provisional: false,
    fixes: [],
    level_db: null,
    ...m,
  }
}

export function session(s: Partial<Session> = {}): Session {
  return {
    status: "running",
    source: { kind: "mic", device: null },
    t: 0,
    llm: { url: "http://llm", model: "m", ok: true },
    asr: { model: "whisper", state: "ready" },
    training: null,
    audio_monitor: true,
    error: null,
    ...s,
  }
}

export function savedSession(s: Partial<SavedSession> & { id: string }): SavedSession {
  return {
    started_at: 1_760_000_000,
    source: null,
    duration_s: 0,
    lines: 0,
    audio: false,
    replayable: false,
    restorable: false,
    quality: null,
    ...s,
  }
}

export function recordMessage(m: Partial<RecordMessage> & { id: string }): RecordMessage {
  return {
    t_start: 0,
    t_end: 1,
    speaker: "Unknown",
    player_id: null,
    character: null,
    voice_id: null,
    provisional: false,
    confidence: 1,
    text: "",
    ...m,
  }
}

export function sessionRecord(r: Partial<SessionRecord> & { id: string }): SessionRecord {
  const { id, ...rest } = r
  return {
    version: 1,
    session: { id, started_at: 1_760_000_000, source: null, duration_s: 0, lines: 0 },
    players: [],
    voices: [],
    messages: [],
    ...rest,
  }
}

export function entity(e: Partial<Entity> & { name: string }): Entity {
  return {
    type: "npc",
    aliases: [],
    heard_as: [],
    heard_as_suggested: [],
    heard_as_rejected: [],
    status: "confirmed",
    description: "",
    facts: [],
    suggested_facts: [],
    path: `People/${e.name}.md`,
    read_only: false,
    ...e,
  }
}

export function campaignJob(j: Partial<CampaignJob> & { id: string }): CampaignJob {
  return {
    kind: "import",
    title: "",
    state: "queued",
    done: 0,
    total: 0,
    message: "",
    created: [],
    updated: [],
    suggested: 0,
    note: null,
    ...j,
  }
}

export function campaign(c: Partial<Campaign> = {}): Campaign {
  return {
    vault: "/Users/me/Vault",
    obsidian: { id: "abc123", folder: "" },
    types: ["pc", "npc", "place", "faction", "item", "creature", "spell", "term"],
    entities: [],
    facts: [],
    jobs: [],
    written_up: {},
    ignored: [],
    ...c,
  }
}

/** A live quality read-out (quiet speech, some rumble). */
export function audioQuality(q: Partial<AudioQuality> = {}): AudioQuality {
  return {
    source: "session",
    window_s: 15,
    speech_db: -34.2,
    noise_db: -58.6,
    snr_db: 24.4,
    rumble_pct: 31,
    clipped_pct: 0.004,
    talking: true,
    grades: { speech: "ok", noise: "ok", snr: "ok", rumble: "ok", clipping: "good" },
    hints: [
      "Speech is quiet: raise the mic gain until talking peaks around −20 to −12 dBFS.",
      "Lots of low rumble: turn on the mic's high-pass filter (80–100 Hz) and put the mic on something soft.",
    ],
    ...q,
  }
}

/** A mic as GET /api/mics describes it. */
export function mic(m: Partial<MicInfo> & { name: string }): MicInfo {
  return {
    maker: null,
    connection: "USB",
    rate: 48000,
    default: false,
    specs: { source: "unknown" },
    measured: [],
    checks: [],
    ...m,
  }
}
