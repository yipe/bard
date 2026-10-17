import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { campaignActions, useCampaign } from "@/lib/campaignStore"
import { applyEvent } from "@/lib/store"
import type { CampaignAnswer } from "@/lib/types"
import { campaign } from "@/test/fixtures"
import { mockFetch } from "@/test/mockFetch"
import { AskPanel } from "./AskPanel"

const initial = useCampaign.getState()
const SID = "2026-10-08_193401-eebf"

function answer(a: Partial<CampaignAnswer> = {}): CampaignAnswer {
  return {
    id: "a1",
    question: "Who did we suspect has the diamond?",
    state: "queued",
    answer: "",
    found: false,
    sources: [],
    message: "",
    created: 1,
    ...a,
  }
}

const done = answer({
  state: "done",
  answer: "Nora suspected Magenta: she was in the vault that night.",
  found: true,
  sources: [
    {
      id: "S1",
      kind: "transcript",
      label: "2026-10-08 The Desert Road · 00:15",
      session: SID,
      t: 15,
      path: null,
      entry: null,
      excerpt: "[00:15] Nora: I bet Magenta has the gem.",
    },
    {
      id: "S2",
      kind: "entry",
      label: "People/Magenta.md",
      session: null,
      t: null,
      path: "People/Magenta.md",
      entry: "Magenta",
      excerpt: "Magenta (NPC)",
    },
    {
      id: "S3",
      kind: "note",
      label: "Notes/Session 3",
      session: null,
      t: null,
      path: "Notes/Session 3.md",
      entry: null,
      excerpt: "The duke keeps it.",
    },
  ],
})

beforeEach(() => useCampaign.setState({ ...initial, campaign: campaign() }, true))
afterEach(() => vi.restoreAllMocks())

describe("AskPanel", () => {
  it("asks, shows progress, then the answer with its sources", async () => {
    const { writes, calls } = mockFetch((call) => {
      if (call.url === "/api/campaign/ask") return answer()
      if (call.url === "/api/sessions") return []
      return new Response(null, { status: 500 })
    })
    render(<AskPanel />)
    const box = screen.getByRole("textbox", { name: /Ask about the campaign/ })
    expect(screen.getByRole("button", { name: "Ask" })).toBeDisabled()
    fireEvent.change(box, { target: { value: "  Who did we suspect has the diamond? " } })
    fireEvent.keyDown(box, { key: "Enter" })
    await waitFor(() => expect(screen.getByText("Waiting for the model…")).toBeInTheDocument())
    expect(writes()).toEqual([
      { method: "POST", url: "/api/campaign/ask", body: { question: "Who did we suspect has the diamond?" } },
    ])
    expect(box).toHaveValue("")

    act(() => applyEvent({ type: "campaign.answer", data: answer({ state: "searching" }) }))
    expect(screen.getByText("Searching transcripts and notes…")).toBeInTheDocument()
    act(() => applyEvent({ type: "campaign.answer", data: done }))
    expect(screen.getByText(/Nora suspected Magenta/)).toBeInTheDocument()

    const sources = within(screen.getByRole("list", { name: "Sources" }))
    expect(sources.getByRole("link", { name: /Notes\/Session 3/ })).toHaveAttribute(
      "href",
      "obsidian://open?vault=abc123&file=Notes%2FSession%203.md",
    )
    fireEvent.click(sources.getByRole("button", { name: /Magenta/ }))
    expect(useCampaign.getState().selected).toBe("Magenta")
    fireEvent.click(sources.getByRole("button", { name: /The Desert Road · 00:15/ }))
    await waitFor(() => expect(calls.some((c) => c.url === "/api/sessions")).toBe(true))
  })

  it("shows an error and asks again on request", async () => {
    const { writes } = mockFetch(() => answer({ id: "a2", created: 2 }))
    useCampaign.setState({ answers: { a1: answer({ state: "error", message: "Can't reach LM Studio." }) } })
    render(<AskPanel />)
    expect(screen.getByRole("alert")).toHaveTextContent("Couldn't answer: Can't reach LM Studio.")
    fireEvent.click(screen.getByRole("button", { name: "Ask again" }))
    await waitFor(() => expect(writes()).toHaveLength(1))
    expect(writes()[0].body).toEqual({ question: "Who did we suspect has the diamond?" })
  })

  it("shows a not-found answer without sources", () => {
    useCampaign.setState({ answers: { a1: answer({ state: "done", answer: "I couldn't find anything." }) } })
    render(<AskPanel />)
    expect(screen.getByText("I couldn't find anything.")).toBeInTheDocument()
    expect(screen.queryByRole("list", { name: "Sources" })).toBeNull()
  })
})

describe("answers in the campaign store", () => {
  it("a refetch doesn't undo an answer that already ended, and a switch clears them", async () => {
    mockFetch(() => campaign({ answers: [answer({ state: "thinking" }), answer({ id: "a3", created: 3 })] }))
    useCampaign.setState({ answers: { a1: done, gone: answer({ id: "gone" }) } })
    await campaignActions.refresh()
    const answers = useCampaign.getState().answers
    expect(Object.keys(answers).sort()).toEqual(["a1", "a3"]) // "gone" belongs to another campaign
    expect(answers.a1.state).toBe("done")
    campaignActions.campaignChanged()
    expect(useCampaign.getState().answers).toEqual({})
  })
})
