import { act, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { useCampaign } from "@/lib/campaignStore"
import { useCloud } from "@/lib/cloudStore"
import { applyEvent } from "@/lib/store"
import type { CloudStatus } from "@/lib/types"
import { campaignJob } from "@/test/fixtures"
import { mockFetch } from "@/test/mockFetch"
import { CloudBackupButton } from "./CloudBackupButton"

const initialCampaign = useCampaign.getState()

function cloudStatus(over: Partial<CloudStatus> = {}): CloudStatus {
  return {
    url: "https://cloud.test",
    connected: true,
    campaign: "main",
    include_transcripts: false,
    auto_backup: true,
    last_backup: null,
    last_error: null,
    ...over,
  }
}

async function openPopover() {
  render(<CloudBackupButton />)
  fireEvent.click(screen.getByRole("button", { name: /Cloud backup/ }))
  await act(async () => {})
}

beforeEach(() => {
  useCampaign.setState(initialCampaign, true)
  useCloud.setState({ status: null, account: null }, true)
})
afterEach(() => vi.restoreAllMocks())

describe("Cloud backup", () => {
  it("offers Connect when not connected, and opens the sign-in page in a new tab", async () => {
    const tab = { opener: {} as unknown, location: { href: "" }, close: vi.fn() }
    vi.spyOn(window, "open").mockReturnValue(tab as unknown as Window)
    const { writes } = mockFetch((call) => {
      if (call.url === "/api/cloud") return cloudStatus({ connected: false })
      if (call.url === "/api/cloud/connect") return { authorize_url: "https://auth.test/oauth/authorize?x=1" }
    })
    await openPopover()
    expect(screen.getByText(/Nothing leaves this Mac until you connect/)).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: /Back up now/ })).toBeNull()

    fireEvent.click(screen.getByRole("button", { name: "Connect" }))
    await act(async () => {})
    expect(writes()).toEqual([{ method: "POST", url: "/api/cloud/connect", body: undefined }])
    expect(tab.opener).toBeNull()
    expect(tab.location.href).toBe("https://auth.test/oauth/authorize?x=1")
  })

  it("closes the blank tab when connecting fails", async () => {
    const tab = { opener: null, location: { href: "" }, close: vi.fn() }
    vi.spyOn(window, "open").mockReturnValue(tab as unknown as Window)
    mockFetch((call) => {
      if (call.url === "/api/cloud") return cloudStatus({ connected: false })
      return new Response(JSON.stringify({ detail: "Couldn't reach Troubadour Cloud" }), { status: 502 })
    })
    await openPopover()
    fireEvent.click(screen.getByRole("button", { name: "Connect" }))
    await act(async () => {})
    expect(tab.close).toHaveBeenCalled()
  })

  it("shows the last backup and backs up as a job", async () => {
    const at = Date.UTC(2026, 9, 16, 20, 0) / 1000
    const last = {
      at,
      snapshot: "s1",
      files: 42,
      bytes: 1000,
      uploaded: 3,
      unchanged: false,
      skipped: ["map.png: larger than 20 MB"],
      quota: { used_bytes: 1000, limit_bytes: 10000, after_bytes: 1000 },
      fingerprint: "f",
    }
    const { writes } = mockFetch((call) => {
      if (call.url === "/api/cloud") return cloudStatus({ last_backup: last })
      if (call.url === "/api/cloud/backup")
        return campaignJob({ id: "b1", kind: "backup", title: "My campaign" })
    })
    await openPopover()
    expect(screen.getByText(/42 files/)).toBeInTheDocument()
    expect(screen.getByText("1 files left out")).toBeInTheDocument()

    fireEvent.click(screen.getByRole("button", { name: /Back up now/ }))
    await act(async () => {})
    expect(writes()).toEqual([{ method: "POST", url: "/api/cloud/backup", body: undefined }])
    expect(screen.getByRole("button", { name: /Back up now/ })).toBeDisabled()

    act(() =>
      applyEvent({ type: "campaign.job", data: campaignJob({ id: "b1", kind: "backup", state: "done" }) }),
    )
    expect(screen.getByRole("button", { name: /Back up now/ })).toBeEnabled()
  })

  it("switches transcripts on and automatic backups off for this campaign", async () => {
    const { writes } = mockFetch((call) => {
      if (call.url === "/api/cloud") return cloudStatus()
      if (call.url === "/api/cloud/options") return cloudStatus({ ...(call.body as object) })
    })
    await openPopover()
    expect(screen.getByRole("switch", { name: /Back up automatically/ })).toBeChecked()
    fireEvent.click(screen.getByRole("switch", { name: /Include transcripts/ }))
    await act(async () => {})
    fireEvent.click(screen.getByRole("switch", { name: /Back up automatically/ }))
    await act(async () => {})
    expect(writes()).toEqual([
      { method: "PUT", url: "/api/cloud/options", body: { include_transcripts: true } },
      { method: "PUT", url: "/api/cloud/options", body: { auto_backup: false } },
    ])
    expect(screen.getByRole("switch", { name: /Back up automatically/ })).not.toBeChecked()
  })

  it("shows a failed backup until a later one succeeds", async () => {
    const at = Date.UTC(2026, 9, 16, 20, 0) / 1000
    const last = {
      at,
      snapshot: "s1",
      files: 4,
      bytes: 10,
      uploaded: 0,
      unchanged: true,
      skipped: [],
      quota: { used_bytes: 0, limit_bytes: 1, after_bytes: 0 },
      fingerprint: "f",
    }
    mockFetch((call) => {
      if (call.url === "/api/cloud")
        return cloudStatus({ last_backup: last, last_error: { at: at + 60, message: "offline" } })
    })
    await openPopover()
    expect(screen.getByRole("alert")).toHaveTextContent("offline")
    act(() =>
      applyEvent({
        type: "cloud.updated",
        data: cloudStatus({
          last_backup: { ...last, at: at + 120 },
          last_error: { at: at + 60, message: "offline" },
        }),
      }),
    )
    expect(screen.queryByRole("alert")).toBeNull()
  })

  it("restores this campaign by default, from the campaigns backed up", async () => {
    const vaults = [
      { id: "v-other", name: "Old table", campaign_id: "c1", head_snapshot: "s9", created_at: "" },
      {
        id: "v-main",
        name: "The Sunken Gate",
        campaign_id: "main",
        head_snapshot: "s1",
        created_at: "",
      },
      { id: "v-empty", name: "Never backed up", campaign_id: "c2", head_snapshot: null, created_at: "" },
    ]
    const { writes } = mockFetch((call) => {
      if (call.url === "/api/cloud") return cloudStatus()
      if (call.url === "/api/cloud/vaults") return vaults
      if (call.url === "/api/cloud/restore")
        return campaignJob({ id: "r1", kind: "restore", title: "The Sunken Gate" })
    })
    await openPopover()
    fireEvent.click(screen.getByRole("button", { name: /Restore…/ }))
    await act(async () => {})
    const select = screen.getByLabelText("Restore campaign") as HTMLSelectElement
    expect(select.value).toBe("v-main")
    expect([...select.options].map((o) => o.textContent)).toEqual([
      "Old table",
      "The Sunken Gate (this campaign)",
    ])
    expect(screen.getByText(/Nothing is deleted/)).toBeInTheDocument()

    fireEvent.click(screen.getByRole("button", { name: "Restore" }))
    await act(async () => {})
    expect(writes()).toEqual([{ method: "POST", url: "/api/cloud/restore", body: { vault_id: "v-main" } }])
    expect(screen.getByRole("button", { name: /Restore…/ })).toBeDisabled() // until the job ends
    act(() =>
      applyEvent({ type: "campaign.job", data: campaignJob({ id: "r1", kind: "restore", state: "done" }) }),
    )
    expect(screen.getByRole("button", { name: /Restore…/ })).toBeEnabled()
  })

  it("follows cloud.updated events (sign-in finished in the other tab)", async () => {
    mockFetch((call) => {
      if (call.url === "/api/cloud") return cloudStatus({ connected: false })
    })
    await openPopover()
    act(() => applyEvent({ type: "cloud.updated", data: cloudStatus() }))
    expect(screen.getByRole("button", { name: /Back up now/ })).toBeInTheDocument()
  })

  describe("the header button", () => {
    const backup = (minutesAgo: number) => ({
      at: Date.now() / 1000 - minutesAgo * 60,
      snapshot: "s1",
      files: 4,
      bytes: 10,
      uploaded: 1,
      unchanged: false,
      skipped: [],
      quota: { used_bytes: 0, limit_bytes: 1, after_bytes: 0 },
      fingerprint: "f",
    })
    const button = () => screen.getByRole("button", { name: /Cloud backup/ })

    it("loads by itself and says when the campaign was last backed up", async () => {
      const { calls } = mockFetch((call) => {
        // 5.5, not 5: exactly 300 s can come out as 299.99… after float division.
        if (call.url === "/api/cloud") return cloudStatus({ last_backup: backup(5.5) })
      })
      render(<CloudBackupButton />)
      await act(async () => {})
      expect(calls.map((c) => c.url)).toEqual(["/api/cloud"]) // without opening the popover
      expect(button()).toHaveTextContent("Backed up 5 min ago")
      expect(button()).toHaveAttribute("title", expect.stringMatching(/^Last backup /))
    })

    it("says so when the newest backup failed, or none has run, or it isn't connected", async () => {
      const failed = cloudStatus({
        last_backup: backup(30),
        last_error: { at: Date.now() / 1000, message: "offline" },
      })
      mockFetch((call) => {
        if (call.url === "/api/cloud") return failed
      })
      render(<CloudBackupButton />)
      await act(async () => {})
      expect(button()).toHaveTextContent("Backup failed")
      act(() => applyEvent({ type: "cloud.updated", data: cloudStatus() }))
      expect(button()).toHaveTextContent("Not backed up yet")
      act(() => applyEvent({ type: "cloud.updated", data: cloudStatus({ connected: false }) }))
      expect(button()).toHaveTextContent(/^\s*Cloud backup$/)
    })

    it("stays quiet if the local server can't be reached", async () => {
      mockFetch(() => new Response(null, { status: 500 }))
      render(<CloudBackupButton />)
      await act(async () => {})
      expect(button()).toHaveTextContent("Cloud backup")
    })
  })
})
