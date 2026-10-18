import { act, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"
import { useStore } from "@/lib/store"
import type * as Store from "@/lib/store"
import { session } from "@/test/fixtures"
import App from "./App"

// No WebSocket and no URL syncing: these tests drive the store directly.
vi.mock("@/lib/route", () => ({ startRouting: () => () => {} }))
vi.mock("@/lib/store", async (original) => ({
  ...(await original<typeof Store>()),
  connect: () => () => {},
}))

const initial = useStore.getState()
afterEach(() => useStore.setState(initial, true))

describe("App", () => {
  it("marks the current tab with aria-current", () => {
    render(<App />)
    const tab = (name: string) => screen.getByRole("button", { name })
    fireEvent.click(tab("History"))
    expect(tab("History")).toHaveAttribute("aria-current", "page")
    expect(tab("Setup")).not.toHaveAttribute("aria-current")
    fireEvent.click(tab("Campaign"))
    expect(tab("Campaign")).toHaveAttribute("aria-current", "page")
    expect(tab("History")).not.toHaveAttribute("aria-current")
  })

  it("shows the server's session error as an alert", () => {
    render(<App />)
    expect(screen.queryByRole("alert")).toBeNull()
    act(() => useStore.setState({ session: session({ status: "idle", error: "Mic unplugged" }) }))
    expect(screen.getByRole("alert")).toHaveTextContent("Mic unplugged")
  })
})
