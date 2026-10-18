import { useState } from "react"
import { Check, GitMerge, Lock, Sparkles, Trash2, X } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { NativeSelect } from "@/components/ui/native-select"
import { Textarea } from "@/components/ui/textarea"
import { api, type EntityPatch, type EntityReview } from "@/lib/api"
import { ENTITY_TYPES, TYPE_LABEL } from "@/lib/campaign"
import { deleteEntity, mergeEntity, mutateEntity, useCampaign } from "@/lib/campaignStore"
import type { Entity, EntityType } from "@/lib/types"
import { ObsidianLink } from "./ObsidianLink"
import { ChipEditor } from "./ChipEditor"
import { EntityPicker } from "./EntityPicker"
import { FactList } from "./FactList"
import { FactText } from "./FactText"

/** Queue a PATCH computed from the entry as it is when the write runs (lists replace wholesale). */
function patch(name: string, build: (latest: Entity) => EntityPatch | undefined) {
  return mutateEntity(name, (latest) => {
    const body = build(latest)
    return body && api.updateEntity(latest.name, body)
  })
}

/** Queue an atomic accept/reject of suggestions. */
function review(name: string, body: EntityReview) {
  return mutateEntity(name, (latest) => api.reviewEntity(latest.name, body))
}

const hasCI = (list: string[], v: string) => list.some((x) => x.toLowerCase() === v.toLowerCase())

/** PATCH body for a list field with `value` added, or nothing if it's already there. */
function withAdded(list: string[], value: string): string[] | undefined {
  return hasCI(list, value) ? undefined : [...list, value]
}

/** PATCH body for a list field with `value` removed, or nothing if it's already gone. */
function withRemoved(list: string[], value: string): string[] | undefined {
  return list.includes(value) ? list.filter((x) => x !== value) : undefined
}

