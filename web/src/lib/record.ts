import { speakerKey } from "./speaker"
import type { Fix, ImprovedInfo, RecordPlayer, SessionRecord, SpellingInfo } from "./types"

/** The backend's player palette; records saved before colors were stored fall back to it by index. */
export const PLAYER_PALETTE = [
  "#f59e0b",
  "#10b981",
  "#3b82f6",
  "#ec4899",
  "#8b5cf6",
  "#ef4444",
  "#14b8a6",
  "#f97316",
  "#84cc16",
  "#06b6d4",
]

/** Color for speakers whose player isn't in the record's roster. */
const UNKNOWN_PLAYER_COLOR = "#71717a"

export interface ViewerPlayer {
  id: string
  name: string
  character: string
  is_gm: boolean
  color: string
}

export interface ViewerLine {
  id: string
  t_start: number
  t_end: number
  speaker: string
  player_id: string | null
  /** The speaker's player, or a stand-in when the id isn't in the roster; null for unnamed voices. */
  player: ViewerPlayer | null
  character: string | null
  /** `speakerKey` of the line; consecutive lines with the same non-null key render as one turn. */
  speaker_key: string | null
  uncertain: boolean
  text: string
  fixes: Fix[]
}

export interface LoadedRecord {
  players: ViewerPlayer[]
  lines: ViewerLine[]
  /** Present once the transcript was re-transcribed ("Improve transcript"). */
  improved: ImprovedInfo | null
  /** Present once "Fix spellings" ran over it. */
  spelling: SpellingInfo | null
}

function viewerPlayer(p: RecordPlayer, index: number): ViewerPlayer {
  return {
    id: p.id,
    name: String(p.name ?? ""),
    character: String(p.character ?? ""),
    is_gm: !!p.is_gm,
    color: p.color || PLAYER_PALETTE[index % PLAYER_PALETTE.length],
  }
}

/**
 * Turn a saved record into what the viewer renders. Uses each line's precomputed speaker, never the
 * live store. Missing fields get safe defaults.
 */
export function loadRecord(record: SessionRecord): LoadedRecord {
  const players = (record.players ?? []).map(viewerPlayer)
  const byId = new Map(players.map((p) => [p.id, p]))
  const lines: ViewerLine[] = (record.messages ?? [])
    .map((m) => {
      const text = String(m.text ?? "")
      const speaker = String(m.speaker || "Unknown")
      const player =
        m.player_id == null
          ? null
          : (byId.get(m.player_id) ?? {
              id: m.player_id,
              name: speaker,
              character: "",
              is_gm: false,
              color: UNKNOWN_PLAYER_COLOR,
            })
      return {
        id: m.id,
        t_start: Number(m.t_start) || 0,
        t_end: Number(m.t_end) || 0,
        speaker,
        player_id: m.player_id ?? null,
        player,
        character: m.character || null,
        speaker_key: speakerKey(m.player_id ?? null, m.voice_id ?? null, m.character || null),
        uncertain: !!m.provisional || (m.confidence ?? 1) < 0.5,
        text,
        fixes: Array.isArray(m.fixes) ? m.fixes : [],
      }
    })
    .sort((a, b) => a.t_start - b.t_start)

  return {
    players,
    lines,
    improved: record.session?.improved ?? null,
    spelling: record.session?.spelling ?? null,
  }
}
