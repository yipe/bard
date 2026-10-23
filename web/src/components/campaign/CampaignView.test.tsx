import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import { toast } from "sonner"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { Toaster } from "@/components/ui/sonner"
import { TooltipProvider } from "@/components/ui/tooltip"
import { REFETCH_DEBOUNCE_MS, useCampaign } from "@/lib/campaignStore"
import { applyEvent, useStore } from "@/lib/store"
import type { EntityReview } from "@/lib/api"
import type { CampaignSuggestions, Entity } from "@/lib/types"
import { campaign, campaignJob, entity } from "@/test/fixtures"
import { bodyOf, httpError, mockFetch, type FetchCall } from "@/test/mockFetch"
import { CampaignView } from "./CampaignView"

const ENTITIES: Entity[] = [
  entity({
    name: "Elara",
    aliases: ["Ellie"],
    heard_as: ["Dilara"],
    heard_as_suggested: ["Ilara", "Dallara"],
    heard_as_rejected: ["Clara"],
    facts: ["Met the party in [[Tarsis|the old city]]", "Sister of [[Gorm]]"],
    suggested_facts: ["Fears fire", "Hates the Ashen Order"],
  }),
  entity({ name: "Tarsis", type: "place", path: "Places/Tarsis.md" }),
  entity({ name: "Mystery Box", type: "item", status: "suggested" }),
  entity({ name: "Fireball", type: "spell" }),
  entity({ name: "Broken Note", type: "term", read_only: true, description: "??" }),
]

const SUGGESTIONS: CampaignSuggestions = {
  misheard: [{ entity: "Elara", heard: "Delara", count: 9, example: "and Delara said no" }],
  unknown: [
    { heard: "Olly", count: 21, example: "Olly casts a spell" },
    { heard: "T-Mobile", count: 2, example: "my T-Mobile bill" },
  ],
}

const initialCampaign = useCampaign.getState()
const initialLive = useStore.getState()

/**
 * A tiny fake of the campaign API: PATCH merges the body into the entry, /review and /facts apply
 * their change server-side. With `delayMs`, writes resolve after a delay (in order) and `maxInFlight`
 * reports how many overlapped.
 */
function server(overrides: (c: FetchCall) => unknown = () => undefined, { delayMs = 0 } = {}) {
  let entities = ENTITIES.map((e) => ({ ...e }))
  let ignored = ["Okay"]
  const byUrl = (url: string) => decodeURIComponent(url.split("/api/campaign/entities/")[1].split("/")[0])
  const update = (name: string, change: (e: Entity) => Entity) => {
    const e = change(entities.find((x) => x.name === name)!)
    entities = entities.map((x) => (x.name === name ? e : x))
    return e
  }
  const respond = (c: FetchCall) => {
    const o = overrides(c)
    if (o !== undefined) return o
    if (c.method === "GET" && c.url === "/api/campaign")
      return campaign({ entities, facts: ["The moon is red"], ignored })
    if (c.method === "GET" && c.url === "/api/campaign/suggestions")
      return { ...SUGGESTIONS, unknown: SUGGESTIONS.unknown.filter((u) => !ignored.includes(u.heard)) }
    if (c.method === "POST" && c.url === "/api/campaign/ignore")
      return (ignored = [...ignored, ...bodyOf<{ words: string[] }>(c).words])
    if (c.method === "DELETE" && c.url.startsWith("/api/campaign/ignore/")) {
      const word = decodeURIComponent(c.url.slice("/api/campaign/ignore/".length))
      return (ignored = ignored.filter((w) => w !== word))
    }
    if (c.method === "PATCH") return update(byUrl(c.url), (e) => ({ ...e, ...bodyOf<Partial<Entity>>(c) }))
    if (c.method === "POST" && c.url.endsWith("/review")) {
      const r = bodyOf<EntityReview>(c)
      const touched = [...(r.accept_spellings ?? []), ...(r.reject_spellings ?? [])]
      const facts = [...(r.accept_facts ?? []), ...(r.reject_facts ?? [])]
      return update(byUrl(c.url), (e) => ({
        ...e,
        heard_as: [...e.heard_as, ...(r.accept_spellings ?? [])],
        heard_as_rejected: [...e.heard_as_rejected, ...(r.reject_spellings ?? [])],
        heard_as_suggested: e.heard_as_suggested.filter((x) => !touched.includes(x)),
        facts: [...e.facts, ...(r.accept_facts ?? [])],
        suggested_facts: e.suggested_facts.filter((x) => !facts.includes(x)),
      }))
    }
    if (c.method === "POST" && c.url.startsWith("/api/campaign/entities/") && c.url.endsWith("/facts"))
      return update(byUrl(c.url), (e) => ({ ...e, facts: [...e.facts, bodyOf<{ text: string }>(c).text] }))
    if (c.method === "POST" && c.url === "/api/campaign/entities") return entity(bodyOf<Entity>(c))
    if (c.method === "DELETE") return undefined
    if (c.method === "POST" && c.url === "/api/campaign/import")
      return campaignJob({ id: "j1", title: bodyOf<{ title: string }>(c).title })
    return httpError(500, `unexpected ${c.method} ${c.url}`)
  }
  let inFlight = 0
  const stats = { maxInFlight: 0 }
  const mock = mockFetch(async (c) => {
    if (c.method === "GET" || !delayMs) return respond(c)
    inFlight++
    stats.maxInFlight = Math.max(stats.maxInFlight, inFlight)
    // Executor form: Promise.withResolvers isn't in this project's ES2023 lib.
    await new Promise((resolve) => setTimeout(resolve, delayMs))
    inFlight--
    return respond(c)
  })
  return { ...mock, stats, entity: (name: string) => entities.find((e) => e.name === name) }
}

