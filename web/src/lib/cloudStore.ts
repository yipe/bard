import { create } from "zustand"
import { toast } from "sonner"
import { api } from "./api"
import { campaignActions } from "./campaignStore"
import type { CloudAccount, CloudStatus } from "./types"

/**
 * Troubadour Cloud backup status for the open campaign; from GET /api/cloud and `cloud.updated` events.
 * `account` is who is signed in (the header's avatar menu); null until loaded or when not connected.
 */
export const useCloud = create<{ status: CloudStatus | null; account: CloudAccount | null }>(() => ({
  status: null,
  account: null,
}))

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
  /** `quiet`: a background load (the header's) that shouldn't toast when the server is briefly away. */
  refresh: ({ quiet = false } = {}) => {
    const load = async () => set({ status: await api.cloud() })
    return quiet ? load().catch(() => undefined) : run(load)
  },
  /** Who is signed in, for the header menu. Quiet: with no account the menu just shows initials. */
  loadAccount: async () => {
    try {
      set({ account: await api.cloudAccount() })
    } catch {
      set({ account: null })
    }
  },

  /** Opens the service's sign-in page in a new tab; it comes back to Troubadour, which sends `cloud.updated`.
   * The tab opens during the click (after an await, browsers block pop-ups) and loses its opener, so
   * the sign-in page can't reach back into Troubadour's tab. */
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
  setOptions: (options: { include_transcripts?: boolean; auto_backup?: boolean }) =>
    run(async () => set({ status: await api.cloudOptions(options) })),
  backup: () => run(async () => campaignActions.putJob(await api.cloudBackup())),
  /** Campaigns in Troubadour Cloud to restore from; undefined if the service couldn't be reached (toasted). */
  vaults: () => run(() => api.cloudVaults()),
  restore: (vaultId: string) => run(async () => campaignActions.putJob(await api.cloudRestore(vaultId))),
}

export function applyCloudEvent(ev: { type: "cloud.updated"; data: CloudStatus }) {
  set({ status: ev.data })
}
