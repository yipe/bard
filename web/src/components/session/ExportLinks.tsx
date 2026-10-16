import { Download } from "lucide-react"
import { Button, buttonVariants } from "@/components/ui/button"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { transcriptUrl, type TranscriptFormat } from "@/lib/api"
import { useStore } from "@/lib/store"

const FORMATS: { format: TranscriptFormat; label: string }[] = [
  { format: "md", label: "Transcript (.md)" },
  { format: "json", label: "Data (.json)" },
]

/** Download the current (or just-finished) session. Same-origin links, so `download` works. */
export function ExportLinks() {
  const hasMessages = useStore((s) => s.messages.length > 0)

  if (!hasMessages) {
    return (
      <Tooltip>
        <TooltipTrigger render={<span className="flex items-center gap-1" tabIndex={0} />}>
          {FORMATS.map((f) => (
            <Button key={f.format} size="sm" variant="ghost" disabled>
              <Download /> {f.label}
            </Button>
          ))}
        </TooltipTrigger>
        <TooltipContent>Nothing to export yet</TooltipContent>
      </Tooltip>
    )
  }

  return (
    <div className="flex items-center gap-1">
      {FORMATS.map((f) => (
        <a
          key={f.format}
          href={transcriptUrl(f.format)}
          download
          className={buttonVariants({ size: "sm", variant: "ghost" })}
        >
          <Download /> {f.label}
        </a>
      ))}
    </div>
  )
}
