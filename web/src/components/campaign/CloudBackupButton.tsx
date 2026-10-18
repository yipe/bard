import { useEffect, useState } from "react"
import { AlertCircle, Cloud, CloudOff, ExternalLink, Loader2 } from "lucide-react"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { Button, buttonVariants } from "@/components/ui/button"
import { Label } from "@/components/ui/label"
import { NativeSelect } from "@/components/ui/native-select"
import { Popover, PopoverContent, PopoverTitle, PopoverTrigger } from "@/components/ui/popover"
import { Switch } from "@/components/ui/switch"
import { jobActive } from "@/lib/campaign"
import { useCampaign } from "@/lib/campaignStore"
import { cloudActions, useCloud } from "@/lib/cloudStore"
import { useStore } from "@/lib/store"
import { ago, when } from "@/lib/time"
import type { CampaignJob, CloudStatus, CloudVault } from "@/lib/types"

export const running = (kind: CampaignJob["kind"]) => (s: { jobs: Record<string, CampaignJob> }) =>
  Object.values(s.jobs).some((j) => j.kind === kind && jobActive(j))

/** A failure newer than the last good backup; an older one is history, not news. */
const failure = (status: CloudStatus) =>
  status.last_error && (!status.last_backup || status.last_error.at > status.last_backup.at)
    ? status.last_error
    : null

/** The current time, updated every minute, so "5 min ago" stays true. */
function useMinuteClock(): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 60_000)
    return () => window.clearInterval(timer)
  }, [])
  return now
}

/** What the header button says: when the open campaign was last backed up, at a glance. */
function summary(
  status: CloudStatus | null,
  now: number,
): { text: string; title?: string; failed?: boolean } {
  if (!status?.connected) return { text: "Cloud backup" }
  const failed = failure(status)
  if (failed) return { text: "Backup failed", title: `${when(failed.at)}: ${failed.message}`, failed: true }
  const last = status.last_backup
  if (!last) return { text: "Not backed up yet" }
  return { text: `Backed up ${ago(last.at, now)}`, title: `Last backup ${when(last.at)}` }
}

/**
 * Campaign header: back up the open campaign's notes to Troubadour Cloud (opt-in), automatically or now, and
 * restore them. Nothing reaches the service until Connect; the status shown here comes from this Mac.
 */
export function CloudBackupButton() {
  const status = useCloud((s) => s.status)
  const campaign = useStore((s) => s.campaign)
  const [open, setOpen] = useState(false)
  const now = useMinuteClock()

  // GET /api/cloud is local (never calls the service): load it with the campaign, and again on opening.
  useEffect(() => {
    void cloudActions.refresh({ quiet: true })
  }, [campaign])
  useEffect(() => {
    if (open) void cloudActions.refresh()
  }, [open])

  const label = summary(status, now)
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger
        render={
          <Button
            size="sm"
            variant="outline"
            title={label.title}
            aria-label={`Cloud backup: ${label.text}`}
            className={label.failed ? "text-destructive" : undefined}
          />
        }
      >
        {label.failed ? <AlertCircle /> : status?.connected === false ? <CloudOff /> : <Cloud />} {label.text}
      </PopoverTrigger>
      <PopoverContent align="end" className="w-80 space-y-3 p-3 text-sm">
        <PopoverTitle className="font-medium">Troubadour Cloud backup</PopoverTitle>
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
          <Connected status={status} />
        )}
      </PopoverContent>
    </Popover>
  )
}

