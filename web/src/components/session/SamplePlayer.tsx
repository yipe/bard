import { useEffect, useRef, useState } from "react"
import { Pause, Play } from "lucide-react"
import { Button } from "@/components/ui/button"

/** `url` is opaque (carries a cache-busting query); a new url remounts so playback state resets. */
export function SamplePlayer({ url }: { url: string }) {
  return <SampleButton key={url} url={url} />
}

function SampleButton({ url }: { url: string }) {
  const ref = useRef<HTMLAudioElement>(null)
  const [playing, setPlaying] = useState(false)
  useEffect(() => () => ref.current?.pause(), [])
  return (
    <>
      <audio
        ref={ref}
        src={url}
        preload="none"
        onPlay={() => setPlaying(true)}
        onPause={() => setPlaying(false)}
        onEnded={() => setPlaying(false)}
      />
      <Button
        size="icon-sm"
        variant="ghost"
        aria-label={playing ? "Pause sample" : "Play sample"}
        onClick={() => {
          const a = ref.current
          if (!a) return
          if (a.paused) {
            a.currentTime = 0
            void a.play()
          } else a.pause()
        }}
      >
        {playing ? <Pause /> : <Play />}
      </Button>
    </>
  )
}
