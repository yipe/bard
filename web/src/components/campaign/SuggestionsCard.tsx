import { useEffect, useState, type ReactElement } from "react"
import { toast } from "sonner"
import { ChevronDown, RotateCcw, SpellCheck, X } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { api, type EntityReview } from "@/lib/api"
import { findEntity } from "@/lib/campaign"
import {
  campaignActions,
  ignoreWord,
  mutateEntity,
  rejectMisheard,
  suggestionKey,
  unignoreWord,
  unrejectMisheard,
  useCampaign,
} from "@/lib/campaignStore"
import { cn } from "@/lib/utils"
import type { Entity } from "@/lib/types"
import { EntityPicker } from "./EntityPicker"
import { NewEntryForm } from "./GlossaryList"

/** Accept or reject a heard spelling on an entry (atomic on the server), then reload suggestions. */
async function reviewSpelling(name: string, body: EntityReview) {
  const e = await mutateEntity(name, (latest) => api.reviewEntity(latest.name, body))
  if (e) void campaignActions.refreshSuggestions()
}

/** Small ✕ / ↺ icon button with a tooltip. */
function IconAction({
  label,
  tip,
  icon,
  disabled,
  onClick,
}: {
  label: string
  tip: string
  icon: ReactElement
  disabled?: boolean
  onClick: () => void
}) {
  return (
    <Tooltip>
      <TooltipTrigger
        render={
          <Button
            size="icon-xs"
            variant="ghost"
            aria-label={label}
            disabled={disabled}
            className="text-muted-foreground hover:text-foreground"
            onClick={onClick}
          />
        }
      >
        {icon}
      </TooltipTrigger>
      <TooltipContent>{tip}</TooltipContent>
    </Tooltip>
  )
}

async function ignore(word: string) {
  if (await ignoreWord(word))
    toast(`Ignored "${word}"`, { action: { label: "Undo", onClick: () => void unignoreWord(word) } })
}

async function notAMatch(suggested: string, entity: Entity, heard: string) {
  if (await rejectMisheard(suggested, entity.name, heard))
    toast(`"${heard}" isn't ${entity.name}`, {
      action: { label: "Undo", onClick: () => void unrejectMisheard(suggested, entity.name, heard) },
    })
}

