import { useRef, useState } from "react"
import { toast } from "sonner"
import { AudioWaveform, Camera, Crown, UserMinus } from "lucide-react"
import { PlayerAvatar } from "@/components/PlayerAvatar"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import { api, type PlayerPatch } from "@/lib/api"
import { fileToAvatar } from "@/lib/format"
import { actions, useStore } from "@/lib/store"
import type { Player } from "@/lib/types"
import { TrainDialog } from "./TrainDialog"

async function patch(id: string, body: PlayerPatch) {
  try {
    actions.upsertPlayer(await api.updatePlayer(id, body))
  } catch (e) {
    toast.error((e as Error).message)
  }
}

export function PlayerCard({ player }: { player: Player }) {
  const fileRef = useRef<HTMLInputElement>(null)
  const [training, setTraining] = useState(false)
  const trained = useStore(
    (s) =>
      player.trained ||
      s.voices.some(
        (v) => v.status === "named" && v.trained && v.player_id === player.id && v.character == null,
      ),
  )
  const serverError = useStore((s) => !!s.session?.error)
  const idle = useStore((s) => (s.session?.status ?? "idle") === "idle")

  const onFile = async (file: File | undefined) => {
    if (!file) return
    try {
      await patch(player.id, { avatar: await fileToAvatar(file) })
    } catch {
      toast.error("Couldn't read that image")
    }
  }

  // Back to the library: their voice training stays for other campaigns.
  const onRemove = async () => {
    try {
      await api.benchPlayer(player.id) // a snapshot follows
    } catch (e) {
      toast.error((e as Error).message)
    }
  }

  return (
    <Card className="group relative">
      <CardContent className="flex gap-4">
        <div className="flex flex-col items-center gap-2">
          <button
            type="button"
            className="relative rounded-full focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none"
            onClick={() => fileRef.current?.click()}
            title="Upload avatar"
          >
            <PlayerAvatar player={player} className="size-16 text-lg" />
            <span className="absolute inset-0 flex items-center justify-center rounded-full bg-black/50 opacity-0 transition-opacity group-hover:opacity-100">
              <Camera className="size-5" />
            </span>
          </button>
          <input
            ref={fileRef}
            type="file"
            accept="image/*"
            className="hidden"
            onChange={(e) => {
              void onFile(e.target.files?.[0])
              e.target.value = ""
            }}
          />
          <input
            type="color"
            aria-label="Player color"
            className="h-5 w-10 cursor-pointer rounded border-0 bg-transparent"
            defaultValue={player.color}
            key={player.color}
            onBlur={(e) =>
              e.target.value !== player.color && void patch(player.id, { color: e.target.value })
            }
          />
        </div>
        <div className="flex min-w-0 flex-1 flex-col gap-2">
          <div className="grid grid-cols-2 gap-2">
            <div className="space-y-1">
              <Label htmlFor={`name-${player.id}`} className="text-xs text-muted-foreground">
                Player
              </Label>
              <Input
                id={`name-${player.id}`}
                key={player.name}
                defaultValue={player.name}
                onBlur={(e) => {
                  const v = e.target.value.trim()
                  if (v && v !== player.name) void patch(player.id, { name: v })
                  else e.target.value = player.name
                }}
              />
            </div>
            <div className="space-y-1">
              <Label htmlFor={`char-${player.id}`} className="text-xs text-muted-foreground">
                Character
              </Label>
              <Input
                id={`char-${player.id}`}
                key={player.character}
                defaultValue={player.character}
                placeholder={player.is_gm ? "Game master" : "Character name"}
                onBlur={(e) => {
                  const v = e.target.value.trim()
                  if (v !== player.character) void patch(player.id, { character: v })
                }}
              />
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-3">
            <label className="flex items-center gap-2 text-sm">
              <Switch
                checked={player.is_gm}
                onCheckedChange={(v: boolean) => void patch(player.id, { is_gm: v })}
              />
              <Crown className="size-3.5 text-amber-400" /> GM
            </label>
            {trained && (
              <Badge variant="secondary" className="gap-1">
                <AudioWaveform className="size-3" /> Trained
              </Badge>
            )}
            <div className="ml-auto flex gap-1">
              <Button size="sm" variant="outline" disabled={serverError} onClick={() => setTraining(true)}>
                <AudioWaveform /> {trained ? "Retrain" : "Train"}
              </Button>
              <Button
                size="icon-sm"
                variant="ghost"
                onClick={onRemove}
                disabled={!idle}
                title={idle ? "Remove from this campaign (keeps voice training)" : "Stop the session first"}
                aria-label="Remove from this campaign"
              >
                <UserMinus />
              </Button>
            </div>
          </div>
        </div>
      </CardContent>
      {training && <TrainDialog player={player} open={training} onOpenChange={setTraining} />}
    </Card>
  )
}
