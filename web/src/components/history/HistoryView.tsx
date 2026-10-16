import { History } from "lucide-react"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { actions, useStore } from "@/lib/store"
import { useSavedSessions } from "@/lib/useSavedSessions"
import { SavedSessionRow } from "./SavedSessionRow"
import { SessionViewer } from "./SessionViewer"

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

function HistoryList() {
  const { sessions, error } = useSavedSessions()
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
            <ul className="divide-y">
              {sessions.map((s) => (
                <SavedSessionRow key={s.id} session={s} />
              ))}
            </ul>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
