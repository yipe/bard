import { render } from "@testing-library/react"
import { describe, expect, it } from "vitest"
import { FixedText } from "./FixedText"

describe("FixedText", () => {
  it("underlines spell-fixed words with what was heard", () => {
    const text = "🎲 Auggie casts Firebolt for 8"
    const at = (w: string) => text.indexOf(w)
    const { container } = render(
      <FixedText
        text={text}
        fixes={[
          { start: at("Auggie"), end: at("Auggie") + 6, heard: "augie" },
          { start: at("Firebolt"), end: at("Firebolt") + 8, heard: "fire bolt" },
        ]}
      />,
    )
    const fixes = [...container.querySelectorAll("[data-fix]")]
    expect(fixes.map((f) => [f.textContent, f.getAttribute("title")])).toEqual([
      ["Auggie", 'Heard as "augie"'],
      ["Firebolt", 'Heard as "fire bolt"'],
    ])
    expect(container).toHaveTextContent(text)
    expect(container.querySelector("mark")).toBeNull()
  })

  it("renders plain text without fixes", () => {
    const { container } = render(<FixedText text="plain" fixes={[]} />)
    expect(container).toHaveTextContent("plain")
    expect(container.querySelector("[data-fix]")).toBeNull()
  })
})
