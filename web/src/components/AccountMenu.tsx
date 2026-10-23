import { useEffect, useState } from "react"
import { CircleUser, ExternalLink, Loader2, Settings } from "lucide-react"
import { ModelsDialog } from "@/components/ModelsDialog"
import { DprcalcMark } from "@/components/DprcalcMark"
import { running } from "@/components/campaign/CloudBackupButton"
import { Button, buttonVariants } from "@/components/ui/button"
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover"
import { useCampaign } from "@/lib/campaignStore"
import { cloudActions, useCloud } from "@/lib/cloudStore"
import { cn } from "@/lib/utils"

/** Accounts are DPRcalc's: name, email, avatar and password are managed there. */
const MANAGE_ACCOUNT_URL = "https://accounts.dprcalc.com/user"

/** The avatar, with initials underneath so a picture that fails to decode leaves them showing. */
function Avatar({ name, email, src }: { name: string | null; email: string | null; src: string | null }) {
  const initial = (name ?? email ?? "?").trim().charAt(0).toUpperCase()
  return (
    <span className="relative flex size-7 shrink-0 items-center justify-center overflow-hidden rounded-full bg-muted text-xs font-semibold text-muted-foreground">
      {initial}
      {src && <img src={src} alt="" className="absolute inset-0 size-full rounded-full object-cover" />}
    </span>
  )
}

/**
 * Header account menu, like DPRcalc's: the Troubadour Cloud account's avatar (the one from DPRcalc, fetched by
 * the server so this page never contacts the image host), who is signed in, Back up now, and Disconnect.
 * Signed out it is a plain account icon that offers Connect. Nothing reaches the service until Connect.
 */
export function AccountMenu() {
  const status = useCloud((s) => s.status)
  const account = useCloud((s) => s.account)
  const backingUp = useCampaign(running("backup"))
  const [open, setOpen] = useState(false)
  const [models, setModels] = useState(false)
  const connected = !!status?.connected

  useEffect(() => {
    void cloudActions.refresh({ quiet: true })
  }, [])
  useEffect(() => {
    if (connected) void cloudActions.loadAccount()
    else useCloud.setState({ account: null })
  }, [connected])

  const name = account?.name ?? null
  const email = account?.email ?? null
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger
        render={
          <Button
            variant="ghost"
            size="sm"
            aria-label="Account menu"
            title={connected ? (name ?? email ?? "Troubadour Cloud") : "Troubadour Cloud"}
            className="gap-2 px-1.5"
          />
        }
      >
        {connected ? (
          <>
            <Avatar name={name} email={email} src={account?.avatar ?? null} />
            {name && <span className="hidden max-w-32 truncate sm:inline">{name}</span>}
          </>
        ) : (
          <CircleUser className="size-5 text-muted-foreground" />
        )}
      </PopoverTrigger>
      <PopoverContent align="end" className="w-64 space-y-2 p-2">
        <Button
          size="sm"
          variant="ghost"
          className="w-full justify-start"
          onClick={() => {
            setOpen(false)
            setModels(true)
          }}
        >
          <Settings /> Models
        </Button>
        {!connected ? (
          <div className="space-y-2 p-1">
            <p className="text-muted-foreground">
              Back up your campaigns to Troubadour Cloud with a DPRcalc account. Nothing leaves this Mac until
              you connect.
            </p>
            <Button size="sm" onClick={() => void cloudActions.connect()}>
              Sign in with DPRcalc
            </Button>
          </div>
        ) : (
          <>
            <div className="flex items-center gap-3 px-1 py-1">
              <Avatar name={name} email={email} src={account?.avatar ?? null} />
              <div className="min-w-0">
                <p className="truncate font-medium">{name ?? email ?? "Signed in"}</p>
                {name && email && <p className="truncate text-xs text-muted-foreground">{email}</p>}
              </div>
            </div>
            <p className="flex items-center gap-1.5 px-1 text-xs text-muted-foreground">
              <DprcalcMark className="size-3.5" /> Signed in with DPRcalc
            </p>
            <div className="flex flex-col gap-0.5 border-t pt-1">
              <Button
                size="sm"
                variant="ghost"
                className="justify-start"
                disabled={backingUp}
                onClick={() => {
                  void cloudActions.backup()
                  setOpen(false)
                }}
              >
                {backingUp && <Loader2 className="animate-spin" />} Back up now
              </Button>
              <a
                href={`${status?.url}/vaults`}
                target="_blank"
                rel="noreferrer"
                className={cn(buttonVariants({ variant: "ghost", size: "sm" }), "justify-start")}
              >
                <ExternalLink /> Open Troubadour Cloud
              </a>
              <a
                href={MANAGE_ACCOUNT_URL}
                target="_blank"
                rel="noreferrer"
                className={cn(buttonVariants({ variant: "ghost", size: "sm" }), "justify-start")}
              >
                <DprcalcMark className="size-4" /> Manage on DPRcalc
              </a>
              <Button
                size="sm"
                variant="ghost"
                className="justify-start text-muted-foreground"
                onClick={() => {
                  void cloudActions.disconnect()
                  setOpen(false)
                }}
              >
                Disconnect
              </Button>
            </div>
          </>
        )}
      </PopoverContent>
      <ModelsDialog open={models} onOpenChange={setModels} />
    </Popover>
  )
}
