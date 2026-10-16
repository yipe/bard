import { describe, expect, it } from "vitest"
import { hms, initials, mmss } from "./format"

describe("mmss", () => {
  it.each([
    [0, "00:00"],
    [5.9, "00:05"],
    [65, "01:05"],
    [3599, "59:59"],
    [3600, "60:00"],
    [3725, "62:05"],
    [-3, "00:00"],
  ])("%s → %s", (s, out) => expect(mmss(s)).toBe(out))
})

describe("initials", () => {
  it.each([
    ["laura", "L"],
    ["Priya Shah", "PS"],
    ["  Rowan  Ellis Hart ", "RH"],
    ["", "?"],
    ["   ", "?"],
  ])("%j → %s", (name, out) => expect(initials(name)).toBe(out))
})

describe("hms", () => {
  it.each([
    [0, "0:00:00"],
    [65, "0:01:05"],
    [3725.7, "1:02:05"],
    [57600, "16:00:00"],
    [-1, "0:00:00"],
  ])("%s → %s", (s, out) => expect(hms(s)).toBe(out))
})
