import { useEffect, useMemo, useRef, useState, type ReactElement } from "react"
import { AlertCircle, AudioLines, Loader2, SpellCheck, Undo2, Wand2 } from "lucide-react"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { api } from "@/lib/api"
import { jobActive, jobCount } from "@/lib/campaign"
import { attempt, campaignActions, useCampaign } from "@/lib/campaignStore"
import type { CampaignJob, ImprovedInfo, RetranscribedInfo, SavedSession, SpellingInfo } from "@/lib/types"

type ToolKind = "improve" | "spelling" | "retranscribe"

const TOOLS: Record<
  ToolKind,
  {
    label: string
    tip: string
    icon: ReactElement
    done: string
    failed: string
    start: (id: string) => Promise<CampaignJob>
  }
> = {
  improve: {
    label: "Improve transcript",
    tip: "Re-transcribe with what the campaign knows now (names, spellings). Takes a few minutes; the original is kept.",
    icon: <Wand2 />,
    done: "Transcript improved",
    failed: "Improving failed",
    start: api.improveSession,
  },
  spelling: {
    label: "Fix spellings",
    tip: "Fix names and D&D terms with the glossary and the local AI. Quick; the original is kept.",
    icon: <SpellCheck />,
    done: "Spellings fixed",
    failed: "Fixing spellings failed",
    start: api.fixSpellings,
  },
  retranscribe: {
    label: "Retranscribe",
    tip: "Re-assign who spoke each line from the recording. The words stay. A copy of the transcript is saved first. Lines you corrected by hand can move.",
    icon: <AudioLines />,
    done: "Speakers reassigned",
    failed: "Retranscribe failed",
    start: api.retranscribeSession,
  },
}

/**
 * History viewer tools that rewrite a saved transcript as background jobs (`campaign.job` events):
 * Improve transcript (re-listen; needs audio) and Fix spellings (text only). When this viewer sees one
 * of its jobs finish, the record reloads. Restore original undoes both.
 */
