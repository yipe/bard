import { toast } from "sonner"
import { api } from "./api"
import { campaignActions, useCampaign } from "./campaignStore"
import { actions, useStore, type View } from "./store"
import type { SavedSession } from "./types"

/**
 * Where the UI is, as a URL path: `/setup`, `/session`, `/history[/<session id>]`,
 * `/campaign[/<entry name>]`, `/ask`. The stores stay the source of truth; the URL mirrors them, and a
 * URL (deep link, reload, back/forward) is applied to them.
 */
export interface Route {
  view: View
  session?: string
  entry?: string
}

const VIEWS: readonly string[] = ["setup", "session", "history", "campaign", "ask"] satisfies View[]

/** null for `/` and unknown paths: the app picks the view. */
export function parsePath(path: string): Route | null {
  const [first, ...rest] = path.split("/").filter(Boolean)
  if (!VIEWS.includes(first)) return null
  const view = first as View
  let arg: string | undefined
  try {
    arg = rest.length === 1 ? decodeURIComponent(rest[0]) : undefined
  } catch {
    arg = undefined // malformed escape: drop it, keep the view
  }
  if (arg && view === "history") return { view, session: arg }
  if (arg && view === "campaign") return { view, entry: arg }
  return { view }
}

export function routePath(r: Route): string {
  const arg = r.view === "history" ? r.session : r.view === "campaign" ? r.entry : undefined
  return arg ? `/${r.view}/${encodeURIComponent(arg)}` : `/${r.view}`
}

/** Saved session named by the URL while its summary loads; cleared once the user moves on. */
let pending: string | null = null
/** Set while a URL is being applied, so the intermediate store states aren't pushed as history. */
let applying = false

function currentRoute(): Route {
  const { view, viewing } = useStore.getState()
  if (view === "history") {
    const session = viewing?.id ?? pending
    return session ? { view, session } : { view }
  }
  if (view === "campaign") {
    const entry = useCampaign.getState().selected
    return entry ? { view, entry } : { view }
  }
  return { view }
}

function sync(mode: "push" | "replace") {
  const path = routePath(currentRoute())
  if (path === window.location.pathname) return
  if (mode === "push") window.history.pushState(null, "", path)
  else window.history.replaceState(null, "", path)
}

function apply(r: Route) {
  applying = true
  try {
    pending = null
    if (r.view === "history" && r.session) {
      if (useStore.getState().viewing?.id === r.session) actions.setView("history")
      else void openById(r.session)
    } else if (r.view === "history") actions.openHistory()
    else actions.setView(r.view)
    if (r.view === "campaign") campaignActions.select(r.entry ?? null)
  } finally {
    applying = false
  }
}

/** Open a saved session in History by id (e.g. from an answer's source). */
export function openSessionById(id: string) {
  void openById(id)
}

/** The viewer needs the session's summary, which only the list endpoint returns. */
async function openById(id: string) {
  pending = id
  actions.openHistory()
  let found: SavedSession | undefined
  let error: string | null = null
  try {
    found = (await api.sessions()).find((s) => s.id === id)
  } catch (e) {
    error = (e as Error).message
  }
  if (pending !== id) return // the user went somewhere else meanwhile
  pending = null
  if (found) return actions.openSavedSession(found)
  toast.error(error ? `Couldn't load saved sessions: ${error}` : "That saved session isn't in this campaign.")
  sync("replace")
}

/** Mirror navigation into the URL and apply the URL on load and back/forward. Returns a cleanup. */
export function startRouting(): () => void {
  let started = false
  const applyLocation = () => {
    const r = parsePath(window.location.pathname)
    if (r) apply(r)
    sync("replace")
  }
  const onStoreChange = () => {
    if (applying || !useStore.getState().loaded) return
    if (!started) {
      // Wait for the first snapshot: it picks the landing view, which a URL then overrides.
      started = true
      return applyLocation()
    }
    const { view, viewing } = useStore.getState()
    if (view !== "history" || viewing) pending = null
    sync("push")
  }
  const unsubscribe = [useStore.subscribe(onStoreChange), useCampaign.subscribe(onStoreChange)]
  window.addEventListener("popstate", applyLocation)
  onStoreChange()
  return () => {
    unsubscribe.forEach((u) => u())
    window.removeEventListener("popstate", applyLocation)
    pending = null
  }
}
