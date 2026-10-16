import { Crown } from "lucide-react"
import { PlayerAvatar, type AvatarPerson } from "@/components/PlayerAvatar"
import { sharePct, talkDuration } from "@/lib/talkTime"

export interface PlayerBoxPerson extends AvatarPerson {
  id: string
  character: string
  is_gm: boolean
}

/**
 * A player's box with their share of talk time; shared by the live grid and the saved-session viewer.
 * `speaking` drives the live glow; leave it undefined where there is no live speaker.
 */
export function PlayerBox({
  person: p,
  speaking,
  seconds,
  total,
}: {
  person: PlayerBoxPerson
  speaking?: boolean
  seconds: number
  total: number
}) {
  const share = total > 0 ? (seconds / total) * 100 : 0
  return (
    <div
      data-player-id={p.id}
      data-speaking={speaking}
      style={{ "--player": p.color } as React.CSSProperties}
      className="speaker-box relative flex min-w-40 items-center gap-3 overflow-hidden rounded-xl border bg-card px-3 py-2"
    >
      <PlayerAvatar player={p} className="size-10" />
      <div className="min-w-0">
        <div className="flex items-center gap-1 truncate font-medium">
          {p.is_gm && <Crown className="size-3.5 shrink-0 text-amber-400" />}
          {p.name}
        </div>
        <div className="truncate text-xs text-muted-foreground">
          {p.character || (p.is_gm ? "Game master" : "—")}
        </div>
      </div>
      {total > 0 && (
        <>
          <div
            data-testid="talk-share"
            className="ml-auto pl-2 text-right leading-tight"
            title={`${talkDuration(seconds)} of ${talkDuration(total)} of talk`}
          >
            <div className="font-medium tabular-nums">{sharePct(seconds, total)}</div>
            <div className="text-xs text-muted-foreground tabular-nums">{talkDuration(seconds)}</div>
          </div>
          <div
            aria-hidden
            className="absolute bottom-0 left-0 h-[3px] bg-[color-mix(in_oklab,var(--player)_70%,transparent)] transition-[width] duration-500 ease-out"
            style={{ width: `${share}%` }}
          />
        </>
      )}
    </div>
  )
}

/** "Unnamed voices · N%" chip; renders nothing unless there is unassigned speech. */
export function UnnamedShareChip({ seconds, total }: { seconds: number; total: number }) {
  if (total <= 0 || seconds <= 0) return null
  return (
    <div
      data-testid="unnamed-share"
      title="Speech from voices not yet named in the Voices panel"
      className="flex items-center self-center rounded-xl border border-dashed px-3 py-1.5 text-xs text-muted-foreground"
    >
      Unnamed voices · {sharePct(seconds, total)}
    </div>
  )
}
