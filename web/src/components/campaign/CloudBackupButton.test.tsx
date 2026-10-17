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
    last_backup: null,
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
  useCloud.setState({ status: null }, true)
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
      return new Response(JSON.stringify({ detail: "Couldn't reach Bard Cloud" }), { status: 502 })
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

  it("switches transcripts on for this campaign", async () => {
    const { writes } = mockFetch((call) => {
      if (call.url === "/api/cloud") return cloudStatus()
      if (call.url === "/api/cloud/options") return cloudStatus({ include_transcripts: true })
    })
    await openPopover()
    fireEvent.click(screen.getByRole("switch"))
    await act(async () => {})
    expect(writes()).toEqual([
      { method: "PUT", url: "/api/cloud/options", body: { include_transcripts: true } },
    ])
    expect(screen.getByRole("switch")).toBeChecked()
  })

  it("follows cloud.updated events (sign-in finished in the other tab)", async () => {
    mockFetch((call) => {
      if (call.url === "/api/cloud") return cloudStatus({ connected: false })
    })
    await openPopover()
    act(() => applyEvent({ type: "cloud.updated", data: cloudStatus() }))
    expect(screen.getByRole("button", { name: /Back up now/ })).toBeInTheDocument()
  })
})
