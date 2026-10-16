import { Fragment } from "react"
import { splitFixes } from "@/lib/fixes"
import type { Fix } from "@/lib/types"

/** A line's text with spell-fixed words underlined; what speech-to-text heard is in the tooltip. */
export function FixedText({ text, fixes }: { text: string; fixes: Fix[] }) {
  return (
    <>
      {splitFixes(text, fixes).map((p, i) =>
        p.fix ? (
          // A native title keeps this cheap: a long transcript can have many fixes.
          <span
            key={i}
            data-fix=""
            title={`Heard as "${String(p.fix.heard)}"`}
            className="cursor-help underline decoration-muted-foreground/50 decoration-dotted underline-offset-[3px]"
          >
            {p.text}
          </span>
        ) : (
          <Fragment key={i}>{p.text}</Fragment>
        ),
      )}
    </>
  )
}
