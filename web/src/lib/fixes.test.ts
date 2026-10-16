import { describe, expect, it } from "vitest"
import type { Fix } from "./types"
import { splitFixes } from "./fixes"

const shape = (text: string, fixes: Fix[]) =>
  splitFixes(text, fixes).map((p) => [p.text, p.fix?.heard ?? null])

describe("splitFixes", () => {
  it("cuts the text at a fix after an emoji (UTF-16 offsets)", () => {
    const text = "🎲 Elara rolls"
    const start = text.indexOf("Elara") // 3: the emoji is two code units
    expect(shape(text, [{ start, end: start + 5, heard: "Dilara" }])).toEqual([
      ["🎲 ", null],
      ["Elara", "Dilara"],
      [" rolls", null],
    ])
  })

  it("orders fixes, drops overlapping, empty and out-of-range ones", () => {
    expect(
      shape("abcdefghij", [
        { start: 6, end: 8, heard: "c" },
        { start: 0, end: 4, heard: "a" },
        { start: 2, end: 5, heard: "overlaps a" },
        { start: 3, end: 3, heard: "empty" },
        { start: Number.NaN, end: 2, heard: "nan start" },
        { start: 8, end: 99, heard: "clamped" },
      ]),
    ).toEqual([
      ["abcd", "a"],
      ["ef", null],
      ["gh", "c"],
      ["ij", "clamped"],
    ])
  })

  it("no fixes returns the whole text", () => {
    expect(shape("plain", [])).toEqual([["plain", null]])
    expect(shape("", [])).toEqual([])
  })
})
