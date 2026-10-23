import { useCallback, useEffect, useState } from "react"
import { Check, HelpCircle, X } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { NativeSelect } from "@/components/ui/native-select"
import { api } from "@/lib/api"
import { attempt } from "@/lib/campaignStore"
import { cn } from "@/lib/utils"
import type { MicCapsule, MicCheck, MicInfo, MicPattern, MicSpecsPatch } from "@/lib/types"

const PATTERNS: { value: MicPattern; label: string }[] = [
  { value: "omni", label: "Omni" },
  { value: "cardioid", label: "Cardioid" },
  { value: "supercardioid", label: "Supercardioid" },
  { value: "hypercardioid", label: "Hypercardioid" },
  { value: "figure-8", label: "Figure-8" },
  { value: "stereo", label: "Stereo" },
  { value: "boundary", label: "Boundary" },
  { value: "beamforming", label: "Beamforming" },
]

const CAPSULES: { value: MicCapsule; label: string }[] = [
  { value: "condenser", label: "Condenser" },
  { value: "dynamic", label: "Dynamic" },
  { value: "array", label: "Mic array" },
]

/** One line of advice for the first failing check (in order of importance). */
const ADVICE: Record<MicCheck["id"], string> = {
  around:
    "This mic is built for one voice up close. For the middle of a table, use an omnidirectional or boundary mic, or switch this one to omni if it can.",
  distance:
    "This mic needs people close to it. Put it in the middle of the table, or use one made to pick up a room.",
  wired: "Bluetooth squeezes the mic's sound. Use a wired (USB) mic, or the mic on the computer.",
  rate: "Set this input to 48 kHz (or 44.1 kHz) in Audio MIDI Setup.",
  measured: "Past sessions with this mic sounded poor. Open them under Sessions → Audio to see why.",
}
const ADVICE_ORDER: MicCheck["id"][] = ["around", "distance", "wired", "rate", "measured"]

/** Setup: what Troubadour knows about the selected mic and whether it suits a table. */
export function MicDetails({ device }: { device: string | null }) {
  const [mics, setMics] = useState<MicInfo[] | null>(null)
  const [editing, setEditing] = useState(false)

  const load = useCallback(() => api.mics().then(setMics, () => setMics([])), [])
  // Again on every device change: the newly picked mic may have just been plugged in.
  useEffect(() => {
    void load()
  }, [load, device])

  if (mics === null) return <p className="text-xs text-muted-foreground">Checking the microphone…</p>
  const mic =
    mics.find((m) => m.name === device) ?? (device == null ? mics.find((m) => m.default) : undefined)
  if (!mic) return null

  const failing = ADVICE_ORDER.find((id) => mic.checks.some((c) => c.id === id && c.ok === false))
  const unknown = mic.specs.source === "unknown"
  const details = [
    mic.maker,
    mic.connection,
    mic.rate ? `${(mic.rate / 1000).toLocaleString(undefined, { maximumFractionDigits: 1 })} kHz` : null,
  ].filter(Boolean)

  return (
    <section aria-label="Microphone details" className="space-y-2 rounded-lg border p-3 text-sm">
      <div className="flex items-baseline gap-2">
        <span className="min-w-0 flex-1 truncate font-medium" data-testid="mic-model">
          {mic.specs.model ?? mic.name}
        </span>
        {!unknown && !editing && (
          <button
            type="button"
            className="text-xs text-primary hover:underline"
            onClick={() => setEditing(true)}
          >
            Edit
          </button>
        )}
      </div>
      {details.length > 0 && <p className="text-xs text-muted-foreground">{details.join(" · ")}</p>}

      <ul aria-label="Mic checks" className="space-y-1">
        {mic.checks.map((c) => (
          <li key={c.id} data-check={c.id} data-ok={String(c.ok)} className="flex items-start gap-2">
            {c.ok === true ? (
              <Check aria-label="OK" className="mt-0.5 size-3.5 shrink-0 text-emerald-400" />
            ) : c.ok === false ? (
              <X aria-label="Problem" className="mt-0.5 size-3.5 shrink-0 text-red-400" />
            ) : (
              <HelpCircle aria-label="Unknown" className="mt-0.5 size-3.5 shrink-0 text-muted-foreground" />
            )}
            <span className="min-w-0">
              {c.label}
              {c.detail && <span className="block text-xs text-muted-foreground">{c.detail}</span>}
            </span>
          </li>
        ))}
      </ul>

      {failing && (
        <p role="note" className="rounded-md bg-amber-500/10 px-2 py-1.5 text-xs text-amber-200">
          {ADVICE[failing]}
        </p>
      )}

      {(unknown || editing) && (
        <SpecsForm
          mic={mic}
          title={unknown ? "Tell Troubadour about this mic" : "Correct this mic's details"}
          onCancel={unknown ? undefined : () => setEditing(false)}
          onSaved={async () => {
            await load()
            setEditing(false)
          }}
        />
      )}
    </section>
  )
}

function SpecsForm({
  mic,
  title,
  onCancel,
  onSaved,
}: {
  mic: MicInfo
  title: string
  onCancel?: () => void
  onSaved: () => void
}) {
  const [model, setModel] = useState(mic.specs.model ?? "")
  const [capsule, setCapsule] = useState<MicCapsule | "">(mic.specs.capsule ?? "")
  const [patterns, setPatterns] = useState<MicPattern[]>(mic.specs.patterns ?? [])
  const [busy, setBusy] = useState(false)

  const toggle = (p: MicPattern) =>
    setPatterns((list) => (list.includes(p) ? list.filter((x) => x !== p) : [...list, p]))

  return (
    <form
      aria-label={title}
      className="space-y-2 border-t pt-2"
      onSubmit={async (e) => {
        e.preventDefault()
        const body: MicSpecsPatch = { patterns }
        if (model.trim()) body.model = model.trim()
        if (capsule) body.capsule = capsule
        setBusy(true)
        const saved = await attempt(() => api.updateMic(mic.name, body))
        setBusy(false)
        if (saved) onSaved()
      }}
    >
      <p className="text-xs font-medium">{title}</p>
      <Input
        aria-label="Model"
        placeholder="Model, e.g. Yeti GX"
        value={model}
        onChange={(e) => setModel(e.target.value)}
      />
      <NativeSelect
        aria-label="Capsule"
        className="w-full"
        value={capsule}
        onChange={(e) => setCapsule(e.target.value as MicCapsule | "")}
      >
        <option value="">Capsule: not sure</option>
        {CAPSULES.map((c) => (
          <option key={c.value} value={c.value}>
            {c.label}
          </option>
        ))}
      </NativeSelect>
      <div role="group" aria-label="Pickup patterns" className="flex flex-wrap gap-1">
        {PATTERNS.map((p) => (
          <button
            key={p.value}
            type="button"
            aria-pressed={patterns.includes(p.value)}
            onClick={() => toggle(p.value)}
            className={cn(
              "rounded-full px-2 py-0.5 text-xs transition-colors",
              patterns.includes(p.value)
                ? "bg-primary text-primary-foreground"
                : "bg-muted text-muted-foreground hover:text-foreground",
            )}
          >
            {p.label}
          </button>
        ))}
      </div>
      <div className="flex gap-2">
        <Button type="submit" size="sm" disabled={busy}>
          Save
        </Button>
        {onCancel && (
          <Button type="button" size="sm" variant="ghost" onClick={onCancel}>
            Cancel
          </Button>
        )}
      </div>
    </form>
  )
}
