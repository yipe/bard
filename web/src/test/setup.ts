import "@testing-library/jest-dom/vitest"
import { cleanup } from "@testing-library/react"
import { afterEach } from "vitest"

afterEach(cleanup)

// jsdom lacks these layout APIs; components call them but tests don't depend on their effect.
Element.prototype.scrollIntoView ??= function () {}
Element.prototype.scrollTo ??= function () {} // react-virtuoso scrolls the list on new items
globalThis.ResizeObserver ??= class {
  observe() {}
  unobserve() {}
  disconnect() {}
}
