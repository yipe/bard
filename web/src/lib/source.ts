import { dateTime } from "./format"
import type { Source } from "./types"

/**
 * Session ids are `YYYY-MM-DD_HHMMSS-xxxx` in the server's local time (same machine as the browser
 * for this local demo). Returns epoch seconds, or null if the id doesn't follow that shape.
 */
export function sessionIdStartedAt(id: string): number | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})_(\d{2})(\d{2})(\d{2})(?:-|$)/.exec(id)
  if (!m) return null
  const [, y, mo, d, h, mi, s] = m.map(Number)
  const date = new Date(y, mo - 1, d, h, mi, s)
  return Number.isNaN(date.getTime()) ? null : date.getTime() / 1000
}

/** Human label for a session source, e.g. "table.wav · 2×" or "Replay of Oct 8, 2026, 3:30 PM · 1×". */
export function sourceLabel(source: Source | null): string {
  if (source == null) return "No source"
  switch (source.kind) {
    case "mic":
      return source.device ?? "Default microphone"
    case "file":
      return source.speed === 1 ? source.name : `${source.name} · ${source.speed}×`
    case "session": {
      const startedAt = sessionIdStartedAt(source.id)
      const when = startedAt == null ? source.id : dateTime(startedAt)
      return `Replay of ${when} · ${source.speed}×`
    }
  }
}
