import { create } from "zustand"
import { toast } from "sonner"
import { api } from "./api"
import type { Campaign, CampaignJob, CampaignSuggestions, Entity } from "./types"

/**
 * Campaign memory state, separate from the live transcript store. Loaded lazily by the views that
 * need it; `campaign.updated` events refetch whatever has been loaded (debounced).
 */
interface CampaignState {
  campaign: Campaign | null
  loadError: string | null
  suggestions: CampaignSuggestions | null
  /** Suggestion rows dismissed locally (see `suggestionKey`), hidden until the user undoes it. */
  hidden: Record<string, true>
  /** Jobs by id; from GET /api/campaign and `campaign.job` events. */
  jobs: Record<string, CampaignJob>
  /** Failed jobs the user dismissed from the header. */
  dismissed: Record<string, true>
  /** Name of the entry open in the editor. */
  selected: string | null
}

export const useCampaign = create<CampaignState>(() => ({
  campaign: null,
  loadError: null,
  suggestions: null,
  hidden: {},
  jobs: {},
  dismissed: {},
  selected: null,
}))

const set = useCampaign.setState

let loadingCampaign = false
let loadingSuggestions = false

async function loadCampaign() {
  try {
    const campaign = await api.campaign()
    set((s) => ({
      campaign,
      loadError: null,
      jobs: { ...s.jobs, ...Object.fromEntries(campaign.jobs.map((j) => [j.id, j])) },
    }))
  } catch (e) {
    set({ loadError: (e as Error).message })
  } finally {
    loadingCampaign = false
  }
}

/*
 * Campaign writes run one at a time. List fields are replaced wholesale by PATCH, so each write must
 * be computed from the state left by the previous one (not from what was on screen when clicked),
 * and every response is applied before the next write runs. Refetches share the queue so an older
 * snapshot can't land between two writes.
 */
let queue: Promise<unknown> = Promise.resolve()

function serialize<T>(task: () => Promise<T>): Promise<T> {
  const run = queue.then(task)
  queue = run.catch(() => {})
  return run
}

/** Old name -> new name for entries renamed by a queued write, so later writes find them. */
const renamed = new Map<string, string>()

/** The entry called `name` now; if there is none, the entry it was renamed or merged into. */
function latestEntity(name: string): Entity | undefined {
  const entities = useCampaign.getState().campaign?.entities ?? []
  let n = name
  for (let hops = 0; hops < 20; hops++) {
    const e = entities.find((x) => x.name === n)
    if (e) return e
    const next = renamed.get(n)
    if (!next) return undefined
    n = next
  }
  return undefined
}

/**
 * Queue a change to one entry. `change` gets the entry as it is when the write runs and returns the
 * API call (or nothing to skip). The returned entry is applied to the store immediately. Errors show
 * as a toast; the result is then undefined.
 */
export function mutateEntity(
  name: string,
  change: (latest: Entity) => Promise<Entity> | undefined,
): Promise<Entity | undefined> {
  return serialize(() =>
    attempt(async () => {
      const latest = latestEntity(name)
      const call = latest && change(latest)
      if (!latest || !call) return undefined
      const e = await call
      if (e.name !== latest.name) renamed.set(latest.name, e.name)
      campaignActions.putEntity(e, latest.name)
      return e
    }),
  )
}

/** Queue deleting an entry (Discard / Delete). */
export function deleteEntity(name: string): Promise<boolean | undefined> {
  return serialize(() =>
    attempt(async () => {
      const latest = latestEntity(name)
      if (!latest) return false
      await api.deleteEntity(latest.name)
      campaignActions.removeEntity(latest.name)
      return true
    }),
  )
}

/** Queue folding an entry into another; selects the merged entry. */
export function mergeEntity(name: string, into: string): Promise<Entity | undefined> {
  return serialize(() =>
    attempt(async () => {
      const source = latestEntity(name)
      const target = latestEntity(into)
      if (!source || !target) return undefined
      const merged = await api.mergeEntity(source.name, target.name)
      renamed.set(source.name, merged.name)
      campaignActions.removeEntity(source.name)
      campaignActions.putEntity(merged, target.name)
      campaignActions.select(merged.name)
      return merged
    }),
  )
}

/** Queue a change to the campaign-wide facts, computed from the latest list when it runs. */
export function mutateCampaignFacts(change: (latest: string[]) => Promise<string[]>) {
  return serialize(() =>
    attempt(async () => {
      const facts = await change(useCampaign.getState().campaign?.facts ?? [])
      campaignActions.setFacts(facts)
      return facts
    }),
  )
}

