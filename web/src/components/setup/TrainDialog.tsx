import { useEffect, useState } from "react"
import { toast } from "sonner"
import { Loader2, Mic } from "lucide-react"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Progress } from "@/components/ui/progress"
import { api } from "@/lib/api"
import { actions, useStore } from "@/lib/store"
import type { Player } from "@/lib/types"

const SECONDS = 20

/** Driven only by `train.status` events and `session.training`; the POST response is just an ack. */
export function TrainDialog({
  player,
  open,
  onOpenChange,
}: {
  player: Player
  open: boolean
  onOpenChange: (o: boolean) => void
}) {
  const status = useStore((s) => s.train[player.id])
  const serverError = useStore((s) => s.session?.error ?? null)
  const [started, setStarted] = useState(false)
  const [requestError, setRequestError] = useState<string | null>(null)

  const inFlight =
    started && !requestError && (!status || status.state === "recording" || status.state === "processing")

  const start = () => {
    actions.clearTrain(player.id)
    setRequestError(null)
    setStarted(true)
    api.trainPlayer(player.id, SECONDS).catch((e: Error) => setRequestError(e.message))
  }

  const reset = () => {
    actions.clearTrain(player.id)
    setRequestError(null)
    setStarted(false)
  }

  const close = (next: boolean) => {
    if (!next && inFlight) api.cancelTraining().catch((e: Error) => toast.error(e.message))
    onOpenChange(next)
  }

  useEffect(() => {
    if (!started) return
    if (status?.state === "done") {
      toast.success(`${player.name}'s voice is trained`)
      onOpenChange(false)
    }
  }, [started, status?.state, player.name, onOpenChange])

  // If a snapshot/session.updated says nobody is training while we still show progress,
  // the terminal event was lost (e.g. during a reconnect): go back to the start state.
  useEffect(
    () =>
      useStore.subscribe((s, prev) => {
        if (s.session === prev.session || s.session?.training) return
        const st = s.train[player.id]?.state
        if (st === "recording" || st === "processing" || st === "cancelled") {
          actions.clearTrain(player.id)
          setStarted(false)
        }
      }),
    [player.id],
  )

  const cancelled = status?.state === "cancelled"
  const error = started
    ? (requestError ?? (status?.state === "error" ? (status.message ?? "Training failed") : null))
    : null
  const showStart = !started || cancelled
  const secondsLeft = status?.seconds_left ?? SECONDS
  const progress = Math.min(100, Math.max(0, ((SECONDS - secondsLeft) / SECONDS) * 100))
  const level = Math.min(1, Math.max(0, status?.level ?? 0))

  return (
    <Dialog open={open} onOpenChange={close}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{showStart ? `Train ${player.name}'s voice` : `Training ${player.name}`}</DialogTitle>
          <DialogDescription>
            {showStart
              ? `Optional: ${SECONDS} seconds of ${player.name} talking helps Troubadour recognise them. Press Start when ready.`
              : `Only ${player.name} talks now — say anything, e.g. introduce your character.`}
          </DialogDescription>
        </DialogHeader>
        {showStart ? (
          serverError && (
            <p className="rounded-md bg-destructive/15 p-3 text-sm text-destructive">{serverError}</p>
          )
        ) : error ? (
          <p className="rounded-md bg-destructive/15 p-3 text-sm text-destructive">{error}</p>
        ) : status?.state === "processing" ? (
          <div className="flex items-center gap-2 py-4 text-muted-foreground">
            <Loader2 className="size-4 animate-spin" /> Processing…
          </div>
        ) : (
          <div className="space-y-4 py-2">
            <div className="flex items-center gap-4">
              <div className="relative flex size-14 items-center justify-center">
                <span
                  className="absolute inset-0 rounded-full bg-primary/25 transition-transform duration-100"
                  style={{ transform: `scale(${0.6 + level * 0.8})` }}
                />
                <Mic className="relative size-6" />
              </div>
              <div className="flex-1">
                <div className="text-3xl font-semibold tabular-nums">{Math.ceil(secondsLeft)}s</div>
                <div className="text-xs text-muted-foreground">{status ? "Listening…" : "Starting…"}</div>
              </div>
            </div>
            <Progress value={progress} />
          </div>
        )}
        <DialogFooter>
          {showStart && (
            <Button disabled={!!serverError} onClick={start}>
              <Mic /> Start
            </Button>
          )}
          {error && (
            <Button disabled={!!serverError} onClick={reset}>
              Try again
            </Button>
          )}
          <Button variant="outline" onClick={() => close(false)}>
            {error ? "Close" : "Cancel"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
