import { act, render, screen, waitFor, within } from "@testing-library/react"
import { afterEach, describe, expect, it } from "vitest"
import { meterPosition, resetAudio, SEGMENTS, zoneOf } from "@/lib/audio"
import { applyEvent } from "@/lib/store"
import type { AudioLevel, LevelZone } from "@/lib/types"
import { LevelMeter, MeterView } from "./LevelMeter"

const level = (db: number | null, extra: Partial<AudioLevel> = {}): AudioLevel => ({
  source: "session",
  db,
  peak_db: db,
  clipping: false,
  zone: db == null ? "off" : zoneOf(db),
  ...extra,
})

/** [zone, lit] for every segment of the rendered meter. */
function segments(container: HTMLElement) {
  return [...container.querySelectorAll<HTMLElement>("[data-segment]")].map((s) => ({
    zone: s.dataset.zone as LevelZone,
    lit: s.dataset.lit === "true",
    cls: s.className,
  }))
}

afterEach(resetAudio)

describe("LevelMeter", () => {
  it("colours each segment by the zone it sits in: grey-blue below −30, green to −12, amber to −3, red above", () => {
    expect(SEGMENTS.map((s) => s.zone)).toEqual([
      "silent",
      "silent",
      "quiet",
      "quiet",
      "quiet",
      "good",
      "good",
      "good",
      "hot",
      "hot",
      "clip",
    ])
    expect(SEGMENTS.filter((s) => s.zone === "quiet").every((s) => s.to <= -30)).toBe(true)
    expect(SEGMENTS.filter((s) => s.zone === "good").map((s) => [s.from, s.to])).toEqual([
      [-30, -24],
      [-24, -18],
      [-18, -12],
    ])
    // At −20 dBFS (good) the quiet segments stay grey-blue, the good ones are green, nothing above.
    const { container } = render(<MeterView level={level(-20)} />)
    const segs = segments(container)
    for (const s of segs.filter((s) => s.lit)) {
      if (s.zone === "quiet") expect(s.cls).toContain("bg-slate-300")
      if (s.zone === "good") expect(s.cls).toContain("bg-emerald-500")
    }
    expect(segs.filter((s) => s.lit).map((s) => s.zone)).toEqual([
      "silent",
      "silent",
      "quiet",
      "quiet",
      "quiet",
      "good",
      "good",
    ])
  })

  it("lights amber and red segments when hot or clipping, and flashes on clip", () => {
    const hot = segments(render(<MeterView level={level(-6)} />).container).filter((s) => s.lit)
    expect(hot.at(-1)).toMatchObject({ zone: "hot" })
    expect(hot.at(-1)!.cls).toContain("bg-amber-400")

    const { container } = render(<MeterView level={level(-1, { clipping: true, zone: "clip" })} />)
    const clip = segments(container).filter((s) => s.lit)
    expect(clip).toHaveLength(SEGMENTS.length)
    expect(clip.at(-1)!.cls).toContain("bg-red-500")
    expect(within(container).getByRole("meter")).toHaveClass("animate-clip-flash")
  })

  it.each([
    [-60, ""],
    [-40, "Too quiet"],
    [-20, "Good"],
    [-8, "Too loud"],
    [-1, "Clipping!"],
  ])("at %s dBFS the label reads %j", (db, label) => {
    render(<MeterView level={level(db)} />)
    if (label) expect(screen.getByTestId("level-label")).toHaveTextContent(label)
    else expect(screen.queryByTestId("level-label")).toBeNull()
  })

  it("places the peak tick on the same scale as the segments", () => {
    expect(meterPosition(-70)).toBe(0)
    expect(meterPosition(-30)).toBeCloseTo(5 / 11)
    expect(meterPosition(-12)).toBeCloseTo(8 / 11)
    expect(meterPosition(0)).toBe(1)
    expect(meterPosition(5)).toBe(1)
    expect(meterPosition(-90)).toBe(0)
  })

  it("is empty when the mic is off", () => {
    const { container } = render(<MeterView level={level(null)} />)
    expect(segments(container).some((s) => s.lit)).toBe(false)
    expect(screen.getByRole("meter")).toHaveAttribute("aria-valuetext", "Off")
    expect(screen.queryByTestId("peak")).toBeNull()
  })

  it("follows live audio.level events for its source only (drawn on the next frame)", async () => {
    render(<LevelMeter source="test" />)
    act(() => {
      applyEvent({ type: "audio.level", data: level(-20, { source: "session" }) })
      applyEvent({ type: "audio.level", data: level(-40, { source: "test" }) })
      applyEvent({ type: "audio.level", data: level(-8, { source: "test", peak_db: -4 }) })
    })
    await waitFor(() => expect(screen.getByTestId("level-label")).toHaveTextContent("Too loud"))
    expect(screen.getByRole("meter")).toHaveAttribute("aria-valuenow", "-8")
    expect(screen.getByTestId("peak")).toBeInTheDocument()
    act(() => applyEvent({ type: "audio.level", data: level(null, { source: "test" }) }))
    await waitFor(() => expect(screen.getByRole("meter")).toHaveAttribute("aria-valuetext", "Off"))
  })
})
