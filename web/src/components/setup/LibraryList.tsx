import { useState } from "react"
import { toast } from "sonner"
import { AudioWaveform, Plus, Trash2 } from "lucide-react"
import { PlayerAvatar } from "@/components/PlayerAvatar"
import { Button } from "@/components/ui/button"
import { api } from "@/lib/api"
import { actions, useStore } from "@/lib/store"
import type { LibraryPlayer } from "@/lib/types"

/** People from other campaigns: add them here and their voice training comes along. */
export function LibraryList() {
  const library = useStore((s) => s.library)
  if (library.length === 0) return null
  return (
    <div className="space-y-2">
      <h3 className="text-sm font-medium text-muted-foreground">From your other campaigns</h3>
      <ul className="flex flex-wrap gap-2">
        {library.map((p) => (
          <LibraryChip key={p.id} person={p} />
        ))}
      </ul>
    </div>
  )
}

function LibraryChip({ person }: { person: LibraryPlayer }) {
  const [confirming, setConfirming] = useState(false)

  const seat = async () => {
    try {
      // The server restores their seat from before they left; the first at an empty table is GM.
      const seated = await api.seatPlayer(person.id)
      actions.seated(seated)
    } catch (e) {
      toast.error((e as Error).message)
    }
  }

  const remove = async () => {
    try {
      await api.deletePlayer(person.id)
      actions.dropFromLibrary(person.id)
    } catch (e) {
      toast.error((e as Error).message)
    }
  }

  return (
    <li className="flex items-center gap-2 rounded-full border bg-card py-1 pr-1 pl-1">
      <PlayerAvatar player={person} className="size-7" />
      <span className="text-sm">{person.name}</span>
      {person.trained && <AudioWaveform className="size-3.5 text-muted-foreground" aria-label="Trained" />}
      {confirming ? (
        <>
          <Button size="xs" variant="destructive" onClick={() => void remove()}>
            Delete for good
          </Button>
          <Button size="xs" variant="ghost" onClick={() => setConfirming(false)}>
            Keep
          </Button>
        </>
      ) : (
        <>
          <Button size="xs" variant="secondary" onClick={() => void seat()} aria-label={`Add ${person.name}`}>
            <Plus /> Add
          </Button>
          <Button
            size="icon-xs"
            variant="ghost"
            onClick={() => setConfirming(true)}
            aria-label={`Delete ${person.name} and their voice training`}
          >
            <Trash2 />
          </Button>
        </>
      )}
    </li>
  )
}
