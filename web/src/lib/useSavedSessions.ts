import { useEffect, useState } from "react"
import { api } from "./api"
import { useStore } from "./store"
import type { SavedSession } from "./types"

export interface SavedSessionsState {
  /** null while the first load is in flight */
  sessions: SavedSession[] | null
  error: string | null
}

/**
 * The open campaign's saved sessions, newest first. Fetched on mount, again whenever a live
 * session finishes (running/stopping -> idle), since it was just saved, and on a campaign switch.
 */
export function useSavedSessions(): SavedSessionsState & { reload: () => void } {
  const [state, setState] = useState<SavedSessionsState>({ sessions: null, error: null })
  const [tick, setTick] = useState(0)
  useEffect(() => {
    let alive = true
    const load = () =>
      api.sessions().then(
        (sessions) => alive && setState({ sessions, error: null }),
        (e: Error) => alive && setState((s) => ({ ...s, error: e.message })),
      )
    void load()
    const unsubscribe = useStore.subscribe((s, prev) => {
      const was = prev.session?.status
      if (s.session?.status === "idle" && (was === "running" || was === "stopping")) void load()
      else if (s.campaign !== prev.campaign) void load()
    })
    return () => {
      alive = false
      unsubscribe()
    }
  }, [tick])
  return { ...state, reload: () => setTick((n) => n + 1) }
}