export const campaignActions = {
  /** Reload everything. Runs in the write queue, so it never lands between a write and the next. */
  refresh() {
    loadingCampaign = true
    return serialize(() => loadCampaign())
  },

  async refreshSuggestions() {
    loadingSuggestions = true
    try {
      set({ suggestions: await api.campaignSuggestions() })
    } catch (e) {
      toast.error(`Couldn't load suggestions: ${(e as Error).message}`)
    } finally {
      loadingSuggestions = false
    }
  },

  select: (name: string | null) => set({ selected: name }),

  /** Put a returned entry into the list, replacing `previousName` (a rename or merge source). */
  putEntity(e: Entity, previousName: string = e.name) {
    set((s) => {
      if (!s.campaign) return {}
      const rest = s.campaign.entities.filter((x) => x.name !== previousName && x.name !== e.name)
      return {
        campaign: { ...s.campaign, entities: [...rest, e] },
        selected: s.selected === previousName ? e.name : s.selected,
      }
    })
  },

  removeEntity(name: string) {
    set((s) => ({
      campaign: s.campaign && { ...s.campaign, entities: s.campaign.entities.filter((e) => e.name !== name) },
      selected: s.selected === name ? null : s.selected,
    }))
  },

  setIgnored: (ignored: string[]) => set((s) => ({ campaign: s.campaign && { ...s.campaign, ignored } })),

  setFacts: (facts: string[]) => set((s) => ({ campaign: s.campaign && { ...s.campaign, facts } })),

  putJob: (job: CampaignJob) => set((s) => ({ jobs: { ...s.jobs, [job.id]: job } })),

  dismissJob: (id: string) => set((s) => ({ dismissed: { ...s.dismissed, [id]: true } })),

  /** Another campaign was opened: its notes arrive with the `campaign.updated` refetch. */
  campaignChanged: () => set({ selected: null, hidden: {} }),
}

export const REFETCH_DEBOUNCE_MS = 300
let refetchTimer: number | undefined

/** Called by the live event loop for `campaign.*` events. */
export function applyCampaignEvent(
  ev: { type: "campaign.job"; data: CampaignJob } | { type: "campaign.updated" },
) {
  if (ev.type === "campaign.job") return campaignActions.putJob(ev.data)
  clearTimeout(refetchTimer)
  refetchTimer = window.setTimeout(() => {
    const s = useCampaign.getState()
    if (s.campaign || loadingCampaign) void campaignActions.refresh()
    if (s.suggestions || loadingSuggestions) void campaignActions.refreshSuggestions()
  }, REFETCH_DEBOUNCE_MS)
}

/** Run a campaign API call; on failure show the server's detail as a toast and return undefined. */
export async function attempt<T>(call: () => Promise<T>): Promise<T | undefined> {
  try {
    return await call()
  } catch (e) {
    toast.error((e as Error).message)
    return undefined
  }
}

/* ---- Dismissing spelling suggestions (optimistic: the row hides at once, comes back on failure) ---- */

export const suggestionKey = {
  unknown: (heard: string) => `unknown:${heard}`,
  misheard: (entity: string, heard: string) => `misheard:${entity}:${heard}`,
}

function setHidden(key: string, hidden: boolean) {
  set((s) => {
    const next = { ...s.hidden }
    if (hidden) next[key] = true
    else delete next[key]
    return { hidden: next }
  })
}

/** Unknown name → never suggest it again. Resolves true once saved. */
export async function ignoreWord(word: string): Promise<boolean> {
  const key = suggestionKey.unknown(word)
  setHidden(key, true)
  const ignored = await serialize(() => attempt(() => api.ignoreWords([word])))
  if (!ignored) {
    setHidden(key, false)
    return false
  }
  campaignActions.setIgnored(ignored)
  void campaignActions.refreshSuggestions()
  return true
}

/** Undo `ignoreWord` (also the "Stop ignoring" button). */
export async function unignoreWord(word: string): Promise<boolean> {
  const ignored = await serialize(() => attempt(() => api.unignoreWord(word)))
  if (!ignored) return false
  campaignActions.setIgnored(ignored)
  setHidden(suggestionKey.unknown(word), false)
  void campaignActions.refreshSuggestions()
  return true
}

/**
 * Misheard row → "not a misspelling of" that entry (atomic review). `suggested` is the entry name as
 * the suggestion gave it (the row's key); `entityName` the glossary entry it resolved to.
 */
export async function rejectMisheard(suggested: string, entityName: string, heard: string): Promise<boolean> {
  const key = suggestionKey.misheard(suggested, heard)
  setHidden(key, true)
  const e = await mutateEntity(entityName, (latest) =>
    api.reviewEntity(latest.name, { reject_spellings: [heard] }),
  )
  if (!e) {
    setHidden(key, false)
    return false
  }
  void campaignActions.refreshSuggestions()
  return true
}

/** Undo `rejectMisheard`: drop the spelling from the entry's rejected list (from the latest list). */
export async function unrejectMisheard(
  suggested: string,
  entityName: string,
  heard: string,
): Promise<boolean> {
  const e = await mutateEntity(entityName, (latest) =>
    latest.heard_as_rejected.includes(heard)
      ? api.updateEntity(latest.name, {
          heard_as_rejected: latest.heard_as_rejected.filter((x) => x !== heard),
        })
      : undefined,
  )
  setHidden(suggestionKey.misheard(suggested, heard), false)
  void campaignActions.refreshSuggestions()
  return !!e
}
