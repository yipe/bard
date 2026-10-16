import { memo, useEffect, useRef, useState } from "react"
import { Virtuoso, type VirtuosoHandle } from "react-virtuoso"
import { ArrowDown, MessagesSquare } from "lucide-react"
import { TranscriptRow, TranscriptSpacer } from "@/components/transcript/TranscriptRow"
import { Button } from "@/components/ui/button"
import { actions, useStore } from "@/lib/store"
import { speakerFor, speakerKey, type Speaker } from "@/lib/speaker"
import { LineSpeakerPicker } from "./LineSpeakerPicker"
import type { Message } from "@/lib/types"

export function ChatFeed() {
  const messages = useStore((s) => s.messages)
  const voices = useStore((s) => s.voices)
  const players = useStore((s) => s.players)
  const bumps = useStore((s) => s.bumps)
  const ref = useRef<VirtuosoHandle>(null)
  const [atBottom, setAtBottom] = useState(true)
  const hasMessages = messages.length > 0

  // Belt and braces: land on the newest line once the list has real layout.
  useEffect(() => {
    if (!hasMessages) return
    const id = requestAnimationFrame(() => ref.current?.scrollToIndex({ index: "LAST", align: "end" }))
    return () => cancelAnimationFrame(id)
  }, [hasMessages])

  if (messages.length === 0) {
    return (
      <div className="flex min-h-0 flex-1 flex-col items-center justify-center gap-2 px-4 text-center text-muted-foreground">
        <MessagesSquare className="size-8" />
        <p>No messages yet — start talking and the transcript will appear here.</p>
      </div>
    )
  }

  return (
    <div className="relative min-h-0 flex-1">
      <Virtuoso
        ref={ref}
        // A definite size from the first paint: with h-full inside flex the list mounted at 0 px
        // tall, rendered nothing, and stayed blank until the user scrolled.
        style={{ position: "absolute", inset: 0 }}
        data={messages}
        computeItemKey={(_, m) => m.id}
        initialTopMostItemIndex={messages.length - 1}
        alignToBottom
        // Stick to the bottom only while the user is already there (also covers rows growing in place).
        followOutput={(isAtBottom) => (isAtBottom ? "auto" : false)}
        atBottomThreshold={48}
        atBottomStateChange={setAtBottom}
        increaseViewportBy={400}
        components={{ Header: TranscriptSpacer, Footer: TranscriptSpacer }}
        itemContent={(i, m) => {
          const speaker = speakerFor(m.voice_id, voices, players)
          const key = speakerKey(speaker.player?.id ?? null, speaker.voice?.id ?? null, speaker.character)
          const prev = i > 0 ? speakerFor(messages[i - 1].voice_id, voices, players) : null
          const prevKey = prev && speakerKey(prev.player?.id ?? null, prev.voice?.id ?? null, prev.character)
          return (
            <MessageRow
              message={m}
              bump={bumps[m.id] ?? 0}
              speaker={speaker}
              continued={key !== null && key === prevKey}
            />
          )
        }}
      />
      {!atBottom && (
        <Button
          size="sm"
          className="absolute bottom-4 left-1/2 -translate-x-1/2 shadow-lg"
          onClick={() => ref.current?.scrollToIndex({ index: "LAST", align: "end", behavior: "smooth" })}
        >
          <ArrowDown /> Jump to latest
        </Button>
      )}
    </div>
  )
}
const MessageRow = memo(
  function MessageRow({
    message: m,
    speaker,
    bump,
    continued,
  }: {
    message: Message
    speaker: Speaker
    bump: number
    continued: boolean
  }) {
    const pending = speaker.voice?.status === "pending"
    return (
      <TranscriptRow
        id={m.id}
        text={m.text}
        fixes={m.fixes}
        tStart={m.t_start}
        uncertain={m.provisional || m.confidence < 0.5}
        speaker={speaker.name}
        person={speaker.player}
        character={speaker.character}
        bump={bump}
        continued={continued}
        renderName={(name) => <LineSpeakerPicker messageId={m.id}>{name}</LineSpeakerPicker>}
        extra={
          pending &&
          speaker.voice && (
            <button
              type="button"
              className="text-xs text-primary underline-offset-2 hover:underline"
              onClick={() => actions.focusVoice(speaker.voice!.id)}
            >
              Name this voice
            </button>
          )
        }
      />
    )
  },
  (a, b) =>
    a.message === b.message &&
    a.bump === b.bump &&
    a.continued === b.continued &&
    a.speaker.voice === b.speaker.voice &&
    a.speaker.player === b.speaker.player &&
    a.speaker.name === b.speaker.name &&
    a.speaker.character === b.speaker.character,
)
