/** "just now", "5 min ago", "3 h ago", then the date ("Oct 15"): how long since something happened. */
export function ago(epochS: number, nowMs: number): string {
  const seconds = Math.max(0, nowMs / 1000 - epochS)
  if (seconds < 60) return "just now"
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} h ago`
  return new Date(epochS * 1000).toLocaleDateString(undefined, { month: "short", day: "numeric" })
}

/** The full local date and time, for tooltips and details. */
export function when(epochS: number): string {
  return new Date(epochS * 1000).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })
}
