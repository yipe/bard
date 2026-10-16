import { render, screen } from "@testing-library/react"
import { beforeEach, describe, expect, it } from "vitest"
import { TooltipProvider } from "@/components/ui/tooltip"
import { applyEvent, useStore } from "@/lib/store"
import { message } from "@/test/fixtures"
import { ExportLinks } from "./ExportLinks"

const initial = useStore.getState()
beforeEach(() => useStore.setState(initial, true))

const renderLinks = () =>
  render(
    <TooltipProvider>
      <ExportLinks />
    </TooltipProvider>,
  )

describe("ExportLinks", () => {
  it("is disabled with no messages", () => {
    renderLinks()
    expect(screen.queryAllByRole("link")).toHaveLength(0)
    expect(screen.getByRole("button", { name: /Transcript \(\.md\)/ })).toBeDisabled()
    expect(screen.getByRole("button", { name: /Data \(\.json\)/ })).toBeDisabled()
  })

  it("links to the current transcript downloads once there are messages", () => {
    applyEvent({ type: "message.created", data: message({ id: "m1", text: "hi" }) })
    renderLinks()
    const md = screen.getByRole("link", { name: /Transcript \(\.md\)/ })
    const json = screen.getByRole("link", { name: /Data \(\.json\)/ })
    expect(md).toHaveAttribute("href", "/api/transcript.md")
    expect(json).toHaveAttribute("href", "/api/transcript.json")
    expect(md).toHaveAttribute("download")
  })
})
