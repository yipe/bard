import { useState, type ReactNode } from "react"
import { toast } from "sonner"
import { PlayerAvatar } from "@/components/PlayerAvatar"
import { Input } from "@/components/ui/input"
import { Popover, PopoverContent, PopoverTitle, PopoverTrigger } from "@/components/ui/popover"
import { api } from "@/lib/api"
import { useStore } from "@/lib/store"
import type { Player } from "@/lib/types"

/**
 * The speaker's name on a live line, as a button: pick who said it and the line is theirs at
 * once (the server pins it and learns their voice from it). Characters someone already voices
 * are offered too; typing a character applies it to whichever player is picked.
 */
export function LineSpeakerPicker({ messageId, children }: { messageId: string; children: ReactNode }) {
  const players = useStore((s) => s.players)
  const voices = useStore((s) => s.voices)
  const [open, setOpen] = useState(false)
  const [typed, setTyped] = useState("")
  const [busy, setBusy] = useState(false)
  const character = typed.trim() || null

  // Each player as themselves (or as the typed character), then the characters already voiced.
  const options: { key: string; player: Player; character: string | null; label: string }[] = players.map(
    (p) => ({
      key: p.id,
      player: p,
      character,
      label: character ? `as ${character}` : p.character,
    }),
  )
  if (!character) {
    for (const v of voices) {
      const player = players.find((p) => p.id === v.player_id)
      if (v.status === "named" && v.character && player) {
        options.push({ key: v.id, player, character: v.character, label: `as ${v.character}` })
      }
    }
  }

  const pick = async (playerId: string, char: string | null) => {
    setBusy(true)
    try {
      await api.assignMessage(messageId, playerId, char)
      setOpen(false)
      setTyped("")
    } catch (e) {
      toast.error((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger
        render={
          <button
            type="button"
            title="Who said this?"
            className="cursor-pointer rounded-sm underline-offset-2 hover:underline focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-hidden"
          />
        }
      >
        {children}
      </PopoverTrigger>
      <PopoverContent align="start" className="w-64 space-y-2 p-2">
        <PopoverTitle className="px-1 text-xs text-muted-foreground">Who said this line?</PopoverTitle>
        {players.length === 0 ? (
          <p className="px-1 text-xs text-muted-foreground">Add players in Setup first.</p>
        ) : (
          <>
            <ul className="space-y-0.5">
              {options.map((o) => (
                <li key={o.key}>
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => void pick(o.player.id, o.character)}
                    className="flex w-full items-center gap-2 rounded-md px-1 py-1 text-left hover:bg-muted disabled:opacity-50"
                  >
                    <PlayerAvatar player={o.player} className="size-5 text-[10px]" />
                    <span style={{ color: o.player.color }}>{o.player.name}</span>
                    <span className="truncate text-muted-foreground">{o.label}</span>
                  </button>
                </li>
              ))}
            </ul>
            <Input
              className="h-7"
              placeholder="As a character / NPC (optional)"
              value={typed}
              onChange={(e) => setTyped(e.target.value)}
            />
          </>
        )}
      </PopoverContent>
    </Popover>
  )
}
