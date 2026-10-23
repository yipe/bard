import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { toast } from "sonner"
import { Virtuoso } from "react-virtuoso"
import { ArrowLeft, AudioLines, FileX, Loader2, PanelRightClose } from "lucide-react"
import { RecordingQualityPanel } from "@/components/audio/RecordingQualityPanel"
import { PlayerBox, UnnamedShareChip } from "@/components/transcript/PlayerBox"
import { TranscriptRow, TranscriptSpacer } from "@/components/transcript/TranscriptRow"
import { Button } from "@/components/ui/button"
import { api, sessionAudioUrl } from "@/lib/api"
import { dateTime, hms } from "@/lib/format"
import { loadRecord, type LoadedRecord } from "@/lib/record"
import { sourceLabel } from "@/lib/source"
import { recordTalkTime } from "@/lib/talkTime"
import type { SavedSession } from "@/lib/types"
import { WriteUpButton } from "@/components/campaign/WriteUpButton"
import { actions, useStore } from "@/lib/store"
import { TranscriptTools } from "./TranscriptTools"
import { SessionDownloads } from "./SavedSessionRow"

type LoadState =
  { status: "loading" } | { status: "error"; message: string } | { status: "ready"; record: LoadedRecord }

/**
 * Read-only view of a saved session. Its data comes only from the saved record and lives in local
 * state: it never reads or writes the live store's players/voices/messages.
 */
export function SessionViewer({ summary, onBack }: { summary: SavedSession; onBack: () => void }) {
  const [state, setState] = useState<LoadState>({ status: "loading" })
  /** Bumped to reload the record after it changed on the server (improved, spell-fixed, restored). */
  const [revision, setRevision] = useState(0)
  const audioRef = useRef<HTMLAudioElement>(null)

  useEffect(() => {
    let alive = true
    api
      .sessionRecord(summary.id)
      .then(loadRecord)
      .then(
        (record) => alive && setState({ status: "ready", record }),
        // A failed reload keeps the transcript already on screen.
        (e: Error) =>
          alive &&
          setState((s) => {
            if (s.status !== "ready") return { status: "error", message: e.message }
            toast.error(`Couldn't reload the transcript: ${e.message}`)
            return s
          }),
      )
    return () => {
      alive = false
    }
  }, [summary.id, revision])

  const reload = useCallback(() => {
    setRevision((r) => r + 1)
    // The summary's `restorable` changed too; refresh it (the list refetches when it opens).
    api.sessions().then(
      (list) => {
        const fresh = list.find((s) => s.id === summary.id)
        if (fresh && useStore.getState().viewing?.id === summary.id) actions.openSavedSession(fresh)
      },
      () => {},
    )
  }, [summary.id])

  // Explicit user action (timestamp click), so starting playback is fine.
  const seek = summary.audio
    ? (t: number) => {
        const audio = audioRef.current
        if (!audio) return
        audio.currentTime = t
        audio.play().catch(() => {})
      }
    : undefined

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <header className="border-b">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2 px-4 py-2">
          <Button size="sm" variant="ghost" onClick={onBack}>
            <ArrowLeft /> All sessions
          </Button>
          <div className="min-w-0">
            <div className="text-sm font-medium">{dateTime(summary.started_at)}</div>
            <div className="truncate text-xs text-muted-foreground">
              {sourceLabel(summary.source)} · <span className="tabular-nums">{hms(summary.duration_s)}</span> ·{" "}
              {summary.lines} lines
            </div>
          </div>
          <div className="ml-auto">
            <WriteUpButton sessionId={summary.id} />
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2 border-t px-4 py-1.5">
          {summary.audio && (
            <audio
              ref={audioRef}
              controls
              preload="none"
              src={sessionAudioUrl(summary.id)}
              className="h-8 w-72 max-w-full"
            />
          )}
          <div className="ml-auto flex flex-wrap items-center gap-x-3 gap-y-1">
            <SessionDownloads session={summary} />
            <TranscriptTools
              summary={summary}
              improved={state.status === "ready" ? state.record.improved : null}
              spelling={state.status === "ready" ? state.record.spelling : null}
              retranscribed={state.status === "ready" ? (state.record.retranscribed ?? null) : null}
              onReload={reload}
            />
          </div>
        </div>
      </header>
      {state.status === "loading" ? (
        <div className="flex flex-1 items-center justify-center gap-2 text-muted-foreground">
          <Loader2 className="size-4 animate-spin" /> Loading session…
        </div>
      ) : state.status === "error" ? (
        <div role="alert" className="flex flex-1 flex-col items-center justify-center gap-3 p-6 text-center">
          <FileX className="size-8 text-muted-foreground" />
          <p>Couldn't load this session: {state.message}</p>
          <Button variant="outline" onClick={onBack}>
            <ArrowLeft /> Back to all sessions
          </Button>
        </div>
      ) : (
        <RecordBody record={state.record} seek={seek} audioQuality={summary.audio ? summary.id : null} />
      )}
    </div>
  )
}