/** Editor for one glossary entry. Read-only entries (unparseable note properties) can't be edited. */
export function EntityEditor({ entity }: { entity: Entity }) {
  const e = entity

  return (
    <div className="space-y-5">
      {e.read_only && (
        <div
          role="status"
          className="flex items-center gap-2 rounded-lg bg-amber-500/15 p-3 text-sm text-amber-200"
        >
          <Lock className="size-4 shrink-0" />
          This note's properties can't be read. Fix it in Obsidian.
        </div>
      )}
      {e.status === "suggested" && !e.read_only && (
        <div className="flex flex-wrap items-center gap-2 rounded-lg bg-primary/10 p-3 text-sm">
          <Sparkles className="size-4 shrink-0 text-primary" />
          <span className="flex-1">Troubadour suggested this from a session.</span>
          <Button size="sm" onClick={() => patch(e.name, () => ({ status: "confirmed" }))}>
            <Check /> Keep
          </Button>
          <Button size="sm" variant="outline" onClick={() => deleteEntity(e.name)}>
            <X /> Discard
          </Button>
        </div>
      )}

      <fieldset disabled={e.read_only} className="m-0 min-w-0 space-y-5 border-0 p-0">
        <div className="grid gap-3 sm:grid-cols-[1fr_auto]">
          <div className="space-y-1">
            <Label htmlFor="entity-name" className="text-xs text-muted-foreground">
              Name
            </Label>
            <Input
              id="entity-name"
              key={e.name}
              defaultValue={e.name}
              className="text-base font-semibold md:text-base"
              onKeyDown={(ev) => ev.key === "Enter" && ev.currentTarget.blur()}
              onBlur={(ev) => {
                const v = ev.target.value.trim()
                if (v && v !== e.name) void patch(e.name, (l) => (l.name === v ? undefined : { name: v }))
                else ev.target.value = e.name
              }}
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="entity-type" className="text-xs text-muted-foreground">
              Type
            </Label>
            <NativeSelect
              id="entity-type"
              value={e.type}
              className="w-36"
              onChange={(ev) => {
                const type = ev.target.value as EntityType
                void patch(e.name, () => ({ type }))
              }}
            >
              {ENTITY_TYPES.map((t) => (
                <option key={t} value={t}>
                  {TYPE_LABEL[t]}
                </option>
              ))}
            </NativeSelect>
          </div>
        </div>

        <div className="space-y-1">
          <Label htmlFor="entity-description" className="text-xs text-muted-foreground">
            Description
          </Label>
          <Textarea
            id="entity-description"
            key={e.description}
            defaultValue={e.description}
            placeholder="Who or what this is, in a sentence or two."
            onBlur={(ev) => {
              const description = ev.target.value
              if (description !== e.description) void patch(e.name, () => ({ description }))
            }}
          />
        </div>

        <Section title="Aliases">
          <ChipEditor
            label="alias"
            values={e.aliases}
            placeholder="Add alias…"
            onAdd={(v) =>
              patch(e.name, (l) => {
                const aliases = withAdded(l.aliases, v)
                return aliases && { aliases }
              })
            }
            onRemove={(v) =>
              patch(e.name, (l) => {
                const aliases = withRemoved(l.aliases, v)
                return aliases && { aliases }
              })
            }
          />
        </Section>

        <Section
          title="Heard as"
          hint="How speech-to-text misspells it; Troubadour fixes these in new transcripts."
        >
          <ChipEditor
            label="spelling"
            values={e.heard_as}
            placeholder="Add spelling…"
            onAdd={(v) =>
              patch(e.name, (l) => {
                const heard_as = withAdded(l.heard_as, v)
                return heard_as && { heard_as }
              })
            }
            onRemove={(v) =>
              patch(e.name, (l) => {
                const heard_as = withRemoved(l.heard_as, v)
                return heard_as && { heard_as }
              })
            }
            pending={e.heard_as_suggested.map((x) => (
              <span
                key={x}
                data-testid="suggested-spelling"
                title="Suggested by Troubadour"
                className="inline-flex h-6 items-center gap-0.5 rounded-md border border-dashed border-primary/50 pr-0.5 pl-2 text-xs"
              >
                {x}?
                <button
                  type="button"
                  aria-label={`Accept spelling ${x}`}
                  className="rounded p-0.5 text-emerald-400 hover:bg-emerald-500/15"
                  onClick={() => review(e.name, { accept_spellings: [x] })}
                >
                  <Check className="size-3" />
                </button>
                <button
                  type="button"
                  aria-label={`Reject spelling ${x}`}
                  className="rounded p-0.5 text-red-400 hover:bg-red-500/15"
                  onClick={() => review(e.name, { reject_spellings: [x] })}
                >
                  <X className="size-3" />
                </button>
              </span>
            ))}
          />
        </Section>

        {e.suggested_facts.length > 0 && (
          <Section title="Suggested facts" badge={e.suggested_facts.length}>
            <ul className="space-y-1.5">
              {e.suggested_facts.map((f) => (
                <SuggestedFact key={f} entity={e} fact={f} />
              ))}
            </ul>
          </Section>
        )}

        <Section title="Facts">
          <FactList
            facts={e.facts}
            empty="No facts yet."
            addPlaceholder="Add a fact, e.g. Owes Pip 20 gold"
            onEdit={(fact, next) =>
              patch(e.name, (l) =>
                l.facts.includes(fact) ? { facts: l.facts.map((f) => (f === fact ? next : f)) } : undefined,
              )
            }
            onDelete={(fact) =>
              patch(e.name, (l) => {
                const facts = withRemoved(l.facts, fact)
                return facts && { facts }
              })
            }
            onAdd={(text) => mutateEntity(e.name, (l) => api.addEntityFact(l.name, text))}
          />
        </Section>

        <EntityFooter entity={e} />
      </fieldset>
    </div>
  )
}

function Section({
  title,
  hint,
  badge,
  children,
}: {
  title: string
  hint?: string
  badge?: number
  children: React.ReactNode
}) {
  return (
    <section className="space-y-1.5">
      <h3 className="flex items-center gap-2 text-xs font-medium text-muted-foreground">
        {title}
        {badge != null && <Badge variant="secondary">{badge}</Badge>}
      </h3>
      {hint && <p className="text-xs text-muted-foreground/80">{hint}</p>}
      {children}
    </section>
  )
}

