import type { QualityVerdict } from "@/lib/types"

/** Display for a recording's verdict (History list badge and the quality panel banner). */
export const VERDICT: Record<
  QualityVerdict | "unknown",
  { title: string; short: string; dot: string; banner: string }
> = {
  good: {
    title: "This mic setup works",
    short: "Audio: good",
    dot: "bg-emerald-500",
    banner: "bg-emerald-500/10 text-emerald-200 ring-emerald-500/30",
  },
  settings: {
    title: "Change mic settings",
    short: "Audio: change mic settings",
    dot: "bg-amber-400",
    banner: "bg-amber-500/10 text-amber-200 ring-amber-500/30",
  },
  microphone: {
    title: "Consider a different microphone",
    short: "Audio: consider a different mic",
    dot: "bg-red-500",
    banner: "bg-red-500/10 text-red-200 ring-red-500/30",
  },
  unknown: {
    title: "Not enough speech to judge this recording",
    short: "Audio: not measured",
    dot: "bg-muted-foreground",
    banner: "bg-muted text-muted-foreground ring-border",
  },
}
