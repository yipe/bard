import { History } from "lucide-react"
import { SavedSessionRow } from "@/components/history/SavedSessionRow"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { actions } from "@/lib/store"
import { useSavedSessions } from "@/lib/useSavedSessions"

const RECENT = 5

/** The most recent saved sessions; each opens in the History viewer. */
export function PastSessions() {
  const { sessions, error } = useSavedSessions()

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <History className="size-4" /> Past sessions
          <Button size="xs" variant="ghost" className="ml-auto font-normal" onClick={actions.openHistory}>
            All sessions
          </Button>
        </CardTitle>
      </CardHeader>
      <CardContent>
        {error ? (
          <p className="text-sm text-destructive">Couldn't load past sessions: {error}</p>
        ) : sessions === null ? (
          <p className="text-sm text-muted-foreground">Loading…</p>
        ) : sessions.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No past sessions yet. Sessions are saved automatically.
          </p>
        ) : (
          <ul className="divide-y">
            {sessions.slice(0, RECENT).map((s) => (
              <SavedSessionRow key={s.id} session={s} />
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  )
}
