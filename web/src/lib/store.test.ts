import { beforeEach, describe, expect, it } from "vitest"
import { message, player, session, voice, snapshotBase } from "@/test/fixtures"
import { useCampaign } from "./campaignStore"
import { applyEvent, useSpeaking, useStore } from "./store"

const initial = useStore.getState()
const state = () => useStore.getState()

beforeEach(() => {
  useStore.setState(initial, true)
  useSpeaking.setState({ voiceId: null })
})

describe("applyEvent", () => {
  it("snapshot replaces all state", () => {
    applyEvent({ type: "message.created", data: message({ id: "old" }) })
    applyEvent({ type: "player.upserted", data: player({ id: "old" }) })
    applyEvent({
      type: "snapshot",
      data: {
        ...snapshotBase(),
        session: session({ t: 12 }),
        players: [player({ id: "p1" })],
        voices: [voice({ id: "v1" })],
        messages: [message({ id: "m1" })],
      },
    })
    const s = state()
    expect(s.loaded).toBe(true)
    expect(s.session?.t).toBe(12)
    expect(s.players.map((p) => p.id)).toEqual(["p1"])
    expect(s.voices.map((v) => v.id)).toEqual(["v1"])
    expect(s.messages.map((m) => m.id)).toEqual(["m1"])
  })

  it("message.created is idempotent by id", () => {
    applyEvent({ type: "message.created", data: message({ id: "m1", text: "a" }) })
    applyEvent({ type: "message.created", data: message({ id: "m1", text: "b" }) })
    expect(state().messages).toHaveLength(1)
    expect(state().messages[0].text).toBe("b")
  })

  it("messages.updated patches a batch in one update, ignores unknown ids, keeps other fields", () => {
    const fix = { start: 0, end: 1, heard: "y" }
    applyEvent({ type: "message.created", data: message({ id: "m1", text: "x", fixes: [fix] }) })
    applyEvent({ type: "message.created", data: message({ id: "m2", voice_id: "v1" }) })
    let updates = 0
    const unsub = useStore.subscribe(() => updates++)
    applyEvent({
      type: "messages.updated",
      data: [
        { id: "m1", provisional: true },
        { id: "m2", voice_id: "v2", text: "hi" },
        { id: "nope", text: "ignored" },
      ],
    })
    unsub()
    expect(updates).toBe(1)
    const [m1, m2] = state().messages
    expect(m1).toMatchObject({ provisional: true, text: "x", fixes: [fix] })
    expect(m2).toMatchObject({ voice_id: "v2", text: "hi" })
    expect(state().messages).toHaveLength(2)
    expect(state().bumps).toEqual({ m1: 1, m2: 1 })
  })

  it("message.created keeps t_start order and stays addressable after an out-of-order insert", () => {
    applyEvent({ type: "message.created", data: message({ id: "a", t_start: 1 }) })
    applyEvent({ type: "message.created", data: message({ id: "c", t_start: 3 }) })
    applyEvent({ type: "message.created", data: message({ id: "b", t_start: 2 }) })
    expect(state().messages.map((m) => m.id)).toEqual(["a", "b", "c"])
    applyEvent({ type: "messages.updated", data: [{ id: "c", text: "late" }] })
    const [a, b, c] = state().messages
    expect([a.text, b.text, c.text]).toEqual(["", "", "late"])
  })

  it("a snapshot of another campaign drops the open glossary entry; the same campaign keeps it", () => {
    const snap = { ...snapshotBase(), session: session(), players: [], voices: [], messages: [] }
    applyEvent({ type: "snapshot", data: snap })
    useCampaign.setState({ selected: "Elara" })
    applyEvent({ type: "snapshot", data: snap })
    expect(useCampaign.getState().selected).toBe("Elara")
    applyEvent({ type: "snapshot", data: { ...snap, campaign: "c2" } })
    expect(useCampaign.getState().selected).toBeNull()
    expect(state().campaign).toBe("c2")
  })

  it("snapshot sorts messages only if they arrive out of order", () => {
    const ordered = [message({ id: "a", t_start: 1 }), message({ id: "b", t_start: 2 })]
    applyEvent({
      type: "snapshot",
      data: { ...snapshotBase(), session: session(), players: [], voices: [], messages: ordered },
    })
    expect(state().messages).toBe(ordered)
    applyEvent({
      type: "snapshot",
      data: {
        ...snapshotBase(),
        session: session(),
        players: [],
        voices: [],
        messages: [...ordered].reverse(),
      },
    })
    expect(state().messages.map((m) => m.id)).toEqual(["a", "b"])
  })

  it("lookups survive the store being replaced outside applyEvent", () => {
    applyEvent({ type: "message.created", data: message({ id: "m1" }) })
    useStore.setState({ messages: [message({ id: "other" }), message({ id: "m1", text: "fresh" })] })
    applyEvent({ type: "messages.updated", data: [{ id: "m1", provisional: true }] })
    expect(state().messages[1]).toMatchObject({ id: "m1", text: "fresh", provisional: true })
  })

  it("messages.updated with only unknown ids changes nothing", () => {
    applyEvent({ type: "message.created", data: message({ id: "m1" }) })
    const before = state().messages
    applyEvent({ type: "messages.updated", data: [{ id: "nope", text: "x" }] })
    expect(state().messages).toBe(before)
  })

  it("voice.upserted / voice.removed", () => {
    applyEvent({ type: "voice.upserted", data: voice({ id: "v1" }) })
    applyEvent({ type: "voice.upserted", data: voice({ id: "v1", status: "named", player_id: "p1" }) })
    applyEvent({ type: "voice.upserted", data: voice({ id: "v2" }) })
    expect(state().voices).toHaveLength(2)
    expect(state().voices[0].player_id).toBe("p1")
    applyEvent({ type: "voice.removed", data: { id: "v1", merged_into: "v2" } })
    expect(state().voices.map((v) => v.id)).toEqual(["v2"])
  })

  it("player.upserted / player.removed", () => {
    applyEvent({ type: "player.upserted", data: player({ id: "p1", name: "Priya" }) })
    applyEvent({ type: "player.upserted", data: player({ id: "p1", name: "Priya B" }) })
    expect(state().players).toEqual([player({ id: "p1", name: "Priya B" })])
    applyEvent({ type: "player.removed", data: { id: "p1" } })
    expect(state().players).toEqual([])
  })

  it("session.updated replaces the session including training and error", () => {
    applyEvent({ type: "session.updated", data: session({ status: "idle" }) })
    const next = session({ status: "running", training: { player_id: "p1" }, error: "models failed" })
    applyEvent({ type: "session.updated", data: next })
    expect(state().session).toEqual(next)
  })

  it("speaker.active updates speaking state without touching the main store", () => {
    applyEvent({ type: "message.created", data: message({ id: "m1" }) })
    const before = state()
    applyEvent({ type: "speaker.active", data: { voice_id: "v1", confidence: 0.9 } })
    expect(useSpeaking.getState().voiceId).toBe("v1")
    expect(state()).toBe(before)
    applyEvent({ type: "speaker.active", data: { voice_id: null, confidence: 0 } })
    expect(useSpeaking.getState().voiceId).toBeNull()
  })
})
