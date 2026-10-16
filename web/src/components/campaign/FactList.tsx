import { useState } from "react"
import { Pencil, Plus, Trash2 } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { FactText } from "./FactText"

/**
 * Facts with inline edit and delete, plus an add input. Changes are reported by fact text (not
 * index or whole list) so the caller can apply them to the latest saved list.
 */
export function FactList({
  facts,
  onEdit,
  onDelete,
  onAdd,
  addPlaceholder,
  addLabel = "New fact",
  empty,
}: {
  facts: string[]
  onEdit: (fact: string, next: string) => void
  onDelete: (fact: string) => void
  onAdd: (text: string) => void
  addPlaceholder: string
  addLabel?: string
  empty: string
}) {
  const [editing, setEditing] = useState<number | null>(null)
  const [draft, setDraft] = useState("")

  const save = (i: number, text: string) => {
    setEditing(null)
    const v = text.trim()
    if (!v) onDelete(facts[i])
    else if (v !== facts[i]) onEdit(facts[i], v)
  }

  return (
    <div className="space-y-1.5">
      {facts.length === 0 && <p className="text-sm text-muted-foreground">{empty}</p>}
      <ul className="space-y-1">
        {facts.map((f, i) => (
          <li key={`${i}:${f}`} className="group flex items-start gap-1 text-sm">
            {editing === i ? (
              <Input
                autoFocus
                aria-label="Edit fact"
                defaultValue={f}
                onBlur={(e) => save(i, e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") e.currentTarget.blur()
                  if (e.key === "Escape") setEditing(null)
                }}
              />
            ) : (
              <>
                <span className="mt-1.5 size-1 shrink-0 rounded-full bg-muted-foreground" />
                <span className="min-w-0 flex-1 leading-relaxed">
                  <FactText text={f} />
                </span>
                <span className="flex opacity-60 transition-opacity group-hover:opacity-100">
                  <Button
                    size="icon-xs"
                    variant="ghost"
                    aria-label={`Edit fact ${i + 1}`}
                    onClick={() => setEditing(i)}
                  >
                    <Pencil />
                  </Button>
                  <Button
                    size="icon-xs"
                    variant="ghost"
                    aria-label={`Delete fact ${i + 1}`}
                    onClick={() => onDelete(f)}
                  >
                    <Trash2 />
                  </Button>
                </span>
              </>
            )}
          </li>
        ))}
      </ul>
      <form
        className="flex gap-1.5"
        onSubmit={(e) => {
          e.preventDefault()
          const v = draft.trim()
          if (!v) return
          onAdd(v)
          setDraft("")
        }}
      >
        <Input
          aria-label={addLabel}
          value={draft}
          placeholder={addPlaceholder}
          onChange={(e) => setDraft(e.target.value)}
        />
        <Button type="submit" size="sm" variant="outline" disabled={!draft.trim()}>
          <Plus /> Add
        </Button>
      </form>
    </div>
  )
}
