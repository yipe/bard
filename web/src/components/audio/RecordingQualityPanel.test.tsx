import { render, screen, waitFor, within } from "@testing-library/react"
import { VirtuosoMockContext } from "react-virtuoso"
import { afterEach, describe, expect, it, vi } from "vitest"
import type { RecordingQuality } from "@/lib/types"
import { campaign, savedSession, sessionRecord } from "@/test/fixtures"
import { mockFetch } from "@/test/mockFetch"
import { SavedSessionRow } from "@/components/history/SavedSessionRow"
import { SessionViewer } from "@/components/history/SessionViewer"
import { TooltipProvider } from "@/components/ui/tooltip"
import { RecordingQualityPanel } from "./RecordingQualityPanel"

const ID = "2026-10-08_193401-eebf"

const REPORT: RecordingQuality = {
  mic: "Yeti GX",
  overall: {
    speech_db: -36,
    noise_db: -55,
    snr_db: 19,
    rumble_pct: 42,
    clipped_pct: 0,
    grades: { speech: "ok", noise: "ok", snr: "poor", rumble: "ok", clipping: "good" },
  },
  timeline: [0, 60, 120, 180].map((t, i) => ({
    t,
    speech_db: -36 + i,
    noise_db: -55,
    snr_db: 19 + i,
    rumble_pct: [10, 30, 60, 42][i],
    clipped_pct: 0,
  })),
  speakers: [
    { speaker: "Theo", level_db: -30, lines: 400 },
    { speaker: "Omar", level_db: -34.2, lines: 120 },
  ],
  spread_db: 4.2,
  verdict: {
    kind: "microphone",
    reasons: [
      "The mic picks up one direction; voices around the table come through quiet.",
      "Lots of room noise.",
    ],
  },
}

afterEach(() => vi.restoreAllMocks())

describe("History audio quality", () => {
  it("shows loading, then the verdict, numbers, sparkline and speaker bars", async () => {
    let answer: (v: unknown) => void = () => {}
    const pending = new Promise((resolve) => (answer = resolve))
    const { calls } = mockFetch(() => pending)
    const { container } = render(<RecordingQualityPanel sessionId={ID} />)
    expect(screen.getByText(/Measuring the recording/)).toBeInTheDocument()
    expect(calls).toEqual([{ method: "GET", url: `/api/sessions/${ID}/quality`, body: undefined }])

    answer(REPORT)
    await waitFor(() => expect(screen.getByTestId("recording-quality")).toBeInTheDocument())
    const banner = screen.getByRole("status")
    expect(banner).toHaveTextContent("Consider a different microphone")
    expect(within(banner).getAllByRole("listitem")).toHaveLength(2)
    expect(document.querySelector('[data-quality="snr"]')).toHaveTextContent("19 dB")
    expect(
      within(document.querySelector<HTMLElement>('[data-quality="snr"]')!).getByLabelText("Grade: poor"),
    ).toHaveClass("bg-red-500")

    const svg = container.querySelector("svg[role=img]")!
    expect(svg).toBeInTheDocument()
    expect(svg.querySelectorAll("polyline")).toHaveLength(2)
    expect(svg.querySelectorAll("[data-rumble]")).toHaveLength(4)

    const bars = within(screen.getByRole("list", { name: "Loudness by player" }))
    expect(bars.getAllByRole("listitem").map((li) => li.textContent)).toEqual(["Theo-30 dB", "Omar-34 dB"])
    expect(screen.getByText("Omar comes through 4 dB quieter than Theo.")).toBeInTheDocument()
  })

  it("shows the server's error (e.g. no audio)", async () => {
    mockFetch(() => new Response(JSON.stringify({ detail: "no audio for this session" }), { status: 404 }))
    render(<RecordingQualityPanel sessionId={ID} />)
    expect(await screen.findByRole("alert")).toHaveTextContent("no audio for this session")
  })

  it("History list rows show the quality badge", () => {
    const { rerender } = render(<SavedSessionRow session={savedSession({ id: ID, quality: "settings" })} />)
    const badge = screen.getByTestId("quality-badge")
    expect(badge).toHaveAttribute("aria-label", "Audio: change mic settings")
    expect(badge).toHaveClass("bg-amber-400")
    rerender(<SavedSessionRow session={savedSession({ id: ID, quality: "good" })} />)
    expect(screen.getByTestId("quality-badge")).toHaveClass("bg-emerald-500")
    rerender(<SavedSessionRow session={savedSession({ id: ID, quality: null })} />)
    expect(screen.queryByTestId("quality-badge")).toBeNull()
  })

  it("the History viewer shows the Audio quality panel only for sessions with audio", async () => {
    const { calls } = mockFetch((c) => {
      if (c.url.endsWith("/transcript.json")) return sessionRecord({ id: ID })
      if (c.url.endsWith("/quality")) return REPORT
      if (c.url === "/api/campaign") return campaign()
      return new Response(null, { status: 500 })
    })
    const view = (audio: boolean) => (
      <VirtuosoMockContext.Provider value={{ viewportHeight: 600, itemHeight: 60 }}>
        <TooltipProvider>
          <SessionViewer key={String(audio)} summary={savedSession({ id: ID, audio })} onBack={() => {}} />
        </TooltipProvider>
      </VirtuosoMockContext.Provider>
    )
    const { rerender } = render(view(false))
    await waitFor(() => expect(screen.getByText("This session has no lines.")).toBeInTheDocument())
    expect(screen.queryByText("Audio quality")).toBeNull()
    expect(calls.filter((c) => c.url.endsWith("/quality"))).toHaveLength(0)

    rerender(view(true))
    await waitFor(() => expect(screen.getByTestId("recording-quality")).toBeInTheDocument())
    expect(screen.getByText("Audio quality")).toBeInTheDocument()
    expect(calls.filter((c) => c.url.endsWith("/quality"))).toHaveLength(1)
  })
})
