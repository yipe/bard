import { act, render, screen, within } from "@testing-library/react"
import { forwardRef, type ComponentProps } from "react"
import { VirtuosoMockContext, type VirtuosoHandle } from "react-virtuoso"
import { beforeEach, describe, expect, it, vi } from "vitest"
import { TooltipProvider } from "@/components/ui/tooltip"
import { applyEvent, useStore } from "@/lib/store"
import type { Message } from "@/lib/types"
import { message, player, session, voice, snapshotBase } from "@/test/fixtures"
import { ChatFeed } from "./ChatFeed"

// `initialTopMostItemIndex` waits for real layout/scrolling, which jsdom lacks, and then renders no rows.
// Strip it in tests and scroll to the bottom by hand (see `scrollToBottom`).
vi.mock("react-virtuoso", async (importOriginal) => {
  const mod = await importOriginal<typeof import("react-virtuoso")>()
  const Virtuoso = forwardRef<VirtuosoHandle, ComponentProps<typeof mod.Virtuoso>>(
    function Virtuoso(props, ref) {
      return <mod.Virtuoso {...props} ref={ref} initialTopMostItemIndex={undefined} />
    },
  )
  return { ...mod, Virtuoso }
})

const ROW = 60
const VIEWPORT = 600
/** Virtuoso debounces scroll-state updates (~200 ms). */
const SETTLE_MS = 250

const initial = useStore.getState()

beforeEach(() => useStore.setState(initial, true))

/** jsdom has no layout; give the virtualizer a fixed viewport and row height. */
function renderFeed() {
  return render(
    <VirtuosoMockContext.Provider value={{ viewportHeight: VIEWPORT, itemHeight: ROW }}>
      <TooltipProvider>
        <ChatFeed />
      </TooltipProvider>
    </VirtuosoMockContext.Provider>,
  )
}

function load(messages: Message[]) {
  applyEvent({
    type: "snapshot",
    data: {
      ...snapshotBase(),
      session: session(),
      players: [player({ id: "p1", name: "Priya", character: "Pip" })],
      voices: [voice({ id: "v1", status: "named", player_id: "p1" }), voice({ id: "v2", label: "Voice 2" })],
      messages,
    },
  })
}

const rows = (container: HTMLElement) => container.querySelectorAll("[data-message-id]")
const row = (container: HTMLElement, id: string) => container.querySelector(`[data-message-id="${id}"]`)

async function scrollTo(container: HTMLElement, top: number) {
  const scroller = container.querySelector<HTMLElement>("[data-virtuoso-scroller]")!
  // jsdom has no layout: report the virtual list height so "at bottom" can be computed.
  Object.defineProperty(scroller, "scrollHeight", {
    configurable: true,
    get: () => useStore.getState().messages.length * ROW,
  })
  Object.defineProperty(scroller, "offsetHeight", { configurable: true, get: () => VIEWPORT })
  await act(async () => {
    scroller.scrollTop = top
    scroller.dispatchEvent(new Event("scroll"))
    await new Promise((r) => setTimeout(r, SETTLE_MS))
  })
}

const scrollToBottom = (container: HTMLElement) =>
  scrollTo(container, useStore.getState().messages.length * ROW - VIEWPORT)