/** Accept (moves it to facts), reject, or edit-then-accept a fact Troubadour suggested. */
function SuggestedFact({ entity: e, fact }: { entity: Entity; fact: string }) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(fact)
  const accept = () => review(e.name, { accept_facts: [fact] })
  // Edited: add the new wording first, then drop the suggestion, so a failure can't lose both.
  const acceptEdited = async (text: string) => {
    const added = await mutateEntity(e.name, (l) => api.addEntityFact(l.name, text.trim()))
    if (added) await review(e.name, { reject_facts: [fact] })
  }

  if (editing)
    return (
      <li className="flex gap-1.5">
        <Input
          aria-label="Edit suggested fact"
          autoFocus
          value={draft}
          onChange={(ev) => setDraft(ev.target.value)}
        />
        <Button size="sm" disabled={!draft.trim()} onClick={() => acceptEdited(draft)}>
          <Check /> Accept edited
        </Button>
        <Button size="sm" variant="ghost" onClick={() => setEditing(false)}>
          Cancel
        </Button>
      </li>
    )

  return (
    <li className="flex items-start gap-2 rounded-md border border-dashed border-primary/40 px-2 py-1.5 text-sm">
      <span className="min-w-0 flex-1 leading-relaxed">
        <FactText text={fact} />
      </span>
      <Button size="xs" variant="secondary" aria-label={`Accept fact: ${fact}`} onClick={accept}>
        <Check /> Accept
      </Button>
      <Button size="xs" variant="ghost" aria-label={`Edit fact: ${fact}`} onClick={() => setEditing(true)}>
        Edit
      </Button>
      <Button
        size="xs"
        variant="ghost"
        aria-label={`Reject fact: ${fact}`}
        onClick={() => review(e.name, { reject_facts: [fact] })}
      >
        <X /> Reject
      </Button>
    </li>
  )
}

function EntityFooter({ entity: e }: { entity: Entity }) {
  const entities = useCampaign((s) => s.campaign?.entities ?? [])
  const [into, setInto] = useState("")
  const [confirm, setConfirm] = useState<"merge" | "delete" | null>(null)

  const merge = () => mergeEntity(e.name, into)

  return (
    <footer className="space-y-3 border-t pt-4 text-sm">
      {e.path && (
        <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
          <span className="font-mono">{e.path}</span>
          <ObsidianLink relPath={e.path}>Open in Obsidian</ObsidianLink>
        </div>
      )}
      {confirm === "merge" ? (
        <div role="alert" className="flex flex-wrap items-center gap-2 rounded-lg bg-muted p-3">
          <span className="flex-1">
            Merge <b>{e.name}</b> into <b>{into}</b>? Its names become aliases of {into}.
          </span>
          <Button size="sm" onClick={merge}>
            Confirm merge
          </Button>
          <Button size="sm" variant="ghost" onClick={() => setConfirm(null)}>
            Cancel
          </Button>
        </div>
      ) : confirm === "delete" ? (
        <div role="alert" className="flex flex-wrap items-center gap-2 rounded-lg bg-destructive/10 p-3">
          <span className="flex-1">
            Delete <b>{e.name}</b>? The note moves to the vault's <code>.trash</code>; you can recover it in
            Obsidian.
          </span>
          <Button size="sm" variant="destructive" onClick={() => deleteEntity(e.name)}>
            Confirm delete
          </Button>
          <Button size="sm" variant="ghost" onClick={() => setConfirm(null)}>
            Cancel
          </Button>
        </div>
      ) : (
        <div className="flex flex-wrap items-center gap-2">
          <EntityPicker
            label="Merge into"
            entities={entities}
            exclude={e.name}
            value={into}
            onChange={setInto}
            className="w-48"
          />
          <Button size="sm" variant="outline" disabled={!into} onClick={() => setConfirm("merge")}>
            <GitMerge /> Merge into…
          </Button>
          <Button
            size="sm"
            variant="ghost"
            className="ml-auto text-destructive"
            onClick={() => setConfirm("delete")}
          >
            <Trash2 /> Delete
          </Button>
        </div>
      )}
    </footer>
  )
}
