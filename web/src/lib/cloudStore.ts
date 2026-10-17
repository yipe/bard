import { create } from "zustand"
import { toast } from "sonner"
import { api } from "./api"
import { campaignActions } from "./campaignStore"
import type { CloudStatus } from "./types"

/** Bard Cloud backup status for the open campaign; from GET /api/cloud and `cloud.updated` events. */
export const useCloud = create<{ status: CloudStatus | null }>(() => ({ status: null }))

const set = useCloud.setState

async function run<T>(work: () => Promise<T>): Promise<T | undefined> {
  try {
    return await work()
  } catch (e) {
    toast.error(e instanceof Error ? e.message : String(e))
    return undefined
  }
}

export const cloudActions = {
  refresh: () => run(async () => set({ status: await api.cloud() })),
  /** Opens the service's sign-in page in a new tab; it comes back to Bard, which sends `cloud.updated`.
   * The tab opens during the click (after an await, browsers block pop-ups) and loses its opener, so
   * the sign-in page can't reach back into Bard's tab. */
  connect: () => {
    const tab = window.open("about:blank", "_blank")
    if (tab) tab.opener = null
    return run(async () => {
      try {
        const { authorize_url } = await api.cloudConnect()
        if (tab) tab.location.href = authorize_url
        else window.location.href = authorize_url
      } catch (e) {
        tab?.close()
        throw e
      }
    })
  },
  disconnect: () => run(async () => set({ status: await api.cloudDisconnect() })),
  setIncludeTranscripts: (on: boolean) => run(async () => set({ status: await api.cloudOptions(on) })),
  backup: () => run(async () => campaignActions.putJob(await api.cloudBackup())),
}

export function applyCloudEvent(ev: { type: "cloud.updated"; data: CloudStatus }) {
  set({ status: ev.data })
}