describe("ChatFeed", () => {
  it("shows an empty state", () => {
    renderFeed()
    expect(screen.getByText(/No messages yet/)).toBeInTheDocument()
  })

  it("renders speaker, character, time and plain text (no mechanics marks)", () => {
    load([
      message({ id: "m1", voice_id: "v1", text: "I deal 12 damage", t_start: 65 }),
      message({ id: "m2", voice_id: "v2", text: "Who goes next?", t_start: 70, provisional: true }),
    ])
    const { container } = renderFeed()
    expect(screen.getByText("Priya")).toBeInTheDocument()
    expect(screen.getByText("Pip")).toBeInTheDocument()
    expect(screen.getByText("01:05")).toBeInTheDocument()
    expect(row(container, "m1")).toHaveTextContent("I deal 12 damage")
    expect(container.querySelector("mark")).toBeNull()
    expect(screen.getByText("Voice 2")).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Name this voice" })).toBeInTheDocument()
  })

  it("shows a speaker once per turn; unknown speakers never group", () => {
    load([
      message({ id: "m1", voice_id: "v1", text: "one", t_start: 1 }),
      message({ id: "m2", voice_id: "v1", text: "two", t_start: 2 }),
      message({ id: "m3", voice_id: "v2", text: "three", t_start: 3 }),
      message({ id: "m4", voice_id: null, text: "four", t_start: 4 }),
      message({ id: "m5", voice_id: null, text: "five", t_start: 5 }),
    ])
    const { container } = renderFeed()
    const named = (id: string) => within(row(container, id) as HTMLElement)
    expect(named("m1").getByText("Priya")).toBeInTheDocument()
    expect(named("m2").queryByText("Priya")).toBeNull()
    expect(named("m2").getByText("two")).toBeInTheDocument()
    // A continued line can still be reassigned and keeps its timestamp.
    expect(named("m2").getByRole("button", { name: "Who said this?" })).toBeInTheDocument()
    expect(named("m2").getByText("00:02")).toBeInTheDocument()
    expect(named("m3").getByText("Voice 2")).toBeInTheDocument()
    expect(named("m4").getByText("…")).toBeInTheDocument()
    expect(named("m5").getByText("…")).toBeInTheDocument()

    // Reassigning the middle line splits the turn: it now heads Voice 2's turn.
    act(() => applyEvent({ type: "messages.updated", data: [{ id: "m2", voice_id: "v2" }] }))
    expect(named("m2").getByText("Voice 2")).toBeInTheDocument()
    expect(named("m3").queryByText("Voice 2")).toBeNull()
  })

  it("only renders a window of rows for a long session", async () => {
    load(
      Array.from({ length: 5000 }, (_, i) =>
        message({ id: `m${i}`, voice_id: "v1", text: `line ${i}`, t_start: i }),
      ),
    )
    const { container } = renderFeed()
    await scrollToBottom(container)
    const count = rows(container).length
    expect(count).toBeGreaterThan(0)
    expect(count).toBeLessThan(100)
    expect(row(container, "m4999")).not.toBeNull()
    expect(row(container, "m0")).toBeNull()
  })

  it("follows new messages at the bottom, not after the user scrolled up", async () => {
    load(Array.from({ length: 200 }, (_, i) => message({ id: `m${i}`, voice_id: "v1", t_start: i })))
    const { container } = renderFeed()
    await scrollToBottom(container)
    expect(screen.queryByRole("button", { name: /Jump to latest/ })).toBeNull()

    await act(async () => {
      applyEvent({ type: "message.created", data: message({ id: "new1", voice_id: "v1", t_start: 500 }) })
      await new Promise((r) => setTimeout(r, SETTLE_MS))
    })
    expect(row(container, "new1")).not.toBeNull()
    await scrollToBottom(container) // a real browser emits the scroll that follow-output triggered

    await scrollTo(container, 0)
    expect(screen.getByRole("button", { name: /Jump to latest/ })).toBeInTheDocument()
    await act(async () => {
      applyEvent({ type: "message.created", data: message({ id: "new2", voice_id: "v1", t_start: 501 }) })
      await new Promise((r) => setTimeout(r, SETTLE_MS))
    })
    expect(row(container, "new2")).toBeNull()
    expect(row(container, "m0")).not.toBeNull()
  })

  it("applies a messages.updated patch with new text and fixes to a rendered row", async () => {
    const before = "Dallara takes 8 damage"
    load([message({ id: "m1", voice_id: "v1", text: before })])
    const { container } = renderFeed()
    await scrollToBottom(container)
    expect(row(container, "m1")).toHaveTextContent(before)

    const text = "🎲 Elara takes 8 damage"
    act(() =>
      applyEvent({
        type: "messages.updated",
        data: [
          {
            id: "m1",
            text,
            fixes: [{ start: text.indexOf("Elara"), end: text.indexOf("Elara") + 5, heard: "Dallara" }],
          },
        ],
      }),
    )
    const r = row(container, "m1")!
    expect(r).toHaveTextContent(text)
    const fix = r.querySelector("[data-fix]")!
    expect(fix).toHaveTextContent("Elara")
    expect(fix).toHaveAttribute("title", 'Heard as "Dallara"')
  })
})
