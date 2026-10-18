import { act, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { useCampaign } from "@/lib/campaignStore"
import { useCloud } from "@/lib/cloudStore"
import type { CloudStatus } from "@/lib/types"
import { mockFetch } from "@/test/mockFetch"
import { AccountMenu } from "./AccountMenu"

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

const PICTURE = "data:image/png;base64,AAAA"

async function openMenu() {
  render(<AccountMenu />)
  await act(async () => {})
  fireEvent.click(screen.getByRole("button", { name: "Account menu" }))
  await act(async () => {})
}

beforeEach(() => {
  useCampaign.setState(initialCampaign, true)
  useCloud.setState({ status: null, account: null }, true)
})
afterEach(() => vi.restoreAllMocks())

describe("Account menu", () => {
  it("shows the DPRcalc account's avatar and name once connected", async () => {
    const { writes } = mockFetch((call) => {
      if (call.url === "/api/cloud") return cloudStatus()
      if (call.url === "/api/cloud/account") return { email: "gm@example.com", name: "gm", avatar: PICTURE }
    })
    await openMenu()
    expect(screen.getAllByText("gm").length).toBeGreaterThan(0)
    expect(screen.getAllByText("gm@example.com").length).toBeGreaterThan(0)
    expect(screen.getByText(/Signed in with DPRcalc/)).toBeInTheDocument()
    expect(document.querySelector(`img[src="${PICTURE}"]`)).not.toBeNull()
    expect(screen.getByRole("link", { name: /Open Troubadour Cloud/ })).toHaveAttribute(
      "href",
      "https://cloud.test/vaults",
    )
    expect(screen.getByRole("link", { name: /Manage on DPRcalc/ })).toHaveAttribute(
      "href",
      "https://accounts.dprcalc.com/user",
    )
    expect(writes()).toEqual([])
  })

  it("falls back to the initial and the address for a sign-in without a profile", async () => {
    mockFetch((call) => {
      if (call.url === "/api/cloud") return cloudStatus()
      if (call.url === "/api/cloud/account") return { email: "gm@example.com", name: null, avatar: null }
    })
    await openMenu()
    expect(screen.getAllByText("gm@example.com").length).toBeGreaterThan(0)
    expect(screen.getAllByText("G").length).toBeGreaterThan(0)
    expect(document.querySelector("img")).toBeNull()
  })

  it("signed out, offers Sign in and asks the service for nothing else", async () => {
    const tab = { opener: {} as unknown, location: { href: "" }, close: vi.fn() }
    vi.spyOn(window, "open").mockReturnValue(tab as unknown as Window)
    const { writes, calls } = mockFetch((call) => {
      if (call.url === "/api/cloud") return cloudStatus({ connected: false })
      if (call.url === "/api/cloud/connect") return { authorize_url: "https://auth.test/oauth/authorize?x=1" }
    })
    await openMenu()
    expect(calls.map((c) => c.url)).not.toContain("/api/cloud/account")
    fireEvent.click(screen.getByRole("button", { name: "Sign in with DPRcalc" }))
    await act(async () => {})
    expect(writes()).toEqual([{ method: "POST", url: "/api/cloud/connect", body: undefined }])
    expect(tab.location.href).toBe("https://auth.test/oauth/authorize?x=1")
  })

  it("disconnect forgets the account", async () => {
    const { writes } = mockFetch((call) => {
      if (call.url === "/api/cloud") return cloudStatus()
      if (call.url === "/api/cloud/account") return { email: "gm@example.com", name: "gm", avatar: null }
      if (call.url === "/api/cloud/disconnect") return cloudStatus({ connected: false })
    })
    await openMenu()
    fireEvent.click(screen.getByRole("button", { name: "Disconnect" }))
    await act(async () => {})
    expect(writes()).toEqual([{ method: "POST", url: "/api/cloud/disconnect", body: undefined }])
    expect(useCloud.getState().account).toBeNull()
  })
})
