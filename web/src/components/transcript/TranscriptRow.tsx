import type { ReactNode } from "react"
import { UserRound } from "lucide-react"
import { PlayerAvatar, type AvatarPerson } from "@/components/PlayerAvatar"
import { FixedText } from "@/components/transcript/FixedText"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { mmss } from "@/lib/format"
import { cn } from "@/lib/utils"
import type { Fix } from "@/lib/types"

export interface TranscriptRowProps {
  id: string
  text: string
  /** Spell-fixed spans (may be absent on older records). */
  fixes?: Fix[]
  tStart: number
  /** Provisional or low-confidence speaker: muted italic text plus a "?" hint. */
  uncertain: boolean
  speaker: string
  /** Avatar and name color; null shows a "?" avatar and an uncolored name. */
  person: AvatarPerson | null
  /** Muted label after the name (character). */
  character?: string | null
  /** Extra inline controls after the name, e.g. "Name this voice". */
  extra?: ReactNode
  /** Wraps the speaker's name (or, on a continued line, an icon), e.g. in a button that reassigns the line. */
  renderName?: (name: ReactNode) => ReactNode
  /** Same speaker as the line above: no avatar or name, chat-style; time and controls show on hover. */
  continued?: boolean
  /** Changing this replays the highlight flash. */
  bump?: number
  /** Makes the timestamp a button (e.g. seek audio). */
  onTimestampClick?: () => void
}

/** One transcript line; shared by the live chat feed and the saved-session viewer. */
export function TranscriptRow({
  id,
  text,
  fixes,
  tStart,
  uncertain,
  speaker,
  person,
  character,
  extra,
  renderName,
  continued = false,
  bump = 0,
  onTimestampClick,
}: TranscriptRowProps) {
  const time = mmss(tStart)
  const timeClass = "font-mono text-xs text-muted-foreground tabular-nums"
  const timestamp = onTimestampClick ? (
    <button
      type="button"
      title="Play from here"
      className={cn(timeClass, "hover:text-foreground hover:underline")}
      onClick={onTimestampClick}
    >
      {time}
    </button>
  ) : (
    <span className={timeClass}>{time}</span>
  )
  const uncertainHint = uncertain && (
    <Tooltip>
      <TooltipTrigger
        render={
          <span className="inline-flex size-4 cursor-help items-center justify-center rounded-full bg-muted text-[10px] text-muted-foreground" />
        }
      >
        ?
      </TooltipTrigger>
      <TooltipContent>speaker not certain yet</TooltipContent>
    </Tooltip>
  )
  const textBlock = (
    <p
      className={cn(
        "min-w-0 flex-1 leading-relaxed break-words",
        uncertain && "text-muted-foreground italic",
      )}
    >
      <FixedText text={text} fixes={fixes ?? []} />
    </p>
  )
  const name = (
    <span className="font-medium" style={person ? { color: person.color } : undefined}>
      {speaker}
    </span>
  )
  return (
    // Padding (not margin) on the outer box so the virtualizer measures rows exactly.
    <div data-message-id={id} className={cn("px-4", !continued && "pt-2")}>
      <div
        key={bump}
        className={cn(
          "group flex gap-3 rounded-lg px-2",
          continued ? "py-0.5" : "py-1",
          bump > 0 && "animate-flash",
        )}
      >
        {continued ? (
          <>
            {/* Avatar-wide gutter keeps the text aligned under the speaker's first line. */}
            <div className="w-9 shrink-0" />
            {textBlock}
            <div className="flex shrink-0 items-baseline gap-2">
              {uncertainHint}
              {/* Hover-capable pointers reveal on hover/focus; touch screens always show them. */}
              <span className="flex items-baseline gap-2 opacity-0 group-focus-within:opacity-100 group-hover:opacity-100 pointer-coarse:opacity-100">
                {renderName?.(<UserRound className="size-3.5 translate-y-0.5 text-muted-foreground" />)}
                {timestamp}
              </span>
            </div>
          </>
        ) : (
          <>
            <PlayerAvatar player={person} className="mt-0.5" />
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-baseline gap-x-2 text-sm">
                {renderName ? renderName(name) : name}
                {character && <span className="text-muted-foreground">{character}</span>}
                {extra}
                {uncertainHint}
                <span className="ml-auto">{timestamp}</span>
              </div>
              {textBlock}
            </div>
          </>
        )}
      </div>
    </div>
  )
}

/** Top/bottom breathing room inside virtualized transcripts. */
export function TranscriptSpacer() {
  return <div className="h-3" />
}
