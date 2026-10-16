import { act, fireEvent, render, screen, within } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { api } from "@/lib/api"
import { dateTime } from "@/lib/format"
import { applyEvent, useStore } from "@/lib/store"
import { savedSession as saved, session } from "@/test/fixtures"
import { PastSessions } from "./PastSessions"

const initial = useStore.getState()

const flush = () => act(async () => {})

beforeEach(() => useStore.setState(initial, true))
afterEach(() => vi.restoreAllMocks())

describe("PastSessions", () => {
  it("renders saved sessions with date, source, length, lines and downloads", async () => {
    vi.spyOn(api, "sessions").mockResolvedValue([
      saved({
        id: "s2",
        started_at: 1_760_000_000,
        source: { kind: "file", name: "table.wav", speed: 2 },
        duration_s: 3725,
        lines: 412,
        audio: true,
      }),
      saved({ id: "s1", source: { kind: "mic", device: "Yeti GX" }, duration_s: 65, lines: 3 }),
    ])
    const { container } = render(<PastSessions />)
    await flush()

    const rows = container.querySelectorAll("[data-session-id]")
    expect([...rows].map((r) => r.getAttribute("data-session-id"))).toEqual(["s2", "s1"])
    const first = within(rows[0] as HTMLElement)
    expect(first.getByText(dateTime(1_760_000_000))).toBeInTheDocument()
    expect(first.getByText("1:02:05")).toBeInTheDocument()
    expect(first.getByText("table.wav · 2× · 412 lines")).toBeInTheDocument()
    expect(first.getByRole("link", { name: /\.md/ })).toHaveAttribute(
      "href",
      "/api/sessions/s2/transcript.md",
    )
    expect(first.getByRole("link", { name: /\.json/ })).toHaveAttribute(
      "href",
      "/api/sessions/s2/transcript.json",
    )
    expect(first.getByRole("link", { name: /Audio \(\.wav\)/ })).toHaveAttribute(
      "href",
      "/api/sessions/s2/audio.wav",
    )
    const second = within(rows[1] as HTMLElement)
    expect(second.getByText("0:01:05")).toBeInTheDocument()
    expect(second.getByText(/^Yeti GX · 3 lines/)).toBeInTheDocument()
    expect(second.queryByRole("link", { name: /Audio/ })).toBeNull()
  })

  it("has no Replay controls anywhere", async () => {
    vi.spyOn(api, "sessions").mockResolvedValue([
      saved({ id: "s1", replayable: true, audio: true }),
      saved({ id: "s2", replayable: true }),
    ])
    const { container } = render(<PastSessions />)
    await flush()
    expect(container.querySelectorAll("[data-session-id]")).toHaveLength(2)
    expect(container).not.toHaveTextContent(/replay/i)
    expect(screen.queryByRole("group", { name: /speed/i })).toBeNull()
  })

  it("opens a row in the History viewer", async () => {
    const s = saved({ id: "2026-10-08_193401-eebf", lines: 9 })
    vi.spyOn(api, "sessions").mockResolvedValue([s])
    render(<PastSessions />)
    await flush()
    fireEvent.click(screen.getByRole("button", { name: /9 lines/ }))
    expect(useStore.getState()).toMatchObject({ view: "history", viewing: s })
  })

  it("shows only the 5 most recent, with an All sessions link to History", async () => {
    vi.spyOn(api, "sessions").mockResolvedValue(Array.from({ length: 8 }, (_, i) => saved({ id: `s${i}` })))
    useStore.setState({ viewing: saved({ id: "open" }) })
    const { container } = render(<PastSessions />)
    await flush()
    const ids = [...container.querySelectorAll("[data-session-id]")].map((r) =>
      r.getAttribute("data-session-id"),
    )
    expect(ids).toEqual(["s0", "s1", "s2", "s3", "s4"])
    fireEvent.click(screen.getByRole("button", { name: "All sessions" }))
    expect(useStore.getState()).toMatchObject({ view: "history", viewing: null })
  })

  it("shows the empty state", async () => {
    vi.spyOn(api, "sessions").mockResolvedValue([])
    render(<PastSessions />)
    await flush()
    expect(screen.getByText(/Sessions are saved automatically\./)).toBeInTheDocument()
  })

  it("refetches when a session returns to idle, not on other status changes", async () => {
    const spy = vi.spyOn(api, "sessions").mockResolvedValue([])
    applyEvent({ type: "session.updated", data: session({ status: "idle" }) })
    render(<PastSessions />)
    await flush()
    expect(spy).toHaveBeenCalledTimes(1)

    spy.mockResolvedValue([saved({ id: "new" })])
    await act(async () => applyEvent({ type: "session.updated", data: session({ status: "running" }) }))
    await act(async () => applyEvent({ type: "session.updated", data: session({ status: "running", t: 5 }) }))
    expect(spy).toHaveBeenCalledTimes(1)
    await act(async () => applyEvent({ type: "session.updated", data: session({ status: "stopping" }) }))
    await act(async () => applyEvent({ type: "session.updated", data: session({ status: "idle" }) }))
    expect(spy).toHaveBeenCalledTimes(2)
    expect(screen.getByText(/0 lines/)).toBeInTheDocument()
  })
})
