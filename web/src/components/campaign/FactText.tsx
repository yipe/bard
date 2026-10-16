import { findEntity, splitWikiLinks } from "@/lib/campaign"
import { campaignActions, useCampaign } from "@/lib/campaignStore"

/** Text with Obsidian links styled; links to glossary entries select them. */
export function FactText({ text }: { text: string }) {
  const entities = useCampaign((s) => s.campaign?.entities ?? [])
  return (
    <>
      {splitWikiLinks(text).map((seg, i) => {
        if (!("target" in seg)) return <span key={i}>{seg.text}</span>
        const entity = findEntity(entities, seg.target)
        const cls = "font-medium text-primary underline decoration-primary/40 underline-offset-2"
        if (!entity)
          return (
            <span key={i} className="font-medium text-primary/80" title={seg.target}>
              {seg.text}
            </span>
          )
        return (
          <a
            key={i}
            href={`#${encodeURIComponent(entity.name)}`}
            className={cls}
            title={entity.name}
            onClick={(e) => {
              e.preventDefault()
              campaignActions.select(entity.name)
            }}
          >
            {seg.text}
          </a>
        )
      })}
    </>
  )
}
