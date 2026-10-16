import { useEffect, useRef, useState } from "react"
import {
  METER_MIN_DB,
  SEGMENTS,
  ZONE_LABEL,
  ZONE_SEGMENT_CLASS,
  ZONE_TEXT_CLASS,
  currentLevel,
  litSegments,
  meterPosition,
  subscribeLevel,
} from "@/lib/audio"
import { cn } from "@/lib/utils"
import type { AudioLevel, AudioSource } from "@/lib/types"

const OFF: AudioLevel = { source: "session", db: null, peak_db: null, clipping: false, zone: "off" }

/**
 * Live input meter for one source. Level events (~10/s) land in a ref and are drawn at most once
 * per animation frame.
 */
export function LevelMeter({ source, className }: { source: AudioSource; className?: string }) {
  const [level, setLevel] = useState<AudioLevel>(() => currentLevel(source) ?? OFF)
  const pending = useRef<AudioLevel | null>(null)
  const frame = useRef(0)

  useEffect(() => {
    const unsubscribe = subscribeLevel(source, (l) => {
      pending.current = l
      if (frame.current) return
      frame.current = requestAnimationFrame(() => {
        frame.current = 0
        if (pending.current) setLevel(pending.current)
      })
    })
    return () => {
      unsubscribe()
      cancelAnimationFrame(frame.current)
      frame.current = 0
    }
  }, [source])

  return <MeterView level={level} className={className} />
}

/** The meter for one level reading (pure; `LevelMeter` feeds it live). */
export function MeterView({ level, className }: { level: AudioLevel; className?: string }) {
  const off = level.zone === "off" || level.db == null
  const lit = off ? 0 : litSegments(level.db)
  const label = ZONE_LABEL[level.zone]
  const peakPct = !off && level.peak_db != null ? meterPosition(level.peak_db) * 100 : null

  return (
    <span className={cn("inline-flex items-center gap-2", className)}>
      <span
        role="meter"
        aria-label="Microphone level"
        aria-valuemin={METER_MIN_DB}
        aria-valuemax={0}
        aria-valuenow={off ? METER_MIN_DB : Math.round(level.db!)}
        aria-valuetext={off ? "Off" : `${Math.round(level.db!)} dBFS${label ? `, ${label}` : ""}`}
        data-zone={level.zone}
        className={cn(
          "relative inline-flex h-3 items-center gap-0.5 rounded-full bg-muted/40 px-1",
          off && "opacity-50",
          level.zone === "clip" && "animate-clip-flash",
        )}
      >
        {SEGMENTS.map((s, i) => (
          <span
            key={i}
            data-segment={i}
            data-lit={i < lit}
            data-zone={s.zone}
            className={cn(
              "h-2 w-1.5 rounded-full transition-colors duration-100",
              i < lit ? ZONE_SEGMENT_CLASS[s.zone] : "bg-muted",
            )}
          />
        ))}
        {peakPct != null && (
          <span
            data-testid="peak"
            aria-hidden
            className="absolute top-0.5 bottom-0.5 w-px bg-foreground/80 transition-[left] duration-100"
            // Inside the 4px horizontal padding of the track.
            style={{ left: `calc(4px + (100% - 8px) * ${peakPct / 100})` }}
          />
        )}
      </span>
      {label && (
        <span
          data-testid="level-label"
          className={cn("text-xs font-medium whitespace-nowrap", ZONE_TEXT_CLASS[level.zone])}
        >
          {label}
        </span>
      )}
    </span>
  )
}
