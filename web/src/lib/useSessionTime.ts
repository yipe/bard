import { useEffect, useState } from "react"
import { useStore } from "./store"

/** Current session seconds, interpolated client-side from the last `session.t` while running. */
export function sessionTimeNow(): number {
  const { session, sessionAt } = useStore.getState()
  if (!session) return 0
  if (session.status !== "running") return session.t
  const rate =
    session.source?.kind === "file" || session.source?.kind === "session" ? session.source.speed : 1
  return session.t + ((performance.now() - sessionAt) / 1000) * rate
}

export function useSessionTime(intervalMs = 500): number {
  const session = useStore((s) => s.session)
  const [, tick] = useState(0)
  useEffect(() => {
    if (session?.status !== "running") return
    const id = setInterval(() => tick((n) => n + 1), intervalMs)
    return () => clearInterval(id)
  }, [session?.status, intervalMs])
  return sessionTimeNow()
}
