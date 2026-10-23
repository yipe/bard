import { LiveQualityBox } from "@/components/audio/LiveQualityBox"
import { useStore } from "@/lib/store"
import { ChatFeed } from "./ChatFeed"
import { PlayerGrid } from "./PlayerGrid"
import { SessionHeader } from "./SessionHeader"
import { VoicesPanel } from "./VoicesPanel"

export function SessionView() {
  const liveMic = useStore((s) => s.session?.source?.kind === "mic" && s.session.status !== "idle")
  const qualityOpen = useStore((s) => s.session?.audio_monitor ?? true)
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <SessionHeader />
      <div className="flex min-h-0 flex-1 flex-col lg:flex-row">
        <main className="flex min-h-[60vh] min-w-0 flex-1 flex-col lg:min-h-0">
          <PlayerGrid />
          <div className="border-t" />
          <ChatFeed />
        </main>
        <aside className="space-y-6 overflow-y-auto border-t p-4 lg:w-80 lg:border-t-0 lg:border-l">
          <VoicesPanel />
        </aside>
        {liveMic &&
          (qualityOpen ? (
            <aside className="flex min-h-0 w-full shrink-0 flex-col overflow-y-auto border-t p-4 lg:w-80 lg:border-t-0 lg:border-l">
              <LiveQualityBox />
            </aside>
          ) : (
            <div className="flex w-full shrink-0 justify-center border-t py-2 lg:w-12 lg:flex-col lg:border-t-0 lg:border-l">
              <LiveQualityBox />
            </div>
          ))}
      </div>
    </div>
  )
}
