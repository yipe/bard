import { useEffect, useMemo, useRef, useState } from "react"
import { toast } from "sonner"
import { AudioLines, Sparkles, Undo2 } from "lucide-react"
import { PlayerAvatar } from "@/components/PlayerAvatar"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { api } from "@/lib/api"
import { speakerFor, type Speaker } from "@/lib/speaker"
import { actions, useStore } from "@/lib/store"
import { cn } from "@/lib/utils"
import type { Player, Voice } from "@/lib/types"
import { SamplePlayer } from "./SamplePlayer"

/** Pending voices shown before "Show all"; the longest-heard voices come first. */
const PENDING_CAP = 8

async function assign(voiceId: string, playerId: string, character: string | null) {
  try {
    const { voice, merged_into } = await api.assignVoice(voiceId, playerId, character)
    if (!merged_into) actions.upsertVoice(voice)
  } catch (e) {
    toast.error((e as Error).message)
  }
}

export function VoicesPanel() {
  const voices = useStore((s) => s.voices)
  const players = useStore((s) => s.players)
  const [showAll, setShowAll] = useState(false)
  const focusId = useStore((s) => s.focus?.voiceId ?? null)
  const named = voices.filter((v) => v.status === "named")
  const pending = useMemo(
    () => voices.filter((v) => v.status === "pending").sort((a, b) => b.seconds - a.seconds),
    [voices],
  )
  const shown = useMemo(() => {
    if (showAll) return pending
    const top = pending.slice(0, PENDING_CAP)
    // Keep a voice focused from the chat ("Name this voice") visible even when it's outside the top N.
    const focused = pending.find((v) => v.id === focusId)
    return focused && !top.includes(focused) ? [...top, focused] : top
  }, [pending, showAll, focusId])

  return (
    <div className="space-y-4">
      <h3 className="flex items-center gap-2 text-sm font-semibold">
        <AudioLines className="size-4" /> Voices
      </h3>
      {voices.length === 0 && (
        <p className="text-sm text-muted-foreground">No voices yet — they appear here as people speak.</p>
      )}
      {pending.length > 0 && (
        <ul className="space-y-2">
          {shown.map((v) => (
            <PendingVoice key={v.id} voice={v} players={players} />
          ))}
        </ul>
      )}
      {pending.length > PENDING_CAP && (
        <Button size="sm" variant="ghost" className="w-full" onClick={() => setShowAll((x) => !x)}>
          {showAll ? "Show fewer" : `Show all (${pending.length})`}
        </Button>
      )}
      {named.length > 0 && (
        <ul className="space-y-1">
          {named.map((v) => (
            <NamedVoice key={v.id} voice={v} speaker={speakerFor(v.id, voices, players)} />
          ))}
        </ul>
      )}
    </div>
  )
}

function PendingVoice({ voice, players }: { voice: Voice; players: Player[] }) {
  const focus = useStore((s) => (s.focus?.voiceId === voice.id ? s.focus.n : 0))
  const ref = useRef<HTMLLIElement>(null)
  const [playerId, setPlayerId] = useState<string | null>(null)
  const [character, setCharacter] = useState("")
  const [busy, setBusy] = useState(false)
  const suggested = players.find((p) => p.id === voice.suggested_player_id)

  useEffect(() => {
    if (!focus) return
    ref.current?.scrollIntoView({ behavior: "smooth", block: "center" })
    ref.current?.querySelector("button")?.focus({ preventScroll: true })
  }, [focus])

  const run = async (pid: string, char: string | null) => {
    setBusy(true)
    await assign(voice.id, pid, char)
    setBusy(false)
  }

  return (
    <li
      ref={ref}
      key={focus}
      className={cn("space-y-2 rounded-lg border border-dashed bg-card/60 p-3", focus > 0 && "animate-flash")}
    >
      <div className="flex items-center gap-2">
        <span className="font-medium">{voice.label}</span>
        <span className="text-xs text-muted-foreground tabular-nums">{Math.round(voice.seconds)}s heard</span>
        <span className="ml-auto">{voice.sample_url && <SamplePlayer url={voice.sample_url} />}</span>
      </div>
      {suggested && (
        <Button size="sm" variant="secondary" disabled={busy} onClick={() => run(suggested.id, null)}>
          <Sparkles /> {suggested.name}?
          {voice.suggested_score != null && (
            <span className="text-muted-foreground tabular-nums">
              {Math.round(voice.suggested_score * 100)}%
            </span>
          )}
        </Button>
      )}
      {players.length === 0 ? (
        <p className="text-xs text-muted-foreground">Add players in Setup to name this voice.</p>
      ) : (
        <div className="space-y-2">
          <Select
            value={playerId}
            onValueChange={(v) => setPlayerId(v as string | null)}
            items={players.map((p) => ({ value: p.id, label: p.name }))}
          >
            <SelectTrigger size="sm" className="w-full">
              <SelectValue placeholder="Who is this?" />
            </SelectTrigger>
            <SelectContent>
              {players.map((p) => (
                <SelectItem key={p.id} value={p.id}>
                  {p.name}
                  {p.character && <span className="text-muted-foreground">{p.character}</span>}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <div className="flex gap-2">
            <Input
              className="h-7"
              placeholder="Character / NPC (optional)"
              value={character}
              onChange={(e) => setCharacter(e.target.value)}
            />
            <Button
              size="sm"
              disabled={!playerId || busy}
              onClick={() => playerId && run(playerId, character.trim() || null)}
            >
              Assign
            </Button>
          </div>
        </div>
      )}
    </li>
  )
}

function NamedVoice({ voice, speaker: { player, character } }: { voice: Voice; speaker: Speaker }) {
  const unassign = async () => {
    try {
      actions.upsertVoice(await api.unassignVoice(voice.id))
    } catch (e) {
      toast.error((e as Error).message)
    }
  }
  return (
    <li className="flex items-center gap-2 rounded-lg px-1 py-1.5">
      <PlayerAvatar player={player} className="size-7 text-[10px]" />
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-1.5">
          <span className="truncate text-sm font-medium">{player?.name ?? voice.label}</span>
          {voice.trained && (
            <Badge variant="outline" className="h-4 px-1.5 text-[10px]">
              trained
            </Badge>
          )}
        </div>
        {character && <div className="truncate text-xs text-muted-foreground">{character}</div>}
      </div>
      {voice.sample_url && <SamplePlayer url={voice.sample_url} />}
      <Button size="icon-sm" variant="ghost" aria-label="Unassign voice" title="Unassign" onClick={unassign}>
        <Undo2 />
      </Button>
    </li>
  )
}
