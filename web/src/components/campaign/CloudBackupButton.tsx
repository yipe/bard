import { useEffect, useState } from "react"
import { Cloud, CloudOff, ExternalLink, Loader2 } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Label } from "@/components/ui/label"
import { Popover, PopoverContent, PopoverTitle, PopoverTrigger } from "@/components/ui/popover"
import { Switch } from "@/components/ui/switch"
import { jobActive } from "@/lib/campaign"
import { useCampaign } from "@/lib/campaignStore"
import { cloudActions, useCloud } from "@/lib/cloudStore"

function when(epochS: number): string {
  return new Date(epochS * 1000).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })
}

/**
 * Campaign header: back up the open campaign's notes to Bard Cloud (opt-in). Nothing reaches the service
 * until Connect; the status shown here comes from this Mac.
 */
export function CloudBackupButton() {
  const status = useCloud((s) => s.status)
  const backingUp = useCampaign((s) => Object.values(s.jobs).some((j) => j.kind === "backup" && jobActive(j)))
  const [open, setOpen] = useState(false)

  useEffect(() => {
    if (open) void cloudActions.refresh()
  }, [open])

  const last = status?.last_backup ?? null
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger render={<Button size="sm" variant="outline" />}>
        {status?.connected === false ? <CloudOff /> : <Cloud />} Cloud backup
      </PopoverTrigger>
      <PopoverContent align="end" className="w-80 space-y-3 p-3 text-sm">
        <PopoverTitle className="font-medium">Bard Cloud backup</PopoverTitle>
        {!status ? (
          <Loader2 className="size-4 animate-spin text-muted-foreground" />
        ) : !status.connected ? (
          <>
            <p className="text-muted-foreground">
              Keep a copy of this campaign's notes online, with history, and read them in a browser. Nothing
              leaves this Mac until you connect.
            </p>
            <Button size="sm" onClick={() => void cloudActions.connect()}>
              Connect
            </Button>
          </>
        ) : (
          <>
            <p className="text-muted-foreground">
              {last
                ? `Last backup ${when(last.at)}: ${last.files} files${last.unchanged ? ", nothing new" : ""}.`
                : "Not backed up yet."}
            </p>
            {last && last.skipped.length > 0 && (
              <details className="text-xs text-muted-foreground">
                <summary>{last.skipped.length} files left out</summary>
                <ul className="mt-1 list-disc pl-4">
                  {last.skipped.slice(0, 10).map((s) => (
                    <li key={s} className="break-all">
                      {s}
                    </li>
                  ))}
                </ul>
              </details>
            )}
            <div className="flex items-start gap-2">
              <Switch
                id="cloud-transcripts"
                checked={status.include_transcripts}
                onCheckedChange={(on) => void cloudActions.setIncludeTranscripts(on)}
              />
              <Label htmlFor="cloud-transcripts" className="leading-snug font-normal">
                Include transcripts
                <span className="block text-xs text-muted-foreground">
                  Everything said at the table. Off: only notes, glossary and session write-ups.
                </span>
              </Label>
            </div>
            <div className="flex items-center gap-2">
              <Button size="sm" disabled={backingUp} onClick={() => void cloudActions.backup()}>
                {backingUp && <Loader2 className="animate-spin" />} Back up now
              </Button>
              <a
                href={status.url}
                target="_blank"
                rel="noreferrer"
                className="inline-flex items-center gap-1 text-xs text-primary hover:underline"
              >
                <ExternalLink className="size-3" /> Open Bard Cloud
              </a>
              <button
                type="button"
                className="ml-auto text-xs text-muted-foreground hover:underline"
                onClick={() => void cloudActions.disconnect()}
              >
                Disconnect
              </button>
            </div>
          </>
        )}
      </PopoverContent>
    </Popover>
  )
}
