import type { Fix } from "./types"

/** A run of text; `fix` is set when the run was spell-fixed. */
export interface Part {
  text: string
  fix: Fix | null
}

/**
 * Split `text` at spell-fix boundaries. Offsets are UTF-16 indices (JS string indices); ranges are
 * clamped to the text, and empty, non-numeric or overlapping ones dropped (earliest, then longest, wins).
 */
export function splitFixes(text: string, fixes: Fix[]): Part[] {
  const clamp = (n: number) => (Number.isFinite(n) ? Math.max(0, Math.min(Math.trunc(n), text.length)) : 0)
  const ranges = fixes
    .map((fix) => ({ fix, start: clamp(fix.start), end: clamp(fix.end) }))
    .filter((r) => r.end > r.start)
    .sort((x, y) => x.start - y.start || y.end - x.end)
  const parts: Part[] = []
  let cursor = 0
  for (const { fix, start, end } of ranges) {
    if (start < cursor) continue
    if (start > cursor) parts.push({ text: text.slice(cursor, start), fix: null })
    parts.push({ text: text.slice(start, end), fix })
    cursor = end
  }
  if (cursor < text.length) parts.push({ text: text.slice(cursor), fix: null })
  return parts
}