const flush = () => act(async () => {})

/** The Undo button of the toast with this text (toasts outlive tests in sonner's global state). */
async function undoFor(text: string) {
  const toastEl = (await screen.findByText(text)).closest("li")!
  return within(toastEl).getByRole("button", { name: "Undo" })
}

async function renderView() {
  const utils = render(
    <TooltipProvider>
      <CampaignView />
    </TooltipProvider>,
  )
  await flush()
  return utils
}

const glossary = () => within(screen.getByRole("list", { name: "Glossary" }))
const shownNames = () =>
  glossary()
    .queryAllByRole("button")
    .map((b) => b.getAttribute("data-entity"))

async function open(name: string) {
  fireEvent.click(glossary().getByRole("button", { name: new RegExp(`^${name}`) }))
  await flush()
}

beforeEach(() => {
  useCampaign.setState(initialCampaign, true)
  useStore.setState(initialLive, true)
})
afterEach(() => {
  vi.restoreAllMocks()
  toast.dismiss() // sonner keeps toasts in module state across tests
})

describe("glossary list", () => {
  it("searches names, aliases and heard-as, and filters by type", async () => {
    server()
    await renderView()
    expect(screen.getByText("/Users/me/Vault")).toBeInTheDocument()
    expect(screen.getByRole("link", { name: /Open in Obsidian/ })).toHaveAttribute(
      "href",
      "obsidian://open?vault=abc123",
    )
    expect(shownNames()).toEqual(["Broken Note", "Elara", "Fireball", "Mystery Box", "Tarsis"])

    const search = screen.getByRole("textbox", { name: "Search glossary" })
    fireEvent.change(search, { target: { value: "ellie" } })
    expect(shownNames()).toEqual(["Elara"])
    fireEvent.change(search, { target: { value: "dilara" } })
    expect(shownNames()).toEqual(["Elara"])
    fireEvent.change(search, { target: { value: "" } })

    fireEvent.click(screen.getByRole("button", { name: "Places" }))
    expect(shownNames()).toEqual(["Tarsis"])
    fireEvent.click(screen.getByRole("button", { name: "All" }))
    expect(shownNames()).toHaveLength(5)
  })

  it("Needs review filters to entries awaiting review and shows their count", async () => {
    server()
    await renderView()
    const review = screen.getByRole("button", { name: /^Needs review/ })
    expect(within(review).getByTestId("review-count")).toHaveTextContent("2")
    fireEvent.click(review)
    expect(shownNames()).toEqual(["Elara", "Mystery Box"])
    expect(glossary().getAllByLabelText("Needs review")).toHaveLength(2)
    expect(
      within(glossary().getByRole("button", { name: /^Mystery Box/ })).getByText("suggested"),
    ).toBeInTheDocument()
  })

  it("creates an entry and selects it", async () => {
    const { writes } = server()
    await renderView()
    fireEvent.click(screen.getByRole("button", { name: /New entry/ }))
    fireEvent.change(screen.getByRole("textbox", { name: "Entry name" }), { target: { value: "Gorm" } })
    fireEvent.change(screen.getByRole("combobox", { name: "Entry type" }), { target: { value: "pc" } })
    fireEvent.click(screen.getByRole("button", { name: "Create" }))
    await flush()
    expect(writes()).toEqual([
      { method: "POST", url: "/api/campaign/entities", body: { name: "Gorm", type: "pc" } },
    ])
    expect(shownNames()).toContain("Gorm")
    expect(screen.getByRole("textbox", { name: "Name" })).toHaveValue("Gorm")
  })

  it("shows the server's error when a name is taken", async () => {
    const { writes } = server((c) =>
      c.method === "POST" && c.url === "/api/campaign/entities"
        ? httpError(400, "Name already used")
        : undefined,
    )
    const error = vi.spyOn(toast, "error")
    await renderView()
    fireEvent.click(screen.getByRole("button", { name: /New entry/ }))
    fireEvent.change(screen.getByRole("textbox", { name: "Entry name" }), { target: { value: "Ellie" } })
    fireEvent.click(screen.getByRole("button", { name: "Create" }))
    await flush()
    expect(writes()).toHaveLength(1)
    expect(error).toHaveBeenCalledWith("Name already used")
  })
})

