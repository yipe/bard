import { useState } from "react"
import { AlertCircle, Upload } from "lucide-react"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import { api } from "@/lib/api"
import { attempt, campaignActions, useCampaign } from "@/lib/campaignStore"
import { JobBar } from "./JobProgress"

/** Paste session notes; Troubadour's local AI reads them into the glossary in the background. */
export function ImportDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const [title, setTitle] = useState("")
  const [text, setText] = useState("")
  const [jobId, setJobId] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const job = useCampaign((s) => (jobId ? s.jobs[jobId] : undefined))

  const reset = () => {
    setTitle("")
    setText("")
    setJobId(null)
  }

  const submit = async () => {
    setBusy(true)
    const j = await attempt(() => api.importNotes(title.trim(), text))
    setBusy(false)
    if (!j) return
    campaignActions.putJob(j)
    setJobId(j.id)
  }

  const openEntry = (name: string) => {
    campaignActions.select(name)
    onOpenChange(false)
    reset()
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        onOpenChange(o)
        // Keep a running import's dialog state so reopening shows its progress; start fresh once it ended.
        if (!o && job && (job.state === "done" || job.state === "error")) reset()
      }}
    >
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>Import notes</DialogTitle>
          <DialogDescription>
            Paste session notes or a campaign doc. It can be long — Troubadour reads it with the local AI and
            adds people, places and facts to the glossary.
          </DialogDescription>
        </DialogHeader>

        {!job ? (
          <form
            id="import-notes"
            className="space-y-3"
            onSubmit={(e) => {
              e.preventDefault()
              void submit()
            }}
          >
            <div className="space-y-1">
              <Label htmlFor="import-title">Title</Label>
              <Input
                id="import-title"
                placeholder="Session 12 notes"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
              />
            </div>
            <div className="space-y-1">
              <Label htmlFor="import-text">Notes</Label>
              <Textarea
                id="import-text"
                value={text}
                onChange={(e) => setText(e.target.value)}
                placeholder="Paste here…"
                className="max-h-[50vh] min-h-48 font-mono text-xs md:text-xs"
              />
            </div>
          </form>
        ) : job.state === "error" ? (
          <p
            role="alert"
            className="flex items-start gap-2 rounded-md bg-destructive/15 p-3 text-sm text-destructive"
          >
            <AlertCircle className="mt-0.5 size-4 shrink-0" /> {job.message || "Import failed"}
          </p>
        ) : job.state === "done" ? (
          <div className="space-y-3" data-testid="import-summary">
            <p className="text-sm font-medium">
              {job.created.length} new · {job.updated.length} updated
              {job.suggested > 0 && (
                <span className="text-muted-foreground"> · {job.suggested} to review</span>
              )}
            </p>
            {[...job.created, ...job.updated].length > 0 && (
              <div className="flex flex-wrap gap-1.5">
                {[...job.created, ...job.updated].map((name) => (
                  <button
                    key={name}
                    type="button"
                    className="rounded-md bg-secondary px-2 py-0.5 text-xs hover:bg-primary hover:text-primary-foreground"
                    onClick={() => openEntry(name)}
                  >
                    {name}
                  </button>
                ))}
              </div>
            )}
          </div>
        ) : (
          <JobBar job={job} />
        )}

        <DialogFooter>
          {!job ? (
            <Button type="submit" form="import-notes" disabled={busy || !text.trim()}>
              <Upload /> Import
            </Button>
          ) : job.state === "done" || job.state === "error" ? (
            <Button variant="outline" onClick={reset}>
              Import more
            </Button>
          ) : null}
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            {job && (job.state === "queued" || job.state === "running") ? "Hide" : "Close"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
