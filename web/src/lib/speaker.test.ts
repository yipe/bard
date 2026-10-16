import { describe, expect, it } from "vitest"
import { player, voice } from "@/test/fixtures"
import { speakerFor } from "./speaker"

const laura = player({ id: "p1", name: "Priya", character: "Pip" })
const matt = player({ id: "p2", name: "Nora", character: "", is_gm: true })

describe("speakerFor", () => {
  it("named voice resolves to the player's name and character", () => {
    const voices = [voice({ id: "v1", status: "named", player_id: "p1" })]
    expect(speakerFor("v1", voices, [laura])).toMatchObject({
      name: "Priya",
      character: "Pip",
      player: laura,
    })
  })

  it("voice.character wins over player.character", () => {
    const voices = [voice({ id: "v1", status: "named", player_id: "p2", character: "Morvath" })]
    expect(speakerFor("v1", voices, [matt]).character).toBe("Morvath")
  })

  it("no character on other voices when the player has a character voice", () => {
    const voices = [
      voice({ id: "v1", status: "named", player_id: "p1", character: "Pip" }),
      voice({ id: "v2", status: "named", player_id: "p1" }),
    ]
    expect(speakerFor("v2", voices, [laura])).toMatchObject({ name: "Priya", character: null })
    expect(speakerFor("v1", voices, [laura]).character).toBe("Pip")
  })

  it("empty player.character yields null", () => {
    const voices = [voice({ id: "v1", status: "named", player_id: "p2" })]
    expect(speakerFor("v1", voices, [matt]).character).toBeNull()
  })

  it("pending voice shows its label", () => {
    const voices = [voice({ id: "v3", label: "Voice 3" })]
    expect(speakerFor("v3", voices, [laura])).toMatchObject({
      name: "Voice 3",
      player: null,
      character: null,
    })
  })

  it("null or unknown voice id shows an ellipsis", () => {
    expect(speakerFor(null, [], [laura]).name).toBe("…")
    expect(speakerFor("gone", [voice({ id: "v1" })], [laura])).toMatchObject({ name: "…", voice: null })
  })
})
