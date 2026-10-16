import { describe, expect, it } from "vitest"
import { dateTime } from "./format"
import { sessionIdStartedAt, sourceLabel } from "./source"

describe("sessionIdStartedAt", () => {
  it("parses the server's local-time session id", () => {
    expect(sessionIdStartedAt("2026-10-08_153005-ab12")).toBe(
      new Date(2026, 9, 8, 15, 30, 5).getTime() / 1000,
    )
  })

  it("returns null for ids in another shape", () => {
    expect(sessionIdStartedAt("abc")).toBeNull()
    expect(sessionIdStartedAt("s1")).toBeNull()
  })
})

describe("sourceLabel", () => {
  it("labels a session replay with its start time and speed", () => {
    const when = dateTime(new Date(2026, 9, 8, 15, 30, 5).getTime() / 1000)
    expect(sourceLabel({ kind: "session", id: "2026-10-08_153005-ab12", speed: 2 })).toBe(
      `Replay of ${when} · 2×`,
    )
  })

  it("falls back to the id when it carries no date", () => {
    expect(sourceLabel({ kind: "session", id: "weird", speed: 1 })).toBe("Replay of weird · 1×")
  })

  it("labels files and mics as before", () => {
    expect(sourceLabel({ kind: "file", name: "table.wav", speed: 1 })).toBe("table.wav")
    expect(sourceLabel({ kind: "file", name: "table.wav", speed: 1.5 })).toBe("table.wav · 1.5×")
    expect(sourceLabel({ kind: "mic", device: null })).toBe("Default microphone")
    expect(sourceLabel({ kind: "mic", device: "USB Mic" })).toBe("USB Mic")
    expect(sourceLabel(null)).toBe("No source")
  })
})
