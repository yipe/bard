import { act, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { TooltipProvider } from "@/components/ui/tooltip"
import { applyEvent, useStore } from "@/lib/store"
import { player, session, snapshotBase } from "@/test/fixtures"
import { bodyOf, mockFetch } from "@/test/mockFetch"
import { SetupView } from "./SetupView"

const initial = useStore.getState()
const KRIS = { id: "p2", name: "Jun", avatar: null, color: "#3b82f6", trained: true }

function load(status: "idle" | "running" = "idle") {
  applyEvent({
    type: "snapshot",
    data: {
      ...snapshotBase({ library: [KRIS] }),
      session: session({ status }),
      players: [player({ id: "p1", name: "Nora", character: "DM", is_gm: true })],
      voices: [],
      messages: [],
    },
  })
}

function renderSetup() {
  return render(
    <TooltipProvider>
      <SetupView />
    </TooltipProvider>,
  )
}

/** Setup's own calls (sources, mics, past sessions) answer with empty lists. */
function server(write: (url: string) => unknown) {
  return mockFetch((c) => {
    if (c.method !== "GET") return write(c.url)
    if (c.url === "/api/sources") return { files: [], mics: [] }
    return []
  })
}

beforeEach(() => useStore.setState(initial, true))
afterEach(() => vi.restoreAllMocks())

describe("Player library on Setup", () => {
  it("adds a player from another campaign, keeping their training", async () => {
    const { writes } = server(() => player({ ...KRIS, character: "" }))
    load()
    renderSetup()
    fireEvent.click(screen.getByRole("button", { name: "Add Jun" }))
    await waitFor(() => expect(writes().map((c) => c.url)).toEqual(["/api/players/p2/seat"]))
    expect(bodyOf(writes()[0])).toEqual({}) // their old seat (or GM at an empty table) is the server's call
    expect(useStore.getState().players.map((p) => p.name)).toEqual(["Nora", "Jun"])
    expect(useStore.getState().library).toEqual([])
    expect(screen.queryByRole("button", { name: "Add Jun" })).toBeNull()
  })

  it("removing from the campaign keeps the person (bench, not delete)", async () => {
    const { writes } = server(() => undefined)
    load()
    renderSetup()
    fireEvent.click(screen.getByRole("button", { name: "Remove from this campaign" }))
    await waitFor(() =>
      expect(writes().map((c) => [c.method, c.url])).toEqual([["POST", "/api/players/p1/bench"]]),
    )
  })

  it("can't remove a player while a session runs", () => {
    server(() => undefined)
    load("running")
    renderSetup()
    expect(screen.getByRole("button", { name: "Remove from this campaign" })).toBeDisabled()
  })

  it("deletes someone from the library only after a second click", async () => {
    const { writes } = server(() => undefined)
    load()
    renderSetup()
    fireEvent.click(screen.getByRole("button", { name: "Delete Jun and their voice training" }))
    expect(writes()).toEqual([])
    fireEvent.click(screen.getByRole("button", { name: "Keep" }))
    fireEvent.click(screen.getByRole("button", { name: "Delete Jun and their voice training" }))
    fireEvent.click(screen.getByRole("button", { name: "Delete for good" }))
    await waitFor(() =>
      expect(writes().map((c) => [c.method, c.url])).toEqual([["DELETE", "/api/players/p2"]]),
    )
    expect(useStore.getState().library).toEqual([])
  })

  it("follows library.updated events", () => {
    server(() => undefined)
    load()
    renderSetup()
    act(() => applyEvent({ type: "library.updated", data: [] }))
    expect(screen.queryByText("From your other campaigns")).toBeNull()
  })
})
