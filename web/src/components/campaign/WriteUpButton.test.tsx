import { act, fireEvent, render, screen } from "@testing-library/react"
import { VirtuosoMockContext } from "react-virtuoso"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { SessionViewer } from "@/components/history/SessionViewer"
import { TooltipProvider } from "@/components/ui/tooltip"
import { useCampaign } from "@/lib/campaignStore"
import { applyEvent } from "@/lib/store"
import type { Campaign } from "@/lib/types"
import { campaign, campaignJob, savedSession, sessionRecord } from "@/test/fixtures"
import { mockFetch } from "@/test/mockFetch"

const ID = "2026-10-08_193401-eebf"
const initial = useCampaign.getState()

function server(c: Campaign) {
  return mockFetch((call) => {
    if (call.url === "/api/campaign") return c
    if (call.url === `/api/sessions/${ID}/transcript.json`) return sessionRecord({ id: ID })
    if (call.url === `/api/campaign/writeup/${ID}`)
      return campaignJob({ id: "w1", kind: "writeup", title: ID })
    return new Response(null, { status: 500 })
  })
}

async function renderViewer() {
  render(
    <VirtuosoMockContext.Provider value={{ viewportHeight: 600, itemHeight: 60 }}>
      <TooltipProvider>
        <SessionViewer summary={savedSession({ id: ID })} onBack={() => {}} />
      </TooltipProvider>
    </VirtuosoMockContext.Provider>,
  )
  await act(async () => {})
}

beforeEach(() => useCampaign.setState(initial, true))
afterEach(() => vi.restoreAllMocks())

describe("History viewer: write up for campaign", () => {
  it("posts the write-up and shows its progress until the job ends", async () => {
    const { writes } = server(campaign())
    await renderViewer()
    const button = screen.getByRole("button", { name: /Write up for campaign/ })
    expect(screen.queryByRole("link", { name: /Session page/ })).toBeNull()

    fireEvent.click(button)
    await act(async () => {})
    expect(writes()).toEqual([{ method: "POST", url: `/api/campaign/writeup/${ID}`, body: undefined }])
    expect(screen.getByRole("button", { name: /Write up for campaign/ })).toBeDisabled()

    act(() =>
      applyEvent({
        type: "campaign.job",
        data: campaignJob({ id: "w1", kind: "writeup", title: ID, state: "running", done: 2, total: 5 }),
      }),
    )
    expect(screen.getByRole("button", { name: /Write up for campaign/ })).toHaveTextContent("2/5")

    // A different session's write-up doesn't affect this button.
    act(() =>
      applyEvent({
        type: "campaign.job",
        data: campaignJob({ id: "w2", kind: "writeup", title: "other", state: "running" }),
      }),
    )
    act(() =>
      applyEvent({
        type: "campaign.job",
        data: campaignJob({ id: "w1", kind: "writeup", title: ID, state: "done" }),
      }),
    )
    expect(screen.getByRole("button", { name: /Write up for campaign/ })).toBeEnabled()
  })

  it("offers 'Write up again' and a session-page link once written up", async () => {
    const page = "Sessions/2026-10-08 The Desert Road.md"
    server(campaign({ obsidian: { id: "543fe3e0024d1d47", folder: "" }, written_up: { [ID]: page } }))
    await renderViewer()
    expect(screen.getByRole("button", { name: /Write up again/ })).toBeInTheDocument()
    expect(screen.getByRole("link", { name: /Session page/ })).toHaveAttribute(
      "href",
      `obsidian://open?vault=543fe3e0024d1d47&file=${encodeURIComponent(page)}`,
    )
  })

  it("explains instead of linking when Obsidian doesn't know the campaign folder", async () => {
    server(campaign({ vault: "/V", obsidian: null, written_up: { [ID]: "Sessions/x.md" } }))
    await renderViewer()
    expect(screen.queryByRole("link", { name: /Session page/ })).toBeNull()
    const hint = screen.getByText(/open the folder in Obsidian first/)
    expect(hint).toHaveAttribute("title", expect.stringContaining("Open folder as vault → /V"))
  })
})
