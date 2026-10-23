import { useEffect, useState } from "react"
import { Loader2 } from "lucide-react"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { api } from "@/lib/api"
import type { ModelSettings } from "@/lib/types"

/** Account-menu dialog: which Whisper and which local LLM this Mac uses. */
export function ModelsDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const [settings, setSettings] = useState<ModelSettings | null>(null)
  const [whisper, setWhisper] = useState("")
  const [whisperImprove, setWhisperImprove] = useState("")
  const [llm, setLlm] = useState("")
  const [memory, setMemory] = useState("")
  const [url, setUrl] = useState("")
  const [apiKey, setApiKey] = useState("")
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    if (!open) return
    let gone = false
    setError(null)
    void api
      .modelSettings()
      .then((s) => {
        if (gone) return
        setSettings(s)
        setWhisper(s.whisper)
        setWhisperImprove(s.whisper_improve)
        setLlm(s.llm)
        setMemory(s.memory)
        setUrl(s.llm_url)
        setApiKey(s.api_key)
      })
      .catch((e: Error) => {
        if (!gone) setError(e.message)
      })
    return () => {
      gone = true
    }
  }, [open])

  async function save() {
    setSaving(true)
    setError(null)
    try {
      const s = await api.saveModelSettings({
        whisper,
        whisper_improve: whisperImprove,
        llm: llm.trim(),
        memory: memory.trim(),
        llm_url: url.trim(),
        api_key: apiKey.trim(),
      })
      setSettings(s)
      onOpenChange(false)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setSaving(false)
    }
  }

  const whisperOptions = options(settings?.whisper_options ?? [], whisper, whisperImprove)
  const installed = settings?.installed ?? []

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Models</DialogTitle>
          <DialogDescription>
            Saved on this Mac. A new choice is used the next time Troubadour transcribes or asks. Speaker
            matching stays on {settings?.speaker ?? "the bundled voice model"}.
          </DialogDescription>
        </DialogHeader>
        {!settings && !error ? (
          <p className="flex items-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="size-4 animate-spin" /> Loading…
          </p>
        ) : (
          <div className="space-y-3 text-sm">
            <Field label="Live transcription" value={whisper} options={whisperOptions} onChange={setWhisper} />
            <Field
              label="Improve transcript"
              value={whisperImprove}
              options={whisperOptions}
              onChange={setWhisperImprove}
            />
            <label className="block space-y-1">
              <span className="text-xs text-muted-foreground">Model server</span>
              <input
                className="h-8 w-full rounded-lg border bg-background px-2"
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                placeholder="1235, 10.0.0.8:1234, or https://openrouter.ai/api/v1"
                spellCheck={false}
              />
            </label>
            <label className="block space-y-1">
              <span className="text-xs text-muted-foreground">API key</span>
              <input
                type="password"
                className="h-8 w-full rounded-lg border bg-background px-2"
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
                placeholder="Only for a server that asks for one"
                autoComplete="off"
                spellCheck={false}
              />
            </label>
            <LlmField label="Live spelling" value={llm} installed={installed} onChange={setLlm} />
            <LlmField
              label="Questions, write-ups, and facts"
              value={memory}
              installed={installed}
              onChange={setMemory}
            />
            <p className="text-xs text-muted-foreground">
              A port means this Mac. A host and port reaches another machine. OpenRouter works as
              https://openrouter.ai/api/v1 with an API key; leave the key empty for LM Studio. The model list is
              what that server offers, plus any model you have chosen here before.
            </p>
            {error && <p className="text-sm text-destructive">{error}</p>}
          </div>
        )}
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button disabled={!settings || saving || !llm.trim() || !memory.trim()} onClick={() => void save()}>
            {saving && <Loader2 className="animate-spin" />} Save
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function options(known: { id: string; label: string }[], ...extra: string[]) {
  const seen = new Set(known.map((o) => o.id))
  const out = [...known]
  for (const id of extra) {
    if (id && !seen.has(id)) out.push({ id, label: id })
  }
  return out
}

function Field({
  label,
  value,
  options,
  onChange,
}: {
  label: string
  value: string
  options: { id: string; label: string }[]
  onChange: (value: string) => void
}) {
  return (
    <label className="block space-y-1">
      <span className="text-xs text-muted-foreground">{label}</span>
      <select
        className="h-8 w-full rounded-lg border bg-background px-2"
        value={value}
        onChange={(e) => onChange(e.target.value)}
      >
        {options.map((o) => (
          <option key={o.id} value={o.id}>
            {o.label}
          </option>
        ))}
      </select>
    </label>
  )
}

function LlmField({
  label,
  value,
  installed,
  onChange,
}: {
  label: string
  value: string
  installed: string[]
  onChange: (value: string) => void
}) {
  const choices = installed.includes(value) || !value ? installed : [value, ...installed]
  const listId = `llm-${label.replace(/\W+/g, "-")}`
  return (
    <label className="block space-y-1">
      <span className="text-xs text-muted-foreground">{label}</span>
      <input
        className="h-8 w-full rounded-lg border bg-background px-2"
        value={value}
        list={listId}
        onChange={(e) => onChange(e.target.value)}
        placeholder="google/gemma-4-26b-a4b-qat"
        spellCheck={false}
      />
      <datalist id={listId}>
        {choices.map((id) => (
          <option key={id} value={id} />
        ))}
      </datalist>
    </label>
  )
}
