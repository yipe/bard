import { act, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { resetAudio } from "@/lib/audio"
import { useStore } from "@/lib/store"
import type { MicCheck, MicInfo, SourceList } from "@/lib/types"
import { mic, session } from "@/test/fixtures"
import { mockFetch, type FetchCall } from "@/test/mockFetch"
import { MIC_KEY, OLD_MIC_KEY, SOURCES_POLL_MS, StartPanel } from "./StartPanel"

const BUILT_IN = { name: "MacBook Pro Microphone", default: true }
const YETI = { name: "Yeti Stereo Microphone", default: false }

/** A fake server whose inputs the test plugs in and out; `info` is what GET /api/mics says about them. */
function server(initial: SourceList["mics"], info: MicInfo[] = []) {
  let mics = initial
  const fake = mockFetch((c) => {
    if (c.url === "/api/sources") return { files: [], mics }
    if (c.url === "/api/mics") return info
    return undefined
  })
  return { ...fake, plug: (next: SourceList["mics"]) => (mics = next) }
}

const tick = (ms: number) => act(() => vi.advanceTimersByTimeAsync(ms))
const picked = () => screen.getByRole("combobox").querySelector('[data-slot="select-value"]')?.textContent

async function pick(name: string) {
  fireEvent.click(screen.getByRole("combobox"))
  await tick(0)
  const option = screen.getByRole("option", { name })
  fireEvent.pointerDown(option) // Base UI only takes a mouse click that started on the item
  fireEvent.click(option)
  await tick(0)
}

beforeEach(() => {
  vi.useFakeTimers()
  resetAudio()
  useStore.setState({ sources: null, session: null })
  localStorage.clear()
})
afterEach(() => {
  vi.useRealTimers()
  vi.restoreAllMocks()
})

describe("StartPanel microphone list", () => {
  it("shows a mic plugged in while Setup is open", async () => {
    const { plug } = server([BUILT_IN])
    render(<StartPanel />)
    await tick(0)
    expect(picked()).toBe("MacBook Pro Microphone (default)")
    // macOS makes a newly plugged USB mic the default input.
    plug([
      { ...BUILT_IN, default: false },
      { ...YETI, default: true },
    ])
    await tick(SOURCES_POLL_MS)
    expect(picked()).toBe("Yeti Stereo Microphone (default)")
  })

  it("falls back to the default when the chosen mic is unplugged, says so, and returns to it when it's back", async () => {
    const { plug } = server([BUILT_IN, YETI])
    render(<StartPanel />)
    await tick(0)
    await pick(YETI.name)
    expect(picked()).toBe(YETI.name)
    expect(screen.queryByRole("alert")).toBeNull()

    plug([BUILT_IN])
    await tick(SOURCES_POLL_MS)
    expect(picked()).toBe("MacBook Pro Microphone (default)")
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Yeti Stereo Microphone isn't connected. Using MacBook Pro Microphone instead.",
    )

    plug([BUILT_IN, YETI])
    await tick(SOURCES_POLL_MS)
    expect(picked()).toBe(YETI.name)
    expect(screen.queryByRole("alert")).toBeNull()
  })

  it("remembers the chosen mic after leaving Setup (starting a session does) and after a reload", async () => {
    const { calls } = server([BUILT_IN, YETI])
    const first = render(<StartPanel />)
    await tick(0)
    await pick(YETI.name)
    fireEvent.click(screen.getByRole("button", { name: /Start session/ }))
    await tick(0)
    first.unmount()
    expect(localStorage.getItem(MIC_KEY)).toBe(YETI.name)

    render(<StartPanel />)
    await tick(0)
    expect(picked()).toBe(YETI.name)
    fireEvent.click(screen.getByRole("button", { name: /Start session/ }))
    await tick(0)
    const starts = calls.filter((c) => c.url === "/api/session/start")
    expect(starts.map((c) => c.body)).toEqual([
      { source: { kind: "mic", device: YETI.name } },
      { source: { kind: "mic", device: YETI.name } },
    ])
  })

  it("picks up the mic saved under the pre-rename key and moves it to the new one", async () => {
    server([BUILT_IN, YETI])
    localStorage.setItem(OLD_MIC_KEY, YETI.name)
    render(<StartPanel />)
    await tick(0)
    expect(picked()).toBe(YETI.name)
    expect(localStorage.getItem(MIC_KEY)).toBe(YETI.name)
    expect(localStorage.getItem(OLD_MIC_KEY)).toBeNull()
  })

  it("prefers the new key over the pre-rename one", async () => {
    server([BUILT_IN, YETI])
    localStorage.setItem(OLD_MIC_KEY, BUILT_IN.name)
    localStorage.setItem(MIC_KEY, YETI.name)
    render(<StartPanel />)
    await tick(0)
    expect(picked()).toBe(YETI.name)
  })

  it("stops polling while a session runs", async () => {
    const { calls } = server([BUILT_IN])
    useStore.setState({ session: session({ status: "running" }) })
    render(<StartPanel />)
    await tick(SOURCES_POLL_MS * 3)
    expect(calls.filter((c) => c.url === "/api/sources")).toHaveLength(1)
  })
})

describe("StartPanel mic warning", () => {
  const red = (id: MicCheck["id"], label: string): MicCheck => ({
    id,
    label,
    ok: false,
    detail: `${label}: no`,
  })
  const starts = (calls: FetchCall[]) => calls.filter((c) => c.url === "/api/session/start")
  const builtIn = mic({ ...BUILT_IN, specs: { source: "catalog", model: "MacBook built-in mics" } })

  it("says so when the mic isn't omnidirectional, and starts only on Start anyway", async () => {
    const { calls } = server([BUILT_IN], [{ ...builtIn, checks: [red("around", "Hears the whole table")] }])
    render(<StartPanel />)
    await tick(0)
    fireEvent.click(screen.getByRole("button", { name: /Start session/ }))
    await tick(0)
    expect(screen.getByRole("dialog")).toHaveTextContent("Hey, you aren't using an omnidirectional mic")
    expect(screen.getByRole("dialog")).toHaveTextContent("MacBook built-in mics")
    expect(starts(calls)).toEqual([])
    fireEvent.click(screen.getByRole("button", { name: "Start anyway" }))
    await tick(0)
    expect(starts(calls).map((c) => c.body)).toEqual([{ source: { kind: "mic", device: BUILT_IN.name } }])
  })

  it("names any other red check, and Pick another mic starts nothing", async () => {
    const { calls } = server([BUILT_IN], [{ ...builtIn, checks: [red("wired", "Steady connection")] }])
    render(<StartPanel />)
    await tick(0)
    fireEvent.click(screen.getByRole("button", { name: /Start session/ }))
    await tick(0)
    expect(screen.getByRole("dialog")).toHaveTextContent("Hey, something in this mic's checklist is red")
    expect(screen.getByRole("dialog")).toHaveTextContent("Steady connection")
    fireEvent.click(screen.getByRole("button", { name: "Pick another mic" }))
    await tick(0)
    expect(screen.queryByRole("dialog")).toBeNull()
    expect(starts(calls)).toEqual([])
  })

  it("starts straight away when nothing is red", async () => {
    const ok = { id: "around", label: "Hears the whole table", ok: true, detail: "" } satisfies MicCheck
    const { calls } = server([BUILT_IN], [{ ...builtIn, checks: [ok] }])
    render(<StartPanel />)
    await tick(0)
    fireEvent.click(screen.getByRole("button", { name: /Start session/ }))
    await tick(0)
    expect(screen.queryByRole("dialog")).toBeNull()
    expect(starts(calls)).toHaveLength(1)
  })
})
