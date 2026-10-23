import { act, fireEvent, render, screen, within } from "@testing-library/react"
import { VirtuosoMockContext } from "react-virtuoso"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { TooltipProvider } from "@/components/ui/tooltip"
import { dateTime } from "@/lib/format"
import { PLAYER_PALETTE } from "@/lib/record"
import { applyEvent, useStore } from "@/lib/store"
import type { SavedSession, SessionRecord } from "@/lib/types"
import {
  message,
  player,
  recordMessage,
  savedSession,
  session,
  sessionRecord,
  snapshotBase,
} from "@/test/fixtures"
import { HistoryView } from "./HistoryView"

const ID = "2026-10-08_193401-eebf"
const RECORD_URL = `/api/sessions/${ID}/transcript.json`

const record: SessionRecord = sessionRecord({
  id: ID,
  players: [
    { id: "p2", name: "Priya", character: "Pip", is_gm: false, color: "#ec4899" },
    { id: "p1", name: "Nora", character: "DM", is_gm: true }, // saved before colors were stored
  ],
  messages: [
    recordMessage({
      id: "m1",
      t_start: 0,
      t_end: 30,
      speaker: "Nora",
      player_id: "p1",
      text: "Roll initiative, three.",
    }),
    recordMessage({
      id: "m2",
      t_start: 30,
      t_end: 40,
      speaker: "Priya",
      player_id: "p2",
      character: "Pip",
      text: "I hit for 8 damage",
    }),
    recordMessage({ id: "m3", t_start: 40, t_end: 50, speaker: "Voice 38", text: "Who is that?" }),
  ],
})

const initial = useStore.getState()

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } })
}

/** Route the API: the saved-session list and one transcript. */
function mockApi(list: SavedSession[], transcript: () => Response = () => json(record)) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const url = String(input)
    if (url === "/api/sessions") return json(list)
    if (url === RECORD_URL) return transcript()
    return json({ detail: `unexpected ${url}` }, 500)
  })
}

const flush = () => act(async () => {})

function renderHistory() {
  return render(
    <VirtuosoMockContext.Provider value={{ viewportHeight: 600, itemHeight: 60 }}>
      <TooltipProvider>
        <HistoryView />
      </TooltipProvider>
    </VirtuosoMockContext.Provider>,
  )
}

async function openViewer(summary: SavedSession) {
  const fetchSpy = mockApi([summary])
  const utils = renderHistory()
  await flush()
  fireEvent.click(screen.getByRole("button", { name: new RegExp(`${summary.lines} lines`) }))
  await flush()
  return { ...utils, fetchSpy }
}

const line = (container: HTMLElement, id: string) =>
  container.querySelector<HTMLElement>(`[data-message-id="${id}"]`)

beforeEach(() => useStore.setState({ ...initial, view: "history" }, true))
afterEach(() => vi.restoreAllMocks())

