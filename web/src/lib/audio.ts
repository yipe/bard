import { create } from "zustand"
import type { AudioLevel, AudioQuality, AudioSource, Grade, LevelZone, QualityNumbers } from "./types"

/* ---- Meter scale and zones (mirror backend levels.ZONES) ---- */

export const METER_MIN_DB = -70

/** Upper bound (exclusive) of each zone in dBFS; above the last one is "clip". */
const ZONE_TOPS: [number, LevelZone][] = [
  [-50, "silent"],
  [-30, "quiet"],
  [-12, "good"],
  [-3, "hot"],
]

export function zoneOf(db: number): LevelZone {
  return ZONE_TOPS.find(([top]) => db < top)?.[1] ?? "clip"
}

/**
 * Segment edges on the −70…0 dBFS scale. Finer where it matters (around the good zone) so every
 * zone gets at least one segment: silent 2, quiet 3, good 3, hot 2, clip 1.
 */
const EDGES = [-70, -60, -50, -44, -37, -30, -24, -18, -12, -8, -3, 0]

/** Each segment's [from, to) dB range and the zone it sits in. */
export const SEGMENTS = EDGES.slice(0, -1).map((from, i) => {
  const to = EDGES[i + 1]
  return { from, to, zone: zoneOf((from + to) / 2) }
})

/** How many segments `db` lights (a segment lights once the level reaches its middle). */
export function litSegments(db: number | null): number {
  if (db == null) return 0
  return SEGMENTS.filter((s) => db >= (s.from + s.to) / 2).length
}

/** Where `db` sits along the meter, 0…1 (piecewise, matching the segments). */
export function meterPosition(db: number): number {
  if (db <= METER_MIN_DB) return 0
  const i = SEGMENTS.findIndex((s) => db < s.to)
  if (i < 0) return 1
  const s = SEGMENTS[i]
  return (i + (db - s.from) / (s.to - s.from)) / SEGMENTS.length
}

export const ZONE_LABEL: Record<LevelZone, string> = {
  off: "",
  silent: "",
  quiet: "Too quiet",
  good: "Good",
  hot: "Too loud",
  clip: "Clipping!",
}

/** Lit segment colour by the zone the segment sits in (not the current level's zone). */
export const ZONE_SEGMENT_CLASS: Record<LevelZone, string> = {
  off: "bg-muted",
  silent: "bg-zinc-500/70",
  quiet: "bg-slate-300/80", // light grey-blue: too cool
  good: "bg-emerald-500",
  hot: "bg-amber-400",
  clip: "bg-red-500",
}

export const ZONE_TEXT_CLASS: Record<LevelZone, string> = {
  off: "text-muted-foreground",
  silent: "text-muted-foreground",
  quiet: "text-slate-300",
  good: "text-emerald-400",
  hot: "text-amber-400",
  clip: "text-red-400",
}

export const GRADE_DOT: Record<Grade, string> = {
  good: "bg-emerald-500",
  ok: "bg-amber-400",
  poor: "bg-red-500",
}

/** The five quality rows: label, value formatter, grade key. */
export const QUALITY_ROWS: {
  key: keyof QualityNumbers["grades"]
  label: string
  value: (q: QualityNumbers) => string
}[] = [
  { key: "speech", label: "Speech level", value: (q) => `${q.speech_db.toFixed(0)} dBFS` },
  { key: "noise", label: "Background noise", value: (q) => `${q.noise_db.toFixed(0)} dBFS` },
  { key: "snr", label: "Speech above noise", value: (q) => `${q.snr_db.toFixed(0)} dB` },
  { key: "rumble", label: "Low rumble", value: (q) => `${q.rumble_pct.toFixed(0)}%` },
  {
    key: "clipping",
    label: "Clipping",
    value: (q) => `${q.clipped_pct < 1 ? q.clipped_pct.toFixed(2) : q.clipped_pct.toFixed(1)}%`,
  },
]

/* ---- Live levels: a tiny pub/sub, so ~10 events/s never touch React state directly ---- */

type LevelListener = (level: AudioLevel) => void
const levelListeners: Record<AudioSource, Set<LevelListener>> = { session: new Set(), test: new Set() }
const latestLevel: Partial<Record<AudioSource, AudioLevel>> = {}

export function publishLevel(level: AudioLevel) {
  latestLevel[level.source] = level
  for (const listener of levelListeners[level.source]) listener(level)
}

/** Subscribe to one source's levels; returns the unsubscribe. */
export function subscribeLevel(source: AudioSource, listener: LevelListener): () => void {
  levelListeners[source].add(listener)
  return () => levelListeners[source].delete(listener)
}

export function currentLevel(source: AudioSource): AudioLevel | undefined {
  return latestLevel[source]
}

/* ---- Quality read-outs and the mic test (low rate: plain store) ---- */

interface AudioState {
  quality: Partial<Record<AudioSource, AudioQuality>>
  micTest: "running" | "stopped"
}

export const useAudio = create<AudioState>(() => ({ quality: {}, micTest: "stopped" }))

export function applyAudioEvent(
  ev:
    | { type: "audio.level"; data: AudioLevel }
    | { type: "audio.quality"; data: AudioQuality }
    | { type: "mic.test"; data: { state: "running" | "stopped" } },
) {
  switch (ev.type) {
    case "audio.level":
      return publishLevel(ev.data)
    case "audio.quality":
      return useAudio.setState((s) => ({ quality: { ...s.quality, [ev.data.source]: ev.data } }))
    case "mic.test":
      return useAudio.setState((s) =>
        // A new test starts from a clean read-out; a stopped one keeps its last numbers on screen.
        ev.data.state === "running"
          ? { micTest: "running", quality: { ...s.quality, test: undefined } }
          : { micTest: "stopped" },
      )
  }
}

/** Test seam: forget levels and listeners' latest values. */
export function resetAudio() {
  delete latestLevel.session
  delete latestLevel.test
  useAudio.setState({ quality: {}, micTest: "stopped" })
}