describe("entry editor", () => {
  it("opens the entry in the detail pane, keeps the glossary clickable, and Close deselects", async () => {
    server()
    await renderView()
    expect(screen.queryByRole("textbox", { name: "Name" })).toBeNull()
    await open("Elara")
    expect(screen.getByRole("textbox", { name: "Name" })).toHaveValue("Elara")
    await open("Tarsis")
    expect(screen.getByRole("textbox", { name: "Name" })).toHaveValue("Tarsis")
    expect(useCampaign.getState().selected).toBe("Tarsis")
    fireEvent.click(screen.getByRole("button", { name: "Close" }))
    await waitFor(() => expect(screen.queryByRole("textbox", { name: "Name" })).toBeNull())
    expect(useCampaign.getState().selected).toBeNull()
  })

  it("renames via PATCH {name} and keeps the entry selected", async () => {
    const { writes } = server()
    await renderView()
    await open("Mystery Box")
    const name = screen.getByRole("textbox", { name: "Name" })
    fireEvent.change(name, { target: { value: "Puzzle Box" } })
    fireEvent.blur(name)
    await flush()
    expect(writes()).toEqual([
      { method: "PATCH", url: "/api/campaign/entities/Mystery%20Box", body: { name: "Puzzle Box" } },
    ])
    expect(shownNames()).toContain("Puzzle Box")
    expect(shownNames()).not.toContain("Mystery Box")
    expect(screen.getByRole("textbox", { name: "Name" })).toHaveValue("Puzzle Box")
  })

  it("accepts and rejects suggested spellings through the atomic review endpoint", async () => {
    const { writes } = server()
    await renderView()
    await open("Elara")
    expect(
      screen.getByText("How speech-to-text misspells it; Troubadour fixes these in new transcripts."),
    ).toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: "Accept spelling Ilara" }))
    await flush()
    expect(writes().at(-1)).toEqual({
      method: "POST",
      url: "/api/campaign/entities/Elara/review",
      body: { accept_spellings: ["Ilara"] },
    })
    expect(screen.getByRole("button", { name: "Remove spelling Ilara" })).toBeInTheDocument()

    fireEvent.click(screen.getByRole("button", { name: "Reject spelling Dallara" }))
    await flush()
    expect(writes().at(-1)).toEqual({
      method: "POST",
      url: "/api/campaign/entities/Elara/review",
      body: { reject_spellings: ["Dallara"] },
    })
    expect(screen.queryByTestId("suggested-spelling")).toBeNull()
  })

  it("two rapid spelling accepts both stick: requests run one at a time, in click order", async () => {
    const { writes, stats, entity: saved } = server(undefined, { delayMs: 20 })
    await renderView()
    await open("Elara")
    fireEvent.click(screen.getByRole("button", { name: "Accept spelling Dallara" }))
    fireEvent.click(screen.getByRole("button", { name: "Accept spelling Ilara" }))
    await act(() => vi.waitFor(() => expect(writes()).toHaveLength(2)))
    await act(() =>
      vi.waitFor(() =>
        expect(screen.getByRole("button", { name: "Remove spelling Ilara" })).toBeInTheDocument(),
      ),
    )
    expect(writes().map((w) => [w.url, w.body])).toEqual([
      ["/api/campaign/entities/Elara/review", { accept_spellings: ["Dallara"] }],
      ["/api/campaign/entities/Elara/review", { accept_spellings: ["Ilara"] }],
    ])
    expect(stats.maxInFlight).toBe(1)
    expect(saved("Elara")?.heard_as).toEqual(["Dilara", "Dallara", "Ilara"])
    expect(useCampaign.getState().campaign?.entities.find((e) => e.name === "Elara")?.heard_as).toEqual([
      "Dilara",
      "Dallara",
      "Ilara",
    ])
  })

  it("two rapid chip adds: the second PATCH is built from the first one's result", async () => {
    const { writes, stats } = server(undefined, { delayMs: 20 })
    await renderView()
    await open("Elara")
    const input = screen.getByRole("textbox", { name: "Add alias" })
    for (const v of ["Lara", "El"]) {
      fireEvent.change(input, { target: { value: v } })
      fireEvent.keyDown(input, { key: "Enter" })
    }
    await act(() => vi.waitFor(() => expect(writes()).toHaveLength(2)))
    await act(() =>
      vi.waitFor(() => expect(screen.getByRole("button", { name: "Remove alias El" })).toBeInTheDocument()),
    )
    expect(writes().map((w) => w.body)).toEqual([
      { aliases: ["Ellie", "Lara"] },
      { aliases: ["Ellie", "Lara", "El"] },
    ])
    expect(stats.maxInFlight).toBe(1)
  })

  it("a write queued behind a rename goes to the new name", async () => {
    const { writes } = server(undefined, { delayMs: 20 })
    await renderView()
    await open("Fireball")
    const name = screen.getByRole("textbox", { name: "Name" })
    fireEvent.change(name, { target: { value: "Fire Bolt" } })
    fireEvent.blur(name)
    const alias = screen.getByRole("textbox", { name: "Add alias" })
    fireEvent.change(alias, { target: { value: "FB" } })
    fireEvent.keyDown(alias, { key: "Enter" })
    await act(() => vi.waitFor(() => expect(writes()).toHaveLength(2)))
    await act(() => vi.waitFor(() => expect(shownNames()).toContain("Fire Bolt")))
    expect(writes().map((w) => [w.url, w.body])).toEqual([
      ["/api/campaign/entities/Fireball", { name: "Fire Bolt" }],
      ["/api/campaign/entities/Fire%20Bolt", { aliases: ["FB"] }],
    ])
  })

  it("a refetch waits for in-flight writes, so it can't land an older snapshot between two writes", async () => {
    const { calls, writes } = server(undefined, { delayMs: REFETCH_DEBOUNCE_MS + 200 })
    await renderView()
    await open("Elara")
    const campaignGets = () => calls.filter((c) => c.method === "GET" && c.url === "/api/campaign").length
    expect(campaignGets()).toBe(1)
    fireEvent.click(screen.getByRole("button", { name: "Accept spelling Dallara" }))
    act(() => applyEvent({ type: "campaign.updated", data: {} }))
    // Debounce elapsed, write still in flight: no refetch yet.
    await act(() => new Promise((resolve) => setTimeout(resolve, REFETCH_DEBOUNCE_MS + 50)))
    expect(writes()).toHaveLength(1)
    expect(campaignGets()).toBe(1)
    await act(() => vi.waitFor(() => expect(campaignGets()).toBe(2), { timeout: 2000 }))
    expect(useCampaign.getState().campaign?.entities.find((e) => e.name === "Elara")?.heard_as).toContain(
      "Dallara",
    )
  })

  it("a remove queued behind an add removes from the latest list", async () => {
    const { writes } = server(undefined, { delayMs: 20 })
    await renderView()
    await open("Elara")
    const input = screen.getByRole("textbox", { name: "Add alias" })
    fireEvent.change(input, { target: { value: "Lara" } })
    fireEvent.keyDown(input, { key: "Enter" })
    fireEvent.click(screen.getByRole("button", { name: "Remove alias Ellie" }))
    await act(() => vi.waitFor(() => expect(writes()).toHaveLength(2)))
    await act(() =>
      vi.waitFor(() => expect(screen.queryByRole("button", { name: "Remove alias Ellie" })).toBeNull()),
    )
    expect(writes().map((w) => w.body)).toEqual([{ aliases: ["Ellie", "Lara"] }, { aliases: ["Lara"] }])
  })

  it("accepts, rejects and edits-then-accepts suggested facts", async () => {
    const { writes } = server()
    await renderView()
    await open("Elara")
    const facts = ["Met the party in [[Tarsis|the old city]]", "Sister of [[Gorm]]"]

    fireEvent.click(screen.getByRole("button", { name: "Accept fact: Fears fire" }))
    await flush()
    expect(writes().at(-1)).toEqual({
      method: "POST",
      url: "/api/campaign/entities/Elara/review",
      body: { accept_facts: ["Fears fire"] },
    })

    fireEvent.click(screen.getByRole("button", { name: "Reject fact: Hates the Ashen Order" }))
    await flush()
    expect(writes().at(-1)).toEqual({
      method: "POST",
      url: "/api/campaign/entities/Elara/review",
      body: { reject_facts: ["Hates the Ashen Order"] },
    })
    expect(useCampaign.getState().campaign?.entities.find((e) => e.name === "Elara")).toMatchObject({
      facts: [...facts, "Fears fire"],
      suggested_facts: [],
    })
  })

  it("edit-then-accept adds the edited text, then rejects the original suggestion", async () => {
    const { writes } = server()
    await renderView()
    await open("Elara")
    fireEvent.click(screen.getByRole("button", { name: "Edit fact: Fears fire" }))
    fireEvent.change(screen.getByRole("textbox", { name: "Edit suggested fact" }), {
      target: { value: "Fears fire since the [[Tarsis]] blaze" },
    })
    fireEvent.click(screen.getByRole("button", { name: /Accept edited/ }))
    await act(() => vi.waitFor(() => expect(writes()).toHaveLength(2)))
    await flush()
    expect(writes()).toEqual([
      {
        method: "POST",
        url: "/api/campaign/entities/Elara/facts",
        body: { text: "Fears fire since the [[Tarsis]] blaze" },
      },
      { method: "POST", url: "/api/campaign/entities/Elara/review", body: { reject_facts: ["Fears fire"] } },
    ])
    expect(useCampaign.getState().campaign?.entities.find((e) => e.name === "Elara")).toMatchObject({
      facts: [
        "Met the party in [[Tarsis|the old city]]",
        "Sister of [[Gorm]]",
        "Fears fire since the [[Tarsis]] blaze",
      ],
      suggested_facts: ["Hates the Ashen Order"],
    })
  })

  it("renders [[A|B]] as B; links to entries select them, others are plain text", async () => {
    server()
    await renderView()
    await open("Elara")
    const link = screen.getByRole("link", { name: "the old city" })
    expect(screen.queryByText(/\[\[/)).toBeNull()
    expect(screen.getByText("Gorm").tagName).toBe("SPAN")
    fireEvent.click(link)
    await flush()
    expect(useCampaign.getState().selected).toBe("Tarsis")
    expect(screen.getByRole("textbox", { name: "Name" })).toHaveValue("Tarsis")
  })

  it("adds a fact via POST /facts and deletes one via PATCH facts", async () => {
    const { writes } = server((c) =>
      c.method === "POST" && c.url.endsWith("/facts")
        ? entity({ name: "Tarsis", facts: ["Walled"] })
        : undefined,
    )
    await renderView()
    await open("Tarsis")
    const input = screen.getByRole("textbox", { name: "New fact" })
    fireEvent.change(input, { target: { value: "Walled" } })
    fireEvent.click(within(input.closest("form")!).getByRole("button", { name: /Add/ }))
    await flush()
    expect(writes().at(-1)).toEqual({
      method: "POST",
      url: "/api/campaign/entities/Tarsis/facts",
      body: { text: "Walled" },
    })
    fireEvent.click(
      within(input.closest("form")!.parentElement!).getByRole("button", { name: "Delete fact 1" }),
    )
    await flush()
    expect(writes().at(-1)).toEqual({
      method: "PATCH",
      url: "/api/campaign/entities/Tarsis",
      body: { facts: [] },
    })
  })

  it("Keep confirms a suggested entry; Discard deletes it", async () => {
    const { writes } = server()
    await renderView()
    await open("Mystery Box")
    expect(screen.getByText("Troubadour suggested this from a session.")).toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: /Keep/ }))
    await flush()
    expect(writes().at(-1)).toEqual({
      method: "PATCH",
      url: "/api/campaign/entities/Mystery%20Box",
      body: { status: "confirmed" },
    })
    expect(screen.queryByText("Troubadour suggested this from a session.")).toBeNull()
  })

  it("Discard deletes a suggested entry and clears the selection", async () => {
    const { writes } = server()
    await renderView()
    await open("Mystery Box")
    fireEvent.click(screen.getByRole("button", { name: /Discard/ }))
    await flush()
    expect(writes()).toEqual([
      { method: "DELETE", url: "/api/campaign/entities/Mystery%20Box", body: undefined },
    ])
    expect(shownNames()).not.toContain("Mystery Box")
    expect(screen.queryByRole("textbox", { name: "Name" })).toBeNull()
  })

  it("Delete asks first and mentions the vault's .trash", async () => {
    const { writes } = server()
    await renderView()
    await open("Fireball")
    fireEvent.click(screen.getByRole("button", { name: /^Delete$/ }))
    expect(screen.getByRole("alert")).toHaveTextContent(".trash")
    expect(writes()).toEqual([])
    fireEvent.click(screen.getByRole("button", { name: "Confirm delete" }))
    await flush()
    expect(writes()).toEqual([{ method: "DELETE", url: "/api/campaign/entities/Fireball", body: undefined }])
  })

  it("merges into another entry after confirming", async () => {
    const { writes } = server((c) =>
      c.url.endsWith("/merge") ? entity({ name: "Elara", aliases: ["Ellie", "Fireball"] }) : undefined,
    )
    await renderView()
    await open("Fireball")
    fireEvent.click(screen.getByRole("combobox", { name: "Merge into" }))
    fireEvent.click(screen.getByRole("option", { name: /Elara/ }))
    fireEvent.click(screen.getByRole("button", { name: /Merge into…/ }))
    fireEvent.click(screen.getByRole("button", { name: "Confirm merge" }))
    await flush()
    expect(writes()).toEqual([
      { method: "POST", url: "/api/campaign/entities/Fireball/merge", body: { into: "Elara" } },
    ])
    expect(shownNames()).not.toContain("Fireball")
    expect(screen.getByRole("textbox", { name: "Name" })).toHaveValue("Elara")
  })

  it("read-only entries show a banner and disable editing", async () => {
    server()
    await renderView()
    await open("Broken Note")
    expect(screen.getByRole("status")).toHaveTextContent(
      "This note's properties can't be read. Fix it in Obsidian.",
    )
    expect(screen.getByRole("textbox", { name: "Name" })).toBeDisabled()
    expect(screen.getByRole("textbox", { name: "Description" })).toBeDisabled()
    expect(screen.getByRole("combobox", { name: "Type" })).toBeDisabled()
    expect(screen.getByRole("textbox", { name: "Add alias" })).toBeDisabled()
    expect(screen.getByRole("button", { name: /^Delete$/ })).toBeDisabled()
    // Opening the note in Obsidian still works.
    expect(screen.getAllByRole("link", { name: /Open in Obsidian/ }).at(-1)).toHaveAttribute(
      "href",
      "obsidian://open?vault=abc123&file=People%2FBroken%20Note.md",
    )
  })
})