describe("History", () => {
  it("lists saved sessions newest first, with an empty state", async () => {
    mockApi([
      savedSession({ id: "new", started_at: 1_760_000_100, lines: 7, duration_s: 61 }),
      savedSession({
        id: "old",
        started_at: 1_760_000_000,
        source: { kind: "session", id: "weird", speed: 2 },
      }),
    ])
    const { container, unmount } = renderHistory()
    await flush()
    const rows = container.querySelectorAll("[data-session-id]")
    expect([...rows].map((r) => r.getAttribute("data-session-id"))).toEqual(["new", "old"])
    const first = within(rows[0] as HTMLElement)
    expect(first.getByText(dateTime(1_760_000_100))).toBeInTheDocument()
    expect(first.getByText("0:01:01")).toBeInTheDocument()
    expect(first.getByText(/7 lines$/)).toBeInTheDocument()
    expect(within(rows[1] as HTMLElement).getByText(/Replay of weird · 2×/)).toBeInTheDocument()
    unmount()

    vi.restoreAllMocks()
    mockApi([])
    renderHistory()
    await flush()
    expect(screen.getByText(/No saved sessions yet/)).toBeInTheDocument()
  })

  it("opens a session: fetches exactly its transcript.json and shows lines with speakers", async () => {
    const { container, fetchSpy } = await openViewer(savedSession({ id: ID, lines: 3 }))
    const urls = fetchSpy.mock.calls.map(([url]) => String(url))
    // The session's data comes only from its transcript.json (/api/campaign is the write-up button).
    expect(urls.filter((u) => u !== "/api/sessions" && u !== "/api/campaign")).toEqual([RECORD_URL])

    const m1 = line(container, "m1")!
    expect(m1).toHaveTextContent("Nora")
    expect(m1).toHaveTextContent("Roll initiative, three.")
    const m2 = line(container, "m2")!
    expect(m2).toHaveTextContent("as Pip")
    expect(container.querySelector("mark")).toBeNull()
    const m3 = line(container, "m3")!
    expect(m3).toHaveTextContent("Voice 38")
    expect(m3).toHaveTextContent("?")
    expect(screen.getByText("Who is that?")).toBeInTheDocument()

    expect(screen.queryByText(/Mechanics/)).toBeNull()
  })

  it("computes talk-time shares from the record and applies the palette fallback", async () => {
    const { container } = await openViewer(savedSession({ id: ID, lines: 3 }))
    const box = (id: string) => container.querySelector<HTMLElement>(`[data-player-id="${id}"]`)!
    expect(within(box("p1")).getByTestId("talk-share")).toHaveTextContent("60%")
    expect(within(box("p2")).getByTestId("talk-share")).toHaveTextContent("20%")
    expect(screen.getByTestId("unnamed-share")).toHaveTextContent("Unnamed voices · 20%")

    expect(box("p2").style.getPropertyValue("--player")).toBe("#ec4899")
    expect(box("p1").style.getPropertyValue("--player")).toBe(PLAYER_PALETTE[1])
    expect(box("p1")).not.toHaveAttribute("data-speaking")
  })

  it("has a lazy audio player only when the session has audio; timestamps seek and play", async () => {
    const play = vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue(undefined)
    const { container } = await openViewer(savedSession({ id: ID, lines: 3, audio: true }))
    const audio = container.querySelector("audio")!
    expect(audio).toHaveAttribute("src", `/api/sessions/${ID}/audio.wav`)
    expect(audio).toHaveAttribute("preload", "none")
    expect(audio).not.toHaveAttribute("autoplay")
    expect(play).not.toHaveBeenCalled()

    fireEvent.click(within(line(container, "m2")!).getByRole("button", { name: "00:30" }))
    expect(audio.currentTime).toBe(30)
    expect(play).toHaveBeenCalledTimes(1)
  })

  it("has no audio player or clickable timestamps without audio", async () => {
    const { container } = await openViewer(savedSession({ id: ID, lines: 3, audio: false }))
    expect(container.querySelector("audio")).toBeNull()
    expect(within(line(container, "m2")!).queryByRole("button", { name: "00:30" })).toBeNull()
    expect(line(container, "m2")).toHaveTextContent("00:30")
  })

  it("shows an error state on 404 with a way back to the list", async () => {
    const summary = savedSession({ id: ID, lines: 3 })
    mockApi([summary], () => json({ detail: "Session not found" }, 404))
    renderHistory()
    await flush()
    fireEvent.click(screen.getByRole("button", { name: /3 lines/ }))
    await flush()
    expect(screen.getByRole("alert")).toHaveTextContent("Session not found")
    fireEvent.click(screen.getByRole("button", { name: /Back to all sessions/ }))
    await flush()
    expect(useStore.getState().viewing).toBeNull()
    expect(screen.getByText(/3 lines/)).toBeInTheDocument()
  })

  it("is isolated from the live store: live events don't reach the viewer and still reach the store", async () => {
    applyEvent({
      type: "snapshot",
      data: {
        ...snapshotBase(),
        session: session(),
        players: [player({ id: "live", name: "LivePlayer" })],
        voices: [],
        messages: [message({ id: "live1", text: "earlier live line" })],
      },
    })
    const { container } = await openViewer(savedSession({ id: ID, lines: 3 }))
    const before = container.innerHTML

    act(() => applyEvent({ type: "message.created", data: message({ id: "live2", text: "LIVE LINE" }) }))

    expect(container.innerHTML).toBe(before)
    expect(screen.queryByText("LIVE LINE")).toBeNull()
    expect(screen.queryByText("earlier live line")).toBeNull()
    expect(screen.queryByText("LivePlayer")).toBeNull()
    expect(useStore.getState().messages.map((m) => m.id)).toEqual(["live1", "live2"])
    expect(useStore.getState().players.map((p) => p.id)).toEqual(["live"])
  })

  it("closes the open session and lists the other campaign's sessions after a switch", async () => {
    const live = { ...snapshotBase(), session: session(), players: [], voices: [], messages: [] }
    applyEvent({ type: "snapshot", data: live })
    const { fetchSpy } = await openViewer(savedSession({ id: ID, lines: 3 }))
    expect(useStore.getState().viewing?.id).toBe(ID)
    fetchSpy.mockImplementation(async () => json([savedSession({ id: "other", lines: 9 })]))

    act(() => applyEvent({ type: "snapshot", data: { ...live, campaign: "c2" } }))
    await flush()

    expect(useStore.getState().viewing).toBeNull()
    expect(screen.getByText(/9 lines/)).toBeInTheDocument()
    expect(screen.queryByText(/3 lines/)).toBeNull()
  })

  it("merges every session that starts within 10 minutes, and shows progress", async () => {
    const early = savedSession({ id: "2026-10-08_120000-aaaa", started_at: 1_000, duration_s: 30, audio: true, lines: 1 })
    const mid = savedSession({ id: "2026-10-08_120100-bbbb", started_at: 1_040, duration_s: 30, audio: true, lines: 1 })
    const late = savedSession({ id: "2026-10-08_180000-cccc", started_at: 8_000, duration_s: 30, audio: true, lines: 1 })
    const posts: string[][] = []
    let release: () => void = () => {}
    const gate = new Promise<void>((resolve) => {
      release = resolve
    })
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const url = String(input)
      if (url === "/api/sessions/merge") {
        posts.push((JSON.parse(String(init?.body)) as { ids: string[] }).ids)
        await gate
        return json(early)
      }
      if (url === "/api/sessions") return json([early, mid, late])
      return json({ detail: `unexpected ${url}` }, 500)
    })
    renderHistory()
    await flush()
    expect(screen.getByRole("button", { name: "Merge matching sessions" })).toBeEnabled()
    fireEvent.click(screen.getByRole("button", { name: "Merge matching sessions" }))
    await flush()
    expect(screen.getByRole("progressbar", { name: "Merging matching sessions" })).toHaveAttribute("aria-valuemax", "1")
    expect(posts).toEqual([[early.id, mid.id]])
    await act(async () => release())
    await flush()
    expect(screen.queryByRole("progressbar")).toBeNull()
  })
})
