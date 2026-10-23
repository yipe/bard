import { useEffect, useMemo, useState } from "react"
import { BookOpen, Loader2, Upload, X } from "lucide-react"
import { Button } from "@/components/ui/button"
import { TYPE_LABEL, jobActive } from "@/lib/campaign"
import { campaignActions, useCampaign } from "@/lib/campaignStore"
import { CloudBackupButton } from "./CloudBackupButton"
import { CampaignFacts } from "./CampaignFacts"
import { EntityEditor } from "./EntityEditor"
import { GlossaryList } from "./GlossaryList"
import { ImportDialog } from "./ImportDialog"
import { JobChip } from "./JobProgress"
import { ObsidianLink } from "./ObsidianLink"
import { SuggestionsCard } from "./SuggestionsCard"

/** Campaign tab: the vault-backed glossary, its review queue, and campaign facts. */
export function CampaignView() {
  const campaign = useCampaign((s) => s.campaign)
  const loadError = useCampaign((s) => s.loadError)
  const selected = useCampaign((s) => s.selected)
  const jobs = useCampaign((s) => s.jobs)
  const dismissed = useCampaign((s) => s.dismissed)
  const [importing, setImporting] = useState(false)

  useEffect(() => {
    void campaignActions.refresh()
  }, [])

  const headerJobs = useMemo(
    () => Object.values(jobs).filter((j) => jobActive(j) || (j.state === "error" && !dismissed[j.id])),
    [jobs, dismissed],
  )

  if (!campaign) {
    return (
      <div className="flex flex-1 flex-col items-center justify-center gap-3 text-muted-foreground">
        {loadError ? (
          <>
            <p role="alert">Couldn't load the campaign: {loadError}</p>
            <Button variant="outline" onClick={() => void campaignActions.refresh()}>
              Retry
            </Button>
          </>
        ) : (
          <p className="flex items-center gap-2">
            <Loader2 className="size-4 animate-spin" /> Loading campaign…
          </p>
        )}
      </div>
    )
  }

  const entity = campaign.entities.find((e) => e.name === selected)

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b px-4 py-2">
        <div className="flex min-w-0 items-center gap-2 text-xs text-muted-foreground">
          <BookOpen className="size-4 shrink-0" />
          <span className="truncate font-mono" title={campaign.vault}>
            {campaign.vault}
          </span>
          <ObsidianLink className="shrink-0">Open in Obsidian</ObsidianLink>
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          {headerJobs.map((j) => (
            <JobChip key={j.id} job={j} />
          ))}
        </div>
        <div className="ml-auto flex gap-2">
          <CloudBackupButton />
          <Button size="sm" onClick={() => setImporting(true)}>
            <Upload /> Import notes
          </Button>
        </div>
      </div>

      <div className="flex min-h-0 flex-1">
        <aside className="w-80 shrink-0 space-y-4 overflow-y-auto border-r p-4">
          <SuggestionsCard />
          <GlossaryList entities={campaign.entities} types={campaign.types} />
          <CampaignFacts facts={campaign.facts} />
        </aside>
        <main className="min-h-0 min-w-0 flex-1 overflow-y-auto p-6">
          {entity ? (
            <div className="mx-auto max-w-3xl space-y-4">
              <div className="flex items-start gap-3">
                <div className="min-w-0">
                  <h2 className="truncate text-lg font-semibold">{entity.name}</h2>
                  <p className="text-sm text-muted-foreground">{TYPE_LABEL[entity.type]} in your glossary</p>
                </div>
                <Button
                  variant="ghost"
                  size="icon"
                  className="ml-auto shrink-0"
                  aria-label="Close"
                  onClick={() => campaignActions.select(null)}
                >
                  <X />
                </Button>
              </div>
              <EntityEditor key={entity.name} entity={entity} />
            </div>
          ) : (
            <div className="mx-auto max-w-2xl space-y-2 pt-16 text-center text-sm text-muted-foreground">
              <p>Pick an entry to edit it, or import session notes to fill the glossary.</p>
              <p className="text-xs">
                Every entry is a note in your Obsidian vault. Names and “heard as” spellings help Troubadour
                spell them right in new transcripts; entries marked for review were suggested by the AI.
              </p>
            </div>
          )}
        </main>
      </div>

      <ImportDialog open={importing} onOpenChange={setImporting} />
    </div>
  )
}
