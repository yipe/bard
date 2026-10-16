import { act, render, screen } from "@testing-library/react"
import { beforeEach, describe, expect, it } from "vitest"
import { applyEvent, useStore } from "@/lib/store"
import type { Message } from "@/lib/types"
import { message, player, session, voice, snapshotBase } from "@/test/fixtures"
import { PlayerGrid } from "./PlayerGrid"

const initial = useStore.getState()
beforeEach(() => useStore.setState(initial, true))

function load(messages: Message[]) {
  applyEvent({
    type: "snapshot",
    data: {
      ...snapshotBase(),
      session: session(),
      players: [player({ id: "p1", name: "Priya" }), player({ id: "p2", name: "Nora" })],
      voices: [
        voice({ id: "v1", status: "named", player_id: "p1" }),
        voice({ id: "v2", status: "named", player_id: "p2" }),
        voice({ id: "v3", label: "Voice 3" }),
      ],
      messages,
    },
  })
}

const share = (container: HTMLElement, playerId: string) =>
  container.querySelector(`[data-player-id="${playerId}"] [data-testid="talk-share"]`)

describe("PlayerGrid talk time", () => {
  it("shows each player's share of talk time", () => {
    load([
      message({ id: "a", voice_id: "v1", t_start: 0, t_end: 75 }),
      message({ id: "b", voice_id: "v2", t_start: 75, t_end: 100 }),
    ])
    const { container } = render(<PlayerGrid />)
    expect(share(container, "p1")).toHaveTextContent("75%")
    expect(share(container, "p1")).toHaveTextContent("1m")
    expect(share(container, "p2")).toHaveTextContent("25%")
    expect(share(container, "p2")).toHaveAttribute("title", "25s of 1m of talk")
    expect(screen.queryByTestId("unnamed-share")).toBeNull()
  })

  it("moves the share live when a line is relabeled, and shows/hides the unnamed chip", () => {
    load([
      message({ id: "a", voice_id: "v1", t_start: 0, t_end: 50 }),
      message({ id: "b", voice_id: "v3", t_start: 50, t_end: 100 }),
    ])
    const { container } = render(<PlayerGrid />)
    expect(share(container, "p1")).toHaveTextContent("50%")
    expect(share(container, "p2")).toHaveTextContent("0%")
    expect(screen.getByTestId("unnamed-share")).toHaveTextContent("Unnamed voices · 50%")

    act(() => applyEvent({ type: "messages.updated", data: [{ id: "b", voice_id: "v2" }] }))
    expect(share(container, "p2")).toHaveTextContent("50%")
    expect(screen.queryByTestId("unnamed-share")).toBeNull()

    act(() => applyEvent({ type: "messages.updated", data: [{ id: "a", voice_id: "v2" }] }))
    expect(share(container, "p1")).toHaveTextContent("0%")
    expect(share(container, "p2")).toHaveTextContent("100%")
  })

  it("renders no stats with zero messages", () => {
    load([])
    const { container } = render(<PlayerGrid />)
    expect(screen.getByText("Priya")).toBeInTheDocument()
    expect(container.querySelector('[data-testid="talk-share"]')).toBeNull()
    expect(screen.queryByTestId("unnamed-share")).toBeNull()
  })
})
