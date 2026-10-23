import { useMemo, useState } from "react"
import { Plus, Search } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { NativeSelect } from "@/components/ui/native-select"
import { api } from "@/lib/api"
import { ENTITY_TYPES, TYPE_LABEL, TYPE_PLURAL, matchesSearch, needsReview } from "@/lib/campaign"
import { attempt, campaignActions, useCampaign } from "@/lib/campaignStore"
import { cn } from "@/lib/utils"
import type { Entity, EntityType } from "@/lib/types"

type Filter = EntityType | "all" | "review"

const chip = (active: boolean) =>
  cn(
    "inline-flex h-6 items-center gap-1 rounded-full px-2.5 text-xs transition-colors",
    active ? "bg-primary text-primary-foreground" : "bg-muted text-muted-foreground hover:text-foreground",
  )

/** Searchable, filterable list of glossary entries; selecting one opens it in the editor. */
export function GlossaryList({ entities, types }: { entities: Entity[]; types: EntityType[] }) {
  const selected = useCampaign((s) => s.selected)
  const [query, setQuery] = useState("")
  const [filter, setFilter] = useState<Filter>("all")
  const [adding, setAdding] = useState(false)

  const reviewCount = useMemo(() => entities.filter(needsReview).length, [entities])
  const shown = useMemo(
    () =>
      entities
        .filter((e) => (filter === "all" ? true : filter === "review" ? needsReview(e) : e.type === filter))
        .filter((e) => matchesSearch(e, query))
        .sort(
          (a, b) =>
            (b.sessions ?? 0) - (a.sessions ?? 0) || a.name.localeCompare(b.name, undefined, { sensitivity: "base" }),
        ),
    [entities, filter, query],
  )

  return (
    <div className="space-y-3">
      <div className="flex gap-2">
        <div className="relative flex-1">
          <Search className="pointer-events-none absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2 text-muted-foreground" />
          <Input
            aria-label="Search glossary"
            placeholder="Search names, aliases, heard as…"
            className="pl-8"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>
        <Button variant="outline" onClick={() => setAdding((a) => !a)} aria-expanded={adding}>
          <Plus /> New entry
        </Button>
      </div>

      {adding && <NewEntryForm onDone={() => setAdding(false)} />}

      <div role="group" aria-label="Filter by type" className="flex flex-wrap gap-1.5">
        <button
          type="button"
          aria-pressed={filter === "all"}
          className={chip(filter === "all")}
          onClick={() => setFilter("all")}
        >
          All
        </button>
        {(types.length ? types : ENTITY_TYPES).map((t) => (
          <button
            key={t}
            type="button"
            aria-pressed={filter === t}
            className={chip(filter === t)}
            onClick={() => setFilter(t)}
          >
            {TYPE_PLURAL[t] ?? t}
          </button>
        ))}
        <button
          type="button"
          aria-pressed={filter === "review"}
          className={chip(filter === "review")}
          onClick={() => setFilter("review")}
        >
          Needs review
          {reviewCount > 0 && (
            <span
              data-testid="review-count"
              className="rounded-full bg-amber-500 px-1.5 text-[10px] font-semibold text-black"
            >
              {reviewCount}
            </span>
          )}
        </button>
      </div>

      {shown.length === 0 ? (
        <p className="py-6 text-center text-sm text-muted-foreground">
          {entities.length === 0
            ? "No entries yet — import session notes, or add one."
            : filter === "review"
              ? "Nothing waiting for review."
              : "No entries match."}
        </p>
      ) : (
        <ul aria-label="Glossary" className="space-y-0.5">
          {shown.map((e) => (
            <li key={e.name}>
              <button
                type="button"
                data-entity={e.name}
                aria-current={selected === e.name || undefined}
                onClick={() => campaignActions.select(e.name)}
                className={cn(
                  "flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-sm transition-colors",
                  selected === e.name ? "bg-muted" : "hover:bg-muted/50",
                )}
              >
                <span className="min-w-0 flex-1">
                  <span className="block truncate font-medium">{e.name}</span>
                  {e.aliases.length > 0 && (
                    <span className="block truncate text-xs text-muted-foreground">
                      {e.aliases.join(", ")}
                    </span>
                  )}
                </span>
                {e.status === "suggested" && (
                  <Badge variant="outline" className="border-primary/50 text-primary">
                    suggested
                  </Badge>
                )}
                <Badge variant="secondary">{TYPE_LABEL[e.type] ?? e.type}</Badge>
                {needsReview(e) && (
                  <span
                    aria-label="Needs review"
                    title="Needs review"
                    className="size-2 shrink-0 rounded-full bg-amber-500"
                  />
                )}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

/** Name + type, then create and select the new entry. */
export function NewEntryForm({
  initialName = "",
  extra,
  onDone,
}: {
  initialName?: string
  /** Extra fields sent with the create (e.g. a heard-as spelling). */
  extra?: (name: string) => { heard_as?: string[] }
  /** Called with the created entry, or with nothing on cancel. */
  onDone: (created?: Entity) => void
}) {
  const [name, setName] = useState(initialName)
  const [type, setType] = useState<EntityType>("npc")
  const [busy, setBusy] = useState(false)

  return (
    <form
      aria-label="New entry"
      className="flex flex-wrap items-center gap-2 rounded-lg border border-dashed p-2"
      onSubmit={async (ev) => {
        ev.preventDefault()
        const n = name.trim()
        if (!n) return
        setBusy(true)
        const e = await attempt(() => api.createEntity({ name: n, type, ...extra?.(n) }))
        setBusy(false)
        if (!e) return
        campaignActions.putEntity(e)
        campaignActions.select(e.name)
        onDone(e)
      }}
    >
      <Input
        aria-label="Entry name"
        autoFocus
        placeholder="Name"
        className="min-w-32 flex-1"
        value={name}
        onChange={(ev) => setName(ev.target.value)}
      />
      <NativeSelect
        aria-label="Entry type"
        value={type}
        onChange={(ev) => setType(ev.target.value as EntityType)}
      >
        {ENTITY_TYPES.map((t) => (
          <option key={t} value={t}>
            {TYPE_LABEL[t]}
          </option>
        ))}
      </NativeSelect>
      <Button type="submit" size="sm" disabled={busy || !name.trim()}>
        Create
      </Button>
      <Button type="button" size="sm" variant="ghost" onClick={() => onDone()}>
        Cancel
      </Button>
    </form>
  )
}
