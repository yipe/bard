import { initials } from "@/lib/format"
import { cn } from "@/lib/utils"

/** Anything with a name and color (live `Player` or a saved record's player); avatar image optional. */
export interface AvatarPerson {
  name: string
  color: string
  avatar?: string | null
}

export function PlayerAvatar({ player, className }: { player: AvatarPerson | null; className?: string }) {
  if (player?.avatar) {
    return (
      <img
        src={player.avatar}
        alt=""
        className={cn("size-9 shrink-0 rounded-full object-cover", className)}
      />
    )
  }
  return (
    <div
      className={cn(
        "flex size-9 shrink-0 items-center justify-center rounded-full text-xs font-semibold text-white/90",
        !player && "bg-muted text-muted-foreground",
        className,
      )}
      style={player ? { backgroundColor: player.color } : undefined}
    >
      {player ? initials(player.name) : "?"}
    </div>
  )
}
