import { GRADE_DOT, QUALITY_ROWS } from "@/lib/audio"
import { cn } from "@/lib/utils"
import type { QualityNumbers } from "@/lib/types"

const GRADE_LABEL = { good: "good", ok: "OK", poor: "poor" } as const

/** The five quality numbers with a coloured grade dot each, and optional hints underneath. */
export function QualityRows({ quality, hints }: { quality: QualityNumbers; hints?: string[] }) {
  return (
    <div className="space-y-2">
      <dl className="grid grid-cols-[auto_1fr_auto] items-center gap-x-2 gap-y-1 text-sm">
        {QUALITY_ROWS.map((row) => {
          const grade = quality.grades[row.key]
          return (
            <div key={row.key} data-quality={row.key} className="contents">
              <span
                aria-label={`Grade: ${GRADE_LABEL[grade]}`}
                title={GRADE_LABEL[grade]}
                className={cn("size-2 rounded-full", GRADE_DOT[grade])}
              />
              <dt className="text-muted-foreground">{row.label}</dt>
              <dd className="text-right font-mono text-xs tabular-nums">{row.value(quality)}</dd>
            </div>
          )
        })}
      </dl>
      {hints && hints.length > 0 && (
        <ul aria-label="Hints" className="space-y-1 border-t pt-2 text-xs text-muted-foreground">
          {hints.map((h) => (
            <li key={h}>{h}</li>
          ))}
        </ul>
      )}
    </div>
  )
}
