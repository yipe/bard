import { describe, expect, it } from "vitest"
import { recordMessage, sessionRecord } from "@/test/fixtures"
import { loadRecord, PLAYER_PALETTE } from "./record"

describe("loadRecord", () => {
  it("keeps stored player colors and falls back to the palette by index", () => {
    const { players } = loadRecord(
      sessionRecord({
        id: "s",
        players: [
          { id: "a", name: "A", character: "", is_gm: true },
          { id: "b", name: "B", character: "", is_gm: false, color: "#123456" },
          { id: "c", name: "C", character: "", is_gm: false },
        ],
      }),
    )
    expect(players.map((p) => p.color)).toEqual([PLAYER_PALETTE[0], "#123456", PLAYER_PALETTE[2]])
  })

  it("uses the record's precomputed speaker, in time order", () => {
    const { lines } = loadRecord(
      sessionRecord({
        id: "s",
        players: [{ id: "p1", name: "Nora", character: "DM", is_gm: true }],
        messages: [
          recordMessage({
            id: "m2",
            t_start: 5,
            speaker: "Voice 38",
            text: "I take 7",
          }),
          recordMessage({
            id: "m1",
            t_start: 1,
            speaker: "Nora",
            player_id: "p1",
            character: "Morvath",
            confidence: 0.3,
            text: "Roll 3, 4",
          }),
        ],
      }),
    )
    expect(lines.map((l) => [l.id, l.speaker, l.player?.name ?? null, l.character, l.uncertain])).toEqual([
      ["m1", "Nora", "Nora", "Morvath", true],
      ["m2", "Voice 38", null, null, false],
    ])
  })

  it("keeps fixes and treats records saved before the cleanup as having none", () => {
    const fix = { start: 0, end: 5, heard: "Dilara" }
    const { lines, spelling } = loadRecord({
      ...sessionRecord({
        id: "s",
        messages: [
          recordMessage({ id: "new", t_start: 1, text: "Elara", fixes: [fix] }),
          recordMessage({ id: "old", t_start: 2, text: "Gorm" }),
        ],
      }),
    })
    expect(lines.map((l) => l.fixes)).toEqual([[fix], []])
    expect(spelling).toBeNull()
  })
})
