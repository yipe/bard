import { useEffect } from "react"
import { Feather, TriangleAlert } from "lucide-react"
import { AccountMenu } from "@/components/AccountMenu"
import { CampaignSwitcher } from "@/components/CampaignSwitcher"
import { CampaignView } from "@/components/campaign/CampaignView"
import { HistoryView } from "@/components/history/HistoryView"
import { SessionView } from "@/components/session/SessionView"
import { SetupView } from "@/components/setup/SetupView"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { Toaster } from "@/components/ui/sonner"
import { TooltipProvider } from "@/components/ui/tooltip"
import { startRouting } from "@/lib/route"
import { connect, actions, useStore, type View } from "@/lib/store"
import { cn } from "@/lib/utils"

const TABS: { id: View; label: string }[] = [
  { id: "setup", label: "Setup" },
  { id: "session", label: "Session" },
  { id: "history", label: "History" },
  { id: "campaign", label: "Campaign" },
]

export default function App() {
  const view = useStore((s) => s.view)
  const connected = useStore((s) => s.connected)
  const loaded = useStore((s) => s.loaded)
  const sessionStatus = useStore((s) => s.session?.status)
  const running = sessionStatus === "running" || sessionStatus === "paused"
  const serverError = useStore((s) => s.session?.error ?? null)

  useEffect(connect, [])
  useEffect(startRouting, [])

  return (
    <TooltipProvider>
      <div className="flex h-dvh flex-col">
        <header className="flex items-center gap-4 border-b bg-card/40 px-4 py-2.5">
          <div className="flex items-center gap-2 font-semibold tracking-tight">
            <Feather className="size-5 text-primary" /> Troubadour
          </div>
          {loaded && <CampaignSwitcher />}
          <nav className="flex gap-1 rounded-lg bg-muted p-1">
            {TABS.map((t) => (
              <button
                key={t.id}
                type="button"
                onClick={() => actions.setView(t.id)}
                aria-current={view === t.id ? "page" : undefined}
                className={cn(
                  "flex items-center gap-1.5 rounded-md px-3 py-1 text-sm transition-colors",
                  view === t.id ? "bg-background shadow-sm" : "text-muted-foreground hover:text-foreground",
                )}
              >
                {t.label}
                {t.id === "session" && running && <span className="size-1.5 rounded-full bg-emerald-500" />}
              </button>
            ))}
          </nav>
          <div className="ml-auto flex items-center gap-1.5 text-xs text-muted-foreground">
            <span
              className={cn(
                "size-2 rounded-full",
                connected ? "bg-emerald-500" : "animate-pulse bg-amber-500",
              )}
            />
            {connected ? "Connected" : "Connecting…"}
          </div>
          {loaded && <AccountMenu />}
        </header>
        {serverError && (
          <Alert variant="destructive" className="rounded-none border-x-0 border-t-0 px-4">
            <TriangleAlert />
            <AlertDescription className="text-destructive">{serverError}</AlertDescription>
          </Alert>
        )}
        {!loaded ? (
          <div className="flex flex-1 items-center justify-center text-muted-foreground">
            Waiting for the Troubadour server…
          </div>
        ) : view === "setup" ? (
          <div className="flex-1 overflow-y-auto">
            <SetupView />
          </div>
        ) : view === "history" ? (
          <HistoryView />
        ) : view === "campaign" ? (
          <CampaignView />
        ) : (
          <SessionView />
        )}
      </div>
      <Toaster position="bottom-right" />
    </TooltipProvider>
  )
}