describe("suggestions", () => {
  it("Fix it accepts the spelling; Not a match rejects it (atomic review)", async () => {
    const { writes } = server()
    await renderView()
    const row = within(document.querySelector<HTMLElement>('[data-suggestion="Delara"]')!)
    expect(row.getByText("×9")).toBeInTheDocument()
    expect(row.getByText("“and Delara said no”")).toBeInTheDocument()

    fireEvent.click(row.getByRole("button", { name: "Fix it" }))
    await flush()
    expect(writes().at(-1)).toEqual({
      method: "POST",
      url: "/api/campaign/entities/Elara/review",
      body: { accept_spellings: ["Delara"] },
    })

    fireEvent.click(row.getByRole("button", { name: "Not a match: Delara" }))
    await flush()
    expect(writes().at(-1)).toEqual({
      method: "POST",
      url: "/api/campaign/entities/Elara/review",
      body: { reject_spellings: ["Delara"] },
    })
  })

  it("✕ on an unknown name ignores it: posts {words}, hides the row at once, Undo un-ignores", async () => {
    const { writes } = server(undefined, { delayMs: 50 })
    await renderView()
    render(<Toaster />)
    const row = () => document.querySelector<HTMLElement>('[data-suggestion="T-Mobile"]')
    expect(within(row()!).getByText("×2")).toBeInTheDocument()

    fireEvent.click(within(row()!).getByRole("button", { name: "Ignore T-Mobile" }))
    expect(row()).toBeNull() // before the request has even been answered
    await waitFor(() => expect(writes()).toHaveLength(1))
    expect(writes()[0]).toEqual({
      method: "POST",
      url: "/api/campaign/ignore",
      body: { words: ["T-Mobile"] },
    })

    fireEvent.click(await undoFor('Ignored "T-Mobile"'))
    await waitFor(() => expect(writes()).toHaveLength(2))
    expect(writes()[1]).toEqual({ method: "DELETE", url: "/api/campaign/ignore/T-Mobile", body: undefined })
    await waitFor(() => expect(row()).not.toBeNull())
  })

  it("a failed ignore puts the row back and shows the error", async () => {
    server((c) => (c.url === "/api/campaign/ignore" ? httpError(500, "Vault is read-only") : undefined))
    const error = vi.spyOn(toast, "error")
    await renderView()
    fireEvent.click(screen.getByRole("button", { name: "Ignore T-Mobile" }))
    expect(document.querySelector('[data-suggestion="T-Mobile"]')).toBeNull()
    await waitFor(() => expect(error).toHaveBeenCalledWith("Vault is read-only"))
    expect(document.querySelector('[data-suggestion="T-Mobile"]')).not.toBeNull()
  })

  it("✕ on a misheard name rejects the spelling (atomic review), hides the row, and Undo un-rejects", async () => {
    const { writes } = server()
    await renderView()
    render(<Toaster />)
    fireEvent.click(screen.getByRole("button", { name: "Not a match: Delara" }))
    expect(document.querySelector('[data-suggestion="Delara"]')).toBeNull()
    await waitFor(() => expect(writes()).toHaveLength(1))
    expect(writes()[0]).toEqual({
      method: "POST",
      url: "/api/campaign/entities/Elara/review",
      body: { reject_spellings: ["Delara"] },
    })

    fireEvent.click(await undoFor(`"Delara" isn't Elara`))
    await waitFor(() => expect(writes()).toHaveLength(2))
    expect(writes()[1]).toEqual({
      method: "PATCH",
      url: "/api/campaign/entities/Elara",
      body: { heard_as_rejected: ["Clara"] },
    })
    await waitFor(() => expect(document.querySelector('[data-suggestion="Delara"]')).not.toBeNull())
  })

  it("lists ignored words in a collapsed section; ↺ stops ignoring", async () => {
    const { writes } = server()
    await renderView()
    expect(screen.queryByRole("list", { name: "Ignored words" })).toBeNull()
    fireEvent.click(screen.getByRole("button", { name: "Ignored words (1)" }))
    expect(within(screen.getByRole("list", { name: "Ignored words" })).getByText("Okay")).toBeInTheDocument()

    fireEvent.click(screen.getByRole("button", { name: "Ignore T-Mobile" }))
    await waitFor(() => expect(screen.getByRole("button", { name: "Ignored words (2)" })).toBeInTheDocument())
    fireEvent.click(screen.getByRole("button", { name: "Stop ignoring T-Mobile" }))
    await waitFor(() => expect(writes()).toHaveLength(2))
    expect(writes()[1]).toEqual({ method: "DELETE", url: "/api/campaign/ignore/T-Mobile", body: undefined })
    await waitFor(() => expect(document.querySelector('[data-suggestion="T-Mobile"]')).not.toBeNull())
    expect(screen.getByRole("button", { name: "Ignored words (1)" })).toBeInTheDocument()
  })

  it("Add creates an NPC with the heard name prefilled", async () => {
    const { writes } = server()
    await renderView()
    const row = within(document.querySelector<HTMLElement>('[data-suggestion="Olly"]')!)
    fireEvent.click(row.getByRole("button", { name: "Add" }))
    expect(row.getByRole("textbox", { name: "Entry name" })).toHaveValue("Olly")
    fireEvent.click(row.getByRole("button", { name: "Create" }))
    await flush()
    expect(writes()).toEqual([
      { method: "POST", url: "/api/campaign/entities", body: { name: "Olly", type: "npc" } },
    ])
  })

  it("Add with a corrected spelling keeps the heard one as heard_as", async () => {
    const { writes } = server()
    await renderView()
    const row = within(document.querySelector<HTMLElement>('[data-suggestion="Olly"]')!)
    fireEvent.click(row.getByRole("button", { name: "Add" }))
    fireEvent.change(row.getByRole("textbox", { name: "Entry name" }), { target: { value: "Ollie" } })
    fireEvent.click(row.getByRole("button", { name: "Create" }))
    await flush()
    expect(writes()[0].body).toEqual({ name: "Ollie", type: "npc", heard_as: ["Olly"] })
  })

  it("It's a misspelling of… adds the spelling to the chosen entry", async () => {
    const { writes } = server()
    await renderView()
    const row = within(document.querySelector<HTMLElement>('[data-suggestion="Olly"]')!)
    fireEvent.click(row.getByRole("button", { name: /misspelling of/ }))
    fireEvent.click(row.getByRole("combobox"))
    fireEvent.click(screen.getByRole("option", { name: /Elara/ }))
    fireEvent.click(row.getByRole("button", { name: "Confirm" }))
    await flush()
    expect(writes()).toEqual([
      { method: "PATCH", url: "/api/campaign/entities/Elara", body: { heard_as: ["Dilara", "Olly"] } },
    ])
  })

  it("the entry picker filters fuzzily, and by alias or heard-as spelling", async () => {
    server()
    await renderView()
    await open("Fireball")
    fireEvent.click(screen.getByRole("combobox", { name: "Merge into" }))
    const search = screen.getByRole("combobox", { name: "Search: Merge into" })
    const names = () =>
      within(screen.getByRole("listbox"))
        .queryAllByRole("option")
        .map((o) => o.textContent)
    fireEvent.change(search, { target: { value: "trss" } })
    expect(names()).toEqual(["TarsisPlace"])
    fireEvent.change(search, { target: { value: "Dilara" } }) // a heard-as spelling of Elara
    expect(names()).toEqual(["ElaraNPC"])
    fireEvent.change(search, { target: { value: "zzzz" } })
    expect(screen.getByText("No matching entry.")).toBeInTheDocument()
  })
})

