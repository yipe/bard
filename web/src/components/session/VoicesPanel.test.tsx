import { act, fireEvent, render, screen } from "@testing-library/react"
import { beforeEach, describe, expect, it } from "vitest"
import { actions, applyEvent, useStore } from "@/lib/store"
import { player, session, voice, snapshotBase } from "@/test/fixtures"
import { VoicesPanel } from "./VoicesPanel"

const initial = useStore.getState()

beforeEach(() => {
  useStore.setState(initial, true)
  applyEvent({
    type: "snapshot",
    data: {
      ...snapshotBase(),
      session: session(),
      players: [player({ id: "p1", name: "Priya" })],
      voices: [
        // 12 pending voices; seconds = index so "Voice 11" is the longest-heard.
        ...Array.from({ length: 12 }, (_, i) => voice({ id: `v${i}`, label: `Voice ${i}`, seconds: i })),
        voice({ id: "named", label: "Voice 99", status: "named", player_id: "p1" }),
      ],
      messages: [],
    },
  })
})

const pendingLabels = () => screen.queryAllByText(/^Voice \d+$/).map((el) => el.textContent)

describe("VoicesPanel", () => {
  it("shows the top 8 pending voices by seconds heard, and named voices", () => {
    render(<VoicesPanel />)
    expect(pendingLabels()).toEqual([
      "Voice 11",
      "Voice 10",
      "Voice 9",
      "Voice 8",
      "Voice 7",
      "Voice 6",
      "Voice 5",
      "Voice 4",
    ])
    expect(screen.getByText("Priya")).toBeInTheDocument()
  })

  it("Show all reveals every pending voice and can collapse again", () => {
    render(<VoicesPanel />)
    fireEvent.click(screen.getByRole("button", { name: "Show all (12)" }))
    expect(pendingLabels()).toHaveLength(12)
    fireEvent.click(screen.getByRole("button", { name: "Show fewer" }))
    expect(pendingLabels()).toHaveLength(8)
  })

  it("keeps a voice focused from the chat visible even outside the top 8", () => {
    render(<VoicesPanel />)
    act(() => actions.focusVoice("v0"))
    expect(pendingLabels()).toHaveLength(9)
    expect(screen.getByText("Voice 0")).toBeInTheDocument()
  })

  it("does not show the toggle with 8 or fewer pending voices", () => {
    act(() => {
      for (let i = 0; i < 4; i++)
        applyEvent({ type: "voice.removed", data: { id: `v${i}`, merged_into: null } })
    })
    render(<VoicesPanel />)
    expect(pendingLabels()).toHaveLength(8)
    expect(screen.queryByRole("button", { name: /Show all/ })).toBeNull()
  })
})
