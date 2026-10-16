import { act, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { toast } from "sonner"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { applyEvent, useStore } from "@/lib/store"
import { session, snapshotBase } from "@/test/fixtures"
import { bodyOf, httpError, mockFetch } from "@/test/mockFetch"
import { CampaignSwitcher } from "./CampaignSwitcher"

const initial = useStore.getState()
const TWO = [
  { id: "main", name: "Phandelver" },
  { id: "c2", name: "Morvath" },
]

function load(status: "idle" | "running" = "idle") {
  applyEvent({
    type: "snapshot",
    data: {
      ...snapshotBase({ campaigns: TWO }),
      session: session({ status }),
      players: [],
      voices: [],
      messages: [],
    },
  })
}

const picker = () => screen.getByRole("combobox", { name: "Campaign" }) as HTMLSelectElement

beforeEach(() => useStore.setState(initial, true))
afterEach(() => {
  vi.restoreAllMocks()
  toast.dismiss()
})

describe("CampaignSwitcher", () => {
  it("opens another campaign; the snapshot that follows switches the app", async () => {
    const { writes } = mockFetch((c) =>
      c.url === "/api/campaigns/c2/open" ? { campaign: "c2", campaigns: TWO } : undefined,
    )
    load()
    render(<CampaignSwitcher />)
    expect(picker().value).toBe("main")
    fireEvent.change(picker(), { target: { value: "c2" } })
    await waitFor(() => expect(writes().map((c) => c.url)).toEqual(["/api/campaigns/c2/open"]))
    expect(picker().value).toBe("main") // nothing changes until the server's snapshot
    act(() =>
      applyEvent({
        type: "snapshot",
        data: {
          ...snapshotBase({ campaign: "c2", campaigns: TWO }),
          session: session(),
          players: [],
          voices: [],
          messages: [],
        },
      }),
    )
    expect(picker().value).toBe("c2")
  })

  it("can't switch while a session runs", () => {
    load("running")
    render(<CampaignSwitcher />)
    expect(picker()).toBeDisabled()
  })

  it("creates a campaign and opens it", async () => {
    const { writes } = mockFetch((c) =>
      c.url === "/api/campaigns" ? { id: "c3", name: "Avernus" } : { campaign: "c3", campaigns: TWO },
    )
    load()
    render(<CampaignSwitcher />)
    fireEvent.change(picker(), { target: { value: "__new__" } })
    fireEvent.change(await screen.findByRole("textbox", { name: "Campaign name" }), {
      target: { value: " Avernus " },
    })
    fireEvent.click(screen.getByRole("button", { name: "Create and open" }))
    await waitFor(() =>
      expect(writes().map((c) => c.url)).toEqual(["/api/campaigns", "/api/campaigns/c3/open"]),
    )
    expect(bodyOf<{ name: string }>(writes()[0])).toEqual({ name: "Avernus" })
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull())
  })

  it("renames the open campaign", async () => {
    const { writes } = mockFetch(() => ({ id: "main", name: "Lost Mine" }))
    load()
    render(<CampaignSwitcher />)
    fireEvent.click(screen.getByRole("button", { name: "Rename campaign" }))
    const name = await screen.findByRole("textbox", { name: "Campaign name" })
    expect(name).toHaveValue("Phandelver")
    fireEvent.change(name, { target: { value: "Lost Mine" } })
    fireEvent.click(screen.getByRole("button", { name: "Save" }))
    await waitFor(() =>
      expect(writes().map((c) => [c.method, c.url])).toEqual([["PATCH", "/api/campaigns/main"]]),
    )
    act(() =>
      applyEvent({
        type: "campaigns.updated",
        data: { campaign: "main", campaigns: [{ id: "main", name: "Lost Mine" }, TWO[1]] },
      }),
    )
    expect(screen.getByRole("option", { name: "Lost Mine" })).toBeInTheDocument()
  })

  it("renames the campaign the dialog was opened for, even if another one opened meanwhile", async () => {
    const { writes } = mockFetch(() => ({ id: "main", name: "Lost Mine" }))
    load()
    render(<CampaignSwitcher />)
    fireEvent.click(screen.getByRole("button", { name: "Rename campaign" }))
    const name = await screen.findByRole("textbox", { name: "Campaign name" })
    act(() =>
      applyEvent({
        type: "snapshot",
        data: {
          ...snapshotBase({ campaign: "c2", campaigns: TWO }),
          session: session(),
          players: [],
          voices: [],
          messages: [],
        },
      }),
    )
    fireEvent.change(name, { target: { value: "Lost Mine" } })
    fireEvent.click(screen.getByRole("button", { name: "Save" }))
    await waitFor(() => expect(writes().map((c) => c.url)).toEqual(["/api/campaigns/main"]))
  })

  it("shows why a switch was refused", async () => {
    mockFetch(() => httpError(400, "Wait for the campaign jobs to finish before switching campaigns."))
    const error = vi.spyOn(toast, "error")
    load()
    render(<CampaignSwitcher />)
    fireEvent.change(picker(), { target: { value: "c2" } })
    await waitFor(() => expect(error).toHaveBeenCalledWith(expect.stringMatching(/campaign jobs/)))
  })
})
