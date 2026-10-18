import { useEffect, useState } from "react"
import { toast } from "sonner"
import { FileAudio, Mic, Play, TriangleAlert, X } from "lucide-react"
import { MicDetails } from "@/components/audio/MicDetails"
import { MicTest } from "@/components/audio/MicTest"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { api } from "@/lib/api"
import { mmss } from "@/lib/format"
import { actions, useStore } from "@/lib/store"
import { cn } from "@/lib/utils"
import type { MicCheck } from "@/lib/types"

const SPEEDS = [
  { value: "1", label: "1×" },
  { value: "1.5", label: "1.5×" },
  { value: "2", label: "2×" },
]
/** How often the idle Setup panel re-reads the inputs, so a mic plugged in or removed shows up. */
export const SOURCES_POLL_MS = 2000
/** The chosen mic outlives this panel: starting a session leaves Setup, and a reload starts it fresh. */
export const MIC_KEY = "troubadour.mic"
/** The pre-rename key; read once and moved to MIC_KEY. */
export const OLD_MIC_KEY = "bard.mic"

function readSavedMic(): string | null {
  const saved = localStorage.getItem(MIC_KEY)
  if (saved !== null) return saved
  const old = localStorage.getItem(OLD_MIC_KEY)
  if (old === null) return null
  localStorage.setItem(MIC_KEY, old)
  localStorage.removeItem(OLD_MIC_KEY)
  return old
}

