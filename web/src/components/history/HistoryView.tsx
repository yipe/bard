import { useState } from "react"
import { toast } from "sonner"
import { History } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { api } from "@/lib/api"
import { dateTime } from "@/lib/format"
import { actions, useStore } from "@/lib/store"
import type { SavedSession } from "@/lib/types"
import { useSavedSessions } from "@/lib/useSavedSessions"
import { SavedSessionRow } from "./SavedSessionRow"
import { SessionViewer } from "./SessionViewer"

/** A later session that began within this long of an earlier one ending is the same sitting. */
const SAME_SITTING_S = 10 * 60

function sameSitting(earlier: SavedSession, later: SavedSession): boolean {
  const gap = later.started_at - (earlier.started_at + earlier.duration_s)
  return gap >= -1 && gap <= SAME_SITTING_S
}

/** History tab: every saved session, or the viewer for the one that's open. */
export function HistoryView() {
  const viewing = useStore((s) => s.viewing)
  if (viewing) return <SessionViewer key={viewing.id} summary={viewing} onBack={actions.closeSavedSession} />
  return (
    <div className="flex-1 overflow-y-auto">
      <HistoryList />
    </div>
  )
}

/** Chains of sessions that continue each other, and how many gaps were the right length but only one side has a recording. */
function matchingChains(sessions: SavedSession[]): { chains: SavedSession[][]; skipped: number } {
  const ordered = [...sessions].sort((a, b) => a.started_at - b.started_at)
  const chains: SavedSession[][] = []
  let chain: SavedSession[] = []
  let skipped = 0
  for (const session of ordered) {
    const prev = chain[chain.length - 1]
    if (prev && sameSitting(prev, session) && prev.audio === session.audio) {
      chain.push(session)
      continue
    }
    if (prev && sameSitting(prev, session)) skipped += 1
    if (chain.length > 1) chains.push(chain)
    chain = [session]
  }
  if (chain.length > 1) chains.push(chain)
  return { chains, skipped }
}