/** Spelling suggestions mined from saved transcripts: misheard glossary names and unknown names. */
export function SuggestionsCard() {
  const suggestions = useCampaign((s) => s.suggestions)
  const entities = useCampaign((s) => s.campaign?.entities ?? [])
  const ignored = useCampaign((s) => s.campaign?.ignored ?? [])
  const hidden = useCampaign((s) => s.hidden)
  const [open, setOpen] = useState(true)

  useEffect(() => {
    void campaignActions.refreshSuggestions()
  }, [])

  const misheard = (suggestions?.misheard ?? []).filter(
    (m) => !hidden[suggestionKey.misheard(m.entity, m.heard)],
  )
  const unknown = (suggestions?.unknown ?? []).filter((u) => !hidden[suggestionKey.unknown(u.heard)])
  const count = misheard.length + unknown.length

  return (
    <Collapsible open={open} onOpenChange={setOpen} className="rounded-xl border bg-card p-3">
      <CollapsibleTrigger className="flex w-full items-center gap-2 text-sm font-semibold">
        <SpellCheck className="size-4" /> Spelling suggestions
        {count > 0 && <span className="text-xs font-normal text-muted-foreground">{count}</span>}
        <ChevronDown className={cn("ml-auto size-4 transition-transform", !open && "-rotate-90")} />
      </CollapsibleTrigger>
      <CollapsibleContent>
        {suggestions === null ? (
          <p className="pt-2 text-sm text-muted-foreground">Loading…</p>
        ) : count === 0 ? (
          <p className="pt-2 text-sm text-muted-foreground">No spelling issues found in saved sessions.</p>
        ) : (
          <div className="space-y-3 pt-3">
            {misheard.length > 0 && (
              <div className="space-y-1.5">
                <h4 className="text-xs font-medium text-muted-foreground">Misheard names</h4>
                <ul className="space-y-2">
                  {misheard.map((m) => {
                    const entity = findEntity(entities, m.entity)
                    return (
                      <li key={`${m.entity}:${m.heard}`} data-suggestion={m.heard} className="text-sm">
                        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                          <span>
                            <b>{m.heard}</b>{" "}
                            <span className="text-muted-foreground tabular-nums">×{m.count}</span> →{" "}
                            <b>{m.entity}</b>
                          </span>
                          <span className="ml-auto flex gap-1">
                            <Button
                              size="xs"
                              variant="secondary"
                              disabled={!entity}
                              onClick={() =>
                                entity && reviewSpelling(entity.name, { accept_spellings: [m.heard] })
                              }
                            >
                              Fix it
                            </Button>
                            <IconAction
                              label={`Not a match: ${m.heard}`}
                              tip={`Not a misspelling of ${m.entity}`}
                              icon={<X />}
                              disabled={!entity}
                              onClick={() => entity && notAMatch(m.entity, entity, m.heard)}
                            />
                          </span>
                        </div>
                        {m.example && <p className="truncate text-xs text-muted-foreground">“{m.example}”</p>}
                      </li>
                    )
                  })}
                </ul>
              </div>
            )}
            {unknown.length > 0 && (
              <div className="space-y-1.5">
                <h4 className="text-xs font-medium text-muted-foreground">Unknown names heard</h4>
                <ul className="space-y-2">
                  {unknown.map((u) => (
                    <UnknownName
                      key={u.heard}
                      heard={u.heard}
                      count={u.count}
                      example={u.example}
                      entities={entities}
                    />
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}
        {ignored.length > 0 && <IgnoredWords words={ignored} />}
      </CollapsibleContent>
    </Collapsible>
  )
}

/** Words the user told Troubadour to stop suggesting (kept in the vault note "Ignored words.md"). */
function IgnoredWords({ words }: { words: string[] }) {
  const [open, setOpen] = useState(false)
  const sorted = [...words].sort((a, b) => a.localeCompare(b, undefined, { sensitivity: "base" }))
  return (
    <Collapsible open={open} onOpenChange={setOpen} className="mt-3 border-t pt-2">
      <CollapsibleTrigger className="flex w-full items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground">
        <ChevronDown className={cn("size-3.5 transition-transform", !open && "-rotate-90")} />
        Ignored words ({words.length})
      </CollapsibleTrigger>
      <CollapsibleContent>
        <ul aria-label="Ignored words" className="flex flex-wrap gap-1 pt-2">
          {sorted.map((w) => (
            <li
              key={w}
              className="inline-flex h-6 items-center gap-0.5 rounded-md bg-muted pr-0.5 pl-2 text-xs text-muted-foreground"
            >
              {w}
              <IconAction
                label={`Stop ignoring ${w}`}
                tip="Stop ignoring"
                icon={<RotateCcw />}
                onClick={() => void unignoreWord(w)}
              />
            </li>
          ))}
        </ul>
      </CollapsibleContent>
    </Collapsible>
  )
}

function UnknownName({
  heard,
  count,
  example,
  entities,
}: {
  heard: string
  count: number
  example: string
  entities: Entity[]
}) {
  const [mode, setMode] = useState<"add" | "misspelling" | null>(null)
  const [target, setTarget] = useState("")
  const entity = findEntity(entities, target)

  return (
    <li data-suggestion={heard} className="space-y-1 text-sm">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <span>
          <b>{heard}</b> <span className="text-muted-foreground tabular-nums">×{count}</span>
        </span>
        <span className="ml-auto flex gap-1">
          <Button
            size="xs"
            variant="secondary"
            aria-expanded={mode === "add"}
            onClick={() => setMode(mode === "add" ? null : "add")}
          >
            Add
          </Button>
          <Button
            size="xs"
            variant="ghost"
            aria-expanded={mode === "misspelling"}
            disabled={entities.length === 0}
            onClick={() => setMode(mode === "misspelling" ? null : "misspelling")}
          >
            It's a misspelling of…
          </Button>
          <IconAction
            label={`Ignore ${heard}`}
            tip="Not a name. Don't suggest it again"
            icon={<X />}
            onClick={() => void ignore(heard)}
          />
        </span>
      </div>
      {example && <p className="truncate text-xs text-muted-foreground">“{example}”</p>}
      {mode === "add" && (
        <NewEntryForm
          initialName={heard}
          // If the user corrects the spelling, remember how it was heard.
          extra={(name) => (name.toLowerCase() === heard.toLowerCase() ? {} : { heard_as: [heard] })}
          onDone={(created) => {
            setMode(null)
            if (created) void campaignActions.refreshSuggestions()
          }}
        />
      )}
      {mode === "misspelling" && (
        <div className="flex flex-wrap items-center gap-2">
          <EntityPicker
            label={`${heard} is a misspelling of`}
            entities={entities}
            value={target}
            onChange={setTarget}
            className="w-48"
          />
          <Button
            size="xs"
            disabled={!entity}
            onClick={async () => {
              if (!entity) return
              // Not a pending suggestion on that entry, so add it to heard_as (from the latest list).
              const e = await mutateEntity(entity.name, (latest) =>
                latest.heard_as.some((x) => x.toLowerCase() === heard.toLowerCase())
                  ? undefined
                  : api.updateEntity(latest.name, { heard_as: [...latest.heard_as, heard] }),
              )
              if (e) void campaignActions.refreshSuggestions()
              setMode(null)
            }}
          >
            Confirm
          </Button>
        </div>
      )}
    </li>
  )
}
