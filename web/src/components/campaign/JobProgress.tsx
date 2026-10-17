import { AlertCircle, Loader2, X } from "lucide-react"
import { Progress } from "@/components/ui/progress"
import { jobCount } from "@/lib/campaign"
import { campaignActions } from "@/lib/campaignStore"
import type { CampaignJob } from "@/lib/types"

/** Compact header chip for a queued/running job, or a failed one (dismissible). */
export function JobChip({ job: j }: { job: CampaignJob }) {
  const label =
    j.kind === "writeup"
      ? `Write-up ${j.title}`
      : j.kind === "improve"
        ? `Improve ${j.title}`
        : j.kind === "spelling"
          ? `Spellings ${j.title}`
          : j.kind === "backup"
            ? "Cloud backup"
            : j.title || "Import"
  if (j.state === "error")
    return (
      <span
        role="alert"
        data-job={j.id}
        className="inline-flex max-w-80 items-center gap-1.5 rounded-full bg-destructive/15 py-0.5 pr-1 pl-2 text-xs text-destructive"
      >
        <AlertCircle className="size-3.5 shrink-0" />
        <span className="truncate">
          {label}: {j.message || "failed"}
        </span>
        <button
          type="button"
          aria-label="Dismiss"
          className="rounded-full p-0.5 hover:bg-destructive/20"
          onClick={() => campaignActions.dismissJob(j.id)}
        >
          <X className="size-3" />
        </button>
      </span>
    )
  return (
    <span
      data-job={j.id}
      className="inline-flex max-w-80 items-center gap-1.5 rounded-full bg-muted px-2 py-0.5 text-xs text-muted-foreground"
    >
      <Loader2 className="size-3.5 shrink-0 animate-spin" />
      <span className="truncate">
        {label}
        {j.state === "queued" ? " · queued" : ""} {jobCount(j)}
        {j.message ? ` · ${j.message}` : ""}
      </span>
    </span>
  )
}

/** Full-width progress for a job (used in the import dialog). */
export function JobBar({ job: j }: { job: CampaignJob }) {
  const pct = j.total > 0 ? (j.done / j.total) * 100 : null
  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2 text-sm text-muted-foreground">
        <Loader2 className="size-4 animate-spin" />
        <span className="flex-1 truncate">
          {j.state === "queued" ? "Waiting for the AI…" : j.message || "Reading…"}
        </span>
        <span className="tabular-nums">{jobCount(j)}</span>
      </div>
      <Progress value={pct} />
    </div>
  )
}