describe("import notes", () => {
  it("posts {title, text}, then shows progress and the done summary from campaign.job events", async () => {
    const { writes } = server()
    await renderView()
    fireEvent.click(screen.getByRole("button", { name: /Import notes/ }))
    await flush()
    const dialog = within(screen.getByRole("dialog"))
    fireEvent.change(dialog.getByRole("textbox", { name: "Title" }), {
      target: { value: "Session 12 notes" },
    })
    fireEvent.change(dialog.getByRole("textbox", { name: "Notes" }), { target: { value: "Elara met Olly." } })
    fireEvent.click(dialog.getByRole("button", { name: /^Import$/ }))
    await flush()
    expect(writes()).toEqual([
      {
        method: "POST",
        url: "/api/campaign/import",
        body: { title: "Session 12 notes", text: "Elara met Olly." },
      },
    ])

    act(() =>
      applyEvent({
        type: "campaign.job",
        data: campaignJob({
          id: "j1",
          title: "Session 12 notes",
          state: "running",
          done: 1,
          total: 3,
          message: "Part 1",
        }),
      }),
    )
    expect(dialog.getByText("1/3")).toBeInTheDocument()
    expect(dialog.getByText("Part 1")).toBeInTheDocument()
    // Also a header chip while it runs.
    expect(document.querySelector('[data-job="j1"]')).toHaveTextContent("Session 12 notes")

    act(() =>
      applyEvent({
        type: "campaign.job",
        data: campaignJob({
          id: "j1",
          state: "done",
          done: 3,
          total: 3,
          created: ["Olly"],
          updated: ["Elara"],
        }),
      }),
    )
    expect(dialog.getByTestId("import-summary")).toHaveTextContent("1 new · 1 updated")
    expect(document.querySelector('[data-job="j1"]')).toBeNull()

    fireEvent.click(dialog.getByRole("button", { name: "Elara" }))
    await flush()
    expect(useCampaign.getState().selected).toBe("Elara")
  })

  it("shows a failed import's message", async () => {
    server()
    await renderView()
    fireEvent.click(screen.getByRole("button", { name: /Import notes/ }))
    await flush()
    const dialog = within(screen.getByRole("dialog"))
    fireEvent.change(dialog.getByRole("textbox", { name: "Notes" }), { target: { value: "x" } })
    fireEvent.click(dialog.getByRole("button", { name: /^Import$/ }))
    await flush()
    act(() =>
      applyEvent({
        type: "campaign.job",
        data: campaignJob({ id: "j1", state: "error", message: "LLM offline" }),
      }),
    )
    expect(dialog.getByRole("alert")).toHaveTextContent("LLM offline")
  })
})

