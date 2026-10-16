import type { Player, Voice } from "./types"

export interface Speaker {
  voice: Voice | null
  player: Player | null
  name: string
  character: string | null
}

export function speakerFor(voiceId: string | null, voices: Voice[], players: Player[]): Speaker {
  const voice = voiceId ? (voices.find((v) => v.id === voiceId) ?? null) : null
  const player = voice?.player_id ? (players.find((p) => p.id === voice.player_id) ?? null) : null
  const name = player?.name ?? voice?.label ?? "…"
  // A player with any character voice speaks out-of-character on their other voices.
  const hasCharacterVoice =
    !!player && voices.some((v) => v.status === "named" && v.player_id === player.id && v.character != null)
  const character = voice?.character || (hasCharacterVoice ? null : player?.character) || null
  return { voice, player, name, character }
}

/**
 * Who a line is from, for grouping consecutive lines in a transcript. A player's voices share a key
 * (switching voices doesn't split their turn, a different character does); null when the speaker is
 * unknown, so unknown lines never group.
 */
export function speakerKey(
  playerId: string | null,
  voiceId: string | null,
  character: string | null,
): string | null {
  const who = playerId ? `p:${playerId}` : voiceId ? `v:${voiceId}` : null
  return who ? `${who}|${character ?? ""}` : null
}
