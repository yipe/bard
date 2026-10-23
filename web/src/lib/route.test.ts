import { act } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it } from "vitest"
import { savedSession, session, snapshotBase } from "@/test/fixtures"
import { mockFetch } from "@/test/mockFetch"
import { campaignActions, useCampaign } from "./campaignStore"
import { parsePath, routePath, startRouting } from "./route"
import { actions, applyEvent, useStore } from "./store"

const initialStore = useStore.getState()
const initialCampaign = useCampaign.getState()
let stop: () => void = () => {}

beforeEach(() => {
  useStore.setState(initialStore, true)
  useCampaign.setState(initialCampaign, true)
})
afterEach(() => stop())

/** Open the app at `path`, start routing, then deliver the first snapshot (idle: lands on Setup). */
function boot(path: string) {
  window.history.replaceState(null, "", path)
  stop = startRouting()
  applyEvent({
    type: "snapshot",
    data: { ...snapshotBase(), session: session({ status: "idle" }), players: [], voices: [], messages: [] },
  })
}

const flush = () => act(async () => {})
const where = () => window.location.pathname
const state = () => useStore.getState()

describe("parsePath / routePath", () => {
  it("round-trips every view and its argument, escaping names", () => {
    for (const r of [
      { view: "setup" },
      { view: "session" },
      { view: "history" },
      { view: "history", session: "2026-10-15_193130-762b" },
      { view: "campaign" },
      { view: "campaign", entry: "St. Cuthbert / 100% ok?" },
      { view: "ask" },
    ] as const) {
      expect(parsePath(routePath(r))).toEqual(r)
    }
    expect(routePath({ view: "campaign", entry: "a/b" })).toBe("/campaign/a%2Fb")
  })

  it("lets the app pick for / and unknown paths, and drops bad arguments", () => {
    expect(parsePath("/")).toBeNull()
    expect(parsePath("/nope/x")).toBeNull()
    expect(parsePath("/campaign/%E0%A4%A")).toEqual({ view: "campaign" })
    expect(parsePath("/history/a/b")).toEqual({ view: "history" })
    expect(parsePath("/setup/extra")).toEqual({ view: "setup" })
  })
})

describe("startRouting", () => {
  it("/ lands where the snapshot says and shows it in the URL", () => {
    boot("/")
    expect(state().view).toBe("setup")
    expect(where()).toBe("/setup")
  })

  it("a deep link wins over the landing view", () => {
    boot("/campaign/Elara")
    expect(state().view).toBe("campaign")
    expect(useCampaign.getState().selected).toBe("Elara")
    expect(where()).toBe("/campaign/Elara")
  })

  it("navigation pushes history entries; back/forward applies them", () => {
    boot("/setup")
    const start = window.history.length
    act(() => actions.setView("campaign"))
    act(() => campaignActions.select("Tarsis"))
    expect(where()).toBe("/campaign/Tarsis")
    act(() => actions.openSavedSession(savedSession({ id: "s1" })))
    expect(where()).toBe("/history/s1")
    expect(window.history.length).toBe(start + 3)

    // What the browser does on Back: the URL changes, then popstate fires.
    window.history.replaceState(null, "", "/campaign/Tarsis")
    act(() => window.dispatchEvent(new PopStateEvent("popstate")))
    expect(state().view).toBe("campaign")
    expect(useCampaign.getState().selected).toBe("Tarsis")
    // Viewing the same saved session again doesn't refetch it.
    window.history.replaceState(null, "", "/history/s1")
    act(() => window.dispatchEvent(new PopStateEvent("popstate")))
    expect(state().view).toBe("history")
    expect(state().viewing?.id).toBe("s1")
    expect(window.history.length).toBe(start + 3)
  })

  it("a saved-session link loads its summary and opens the viewer", async () => {
    const { calls } = mockFetch(({ url }) =>
      url === "/api/sessions" ? [savedSession({ id: "s2" })] : undefined,
    )
    boot("/history/s2")
    expect(where()).toBe("/history/s2") // kept while the summary loads
    await flush()
    expect(state().viewing?.id).toBe("s2")
    expect(where()).toBe("/history/s2")
    expect(calls.map((c) => c.url)).toEqual(["/api/sessions"])
  })

  it("a link to a session that isn't in this campaign falls back to the list", async () => {
    mockFetch(() => [savedSession({ id: "other" })])
    boot("/history/gone")
    await flush()
    expect(state().view).toBe("history")
    expect(state().viewing).toBeNull()
    expect(where()).toBe("/history")
  })

  it("leaving before the summary arrives doesn't yank the user back", async () => {
    mockFetch(() => [savedSession({ id: "s3" })])
    boot("/history/s3")
    act(() => actions.setView("setup"))
    await flush()
    expect(state().view).toBe("setup")
    expect(state().viewing).toBeNull()
    expect(where()).toBe("/setup")
  })
})
