import { useEffect, useState } from "react"
import { toast } from "sonner"
import { AudioLines, X } from "lucide-react"
import { Button } from "@/components/ui/button"
import { api } from "@/lib/api"
import { useAudio } from "@/lib/audio"
import { useStore } from "@/lib/store"
import { LevelMeter } from "./LevelMeter"
import { QualityRows } from "./QualityRows"

/**
 * Live mic session: the level meter and the latest quality read-out in a small box. Closing it
 * switches the server's monitor off (no level/quality work per frame) for every open tab, until
 * someone shows it again.
 */
export function LiveQualityBox() {
  const on = useStore((s) => s.session?.audio_monitor ?? true)
  const quality = useAudio((s) => s.quality.session)
  const [busy, setBusy] = useState(false)

  // Off means no new read-outs: drop the last one rather than show stale numbers when it's back on.
  useEffect(() => {
    if (!on) useAudio.setState((s) => ({ quality: { ...s.quality, session: undefined } }))
  }, [on])

  const setMonitor = async (next: boolean) => {
    setBusy(true)
    try {
      await api.setAudioMonitor(next)
    } catch (e) {
      toast.error((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  if (!on) {
    return (
      <Button
        variant="ghost"
        size="icon"
        disabled={busy}
        aria-label="Show audio quality"
        title="Show audio quality"
        onClick={() => setMonitor(true)}
      >
        <AudioLines />
      </Button>
    )
  }
  return (
    <section aria-label="Audio quality" className="flex min-h-0 flex-1 flex-col space-y-3">
      <div className="flex items-center gap-2 text-sm font-semibold">
        <AudioLines className="size-4" /> Audio quality
        <Button
          variant="ghost"
          size="icon-xs"
          className="ml-auto"
          aria-label="Hide audio quality"
          title="Hide and stop monitoring"
          disabled={busy}
          onClick={() => setMonitor(false)}
        >
          <X />
        </Button>
      </div>
      <LevelMeter source="session" />
      {quality ? (
        <>
          <QualityRows quality={quality} hints={quality.hints} />
          <p className="text-[11px] text-muted-foreground">Over the last {Math.round(quality.window_s)} s.</p>
        </>
      ) : (
        <p className="text-sm text-muted-foreground">
          Listening… the first read-out comes after a few seconds.
        </p>
      )}
    </section>
  )
}
