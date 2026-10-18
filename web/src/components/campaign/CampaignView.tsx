import { useEffect, useMemo, useState } from "react"
import { BookOpen, Loader2, Upload } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet"
import { TYPE_LABEL, jobActive } from "@/lib/campaign"
import { campaignActions, useCampaign } from "@/lib/campaignStore"
import { AskPanel } from "./AskPanel"
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

      <div className="flex min-h-0 flex-1 flex-col lg:flex-row">
        <aside className="space-y-4 overflow-y-auto border-b p-4 lg:w-96 lg:border-r lg:border-b-0">
          <SuggestionsCard />
          <GlossaryList entities={campaign.entities} types={campaign.types} />
          <CampaignFacts facts={campaign.facts} />
        </aside>
        <main className="min-w-0 flex-1 overflow-y-auto p-6">
          <div className="mx-auto max-w-2xl space-y-8">
            <AskPanel />
            <div className="space-y-2 text-center text-sm text-muted-foreground">
              <p>Pick an entry to edit it, or import session notes to fill the glossary.</p>
              <p className="text-xs">
                Every entry is a note in your Obsidian vault. Names and “heard as” spellings help Troubadour
                spell them right in new transcripts; entries marked for review were suggested by the AI.
              </p>
            </div>
          </div>
        </main>
      </div>

      {/* Non-modal: the glossary stays clickable, so picking another entry swaps the sheet's content. */}
      <Sheet
        open={!!entity}
        onOpenChange={(open) => !open && campaignActions.select(null)}
        modal={false}
        disablePointerDismissal
      >
        <SheetContent showOverlay={false} className="w-full data-[side=right]:sm:max-w-xl">
          {entity && (
            <>
              <SheetHeader className="pr-12">
                <SheetTitle>{entity.name}</SheetTitle>
                <SheetDescription>{TYPE_LABEL[entity.type]} in your glossary</SheetDescription>
              </SheetHeader>
              <div className="min-h-0 flex-1 overflow-y-auto px-4 pb-4">
                <EntityEditor key={entity.name} entity={entity} />
              </div>
            </>
          )}
        </SheetContent>
      </Sheet>

      <ImportDialog open={importing} onOpenChange={setImporting} />
    </div>
  )
}
