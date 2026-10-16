import { useState, type ReactNode } from "react"
import { X } from "lucide-react"
import { Input } from "@/components/ui/input"

/**
 * Editable list of short strings shown as chips. Reports single adds/removes (not a whole new list)
 * so the caller can apply them to the latest saved list. Duplicates (case-insensitive) are ignored. `pending` renders extra chips after
 * the confirmed ones (e.g. suggestions awaiting review).
 */
export function ChipEditor({
  label,
  values,
  onAdd,
  onRemove,
  placeholder,
  pending,
}: {
  label: string
  values: string[]
  onAdd: (value: string) => void
  onRemove: (value: string) => void
  placeholder: string
  pending?: ReactNode
}) {
  const [draft, setDraft] = useState("")
  const add = () => {
    const v = draft.trim()
    setDraft("")
    if (!v || values.some((x) => x.toLowerCase() === v.toLowerCase())) return
    onAdd(v)
  }
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {values.map((v) => (
        <span
          key={v}
          className="inline-flex h-6 items-center gap-0.5 rounded-md bg-secondary pr-0.5 pl-2 text-xs text-secondary-foreground"
        >
          {v}
          <button
            type="button"
            aria-label={`Remove ${label} ${v}`}
            className="rounded p-0.5 text-muted-foreground hover:bg-background/60 hover:text-foreground disabled:pointer-events-none"
            onClick={() => onRemove(v)}
          >
            <X className="size-3" />
          </button>
        </span>
      ))}
      {pending}
      <Input
        aria-label={`Add ${label}`}
        value={draft}
        placeholder={placeholder}
        className="h-6 w-36 text-xs md:text-xs"
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === ",") {
            e.preventDefault()
            add()
          }
        }}
        onBlur={add}
      />
    </div>
  )
}
