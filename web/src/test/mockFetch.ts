import { vi } from "vitest"

export interface FetchCall {
  method: string
  url: string
  body: unknown
}

/**
 * Route `fetch` through `handler`. It returns a JSON body (200), a Response (as is), or undefined
 * (204). `calls` records every request with its parsed JSON body.
 */
export function mockFetch(handler: (call: FetchCall) => unknown) {
  const calls: FetchCall[] = []
  const spy = vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const call: FetchCall = {
      method: init?.method ?? "GET",
      url: String(input),
      body: typeof init?.body === "string" ? JSON.parse(init.body) : undefined,
    }
    calls.push(call)
    const out = await handler(call)
    if (out instanceof Response) return out
    if (out === undefined) return new Response(null, { status: 204 })
    return new Response(JSON.stringify(out), { status: 200, headers: { "Content-Type": "application/json" } })
  })
  /** Requests other than GETs, in order. */
  const writes = () => calls.filter((c) => c.method !== "GET")
  return { calls, writes, spy }
}

/** The request's JSON body as `T`. Test-only: the fake server trusts what the client under test sent. */
export function bodyOf<T>(call: FetchCall): T {
  const body = call.body as T // parsed JSON from our own client; no validation in a test fake
  return body
}

export function httpError(status: number, detail: string): Response {
  return new Response(JSON.stringify({ detail }), { status, headers: { "Content-Type": "application/json" } })
}