function RecordBody({
  record,
  seek,
  audioQuality,
}: {
  record: LoadedRecord
  seek?: (t: number) => void
  /** Session id when its recording can be measured (it has audio); null: no side panel. */
  audioQuality: string | null
}) {
  const talk = useMemo(() => recordTalkTime(record.lines), [record.lines])
  const [qualityOpen, setQualityOpen] = useState(true)

  return (
    <div className="flex min-h-0 flex-1 flex-col lg:flex-row">
      <main className="flex min-h-[60vh] min-w-0 flex-1 flex-col lg:min-h-0">
        {(record.players.length > 0 || talk.unassigned > 0) && (
          <>
            <div className="flex flex-wrap gap-3 px-4 py-3">
              {record.players.map((p) => (
                <PlayerBox key={p.id} person={p} seconds={talk.byPlayer.get(p.id) ?? 0} total={talk.total} />
              ))}
              <UnnamedShareChip seconds={talk.unassigned} total={talk.total} />
            </div>
            <div className="border-t" />
          </>
        )}
        {record.lines.length === 0 ? (
          <p className="flex flex-1 items-center justify-center text-muted-foreground">
            This session has no lines.
          </p>
        ) : (
          <div className="relative min-h-0 flex-1">
            <Virtuoso
              // Definite size from the first paint (see ChatFeed). Starts at the first line.
              style={{ position: "absolute", inset: 0 }}
              data={record.lines}
              computeItemKey={(_, l) => l.id}
              increaseViewportBy={400}
              components={{ Header: TranscriptSpacer, Footer: TranscriptSpacer }}
              itemContent={(i, l) => (
                <TranscriptRow
                  continued={l.speaker_key !== null && l.speaker_key === record.lines[i - 1]?.speaker_key}
                  id={l.id}
                  text={l.text}
                  fixes={l.fixes}
                  tStart={l.t_start}
                  uncertain={l.uncertain}
                  speaker={l.speaker}
                  person={l.player}
                  character={l.character && `as ${l.character}`}
                  onTimestampClick={seek && (() => seek(l.t_start))}
                />
              )}
            />
          </div>
        )}
      </main>
      {audioQuality &&
        (qualityOpen ? (
          <aside className="flex min-h-0 w-full shrink-0 flex-col overflow-y-auto border-t p-4 lg:w-80 lg:border-t-0 lg:border-l">
            <h3 className="flex items-center gap-2 pb-3 text-sm font-semibold">
              <AudioLines className="size-4" /> Audio quality
              <Button
                variant="ghost"
                size="icon-xs"
                className="ml-auto"
                aria-label="Hide audio quality"
                onClick={() => setQualityOpen(false)}
              >
                <PanelRightClose />
              </Button>
            </h3>
            <RecordingQualityPanel sessionId={audioQuality} />
          </aside>
        ) : (
          <div className="flex w-full shrink-0 justify-center border-t py-2 lg:w-12 lg:flex-col lg:border-t-0 lg:border-l">
            <Button
              variant="ghost"
              size="icon"
              aria-label="Show audio quality"
              title="Show audio quality"
              onClick={() => setQualityOpen(true)}
            >
              <AudioLines />
            </Button>
          </div>
        ))}
    </div>
  )
}