describe("campaign facts", () => {
  it("adds with POST and saves edits with PUT of the whole list", async () => {
    const { writes } = server((c) =>
      c.url === "/api/campaign/facts" ? ["The moon is red", "Gods are silent"] : undefined,
    )
    await renderView()
    const input = screen.getByRole("textbox", { name: "New campaign fact" })
    fireEvent.change(input, { target: { value: "Gods are silent" } })
    fireEvent.submit(input.closest("form")!)
    await flush()
    expect(writes().at(-1)).toEqual({
      method: "POST",
      url: "/api/campaign/facts",
      body: { text: "Gods are silent" },
    })

    const card = within(input.closest("section")!)
    fireEvent.click(card.getByRole("button", { name: "Edit fact 1" }))
    const edit = card.getByRole("textbox", { name: "Edit fact" })
    fireEvent.change(edit, { target: { value: "The moon is blood red" } })
    fireEvent.blur(edit)
    await flush()
    expect(writes().at(-1)).toEqual({
      method: "PUT",
      url: "/api/campaign/facts",
      body: { facts: ["The moon is blood red", "Gods are silent"] },
    })
  })
})

describe("live updates", () => {
  it("campaign.updated refetches the campaign and suggestions (debounced) without touching the live store", async () => {
    const { calls } = server()
    await renderView()
    // The Cloud backup button loads its own status on mount; this test is about campaign refetches.
    const gets = () => calls.filter((c) => c.method === "GET" && c.url !== "/api/cloud").map((c) => c.url)
    expect(gets().sort()).toEqual(["/api/campaign", "/api/campaign/suggestions"])
    const live = useStore.getState()

    act(() => {
      applyEvent({ type: "campaign.updated", data: {} })
      applyEvent({ type: "campaign.updated", data: {} })
    })
    // Two events inside the debounce window → one refetch of each.
    await act(() => vi.waitFor(() => expect(gets()).toHaveLength(4), { timeout: REFETCH_DEBOUNCE_MS * 4 }))
    expect(gets().sort()).toEqual([
      "/api/campaign",
      "/api/campaign",
      "/api/campaign/suggestions",
      "/api/campaign/suggestions",
    ])
    expect(useStore.getState()).toBe(live)
  })
})
