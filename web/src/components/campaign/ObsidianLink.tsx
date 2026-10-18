import type { ReactNode } from "react"
import { ExternalLink } from "lucide-react"
import { buttonVariants } from "@/components/ui/button"
import { obsidianUrl } from "@/lib/campaign"
import { useCampaign } from "@/lib/campaignStore"
import { cn } from "@/lib/utils"

/**
 * Opens the campaign folder, or a note in it, in Obsidian. Obsidian only opens folders it knows as
 * vaults; until then this explains how instead of linking to an error.
 */
export function ObsidianLink({
  relPath,
  children,
  className,
}: {
  /** Vault-relative note path; omitted: the whole vault. */
  relPath?: string
  children: ReactNode
  className?: string
}) {
  const campaign = useCampaign((s) => s.campaign)
  if (!campaign) return null
  if (!campaign.obsidian) {
    return (
      <span
        className={cn("inline-flex cursor-help items-center gap-1 text-muted-foreground", className)}
        title={`Obsidian doesn't know this folder yet. In Obsidian: Open folder as vault → ${campaign.vault}`}
      >
        <ExternalLink className="size-3" /> {children} (open the folder in Obsidian first)
      </span>
    )
  }
  return (
    <a
      href={obsidianUrl(campaign.obsidian, relPath)}
      className={cn(buttonVariants({ variant: "link", size: "xs" }), "h-auto p-0", className)}
    >
      <ExternalLink /> {children}
    </a>
  )
}
