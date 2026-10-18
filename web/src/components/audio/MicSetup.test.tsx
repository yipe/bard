import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { resetAudio, zoneOf } from "@/lib/audio"
import { applyEvent } from "@/lib/store"
import { audioQuality, mic } from "@/test/fixtures"
import { mockFetch } from "@/test/mockFetch"
import { MicDetails } from "./MicDetails"
import { MicTest } from "./MicTest"

const YETI = mic({
  name: "Yeti GX",
  maker: "Logitech",
  specs: { source: "catalog", model: "Yeti GX", capsule: "dynamic", patterns: ["supercardioid"] },
  checks: [
    {
      id: "around",
      label: "Picks up voices all around",
      ok: false,
      detail: "Supercardioid: one voice in front",
    },
    { id: "wired", label: "Wired", ok: true, detail: "USB" },
    { id: "rate", label: "48 kHz", ok: true, detail: "" },
    { id: "measured", label: "Sounded good in past sessions", ok: null, detail: "Not measured yet" },
  ],
})
const MYSTERY = mic({ name: "USB Audio Device", default: true, checks: [] })

beforeEach(resetAudio)
afterEach(() => vi.restoreAllMocks())

describe("Mic details", () => {
  it("shows the model line and ✓ / ✗ / ? per check, with advice when a check fails", async () => {
    mockFetch(() => [YETI, MYSTERY])
    render(<MicDetails device="Yeti GX" />)
    expect(await screen.findByTestId("mic-model")).toHaveTextContent("Yeti GX")
    expect(screen.getByText("Logitech · USB · 48 kHz")).toBeInTheDocument()
    const check = (id: string) => within(document.querySelector<HTMLElement>(`[data-check="${id}"]`)!)
    expect(check("around").getByLabelText("Problem")).toBeInTheDocument()
    expect(check("around").getByText("Supercardioid: one voice in front")).toBeInTheDocument()
    expect(check("wired").getByLabelText("OK")).toBeInTheDocument()
    expect(check("measured").getByLabelText("Unknown")).toBeInTheDocument()
    expect(screen.getByRole("note")).toHaveTextContent("built for one voice up close")
    // Known mic: no form until Edit.
    expect(screen.queryByRole("form")).toBeNull()
    fireEvent.click(screen.getByRole("button", { name: "Edit" }))
    expect(screen.getByRole("form", { name: "Correct this mic's details" })).toBeInTheDocument()
  })

  it("asks about an unknown mic, PATCHes the capsule and patterns, then refetches", async () => {
    let mics = [YETI, MYSTERY]
    const { calls, writes } = mockFetch((c) => {
      if (c.method === "PATCH") {
        const updated = {
          ...MYSTERY,
          specs: { source: "you" as const, capsule: "condenser" as const, patterns: ["omni" as const] },
        }
        mics = [YETI, updated]
        return updated
      }
      return mics
    })
    render(<MicDetails device="USB Audio Device" />)
    const form = await screen.findByRole("form", { name: "Tell Troubadour about this mic" })
    fireEvent.change(within(form).getByRole("combobox", { name: "Capsule" }), {
      target: { value: "condenser" },
    })
    fireEvent.click(within(form).getByRole("button", { name: "Omni" }))
    fireEvent.click(within(form).getByRole("button", { name: "Boundary" }))
    fireEvent.click(within(form).getByRole("button", { name: "Boundary" })) // toggled off again
    fireEvent.click(within(form).getByRole("button", { name: "Save" }))
    await waitFor(() => expect(screen.queryByRole("form")).toBeNull())
    expect(writes()).toEqual([
      {
        method: "PATCH",
        url: "/api/mics/USB%20Audio%20Device",
        body: { patterns: ["omni"], capsule: "condenser" },
      },
    ])
    expect(calls.filter((c) => c.url === "/api/mics" && c.method === "GET")).toHaveLength(2)
  })

  it("reads the mics again when another device is picked (it may have just been plugged in)", async () => {
    let known = [MYSTERY]
    mockFetch(() => known)
    const { rerender } = render(<MicDetails device="USB Audio Device" />)
    expect(await screen.findByTestId("mic-model")).toHaveTextContent("USB Audio Device")
    known = [MYSTERY, YETI]
    rerender(<MicDetails device="Yeti GX" />)
    await waitFor(() => expect(screen.getByTestId("mic-model")).toHaveTextContent("Yeti GX"))
  })
})

describe("Mic test", () => {
  it("posts the test for the selected device, shows live level and quality, and Stop posts stop", async () => {
    const { writes } = mockFetch((c) =>
      c.url === "/api/mic/test" ? { state: "running", device: "Yeti GX", seconds: 20 } : { state: "stopped" },
    )
    render(<MicTest device="Yeti GX" />)
    fireEvent.click(screen.getByRole("button", { name: /Test mic/ }))
    await waitFor(() => expect(screen.getByRole("button", { name: /Stop/ })).toBeInTheDocument())
    expect(writes()).toEqual([{ method: "POST", url: "/api/mic/test", body: { device: "Yeti GX" } }])
    expect(screen.getByTestId("mic-test-countdown")).toHaveTextContent(/^(20|19)s$/)

    act(() => {
      applyEvent({ type: "mic.test", data: { state: "running" } })
      applyEvent({
        type: "audio.level",
        data: { source: "test", db: -20, peak_db: -15, clipping: false, zone: zoneOf(-20) },
      })
      applyEvent({ type: "audio.quality", data: audioQuality({ source: "test" }) })
    })
    await waitFor(() => expect(screen.getByTestId("level-label")).toHaveTextContent("Good"))
    expect(document.querySelector('[data-quality="speech"]')).toHaveTextContent("-34 dBFS")
    expect(screen.getByRole("list", { name: "Hints" })).toBeInTheDocument()

    fireEvent.click(screen.getByRole("button", { name: /Stop/ }))
    await waitFor(() => expect(screen.getByRole("button", { name: /Test mic/ })).toBeInTheDocument())
    expect(writes().at(-1)).toEqual({ method: "POST", url: "/api/mic/test/stop", body: undefined })
    // The last read-out stays on screen.
    expect(screen.getByText("Last test")).toBeInTheDocument()
  })
})
