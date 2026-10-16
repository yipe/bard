import { useMemo } from "react"
import { Mic } from "lucide-react"
import { PlayerBox, UnnamedShareChip } from "@/components/transcript/PlayerBox"
import { useSpeaking, useStore } from "@/lib/store"
import { talkTime } from "@/lib/talkTime"

export function PlayerGrid() {
  const players = useStore((s) => s.players)
  const voices = useStore((s) => s.voices)
  const messages = useStore((s) => s.messages)
  const activeId = useSpeaking((s) => s.voiceId)
  const active = activeId ? (voices.find((v) => v.id === activeId) ?? null) : null
  const speakingPlayerId = active?.player_id ?? null
  const talk = useMemo(() => talkTime(messages, voices, players), [messages, voices, players])

  if (players.length === 0 && !active) {
    return (
      <p className="px-4 py-3 text-sm text-muted-foreground">
        No players yet — add them in Setup, or name voices as they appear.
      </p>
    )
  }

  return (
    <div className="flex flex-wrap gap-3 px-4 py-3">
      {players.map((p) => (
        <PlayerBox
          key={p.id}
          person={p}
          speaking={p.id === speakingPlayerId}
          seconds={talk.byPlayer.get(p.id) ?? 0}
          total={talk.total}
        />
      ))}
      <UnnamedShareChip seconds={talk.unassigned} total={talk.total} />
      {active && !active.player_id && (
        <div
          data-speaking="true"
          style={{ "--player": "#a1a1aa" } as React.CSSProperties}
          className="speaker-box flex items-center gap-2 rounded-xl border border-dashed bg-card/50 px-3 py-2 text-sm"
        >
          <Mic className="size-4" /> {active.label} speaking
        </div>
      )}
    </div>
  )
}