export function StartPanel() {
  const sources = useStore((s) => s.sources)
  const status = useStore((s) => s.session?.status ?? "idle")
  const serverError = useStore((s) => s.session?.error ?? null)
  const [kind, setKind] = useState<"mic" | "file">("mic")
  const [mic, setMicState] = useState<string | null>(readSavedMic)
  const setMic = (name: string | null) => {
    setMicState(name)
    if (name) localStorage.setItem(MIC_KEY, name)
    else localStorage.removeItem(MIC_KEY)
  }
  const [file, setFile] = useState<string | null>(null)
  const [speed, setSpeed] = useState("1")
  const [busy, setBusy] = useState(false)
  /** A mic with red checks, waiting for "Start anyway". */
  const [warning, setWarning] = useState<{ mic: string; red: MicCheck[] } | null>(null)

  useEffect(() => {
    api.sources().then(actions.setSources, (e: Error) => toast.error(`Couldn't load sources: ${e.message}`))
  }, [])
  useEffect(() => {
    if (status !== "idle") return
    // Quiet on failure: the first load already reported it, and a restarting server would spam.
    const id = setInterval(() => void api.sources().then(actions.setSources, () => {}), SOURCES_POLL_MS)
    return () => clearInterval(id)
  }, [status])

  const mics = sources?.mics ?? []
  const files = sources?.files ?? []
  // A chosen mic that was unplugged falls back to the default, and comes back if it's plugged in again.
  const chosenMic = mics.some((m) => m.name === mic) ? mic : null
  const micValue = chosenMic ?? mics.find((m) => m.default)?.name ?? mics[0]?.name ?? null
  const fileValue = file ?? files[0]?.name ?? null

  /** Mic sessions check the chosen mic first: anything red in its checklist asks "start anyway?". */
  const requestStart = async () => {
    if (kind === "mic") {
      setBusy(true)
      const info = await api.mics().catch(() => null) // can't check: never block the game over it
      setBusy(false)
      const m =
        info?.find((x) => x.name === micValue) ??
        (micValue == null ? info?.find((x) => x.default) : undefined)
      const red = m?.checks.filter((c) => c.ok === false) ?? []
      if (m && red.length > 0) {
        setWarning({ mic: m.specs.model ?? m.name, red })
        return
      }
    }
    await start()
  }

  const start = async () => {
    setWarning(null)
    setBusy(true)
    try {
      await api.startSession(
        kind === "mic"
          ? { kind: "mic", device: micValue }
          : { kind: "file", name: fileValue ?? "", speed: Number(speed) },
      )
      actions.setView("session")
    } catch (e) {
      toast.error((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  if (status !== "idle") {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Session in progress</CardTitle>
          <CardDescription>The table is being transcribed right now.</CardDescription>
        </CardHeader>
        <CardContent>
          <Button className="w-full" onClick={() => actions.setView("session")}>
            Go to session
          </Button>
        </CardContent>
      </Card>
    )
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Start session</CardTitle>
        <CardDescription>Pick what Troubadour listens to. Voices can be named during play.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid grid-cols-2 gap-2">
          {(
            [
              ["mic", Mic, "Microphone"],
              ["file", FileAudio, "Sample file"],
            ] as const
          ).map(([k, Icon, label]) => (
            <button
              key={k}
              type="button"
              onClick={() => setKind(k)}
              className={cn(
                "flex flex-col items-center gap-1 rounded-lg border p-3 text-sm transition-colors",
                kind === k
                  ? "border-primary bg-primary/10"
                  : "border-border text-muted-foreground hover:bg-muted/50",
              )}
            >
              <Icon className="size-5" />
              {label}
            </button>
          ))}
        </div>

        {kind === "mic" ? (
          <div className="space-y-1.5">
            <Label>Microphone</Label>
            {mics.length === 0 ? (
              <p className="text-sm text-muted-foreground">
                No microphones found — the default input will be used.
              </p>
            ) : (
              <Select
                value={micValue}
                onValueChange={(v, { reason }) => {
                  // Base UI resets the value itself ("none") when the chosen mic is unplugged; keep
                  // the user's choice so it's picked again when the mic comes back.
                  if (reason !== "none") setMic(v as string | null)
                }}
                items={mics.map((m) => ({
                  value: m.name,
                  label: m.default ? `${m.name} (default)` : m.name,
                }))}
              >
                <SelectTrigger className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {mics.map((m) => (
                    <SelectItem key={m.name} value={m.name}>
                      {m.name}
                      {m.default && <span className="text-muted-foreground">(default)</span>}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            )}
            {sources && mic && micValue !== mic && (
              <p role="alert" className="text-sm text-amber-600 dark:text-amber-400">
                {mic} isn't connected. Using {micValue ?? "the default input"} instead.
              </p>
            )}
            <MicDetails device={micValue} />
            <MicTest device={micValue} />
          </div>
        ) : (
          <div className="grid grid-cols-[1fr_auto] gap-2">
            <div className="space-y-1.5">
              <Label>File</Label>
              {files.length === 0 ? (
                <p className="text-sm text-muted-foreground">No sample files on the server.</p>
              ) : (
                <Select
                  value={fileValue}
                  onValueChange={(v) => setFile(v as string | null)}
                  items={files.map((f) => ({ value: f.name, label: `${f.name} · ${mmss(f.duration)}` }))}
                >
                  <SelectTrigger className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {files.map((f) => (
                      <SelectItem key={f.name} value={f.name}>
                        {f.name}
                        <span className="text-muted-foreground tabular-nums">{mmss(f.duration)}</span>
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              )}
            </div>
            <div className="space-y-1.5">
              <Label>Speed</Label>
              <Select value={speed} onValueChange={(v) => v && setSpeed(v as string)} items={SPEEDS}>
                <SelectTrigger className="w-20">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {SPEEDS.map((s) => (
                    <SelectItem key={s.value} value={s.value}>
                      {s.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>
        )}

        <Button
          size="lg"
          className="w-full"
          disabled={busy || !!serverError || (kind === "file" && !fileValue)}
          onClick={requestStart}
        >
          <Play /> Start session
        </Button>
      </CardContent>
      <Dialog open={warning !== null} onOpenChange={(open) => !open && setWarning(null)}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <TriangleAlert className="size-4 text-amber-400" />
              {warning?.red.some((c) => c.id === "around")
                ? "Hey, you aren't using an omnidirectional mic"
                : "Hey, something in this mic's checklist is red"}
            </DialogTitle>
            <DialogDescription>
              {warning?.mic} may not hear the whole table well. Troubadour will still work, but quieter or
              far-away players get fewer words and more wrong names.
            </DialogDescription>
          </DialogHeader>
          <ul aria-label="Red checks" className="space-y-1.5">
            {warning?.red.map((c) => (
              <li key={c.id} className="flex items-start gap-2">
                <X className="mt-0.5 size-3.5 shrink-0 text-red-400" />
                <span>
                  {c.label}
                  {c.detail && <span className="block text-xs text-muted-foreground">{c.detail}</span>}
                </span>
              </li>
            ))}
          </ul>
          <DialogFooter>
            <Button variant="outline" onClick={() => setWarning(null)}>
              Pick another mic
            </Button>
            <Button onClick={start}>Start anyway</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </Card>
  )
}
