import { create } from "zustand"
import { toast } from "sonner"
import { applyAudioEvent } from "./audio"
import { applyCampaignEvent, campaignActions } from "./campaignStore"
import type {
  CampaignInfo,
  LibraryPlayer,
  Message,
  Player,
  SavedSession,
  ServerEvent,
  Session,
  Snapshot,
  SourceList,
  TrainStatus,
  Voice,
} from "./types"

export type View = "setup" | "session" | "history" | "campaign"

interface State {
  connected: boolean
  loaded: boolean
  view: View
  /** Only ever written from WS `snapshot` / `session.updated`. */
  session: Session | null
  /** performance.now() when `session` was received, for client-side clock interpolation */
  sessionAt: number
  /** Id of the open campaign; players, voices, History and the Campaign tab all belong to it. */
  campaign: string
  campaigns: CampaignInfo[]
  players: Player[]
  /** People not playing in the open campaign (their voice training is kept). */
  library: LibraryPlayer[]
  voices: Voice[]
  messages: Message[]
  /** message id -> update counter, used to replay the "updated" flash */
  bumps: Record<string, number>
  train: Record<string, TrainStatus>
  sources: SourceList | null
  focus: { voiceId: string; n: number } | null
  /** Saved session open in the History viewer (navigation only; its data lives in the viewer). */
  viewing: SavedSession | null
}

export const useStore = create<State>(() => ({
  connected: false,
  loaded: false,
  view: "setup",
  session: null,
  sessionAt: 0,
  campaign: "",
  campaigns: [],
  players: [],
  library: [],
  voices: [],
  messages: [],
  bumps: {},
  train: {},
  sources: null,
  focus: null,
  viewing: null,
}))

/** Speaking state lives in its own store so ~4 Hz updates don't touch the chat. */
export const useSpeaking = create<{ voiceId: string | null }>(() => ({ voiceId: null }))

const set = useStore.setState

function upsert<T extends { id: string }>(list: T[], item: T): T[] {
  const i = list.findIndex((x) => x.id === item.id)
  if (i < 0) return [...list, item]
  const next = list.slice()
  next[i] = item
  return next
}

export const actions = {
  setView: (view: View) => set({ view }),
  openSavedSession: (viewing: SavedSession) => set({ view: "history", viewing }),
  closeSavedSession: () => set({ viewing: null }),
  openHistory: () => set({ view: "history", viewing: null }),
  upsertPlayer: (p: Player) => set((s) => ({ players: upsert(s.players, p) })),
  /** A library player joined the open campaign. */
  seated: (p: Player) =>
    set((s) => ({ players: upsert(s.players, p), library: s.library.filter((x) => x.id !== p.id) })),
  dropFromLibrary: (id: string) => set((s) => ({ library: s.library.filter((p) => p.id !== id) })),
  removePlayer: (id: string) => set((s) => ({ players: s.players.filter((p) => p.id !== id) })),
  upsertVoice: (v: Voice) => set((s) => ({ voices: upsert(s.voices, v) })),
  clearTrain: (playerId: string) =>
    set((s) => {
      const train = { ...s.train }
      delete train[playerId]
      return { train }
    }),
  setSources: (sources: SourceList) => set({ sources }),
  focusVoice: (voiceId: string) => set((s) => ({ focus: { voiceId, n: (s.focus?.n ?? 0) + 1 } })),
}

/** The server sends messages in t_start order; only sort (stably) if a snapshot isn't. */
function inServerOrder(messages: Message[]): Message[] {
  for (let i = 1; i < messages.length; i++) {
    if (messages[i].t_start < messages[i - 1].t_start)
      return [...messages].sort((a, b) => a.t_start - b.t_start)
  }
  return messages
}

function applySnapshot(snap: Snapshot) {
  const prev = useStore.getState()
  const switched = prev.loaded && prev.campaign !== snap.campaign
  // Another campaign's open entry or dismissed suggestions mean nothing here.
  if (switched) campaignActions.campaignChanged()
  set((s) => ({
    loaded: true,
    session: snap.session,
    sessionAt: performance.now(),
    campaign: snap.campaign,
    campaigns: snap.campaigns,
    players: snap.players,
    library: snap.library,
    voices: snap.voices,
    messages: inServerOrder(snap.messages),
    bumps: {},
    viewing: switched ? null : s.viewing,
    // First load: land on the session view if one is already running.
    view: s.loaded ? s.view : snap.session.status === "idle" ? "setup" : "session",
  }))
}

let speakingTimer: number | undefined

/**
 * id -> index into `messages`, kept in sync with the array it was built for. Any write to
 * `messages` outside this module (e.g. tests resetting the store) is detected by identity and
 * triggers a rebuild, so lookups stay O(1) on the hot path without ever going stale.
 */