export function TranscriptTools({
  summary,
  improved,
  spelling,
  retranscribed,
  onReload,
}: {
  summary: SavedSession
  /** From the loaded record; null when not improved (or not loaded yet). */
  improved: ImprovedInfo | null
  /** From the loaded record; null when spellings were never fixed. */
  spelling: SpellingInfo | null
  /** From the loaded record; null when speakers were never reassigned. */
  retranscribed: RetranscribedInfo | null
  /** Reload the record (and the session summary) after the transcript changed. */
  onReload: () => void
}) {
  const jobs = useCampaign((s) => s.jobs)
  const [confirmRestore, setConfirmRestore] = useState(false)
  const [confirmRetranscribe, setConfirmRetranscribe] = useState(false)
  const [busy, setBusy] = useState(false)

  // The latest job of each kind for this session (insertion order; job ids are random).
  const latest = useMemo(() => {
    const mine = Object.values(jobs).filter((j) => j.title === summary.id)
    return {
      improve: mine.filter((j) => j.kind === "improve").at(-1),
      spelling: mine.filter((j) => j.kind === "spelling").at(-1),
      retranscribe: mine.filter((j) => j.kind === "retranscribe").at(-1),
    }
  }, [jobs, summary.id])
  const running = (["improve", "spelling", "retranscribe"] as const).filter((k) => latest[k] && jobActive(latest[k]))
  const anyRunning = running.length > 0

  // Only jobs seen running here count as "just finished" (not a done job from an earlier visit).
  const watched = useRef(new Set<string>())
  useEffect(() => {
    for (const kind of ["improve", "spelling", "retranscribe"] as const) {
      const job = latest[kind]
      if (!job) continue
      if (jobActive(job)) watched.current.add(job.id)
      else if (job.state === "done" && watched.current.delete(job.id)) {
        toast.success(job.message || TOOLS[kind].done)
        onReload()
      }
    }
  }, [latest, onReload])

  const start = async (kind: ToolKind) => {
    setBusy(true)
    const j = await attempt(() => TOOLS[kind].start(summary.id))
    setBusy(false)
    if (!j) return
    watched.current.add(j.id)
    campaignActions.putJob(j)
  }

  const restore = async () => {
    setConfirmRestore(false)
    setBusy(true)
    const ok = await attempt(() => api.restoreSession(summary.id).then(() => true))
    setBusy(false)
    if (!ok) return
    toast.success("Original transcript restored")
    onReload()
  }

  const button = (kind: ToolKind) => {
    const job = latest[kind]
    const isRunning = !!job && jobActive(job)
    return (
      <Tooltip key={kind}>
        <TooltipTrigger
          render={
            // One rewrite at a time per session: both buttons wait while either runs.
            <Button
              size="sm"
              variant="outline"
              disabled={anyRunning || busy}
              onClick={() => (kind === "retranscribe" ? setConfirmRetranscribe(true) : start(kind))}
            >
              {isRunning ? <Loader2 className="animate-spin" /> : TOOLS[kind].icon}
              {TOOLS[kind].label}
              {isRunning && <span className="text-muted-foreground tabular-nums">{jobCount(job)}</span>}
            </Button>
          }
        />
        <TooltipContent className="max-w-64">{TOOLS[kind].tip}</TooltipContent>
      </Tooltip>
    )
  }

  return (
    <span className="flex flex-wrap items-center gap-2 text-xs">
      {summary.replayable && button("improve")}
      {summary.audio && button("retranscribe")}
      {button("spelling")}
      {confirmRetranscribe && (
        <span className="inline-flex flex-wrap items-center gap-1.5 text-muted-foreground">
          A copy of this transcript is saved first. Speakers you fixed by hand can still move. The words stay.
          <Button
            size="xs"
            variant="destructive"
            disabled={busy}
            onClick={() => {
              setConfirmRetranscribe(false)
              void start("retranscribe")
            }}
          >
            Retranscribe anyway
          </Button>
          <Button size="xs" variant="ghost" onClick={() => setConfirmRetranscribe(false)}>
            Cancel
          </Button>
        </span>
      )}
      {running.map((kind) =>
        latest[kind]?.message ? (
          <span key={kind} data-testid={`${kind}-status`} className="text-muted-foreground">
            {latest[kind].message}
          </span>
        ) : null,
      )}
      {(["improve", "spelling", "retranscribe"] as const).map((kind) =>
        latest[kind]?.state === "error" ? (
          <span key={kind} role="alert" className="inline-flex items-center gap-1 text-destructive">
            <AlertCircle className="size-3.5" /> {latest[kind].message || TOOLS[kind].failed}
          </span>
        ) : null,
      )}
      {(improved || spelling || retranscribed) && !anyRunning && (
        <span className="inline-flex items-center gap-1.5 text-muted-foreground">
          {improved && (
            <span>
              Improved · {improved.changed} {improved.changed === 1 ? "line" : "lines"} changed
            </span>
          )}
          {improved && spelling && <span aria-hidden>·</span>}
          {spelling && <span>Spellings fixed · {spelling.fixed}</span>}
          {retranscribed && (
            <span>
              {(improved || spelling) && <span aria-hidden> · </span>}
              Retranscribed · {retranscribed.changed} of {retranscribed.lines} reassigned
              {retranscribed.heard_next > 0 && ` · ${retranscribed.heard_next} were hearing the next person`}
              {retranscribed.rematch > 0 && ` · ${retranscribed.rematch} match someone else on their own audio`}
              {` · previous copy: ${retranscribed.backup}`}
            </span>
          )}
          {confirmRestore ? (
            <>
              <Button size="xs" variant="destructive" disabled={busy} onClick={restore}>
                Confirm restore
              </Button>
              <Button size="xs" variant="ghost" onClick={() => setConfirmRestore(false)}>
                Cancel
              </Button>
            </>
          ) : (
            <Button size="xs" variant="ghost" disabled={busy} onClick={() => setConfirmRestore(true)}>
              <Undo2 /> Restore original
            </Button>
          )}
        </span>
      )}
    </span>
  )
}