function HistoryList() {
  const { sessions, error, reload } = useSavedSessions()
  const [picked, setPicked] = useState<string[]>([])
  const [merging, setMerging] = useState(false)
  const [progress, setProgress] = useState<{ done: number; total: number; detail: string } | null>(null)
  const selected = (sessions ?? []).filter((s) => picked.includes(s.id))
  const pair = selected.length === 2 ? ([...selected].sort((a, b) => a.started_at - b.started_at) as [SavedSession, SavedSession]) : null
  const ordered = [...(sessions ?? [])].sort((a, b) => a.started_at - b.started_at)
  const suggestAt = ordered.findIndex((s, n) => n > 0 && sameSitting(ordered[n - 1], s))
  const suggestPair = suggestAt > 0 ? ([ordered[suggestAt - 1], ordered[suggestAt]] as const) : null
  const plan = matchingChains(sessions ?? [])
  const matchCount = plan.chains.reduce((n, chain) => n + chain.length - 1, 0)

  const select = (id: string, on: boolean) => {
    setPicked((cur) => {
      if (!on) return cur.filter((x) => x !== id)
      if (cur.includes(id)) return cur
      return [...cur, id].slice(-2)
    })
  }

  const merge = async (ids: [string, string]) => {
    setMerging(true)
    try {
      await api.mergeSessions(ids)
      setPicked([])
      reload()
      toast.success("Sessions merged. The later one now follows the earlier recording.")
    } catch (e) {
      toast.error((e as Error).message)
    } finally {
      setMerging(false)
    }
  }

  const mergeMatching = async () => {
    const { chains, skipped } = matchingChains(sessions ?? [])
    const steps = chains.reduce((n, chain) => n + chain.length - 1, 0)
    if (steps === 0) {
      toast.message(
        skipped
          ? `${skipped} pair${skipped === 1 ? "" : "s"} start within 10 minutes, but only one side has a recording, so they were left alone.`
          : "No saved sessions start within 10 minutes of another ending.",
      )
      return
    }
    setMerging(true)
    setPicked([])
    let done = 0
    try {
      for (const chain of chains) {
        for (let i = 1; i < chain.length; i++) {
          const earlier = chain[0]
          const later = chain[i]
          setProgress({
            done,
            total: steps,
            detail: `${dateTime(later.started_at)} onto ${dateTime(earlier.started_at)}`,
          })
          await api.mergeSessions([earlier.id, later.id])
          done += 1
          setProgress({ done, total: steps, detail: `${dateTime(later.started_at)} onto ${dateTime(earlier.started_at)}` })
          reload()
        }
      }
      toast.success(
        `Merged ${done} session${done === 1 ? "" : "s"} into the earlier recording${chains.length === 1 ? "" : "s"}.` +
          (skipped ? ` Left ${skipped} apart because only one has a recording.` : ""),
      )
    } catch (e) {
      toast.error(
        done
          ? `Merged ${done} of ${steps}, then stopped: ${(e as Error).message}`
          : (e as Error).message,
      )
    } finally {
      setProgress(null)
      setMerging(false)
      reload()
    }
  }

  return (
    <div className="mx-auto max-w-3xl p-6">
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <History className="size-4" /> Saved sessions
          </CardTitle>
        </CardHeader>
        <CardContent>
          {error ? (
            <p className="text-sm text-destructive">Couldn't load saved sessions: {error}</p>
          ) : sessions === null ? (
            <p className="text-sm text-muted-foreground">Loading…</p>
          ) : sessions.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              No saved sessions yet. Sessions are saved automatically while you play.
            </p>
          ) : (
            <>
              <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                <p className="text-xs text-muted-foreground">
                  {matchCount
                    ? `${matchCount} session${matchCount === 1 ? "" : "s"} start within 10 minutes of another ending.`
                    : "Nothing in this history starts within 10 minutes of the session before it."}
                </p>
                <Button size="sm" variant="outline" disabled={merging || matchCount === 0} onClick={() => void mergeMatching()}>
                  Merge matching sessions
                </Button>
              </div>
              {progress && (
                <div className="mb-3 space-y-1.5">
                  <p className="text-sm">
                    Merging {Math.min(progress.done + 1, progress.total)} of {progress.total}: {progress.detail}
                  </p>
                  <div
                    role="progressbar"
                    aria-valuemin={0}
                    aria-valuemax={progress.total}
                    aria-valuenow={progress.done}
                    aria-label="Merging matching sessions"
                    className="h-1.5 overflow-hidden rounded-full bg-muted"
                  >
                    <div
                      className="h-full bg-primary transition-[width]"
                      style={{ width: `${(progress.done / progress.total) * 100}%` }}
                    />
                  </div>
                </div>
              )}
              {suggestPair && (
                <div className="mb-3 flex flex-wrap items-center justify-between gap-2 rounded-lg bg-muted/60 px-3 py-2 text-sm">
                  <p>
                    {dateTime(suggestPair[0].started_at)} and {dateTime(suggestPair[1].started_at)} look like one
                    sitting — the next one started within 10 minutes of the first ending.
                  </p>
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={merging}
                    onClick={() => void merge([suggestPair[0].id, suggestPair[1].id])}
                  >
                    Merge
                  </Button>
                </div>
              )}
              <ul className="divide-y">
                {sessions.map((s) => (
                  <SavedSessionRow key={s.id} session={s} selected={picked.includes(s.id)} onSelect={select} />
                ))}
              </ul>
              <div className="mt-3 flex items-center justify-between gap-2">
                <p className="text-xs text-muted-foreground">
                  {pair
                    ? sameSitting(pair[0], pair[1])
                      ? "These two are within 10 minutes. Merging appends the later one and removes it."
                      : "Merging appends the later session onto the earlier one and removes the later session."
                    : "Select two sessions to merge them into one."}
                </p>
                <Button
                  size="sm"
                  disabled={!pair || merging}
                  onClick={() => pair && void merge([pair[0].id, pair[1].id])}
                >
                  Merge selected
                </Button>
              </div>
            </>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
