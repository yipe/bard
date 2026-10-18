import { describe, expect, it } from "vitest"
import { entity } from "@/test/fixtures"
import { findEntity, matchesSearch, needsReview, obsidianUrl, splitWikiLinks } from "./campaign"

describe("splitWikiLinks", () => {
  it("shows the label after | and the target otherwise", () => {
    expect(splitWikiLinks("Met [[Elara|Ellie]] in [[Tarsis]].")).toEqual([
      { text: "Met " },
      { text: "Ellie", target: "Elara" },
      { text: " in " },
      { text: "Tarsis", target: "Tarsis" },
      { text: "." },
    ])
  })

  it("drops a #heading from the lookup target", () => {
    expect(splitWikiLinks("[[2026-10-08 The Desert Road#Fight]]")).toEqual([
      { text: "2026-10-08 The Desert Road#Fight", target: "2026-10-08 The Desert Road" },
    ])
  })

  it("leaves plain and malformed text alone", () => {
    expect(splitWikiLinks("no links [[here")).toEqual([{ text: "no links [[here" }])
    expect(splitWikiLinks("")).toEqual([])
  })
})

describe("glossary helpers", () => {
  it("needsReview covers suggested entries, spellings and facts", () => {
    expect(needsReview(entity({ name: "a" }))).toBe(false)
    expect(needsReview(entity({ name: "a", status: "suggested" }))).toBe(true)
    expect(needsReview(entity({ name: "a", heard_as_suggested: ["x"] }))).toBe(true)
    expect(needsReview(entity({ name: "a", suggested_facts: ["x"] }))).toBe(true)
  })

  it("matchesSearch checks name, aliases and heard-as, case-insensitively", () => {
    const e = entity({ name: "Elara", aliases: ["Ellie"], heard_as: ["Dilara"] })
    expect(["ela", "ELLIE", "dil", " "].map((q) => matchesSearch(e, q))).toEqual([true, true, true, true])
    expect(matchesSearch(e, "gorm")).toBe(false)
  })

  it("findEntity matches names first, then aliases", () => {
    const es = [entity({ name: "Ellie" }), entity({ name: "Elara", aliases: ["ellie", "El"] })]
    expect(findEntity(es, "ellie")?.name).toBe("Ellie")
    expect(findEntity(es, "el")?.name).toBe("Elara")
    expect(findEntity(es, "nobody")).toBeUndefined()
  })

  it("obsidianUrl names the vault by registry id, with the note path inside it", () => {
    expect(obsidianUrl({ id: "543fe3e0024d1d47", folder: "" })).toBe("obsidian://open?vault=543fe3e0024d1d47")
    expect(obsidianUrl({ id: "v1", folder: "" }, "People/Elara.md")).toBe(
      "obsidian://open?vault=v1&file=People%2FElara.md",
    )
    // TROUBADOUR_VAULT can be a folder inside a bigger vault: notes are relative to that vault's root.
    expect(obsidianUrl({ id: "v1", folder: "Campaigns/Morvath" }, "Sessions/A B.md")).toBe(
      "obsidian://open?vault=v1&file=Campaigns%2FMorvath%2FSessions%2FA%20B.md",
    )
  })
})
