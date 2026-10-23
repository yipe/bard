import { useEffect, useMemo } from "react"
import { AlertCircle, BookOpenText, Loader2 } from "lucide-react"
import { Button } from "@/components/ui/button"
import { api } from "@/lib/api"
import { jobActive, jobCount } from "@/lib/campaign"
import { attempt, campaignActions, useCampaign } from "@/lib/campaignStore"
import { ObsidianLink } from "./ObsidianLink"

/** History viewer: summarize this saved session into a campaign session page plus suggestions. */
export function WriteUpButton({ sessionId }: { sessionId: string }) {
  const campaign = useCampaign((s) => s.campaign)
  const jobs = useCampaign((s) => s.jobs)

  useEffect(() => {
    if (!useCampaign.getState().campaign) void campaignActions.refresh()
  }, [])

  // The latest write-up job for this session (job ids are random, so insertion order decides).
  const job = useMemo(
    () =>
      Object.values(jobs)
        .filter((j) => j.kind === "writeup" && j.title === sessionId)
        .at(-1),
    [jobs, sessionId],
  )
  const running = !!job && jobActive(job)
  const page = campaign?.written_up[sessionId]

  return (
    <span className="flex flex-col items-start gap-3 text-sm">
      <Button
        size="sm"
        variant="outline"
        disabled={running}
        onClick={async () => {
          const j = await attempt(() => api.writeUp(sessionId))
          if (j) campaignActions.putJob(j)
        }}
      >
        {running ? <Loader2 className="animate-spin" /> : <BookOpenText />}
        {page ? "Write up again" : "Write up for campaign"}
        {running && <span className="text-muted-foreground tabular-nums">{jobCount(job)}</span>}
      </Button>
      {job?.state === "error" && (
        <span role="alert" className="inline-flex items-center gap-1 text-destructive">
          <AlertCircle className="size-3.5" /> {job.message || "Write-up failed"}
        </span>
      )}
      {page && <ObsidianLink relPath={page}>Session page</ObsidianLink>}
    </span>
  )
}
