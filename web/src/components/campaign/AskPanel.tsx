import { useState } from "react"
import { FileText, Loader2, MessageCircleQuestion, ScrollText, Send, User } from "lucide-react"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import { api } from "@/lib/api"
import { attempt, campaignActions, useCampaign } from "@/lib/campaignStore"
import { openSessionById } from "@/lib/route"
import type { AnswerSource, CampaignAnswer } from "@/lib/types"
import { FactText } from "./FactText"
import { ObsidianLink } from "./ObsidianLink"

const PROGRESS: Partial<Record<CampaignAnswer["state"], string>> = {
  queued: "Waiting for the model…",
  searching: "Searching transcripts and notes…",
  thinking: "Reading what it found…",
}

/** Ask the local LLM about the campaign; it answers from transcripts and notes, citing them. */
export function AskPanel() {
  const answers = useCampaign((s) => s.answers)
  const [question, setQuestion] = useState("")
  const [sending, setSending] = useState(false)

  async function submit(q: string) {
    if (!q.trim() || sending) return
    setSending(true)
    const a = await attempt(() => api.ask(q.trim()))
    setSending(false)
    if (a) {
      campaignActions.putAnswer(a)
      setQuestion("")
    }
  }

  const newestFirst = Object.values(answers).sort((a, b) => b.created - a.created)

  return (
    <section className="space-y-4" aria-label="Ask about the campaign">
      <form
        className="space-y-2"
        onSubmit={(e) => {
          e.preventDefault()
          void submit(question)
        }}
      >
        <label htmlFor="ask-question" className="flex items-center gap-2 text-sm font-semibold">
          <MessageCircleQuestion className="size-4" /> Ask about the campaign
        </label>
        <div className="flex items-end gap-2">
          <Textarea
            id="ask-question"
            rows={2}
            maxLength={500}
            value={question}
            placeholder="Who did we suspect has the diamond?"
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault()
                void submit(question)
              }
            }}
          />
          <Button type="submit" disabled={!question.trim() || sending}>
            <Send /> Ask
          </Button>
        </div>
        <p className="text-xs text-muted-foreground">
          The local model reads your saved transcripts and notes and answers from them.
        </p>
      </form>

      <ul className="space-y-3">
        {newestFirst.map((a) => (
          <AnswerCard key={a.id} answer={a} onRetry={() => void submit(a.question)} />
        ))}
      </ul>
    </section>
  )
}

function AnswerCard({ answer: a, onRetry }: { answer: CampaignAnswer; onRetry: () => void }) {
  const progress = PROGRESS[a.state]
  return (
    <li className="space-y-2 rounded-xl border bg-card p-4" data-answer={a.id}>
      <p className="font-medium">{a.question}</p>
      {progress ? (
        <p className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="size-4 animate-spin" /> {progress}
        </p>
      ) : a.state === "error" ? (
        <Alert variant="destructive" className="flex flex-wrap items-center gap-2">
          <AlertDescription className="text-destructive">Couldn't answer: {a.message}</AlertDescription>
          <Button size="xs" variant="outline" onClick={onRetry}>
            Ask again
          </Button>
        </Alert>
      ) : (
        <>
          <p className={a.found ? "text-sm whitespace-pre-line" : "text-sm text-muted-foreground"}>
            {a.answer}
          </p>
          {a.sources.length > 0 && (
            <ul className="space-y-1 border-t pt-2" aria-label="Sources">
              {a.sources.map((s) => (
                <SourceRow key={s.id} source={s} />
              ))}
            </ul>
          )}
        </>
      )}
    </li>
  )
}

function SourceRow({ source: s }: { source: AnswerSource }) {
  const link = "h-auto p-0 text-left text-xs whitespace-normal"
  return (
    <li className="text-xs" title={s.excerpt}>
      <span className="mr-1.5 text-muted-foreground">[{s.id}]</span>
      {s.kind === "transcript" && s.session ? (
        <Button variant="link" className={link} onClick={() => s.session && openSessionById(s.session)}>
          <ScrollText />
          {s.label}
        </Button>
      ) : s.kind === "entry" && s.entry ? (
        <Button variant="link" className={link} onClick={() => campaignActions.select(s.entry)}>
          <User />
          {s.entry}
        </Button>
      ) : s.path ? (
        <ObsidianLink relPath={s.path}>{s.label}</ObsidianLink>
      ) : (
        <span>
          <FileText className="mr-1 inline size-3" />
          {s.label}
        </span>
      )}
      <span className="mt-0.5 line-clamp-2 text-muted-foreground">
        <FactText text={s.excerpt} />
      </span>
    </li>
  )
}
