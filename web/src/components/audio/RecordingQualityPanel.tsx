import { useEffect, useState } from "react"
import { CircleCheck, HelpCircle, Loader2, Mic, SlidersHorizontal } from "lucide-react"
import { api } from "@/lib/api"
import { cn } from "@/lib/utils"
import type { RecordingQuality } from "@/lib/types"
import { QualityRows } from "./QualityRows"
import { VERDICT } from "./qualityVerdict"

const VERDICT_ICON = { good: CircleCheck, settings: SlidersHorizontal, microphone: Mic, unknown: HelpCircle }

type State =
  { status: "loading" } | { status: "error"; message: string } | { status: "ready"; q: RecordingQuality }

/** History: how good a saved recording is, and whether to change mic settings or the mic. */
export function RecordingQualityPanel({ sessionId }: { sessionId: string }) {
  const [state, setState] = useState<State>({ status: "loading" })

  useEffect(() => {
    let alive = true
    api.sessionQuality(sessionId).then(
      (q) => alive && setState({ status: "ready", q }),
      (e: Error) => alive && setState({ status: "error", message: e.message }),
    )
    return () => {
      alive = false
    }
  }, [sessionId])

  if (state.status === "loading")
    return (
      <p className="flex items-center gap-2 pt-3 text-sm text-muted-foreground">
        <Loader2 className="size-4 animate-spin" /> Measuring the recording… (a few seconds the first time)
      </p>
    )
  if (state.status === "error")
    return (
      <p role="alert" className="pt-3 text-sm text-destructive">
        Couldn't measure this recording: {state.message}
      </p>
    )

  const { q } = state
  const verdict = VERDICT[q.verdict.kind]
  const Icon = VERDICT_ICON[q.verdict.kind]
  return (
    <div className="space-y-4 pt-3" data-testid="recording-quality">
      <div role="status" className={cn("space-y-1 rounded-lg p-3 ring-1", verdict.banner)}>
        <p className="flex items-center gap-2 font-medium">
          <Icon className="size-4 shrink-0" /> {verdict.title}
        </p>
        {q.verdict.reasons.length > 0 && (
          <ul className="list-disc space-y-0.5 pl-6 text-xs">
            {q.verdict.reasons.map((r) => (
              <li key={r}>{r}</li>
            ))}
          </ul>
        )}
        {q.mic && <p className="text-xs opacity-80">Mic: {q.mic}</p>}
      </div>

      <QualityRows quality={q.overall} />

      {q.timeline.length > 0 && <Timeline points={q.timeline} />}

      {q.speakers.length > 0 && <SpeakerLevels speakers={q.speakers} spread={q.spread_db} />}
    </div>
  )
}

const W = 280
const H = 80
const STRIP = 12
const DB_MIN = -80

/** Speech and noise per minute as two lines with the gap (SNR) shaded, and rumble as a strip below. */
function Timeline({ points }: { points: RecordingQuality["timeline"] }) {
  const n = points.length
  const x = (i: number) => (n === 1 ? W / 2 : (i / (n - 1)) * W)
  const y = (db: number) => (Math.min(0, Math.max(DB_MIN, db)) / DB_MIN) * H
  const line = (key: "speech_db" | "noise_db") => points.map((p, i) => `${x(i)},${y(p[key])}`).join(" ")
  const band = [
    ...points.map((p, i) => `${x(i)},${y(p.speech_db)}`),
    ...points.map((p, i) => `${x(i)},${y(p.noise_db)}`).reverse(),
  ].join(" ")
  const minutes = Math.round(points[n - 1].t / 60)
  return (
    <figure className="space-y-1">
      <figcaption className="flex items-center gap-3 text-xs text-muted-foreground">
        <span className="flex items-center gap-1">
          <span className="h-0.5 w-3 rounded bg-emerald-400" /> Speech
        </span>
        <span className="flex items-center gap-1">
          <span className="h-0.5 w-3 rounded bg-slate-400" /> Noise
        </span>
        <span className="ml-auto">per minute</span>
      </figcaption>
      <svg
        role="img"
        aria-label="Speech and noise level per minute, with low rumble underneath"
        viewBox={`0 0 ${W} ${H + 4 + STRIP}`}
        className="w-full overflow-visible"
        preserveAspectRatio="none"
      >
        {[-20, -40, -60].map((db) => (
          <line
            key={db}
            x1={0}
            x2={W}
            y1={y(db)}
            y2={y(db)}
            className="stroke-border"
            strokeDasharray="2 3"
          />
        ))}
        <polygon points={band} className="fill-emerald-500/10" />
        <polyline points={line("noise_db")} fill="none" className="stroke-slate-400" strokeWidth={1.5} />
        <polyline points={line("speech_db")} fill="none" className="stroke-emerald-400" strokeWidth={1.5} />
        {points.map((p, i) => {
          const h = (Math.min(100, Math.max(0, p.rumble_pct)) / 100) * STRIP
          return (
            <rect
              key={i}
              data-rumble={i}
              x={n === 1 ? 0 : (i / n) * W}
              width={Math.max(1, W / n - 1)}
              y={H + 4 + STRIP - h}
              height={h}
              className={
                p.rumble_pct >= 50
                  ? "fill-red-500"
                  : p.rumble_pct >= 20
                    ? "fill-amber-400"
                    : "fill-muted-foreground/40"
              }
            >
              <title>{`Minute ${Math.round(p.t / 60) + 1}: ${p.rumble_pct.toFixed(0)}% rumble`}</title>
            </rect>
          )
        })}
      </svg>
      <div className="flex justify-between text-[10px] text-muted-foreground tabular-nums">
        <span>0 min</span>
        <span>Low rumble</span>
        <span>{minutes} min</span>
      </div>
    </figure>
  )
}

/** How loud each named player comes through; a big spread means some voices are hard to hear. */
function SpeakerLevels({ speakers, spread }: { speakers: RecordingQuality["speakers"]; spread: number }) {
  const width = (db: number) => Math.min(100, Math.max(4, ((db - -60) / 60) * 100))
  const loudest = speakers[0]
  const quietest = speakers[speakers.length - 1]
  return (
    <section className="space-y-1.5">
      <h4 className="text-xs font-medium text-muted-foreground">Loudness by player</h4>
      <ul aria-label="Loudness by player" className="space-y-1">
        {speakers.map((s) => (
          <li
            key={s.speaker}
            data-speaker={s.speaker}
            className="grid grid-cols-[5rem_1fr_3.5rem] items-center gap-2 text-xs"
          >
            <span className="truncate">{s.speaker}</span>
            <span className="h-2 rounded-full bg-muted">
              <span
                className="block h-2 rounded-full bg-primary/70"
                style={{ width: `${width(s.level_db)}%` }}
              />
            </span>
            <span className="text-right font-mono tabular-nums">{s.level_db.toFixed(0)} dB</span>
          </li>
        ))}
      </ul>
      {speakers.length > 1 && Math.round(spread) >= 1 && (
        <p className="text-xs text-muted-foreground">
          {quietest.speaker} comes through {Math.round(spread)} dB quieter than {loudest.speaker}.
        </p>
      )}
    </section>
  )
}
