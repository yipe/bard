import { describe, expect, it } from "vitest"
import { ago } from "./time"

describe("ago", () => {
  const now = Date.UTC(2026, 9, 17, 20, 0, 0)
  const s = now / 1000

  it("counts minutes and hours, then shows the date", () => {
    expect(ago(s - 5, now)).toBe("just now")
    expect(ago(s - 60, now)).toBe("1 min ago")
    expect(ago(s - 59 * 60, now)).toBe("59 min ago")
    expect(ago(s - 3600, now)).toBe("1 h ago")
    expect(ago(s - 23 * 3600, now)).toBe("23 h ago")
    expect(ago(s - 3 * 86400, now)).toMatch(/14/)
  })

  it("treats a clock running slightly behind as just now", () => {
    expect(ago(s + 30, now)).toBe("just now")
  })
})
