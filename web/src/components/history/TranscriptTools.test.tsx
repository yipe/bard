import { act, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { VirtuosoMockContext } from "react-virtuoso"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { TooltipProvider } from "@/components/ui/tooltip"
import { useCampaign } from "@/lib/campaignStore"
import { applyEvent, useStore } from "@/lib/store"
import type { Fix, ImprovedInfo, SavedSession, SpellingInfo } from "@/lib/types"
import { campaign, campaignJob, recordMessage, savedSession, sessionRecord } from "@/test/fixtures"
import { mockFetch } from "@/test/mockFetch"
import { SessionViewer } from "./SessionViewer"

const ID = "2026-10-08_193401-eebf"
const RECORD = `/api/sessions/${ID}/transcript.json`
const initialCampaign = useCampaign.getState()
const initialLive = useStore.getState()

/** Fake server: the transcript says `text` and carries `improved` etc. (all mutable by the test). */
function server(state: { text: string; improved?: ImprovedInfo; spelling?: SpellingInfo; fixes?: Fix[] }) {
  return mockFetch((c) => {
    if (c.url === "/api/campaign") return campaign()
    if (c.url === "/api/sessions")
      return [savedSession({ id: ID, replayable: true, restorable: !!state.improved })]
    if (c.url === RECORD) {
      const r = sessionRecord({
        id: ID,
        messages: [recordMessage({ id: "m1", speaker: "Nora", text: state.text, fixes: state.fixes })],
      })
      return { ...r, session: { ...r.session, improved: state.improved, spelling: state.spelling } }
    }
    if (c.url === `/api/sessions/${ID}/improve`)
      return campaignJob({ id: "i1", kind: "improve", title: ID, state: "queued" })
    if (c.url === `/api/sessions/${ID}/spelling`)
      return campaignJob({ id: "s1", kind: "spelling", title: ID, state: "queued" })
    if (c.url === `/api/sessions/${ID}/retranscribe`)
      return campaignJob({ id: "r1", kind: "retranscribe", title: ID, state: "queued" })
    if (c.url === `/api/sessions/${ID}/restore`) {
      state.improved = undefined
      state.text = "Dilara waves."
      return { ok: true }
    }
    return new Response(null, { status: 500 })
  })
}

async function renderViewer(summary: SavedSession) {
  render(
    <VirtuosoMockContext.Provider value={{ viewportHeight: 600, itemHeight: 60 }}>
      <TooltipProvider>
        <SessionViewer summary={summary} onBack={() => {}} />
      </TooltipProvider>
    </VirtuosoMockContext.Provider>,
  )
  await act(async () => {})
}

const recordLoads = (calls: { url: string }[]) => calls.filter((c) => c.url === RECORD).length
const job = (state: "running" | "done" | "error", extra = {}) =>
  applyEvent({
    type: "campaign.job",
    data: campaignJob({ id: "i1", kind: "improve", title: ID, state, ...extra }),
  })

beforeEach(() => {
  useCampaign.setState(initialCampaign, true)
  useStore.setState(initialLive, true)
})
afterEach(() => vi.restoreAllMocks())

describe("Improve transcript", () => {
  it("is hidden when the session has no replayable audio", async () => {
    server({ text: "Dilara waves." })
    await renderViewer(savedSession({ id: ID, replayable: false }))
    expect(screen.queryByRole("button", { name: /Improve transcript/ })).toBeNull()
  })

  it("posts the improve job, shows progress, and reloads the transcript when it's done", async () => {
    const state = { text: "Dilara waves." }
    const { calls, writes } = server(state)
    await renderViewer(savedSession({ id: ID, replayable: true }))
    expect(screen.getByText("Dilara waves.")).toBeInTheDocument()

    fireEvent.click(screen.getByRole("button", { name: /Improve transcript/ }))
    await act(async () => {})
    expect(writes()).toEqual([{ method: "POST", url: `/api/sessions/${ID}/improve`, body: undefined }])
    expect(screen.getByRole("button", { name: /Improve transcript/ })).toBeDisabled()

    act(() => job("running", { done: 40, total: 2020, message: "Paused while a session runs" }))
    expect(screen.getByRole("button", { name: /Improve transcript/ })).toHaveTextContent("40/2020")
    expect(screen.getByTestId("improve-status")).toHaveTextContent("Paused while a session runs")
    expect(recordLoads(calls)).toBe(1)

    state.text = "Elara waves."
    Object.assign(state, { improved: { at: 1_791_600_000, model: "large-v3", changed: 143 } })
    await act(async () => job("done", { done: 2020, total: 2020, message: "143 of 2020 lines changed" }))
    await waitFor(() => expect(screen.getByText("Elara waves.")).toBeInTheDocument())
    expect(recordLoads(calls)).toBe(2)
    expect(screen.getByText(/Improved · 143 lines changed/)).toBeInTheDocument()
    expect(screen.getByRole("button", { name: /Improve transcript/ })).toBeEnabled()
  })

  it("doesn't reload for an improve job that finished before the viewer opened", async () => {
    const { calls } = server({ text: "Dilara waves." })
    useCampaign.setState({
      jobs: { i1: campaignJob({ id: "i1", kind: "improve", title: ID, state: "done" }) },
    })
    await renderViewer(savedSession({ id: ID, replayable: true }))
    await act(async () => job("done"))
    expect(recordLoads(calls)).toBe(1)
  })

  it("shows the job's error message", async () => {
    server({ text: "Dilara waves." })
    await renderViewer(savedSession({ id: ID, replayable: true }))
    act(() => job("running"))
    act(() => job("error", { message: "Whisper model failed to load" }))
    expect(screen.getByRole("alert")).toHaveTextContent("Whisper model failed to load")
  })

  it("Restore original asks first, posts restore, then reloads the transcript and the session", async () => {
    const state = {
      text: "Elara waves.",
      improved: { at: 1_791_600_000, model: "large-v3", changed: 1 } as ImprovedInfo | undefined,
    }
    const { calls, writes } = server(state)
    useStore.setState({
      view: "history",
      viewing: savedSession({ id: ID, replayable: true, restorable: true }),
    })
    await renderViewer(savedSession({ id: ID, replayable: true, restorable: true }))
    expect(screen.getByText(/Improved · 1 line changed/)).toBeInTheDocument()

    fireEvent.click(screen.getByRole("button", { name: /Restore original/ }))
    expect(writes()).toEqual([])
    fireEvent.click(screen.getByRole("button", { name: "Confirm restore" }))
    await waitFor(() => expect(screen.getByText("Dilara waves.")).toBeInTheDocument())
    expect(writes()).toEqual([{ method: "POST", url: `/api/sessions/${ID}/restore`, body: undefined }])
    expect(recordLoads(calls)).toBe(2)
    expect(calls.some((c) => c.url === "/api/sessions")).toBe(true)
    expect(screen.queryByText(/Improved ·/)).toBeNull()
    expect(useStore.getState().viewing?.restorable).toBe(false)
  })

  it("Fix spellings posts the job, shows progress, reloads on done, and shows how many were fixed", async () => {
    const state: { text: string; spelling?: SpellingInfo; fixes?: Fix[] } = { text: "Dilara waves." }
    const { calls, writes } = server(state)
    // No audio: Improve is hidden, Fix spellings still works (text only).
    await renderViewer(savedSession({ id: ID, replayable: false }))
    expect(screen.queryByRole("button", { name: /Improve transcript/ })).toBeNull()

    fireEvent.click(screen.getByRole("button", { name: /Fix spellings/ }))
    await act(async () => {})
    expect(writes()).toEqual([{ method: "POST", url: `/api/sessions/${ID}/spelling`, body: undefined }])

    const spellingJob = (st: "running" | "done", extra = {}) =>
      applyEvent({
        type: "campaign.job",
        data: campaignJob({ id: "s1", kind: "spelling", title: ID, state: st, ...extra }),
      })
    act(() => spellingJob("running", { done: 1, total: 4, message: "Asking the AI about 3 names" }))
    expect(screen.getByRole("button", { name: /Fix spellings/ })).toBeDisabled()
    expect(screen.getByRole("button", { name: /Fix spellings/ })).toHaveTextContent("1/4")
    expect(screen.getByTestId("spelling-status")).toHaveTextContent("Asking the AI about 3 names")

    state.text = "Elara waves."
    state.fixes = [{ start: 0, end: 5, heard: "Dilara" }]
    state.spelling = { at: 1_791_600_000, fixed: 12 }
    await act(async () => spellingJob("done", { done: 4, total: 4, message: "12 spellings fixed" }))
    await waitFor(() => expect(screen.getByText("Elara")).toBeInTheDocument())
    expect(recordLoads(calls)).toBe(2)
    expect(screen.getByText("Elara")).toHaveAttribute("title", 'Heard as "Dilara"')
    expect(screen.getByText("Spellings fixed · 12")).toBeInTheDocument()
    expect(screen.getByRole("button", { name: /Restore original/ })).toBeInTheDocument()
    expect(screen.getByRole("button", { name: /Fix spellings/ })).toBeEnabled()
  })

  it("Retranscribe warns before it posts, because hand-fixed speakers can move", async () => {
    const { writes } = server({ text: "Dilara waves." })
    await renderViewer(savedSession({ id: ID, audio: true, replayable: true }))
    fireEvent.click(screen.getByRole("button", { name: "Retranscribe" }))
    expect(writes()).toEqual([])
    expect(screen.getByText(/copy of this transcript is saved first/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: "Retranscribe anyway" }))
    await act(async () => {})
    expect(writes()).toEqual([{ method: "POST", url: `/api/sessions/${ID}/retranscribe`, body: undefined }])
  })

  it("Fix spellings shows a failed job's message", async () => {
    server({ text: "Dilara waves." })
    await renderViewer(savedSession({ id: ID }))
    act(() =>
      applyEvent({
        type: "campaign.job",
        data: campaignJob({ id: "s1", kind: "spelling", title: ID, state: "error", message: "LLM offline" }),
      }),
    )
    expect(screen.getByRole("alert")).toHaveTextContent("LLM offline")
  })
})