function Connected({ status }: { status: CloudStatus }) {
  const backingUp = useCampaign(running("backup"))
  const last = status.last_backup
  const failed = failure(status)
  return (
    <>
      <p className="text-muted-foreground">
        {last
          ? `Last backup ${when(last.at)}: ${last.files} files${last.unchanged ? ", nothing new" : ""}.`
          : "Not backed up yet."}
      </p>
      {failed && (
        <Alert variant="destructive" className="px-2 py-1.5">
          <AlertDescription className="text-xs text-destructive">
            Backup failed {when(failed.at)}: {failed.message}
          </AlertDescription>
        </Alert>
      )}
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
      <Option
        id="cloud-auto"
        checked={status.auto_backup}
        onChange={(on) => void cloudActions.setOptions({ auto_backup: on })}
        label="Back up automatically"
        hint="A couple of minutes after the notes change, at most every 10 minutes."
      />
      <Option
        id="cloud-transcripts"
        checked={status.include_transcripts}
        onChange={(on) => void cloudActions.setOptions({ include_transcripts: on })}
        label="Include transcripts"
        hint="Everything said at the table. Off: only notes, glossary and session write-ups."
      />
      <div className="flex items-center gap-2">
        <Button size="sm" disabled={backingUp} onClick={() => void cloudActions.backup()}>
          {backingUp && <Loader2 className="animate-spin" />} Back up now
        </Button>
        <a
          href={`${status.url}/vaults`}
          target="_blank"
          rel="noreferrer"
          className={buttonVariants({ variant: "link", size: "xs" })}
        >
          <ExternalLink /> Open Troubadour Cloud
        </a>
      </div>
      <Restore campaign={status.campaign} />
      <Button
        size="xs"
        variant="ghost"
        className="text-muted-foreground"
        onClick={() => void cloudActions.disconnect()}
      >
        Disconnect
      </Button>
    </>
  )
}

function Option(props: {
  id: string
  checked: boolean
  onChange: (on: boolean) => void
  label: string
  hint: string
}) {
  return (
    <div className="flex items-start gap-2">
      <Switch id={props.id} checked={props.checked} onCheckedChange={props.onChange} />
      <Label htmlFor={props.id} className="leading-snug font-normal">
        {props.label}
        <span className="block text-xs text-muted-foreground">{props.hint}</span>
      </Label>
    </div>
  )
}

/** Bring a backed-up campaign back: this one by default, or one from another Mac (it joins the campaign picker). */
function Restore({ campaign }: { campaign: string }) {
  const restoring = useCampaign(running("restore"))
  const [vaults, setVaults] = useState<CloudVault[] | null>(null)
  const [loading, setLoading] = useState(false)
  const [chosen, setChosen] = useState("")

  async function load() {
    setLoading(true)
    const found = await cloudActions.vaults()
    setLoading(false)
    if (!found) return
    const backedUp = found.filter((v) => v.head_snapshot)
    setVaults(backedUp)
    setChosen((backedUp.find((v) => v.campaign_id === campaign) ?? backedUp[0])?.id ?? "")
  }

  if (vaults === null)
    return (
      <Button size="sm" variant="outline" disabled={loading || restoring} onClick={() => void load()}>
        {(loading || restoring) && <Loader2 className="animate-spin" />} Restore…
      </Button>
    )
  if (vaults.length === 0)
    return <p className="text-xs text-muted-foreground">Nothing backed up to restore yet.</p>
  return (
    <div className="space-y-2 rounded-md border p-2">
      <Label htmlFor="cloud-restore-from" className="text-xs">
        Restore campaign
      </Label>
      <NativeSelect id="cloud-restore-from" value={chosen} onChange={(e) => setChosen(e.target.value)}>
        {vaults.map((v) => (
          <option key={v.id} value={v.id}>
            {v.name}
            {v.campaign_id === campaign ? " (this campaign)" : ""}
          </option>
        ))}
      </NativeSelect>
      <p className="text-xs text-muted-foreground">
        Its newest backup comes back. A campaign this Mac doesn't have yet is added to the campaign picker.
        Notes that differ are replaced, and their current versions are kept in the vault's .trash folder.
        Nothing is deleted.
      </p>
      <div className="flex gap-2">
        <Button
          size="sm"
          variant="destructive"
          disabled={!chosen || restoring}
          onClick={() => {
            void cloudActions.restore(chosen)
            setVaults(null)
          }}
        >
          Restore
        </Button>
        <Button size="sm" variant="ghost" onClick={() => setVaults(null)}>
          Cancel
        </Button>
      </div>
    </div>
  )
}
