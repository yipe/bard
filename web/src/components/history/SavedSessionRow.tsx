import { Download } from "lucide-react"
import { VERDICT } from "@/components/audio/qualityVerdict"
import { buttonVariants } from "@/components/ui/button"
import { sessionAudioUrl, transcriptUrl } from "@/lib/api"
import { dateTime, hms } from "@/lib/format"
import { sourceLabel } from "@/lib/source"
import { actions } from "@/lib/store"
import { cn } from "@/lib/utils"
import type { SavedSession } from "@/lib/types"

const linkClass = buttonVariants({ size: "xs", variant: "ghost" })

/** Download links for a saved session: transcript .md/.json, plus the recording when it has one. */
export function SessionDownloads({ session: s }: { session: SavedSession }) {
  return (
    <span className="flex flex-wrap items-center gap-0.5 text-xs text-muted-foreground">
      {(["md", "json"] as const).map((fmt) => (
        <a key={fmt} href={transcriptUrl(fmt, s.id)} download className={linkClass}>
          <Download /> .{fmt}
        </a>
      ))}
      {s.audio && (
        <a href={sessionAudioUrl(s.id)} download className={linkClass}>
          <Download /> Audio (.wav)
        </a>
      )}
    </span>
  )
}

/** A saved session in a list; clicking it opens the read-only viewer in History. */
export function SavedSessionRow({ session: s }: { session: SavedSession }) {
  return (
    <li data-session-id={s.id} className="py-1.5 first:pt-0 last:pb-0">
      <button
        type="button"
        onClick={() => actions.openSavedSession(s)}
        className="-mx-2 block w-[calc(100%+1rem)] rounded-lg px-2 py-1.5 text-left transition-colors hover:bg-muted/60"
      >
        <div className="flex items-baseline justify-between gap-2 text-sm">
          <span className="font-medium">{dateTime(s.started_at)}</span>
          <span className="flex items-center gap-2 font-mono text-xs text-muted-foreground tabular-nums">
            {s.quality && (
              <span
                data-testid="quality-badge"
                data-quality={s.quality}
                role="img"
                aria-label={VERDICT[s.quality].short}
                title={VERDICT[s.quality].short}
                className={cn("size-2 rounded-full", VERDICT[s.quality].dot)}
              />
            )}
            {hms(s.duration_s)}
          </span>
        </div>
        <p className="truncate text-xs text-muted-foreground">
          {sourceLabel(s.source)} · {s.lines} lines
        </p>
      </button>
      <div className="flex justify-end">
        <SessionDownloads session={s} />
      </div>
    </li>
  )
}
