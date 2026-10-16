import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { TooltipProvider } from "@/components/ui/tooltip"
import { SessionView } from "@/components/session/SessionView"
import { resetAudio, useAudio } from "@/lib/audio"
import { applyEvent, useStore } from "@/lib/store"
import { audioQuality, session, snapshotBase } from "@/test/fixtures"
import { bodyOf, mockFetch } from "@/test/mockFetch"

const initial = useStore.getState()

function live(s: Parameters<typeof session>[0] = {}) {
  applyEvent({
    type: "snapshot",
    data: { ...snapshotBase(), session: session(s), players: [], voices: [], messages: [] },
  })
}

function renderView() {
  return render(
    <TooltipProvider>
      <SessionView />
    </TooltipProvider>,
  )
}

beforeEach(() => {
  useStore.setState(initial, true)
  // File sessions mount an <audio> that plays; jsdom's play() returns nothing.
  vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue(undefined)
})
afterEach(() => {
  resetAudio()
  vi.restoreAllMocks()
})

const box = () => screen.queryByRole("region", { name: "Audio quality" })

describe("Live audio quality box", () => {
  it("shows for live mic sessions only, with the meter", () => {
    live({ source: { kind: "file", name: "a.wav", speed: 1 } })
    const { unmount } = renderView()
    expect(box()).toBeNull()
    expect(screen.queryByRole("meter")).toBeNull()
    unmount()
    live({ source: { kind: "mic", device: null }, status: "idle" })
    const idle = renderView()
    expect(box()).toBeNull()
    idle.unmount()
    live({ source: { kind: "mic", device: null } })
    renderView()
    expect(within(box()!).getByRole("meter")).toBeInTheDocument()
    expect(screen.queryByText("Mechanics")).toBeNull()
  })

  it("fills in from audio.quality events: five rows and hints", () => {
    live()
    renderView()
    expect(screen.getByText(/first read-out comes after a few seconds/)).toBeInTheDocument()
    act(() => applyEvent({ type: "audio.quality", data: audioQuality() }))
    const row = (key: string) => document.querySelector<HTMLElement>(`[data-quality="${key}"]`)!
    expect(within(row("speech")).getByText("Speech level")).toBeInTheDocument()
    expect(row("speech")).toHaveTextContent("-34 dBFS")
    expect(row("noise")).toHaveTextContent("Background noise-59 dBFS")
    expect(row("snr")).toHaveTextContent("Speech above noise24 dB")
    expect(row("rumble")).toHaveTextContent("Low rumble31%")
    expect(row("clipping")).toHaveTextContent("Clipping0.00%")
    expect(within(row("clipping")).getByLabelText("Grade: good")).toHaveClass("bg-emerald-500")
    expect(within(row("speech")).getByLabelText("Grade: OK")).toHaveClass("bg-amber-400")
    const hints = screen.getByRole("list", { name: "Hints" })
    expect(within(hints).getAllByRole("listitem")).toHaveLength(2)
  })

  it("closing switches the server's monitor off; showing it again switches it back on", async () => {
    const { writes } = mockFetch(({ body }) => body)
    live()
    renderView()
    act(() => applyEvent({ type: "audio.quality", data: audioQuality() }))

    fireEvent.click(screen.getByRole("button", { name: "Hide audio quality" }))
    await waitFor(() => expect(writes()).toHaveLength(1))
    expect(writes()[0]).toMatchObject({ method: "PUT", url: "/api/audio/monitor" })
    expect(bodyOf(writes()[0])).toEqual({ on: false })
    // The server confirms through session.updated, for every open tab.
    act(() => applyEvent({ type: "session.updated", data: session({ audio_monitor: false }) }))
    expect(box()).toBeNull()
    expect(useAudio.getState().quality.session).toBeUndefined() // no stale numbers later

    fireEvent.click(screen.getByRole("button", { name: /Show audio quality/ }))
    await waitFor(() => expect(writes()).toHaveLength(2))
    expect(bodyOf(writes()[1])).toEqual({ on: true })
    act(() => applyEvent({ type: "session.updated", data: session({ audio_monitor: true }) }))
    expect(screen.getByText(/first read-out comes after a few seconds/)).toBeInTheDocument()
  })
})
