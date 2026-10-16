import { useEffect, useRef, useState } from "react"
import { Volume2, VolumeX } from "lucide-react"
import { Button } from "@/components/ui/button"
import { useStore } from "@/lib/store"
import { sessionTimeNow } from "@/lib/useSessionTime"

/** Plays the sample file in sync with the session (file sources only). */
export function SessionAudio() {
  const session = useStore((s) => s.session)
  const ref = useRef<HTMLAudioElement>(null)
  const [muted, setMuted] = useState(false)
  const [blocked, setBlocked] = useState(false)

  const source = session?.source
  const file = source?.kind === "file" ? source : null
  const url = file ? `/api/files/${encodeURIComponent(file.name)}` : undefined
  const running = session?.status === "running"

  // Re-sync on every session update; small drift is tolerated to avoid audible jumps.
  useEffect(() => {
    const a = ref.current
    if (!a || !file) return
    if (!running) {
      a.pause()
      return
    }
    a.playbackRate = file.speed
    const t = sessionTimeNow()
    if (Math.abs(a.currentTime - t) > 1) a.currentTime = t
    if (a.paused)
      a.play().then(
        () => setBlocked(false),
        () => setBlocked(true),
      )
  }, [session, file, running, url])

  if (!file || !url) return null

  return (
    <div className="flex items-center gap-1">
      <audio ref={ref} src={url} muted={muted} preload="auto" />
      {blocked && running ? (
        <Button
          size="sm"
          variant="outline"
          onClick={() => {
            const a = ref.current
            if (!a) return
            a.currentTime = sessionTimeNow()
            a.play().then(
              () => setBlocked(false),
              () => setBlocked(true),
            )
          }}
        >
          <Volume2 /> Play audio
        </Button>
      ) : (
        <Button
          size="icon-sm"
          variant="ghost"
          aria-label={muted ? "Unmute" : "Mute"}
          onClick={() => setMuted((m) => !m)}
        >
          {muted ? <VolumeX /> : <Volume2 />}
        </Button>
      )}
    </div>
  )
}