let messageIndex = new Map<string, number>()
let indexedMessages: Message[] | null = null

function indexFor(messages: Message[]): Map<string, number> {
  if (indexedMessages !== messages) {
    messageIndex = new Map(messages.map((m, i) => [m.id, i]))
    indexedMessages = messages
  }
  return messageIndex
}

/** Record that `next` replaced the indexed array with the same id -> index layout. */
function reindexed(next: Message[]): Message[] {
  indexedMessages = next
  return next
}

/**
 * Insert or replace a message, keeping server (t_start) order. New messages almost always
 * belong at the end; an out-of-order one is placed by binary search and forces a reindex.
 */
function addMessage(messages: Message[], m: Message): Message[] {
  const index = indexFor(messages)
  const existing = index.get(m.id)
  if (existing !== undefined) {
    const next = messages.slice()
    next[existing] = m
    return reindexed(next)
  }
  const last = messages[messages.length - 1]
  if (!last || last.t_start <= m.t_start) {
    index.set(m.id, messages.length)
    return reindexed([...messages, m])
  }
  let lo = 0
  let hi = messages.length
  while (lo < hi) {
    const mid = (lo + hi) >> 1
    if (messages[mid].t_start <= m.t_start) lo = mid + 1
    else hi = mid
  }
  return [...messages.slice(0, lo), m, ...messages.slice(lo)]
}

/** Apply one server event to the stores. Exported for tests; `connect()` is the only runtime caller. */
export function applyEvent(ev: ServerEvent) {
  switch (ev.type) {
    case "snapshot":
      return applySnapshot(ev.data)
    case "session.updated":
      return set({ session: ev.data, sessionAt: performance.now() })
    case "player.upserted":
      return actions.upsertPlayer(ev.data)
    case "player.removed":
      return actions.removePlayer(ev.data.id)
    case "library.updated":
      return set({ library: ev.data })
    case "campaigns.updated":
      return set({ campaigns: ev.data.campaigns })
    case "voice.upserted":
      return actions.upsertVoice(ev.data)
    case "voice.removed":
      // On merges the server re-points affected messages via a following `messages.updated`.
      return set((s) => ({ voices: s.voices.filter((v) => v.id !== ev.data.id) }))
    case "speaker.active": {
      clearTimeout(speakingTimer)
      useSpeaking.setState({ voiceId: ev.data.voice_id })
      if (ev.data.voice_id)
        speakingTimer = window.setTimeout(() => useSpeaking.setState({ voiceId: null }), 1500)
      return
    }
    case "message.created":
      return set((s) => ({ messages: addMessage(s.messages, ev.data) }))
    case "messages.updated": {
      const patches = ev.data
      return set((s) => {
        const index = indexFor(s.messages)
        const messages = s.messages.slice()
        const bumps = { ...s.bumps }
        let changed = false
        for (const patch of patches) {
          const i = index.get(patch.id)
          if (i === undefined) continue
          // Fields present in the patch replace the old ones wholesale (text and fixes arrive
          // together and fit each other).
          messages[i] = { ...messages[i], ...patch }
          bumps[patch.id] = (bumps[patch.id] ?? 0) + 1
          changed = true
        }
        return changed ? { messages: reindexed(messages), bumps } : {}
      })
    }
    case "train.status":
      return set((s) => ({ train: { ...s.train, [ev.data.player_id]: ev.data } }))
    case "error":
      toast.error(ev.data.message)
      return
    case "audio.level":
    case "audio.quality":
    case "mic.test":
      // Audio read-outs have their own store/pub-sub; nothing here changes.
      return applyAudioEvent(ev)
    case "campaign.job":
    case "campaign.updated":
      // Campaign memory has its own store; nothing here changes.
      return applyCampaignEvent(ev)
  }
}

let started = false

/** Connect the WebSocket (idempotent). Reconnects with exponential backoff capped at 5 s. */
export function connect() {
  if (started) return
  started = true
  let delay = 500
  const open = () => {
    const proto = location.protocol === "https:" ? "wss" : "ws"
    const ws = new WebSocket(`${proto}://${location.host}/ws`)
    ws.onopen = () => {
      delay = 500
      set({ connected: true })
    }
    ws.onmessage = (e) => {
      try {
        applyEvent(JSON.parse(e.data as string) as ServerEvent)
      } catch (err) {
        console.error("bad server event", err)
      }
    }
    ws.onclose = () => {
      set({ connected: false })
      setTimeout(open, delay)
      delay = Math.min(delay * 2, 5000)
    }
  }
  open()
}
