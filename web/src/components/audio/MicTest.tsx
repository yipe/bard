import { useEffect, useState } from "react"
import { Mic, Square } from "lucide-react"
import { Button } from "@/components/ui/button"
import { api } from "@/lib/api"
import { useAudio } from "@/lib/audio"
import { attempt } from "@/lib/campaignStore"
import { LevelMeter } from "./LevelMeter"
import { QualityRows } from "./QualityRows"

/**
 * Setup: open the mic for a while without a session and watch the meter and quality read-out live,
 * to tune gain, placement and filters before playing. Nothing is recorded or transcribed.
 */
export function MicTest({ device }: { device: string | null }) {
  const running = useAudio((s) => s.micTest === "running")
  const quality = useAudio((s) => s.quality.test)
  const [endsAt, setEndsAt] = useState<number | null>(null)
  const [now, setNow] = useState(() => Date.now())
  const [busy, setBusy] = useState(false)
  const [tested, setTested] = useState(false)

  useEffect(() => {
    if (!running) return
    const id = setInterval(() => setNow(Date.now()), 250)
    return () => clearInterval(id)
  }, [running])

  // Don't leave the mic open when Setup goes away (e.g. a session starts).
  useEffect(
    () => () => {
      if (useAudio.getState().micTest === "running") void api.stopMicTest().catch(() => {})
    },
    [],
  )

  const start = async () => {
    setBusy(true)
    const res = await attempt(() => api.startMicTest(device))
    setBusy(false)
    if (!res) return
    setTested(true)
    setEndsAt(Date.now() + res.seconds * 1000)
    setNow(Date.now())
    useAudio.setState((s) => ({ micTest: "running", quality: { ...s.quality, test: undefined } }))
  }

  const stop = async () => {
    setBusy(true)
    const ok = await attempt(() => api.stopMicTest().then(() => true))
    setBusy(false)
    if (ok) useAudio.setState({ micTest: "stopped" })
  }

  const left = endsAt == null ? null : Math.max(0, Math.ceil((endsAt - now) / 1000))

  return (
    <div className="space-y-3 rounded-lg border p-3" data-testid="mic-test">
      <div className="flex items-center gap-2">
        {running ? (
          <Button size="sm" variant="outline" disabled={busy} onClick={stop}>
            <Square /> Stop
          </Button>
        ) : (
          <Button size="sm" variant="outline" disabled={busy} onClick={start}>
            <Mic /> Test mic
          </Button>
        )}
        {running && left != null && (
          <span className="text-xs text-muted-foreground tabular-nums" data-testid="mic-test-countdown">
            {left}s
          </span>
        )}
        {(running || tested) && <LevelMeter source="test" className="ml-auto" />}
      </div>
      {!running && !tested && (
        <p className="text-xs text-muted-foreground">
          Opens the mic for 20 seconds without recording, so you can tune its gain and placement.
        </p>
      )}
      {running && !quality && (
        <p className="text-xs text-muted-foreground">
          Talk normally from where people will sit. The read-out comes after a few seconds.
        </p>
      )}
      {quality && (
        <div className="space-y-1">
          {!running && <p className="text-[11px] text-muted-foreground">Last test</p>}
          <QualityRows quality={quality} hints={quality.hints} />
        </div>
      )}
    </div>
  )
}
