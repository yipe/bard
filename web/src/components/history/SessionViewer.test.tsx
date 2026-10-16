import { act, render, within } from "@testing-library/react"
import { VirtuosoMockContext } from "react-virtuoso"
import { afterEach, describe, expect, it, vi } from "vitest"
import { TooltipProvider } from "@/components/ui/tooltip"
import { api } from "@/lib/api"
import { recordMessage, savedSession, sessionRecord } from "@/test/fixtures"
import { SessionViewer } from "./SessionViewer"

afterEach(() => vi.restoreAllMocks())

describe("SessionViewer transcript", () => {
  it("shows a speaker once per turn; a character switch or an unknown speaker starts a new one", async () => {
    const nora = { speaker: "Nora", player_id: "p1" }
    vi.spyOn(api, "sessionRecord").mockResolvedValue(
      sessionRecord({
        id: "s",
        players: [{ id: "p1", name: "Nora", character: "DM", is_gm: true, color: "#10b981" }],
        messages: [
          recordMessage({ id: "a", t_start: 1, ...nora, voice_id: "v1", text: "one" }),
          recordMessage({ id: "b", t_start: 2, ...nora, voice_id: "v9", text: "two" }),
          recordMessage({
            id: "c",
            t_start: 3,
            ...nora,
            voice_id: "v1",
            character: "Morvath",
            text: "three",
          }),
          recordMessage({ id: "d", t_start: 4, speaker: "Voice 3", voice_id: "v3", text: "four" }),
          recordMessage({ id: "e", t_start: 5, speaker: "Voice 3", voice_id: "v3", text: "five" }),
          recordMessage({ id: "f", t_start: 6, speaker: "Unknown", text: "six" }),
          recordMessage({ id: "g", t_start: 7, speaker: "Unknown", text: "seven" }),
        ],
      }),
    )
    const { container } = render(
      <VirtuosoMockContext.Provider value={{ viewportHeight: 600, itemHeight: 60 }}>
        <TooltipProvider>
          <SessionViewer summary={savedSession({ id: "s" })} onBack={() => {}} />
        </TooltipProvider>
      </VirtuosoMockContext.Provider>,
    )
    await act(async () => {})
    const name = (id: string, text: string) =>
      within(container.querySelector(`[data-message-id="${id}"]`) as HTMLElement).queryByText(text)
    expect(name("a", "Nora")).not.toBeNull()
    expect(name("b", "Nora")).toBeNull() // another of Nora's voices, same turn
    expect(name("c", "Nora")).not.toBeNull()
    expect(name("d", "Voice 3")).not.toBeNull()
    expect(name("e", "Voice 3")).toBeNull()
    expect(name("f", "Unknown")).not.toBeNull()
    expect(name("g", "Unknown")).not.toBeNull()
  })
})
