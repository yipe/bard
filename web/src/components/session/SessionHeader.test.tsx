import { act, render, screen } from "@testing-library/react"
import { beforeEach, describe, expect, it } from "vitest"
import { TooltipProvider } from "@/components/ui/tooltip"
import { dateTime } from "@/lib/format"
import { applyEvent, useStore } from "@/lib/store"
import type { Session } from "@/lib/types"
import { session } from "@/test/fixtures"
import { SessionHeader } from "./SessionHeader"

const initial = useStore.getState()
beforeEach(() => useStore.setState(initial, true))

describe("SessionHeader", () => {
  it("labels a session-replay source with the replayed session's start time and speed", () => {
    applyEvent({
      type: "session.updated",
      data: session({ source: { kind: "session", id: "2026-10-08_153005-ab12", speed: 4 } }),
    })
    render(
      <TooltipProvider>
        <SessionHeader />
      </TooltipProvider>,
    )
    const when = dateTime(new Date(2026, 9, 8, 15, 30, 5).getTime() / 1000)
    expect(screen.getByText(`Replay of ${when} · 4×`)).toBeInTheDocument()
  })

  it("shows Whisper next to the LLM: loading, then ready, or failed", () => {
    const show = (asr: Session["asr"]["state"]) =>
      applyEvent({ type: "session.updated", data: session({ asr: { model: "whisper", state: asr } }) })
    show("loading")
    render(
      <TooltipProvider>
        <SessionHeader />
      </TooltipProvider>,
    )
    expect(screen.getByLabelText("Whisper: loading")).toBeInTheDocument()
    expect(screen.getByLabelText("LLM: connected")).toBeInTheDocument()
    act(() => show("ready"))
    expect(screen.getByLabelText("Whisper: ready")).toBeInTheDocument()
    act(() => show("error"))
    expect(screen.getByLabelText("Whisper: failed")).toBeInTheDocument()
  })
})
