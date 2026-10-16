import { act, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { beforeEach, describe, expect, it } from "vitest"
import { applyEvent, useStore } from "@/lib/store"
import { message, player, session, snapshotBase, voice } from "@/test/fixtures"
import { mockFetch } from "@/test/mockFetch"
import { LineSpeakerPicker } from "./LineSpeakerPicker"

const initial = useStore.getState()

beforeEach(() => {
  useStore.setState(initial, true)
  applyEvent({
    type: "snapshot",
    data: {
      ...snapshotBase(),
      session: session(),
      players: [
        player({ id: "p1", name: "Priya", character: "Pip" }),
        player({ id: "p2", name: "Nora", character: "", is_gm: true }),
      ],
      voices: [voice({ id: "v9", status: "named", player_id: "p2", character: "Morvath" })],
      messages: [message({ id: "m1" })],
    },
  })
})

function open() {
  render(<LineSpeakerPicker messageId="m1">Voice 2</LineSpeakerPicker>)
  fireEvent.click(screen.getByRole("button", { name: "Voice 2" }))
}

describe("LineSpeakerPicker", () => {
  it("lists every player and the characters already voiced, and assigns the line to the pick", async () => {
    const { writes } = mockFetch(() => ({}))
    open()
    expect(await screen.findByText("Who said this line?")).toBeInTheDocument()
    expect(screen.getByRole("button", { name: /Priya\s*Pip/ })).toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: /Nora\s*as Morvath/ }))
    await waitFor(() =>
      expect(writes()).toEqual([
        { method: "POST", url: "/api/messages/m1/speaker", body: { player_id: "p2", character: "Morvath" } },
      ]),
    )
    await waitFor(() => expect(screen.queryByText("Who said this line?")).toBeNull())
  })

  it("a typed character goes with whichever player is picked", async () => {
    const { writes } = mockFetch(() => ({}))
    open()
    await act(async () => {
      fireEvent.change(await screen.findByPlaceholderText(/character/i), {
        target: { value: "  King Flutus " },
      })
    })
    expect(screen.queryByRole("button", { name: /as Morvath/ })).toBeNull()
    fireEvent.click(screen.getByRole("button", { name: /Nora\s*as King Flutus/ }))
    await waitFor(() =>
      expect(writes()).toEqual([
        {
          method: "POST",
          url: "/api/messages/m1/speaker",
          body: { player_id: "p2", character: "King Flutus" },
        },
      ]),
    )
  })
})
