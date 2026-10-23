import { useState } from "react"
import { toast } from "sonner"
import { FileAudio, History, Mic, Pause, Play, Square } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { api } from "@/lib/api"
import { mmss } from "@/lib/format"
import { sourceLabel } from "@/lib/source"
import { actions, useStore } from "@/lib/store"
import { useSessionTime } from "@/lib/useSessionTime"
import { cn } from "@/lib/utils"
import { ExportLinks } from "./ExportLinks"
import { SessionAudio } from "./SessionAudio"

const ASR_STATUS = {
  loading: { label: "loading", dot: "animate-pulse bg-amber-500", detail: "Loading (about 20 s)" },
  ready: { label: "ready", dot: "bg-emerald-500", detail: "Ready" },
  error: { label: "failed", dot: "bg-red-500", detail: "Failed to load" },
} as const

/** A coloured dot and a name, with what it means on hover. */
function ServiceStatus({
  name,
  state,
  dot,
  detail,
}: {
  name: string
  state: string
  dot: string
  detail: string
}) {
  return (
    <Tooltip>
      <TooltipTrigger
        aria-label={`${name}: ${state}`}
        render={<span className="flex cursor-help items-center gap-1.5 text-xs text-muted-foreground" />}
      >
        <span className={cn("size-2 rounded-full", dot)} /> {name}
      </TooltipTrigger>
      <TooltipContent>{detail}</TooltipContent>
    </Tooltip>
  )
}

export function SessionHeader() {
  const session = useStore((s) => s.session)
  const t = useSessionTime()
  const [stopping, setStopping] = useState(false)
  const [hold, setHold] = useState(false)
  const [recheckingLlm, setRecheckingLlm] = useState(false)
  if (!session) return null
  const { source, status, llm, asr } = session
  const whisper = ASR_STATUS[asr.state]

  const recheckLlm = async () => {
    setRecheckingLlm(true)
    try {
      await api.recheckLlm()
    } catch (e) {
      toast.error((e as Error).message)
    } finally {
      setRecheckingLlm(false)
    }
  }

  const holdSession = async (resume: boolean) => {
    setHold(true)
    try {
      await (resume ? api.resumeSession() : api.pauseSession())
    } catch (e) {
      toast.error((e as Error).message)
    } finally {
      setHold(false)
    }
  }

  const stop = async () => {
    setStopping(true)
    try {
      await api.stopSession()
    } catch (e) {
      toast.error((e as Error).message)
    } finally {
      setStopping(false)
    }
  }

  return (
    <div className="flex flex-wrap items-center gap-3 border-b px-4 py-2">
      <div className="flex min-w-0 items-center gap-2 text-sm">
        {source?.kind === "file" ? (
          <FileAudio className="size-4" />
        ) : source?.kind === "session" ? (
          <History className="size-4" />
        ) : (
          <Mic className="size-4" />
        )}
        <span className="truncate">{sourceLabel(source)}</span>
      </div>
      <Badge
        variant={status === "running" ? "default" : "secondary"}
        className={cn(status === "running" && "bg-emerald-600 text-white")}
      >
        {status === "running" && <span className="size-1.5 animate-pulse rounded-full bg-white" />}
        {status}
      </Badge>
      <span className="font-mono text-sm tabular-nums">{mmss(t)}</span>
      <ServiceStatus
        name="Whisper"
        state={whisper.label}
        dot={whisper.dot}
        detail={`${whisper.detail} · ${asr.model}`}
      />
      <ServiceStatus
        name="LLM"
        state={recheckingLlm ? "checking" : llm.ok ? "connected" : "unavailable"}
        dot={recheckingLlm ? "animate-pulse bg-amber-500" : llm.ok ? "bg-emerald-500" : "bg-red-500"}
        detail={
          recheckingLlm
            ? "Checking…"
            : `${llm.ok ? "Connected" : "Unavailable"} · ${llm.model || "no model"} @ ${llm.url}`
        }
      />
      {!llm.ok && (
        <Button size="xs" variant="outline" disabled={recheckingLlm} onClick={() => void recheckLlm()}>
          {recheckingLlm ? "Checking…" : "Retry"}
        </Button>
      )}
      <div className="ml-auto flex items-center gap-2">
        <SessionAudio />
        <ExportLinks />
        {status === "idle" ? (
          <Button size="sm" variant="outline" onClick={() => actions.setView("setup")}>
            New session
          </Button>
        ) : (
          <>
            {status !== "stopping" && (
              <Button
                size="sm"
                variant="outline"
                disabled={hold}
                onClick={() => void holdSession(status === "paused")}
              >
                {status === "paused" ? <Play /> : <Pause />}
                {status === "paused" ? "Resume" : "Pause"}
              </Button>
            )}
            <Button size="sm" variant="destructive" disabled={stopping || status === "stopping"} onClick={stop}>
              <Square /> Stop
            </Button>
          </>
        )}
      </div>
    </div>
  )
}
