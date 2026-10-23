import { useState } from "react"
import { toast } from "sonner"
import { Plus, Users } from "lucide-react"
import { Button } from "@/components/ui/button"
import { api } from "@/lib/api"
import { actions, useStore } from "@/lib/store"
import { LibraryList } from "./LibraryList"
import { PlayerCard } from "./PlayerCard"
import { StartPanel } from "./StartPanel"

export function SetupView() {
  const players = useStore((s) => s.players)
  const [adding, setAdding] = useState(false)

  const add = async () => {
    setAdding(true)
    try {
      actions.upsertPlayer(
        await api.createPlayer({ name: `Player ${players.length + 1}`, is_gm: players.length === 0 }),
      )
    } catch (e) {
      toast.error((e as Error).message)
    } finally {
      setAdding(false)
    }
  }

  return (
    <div className="mx-auto grid max-w-6xl gap-6 p-6 lg:grid-cols-[1fr_340px]">
      <section className="space-y-4">
        <div className="flex items-end justify-between gap-4">
          <div>
            <h2 className="text-lg font-semibold">Players</h2>
            <p className="text-sm text-muted-foreground">
              Add who's at the table. Voice training is optional, and players keep it in every campaign.
            </p>
          </div>
          <Button onClick={add} disabled={adding}>
            <Plus /> Add player
          </Button>
        </div>
        {players.length === 0 ? (
          <div className="flex flex-col items-center gap-2 rounded-xl border border-dashed p-10 text-center text-muted-foreground">
            <Users className="size-8" />
            <p>No players yet — add the GM and everyone at the table.</p>
          </div>
        ) : (
          <div className="grid gap-3 xl:grid-cols-2">
            {players.map((p) => (
              <PlayerCard key={p.id} player={p} />
            ))}
          </div>
        )}
        <LibraryList />
      </section>
      <aside className="space-y-6">
        <StartPanel />
      </aside>
    </div>
  )
}
