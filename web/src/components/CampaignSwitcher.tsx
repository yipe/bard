import { useState } from "react"
import { toast } from "sonner"
import { Pencil } from "lucide-react"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { NativeSelect } from "@/components/ui/native-select"
import { api } from "@/lib/api"
import { useStore } from "@/lib/store"

const NEW = "__new__"

/** Header picker: which campaign is open. Switching happens between sessions only. */
export function CampaignSwitcher() {
  const campaign = useStore((s) => s.campaign)
  const campaigns = useStore((s) => s.campaigns)
  const busy = useStore((s) => (s.session?.status ?? "idle") !== "idle" || !!s.session?.training)
  // What the dialog is for; a rename keeps its campaign even if another one opens meanwhile.
  const [naming, setNaming] = useState<
    { mode: "create" } | { mode: "rename"; id: string; name: string } | null
  >(null)
  const current = campaigns.find((c) => c.id === campaign)

  const open = async (id: string) => {
    if (id === NEW) return setNaming({ mode: "create" })
    try {
      await api.openCampaign(id) // the server follows up with a snapshot of the other campaign
    } catch (e) {
      toast.error((e as Error).message)
    }
  }

  return (
    <div className="flex items-center gap-1">
      <NativeSelect
        aria-label="Campaign"
        title={busy ? "Stop the session to switch campaigns" : undefined}
        value={campaign}
        disabled={busy}
        onChange={(e) => void open(e.target.value)}
        className="w-48"
      >
        {campaigns.map((c) => (
          <option key={c.id} value={c.id}>
            {c.name}
          </option>
        ))}
        <option value={NEW}>New campaign…</option>
      </NativeSelect>
      <Button
        size="icon-sm"
        variant="ghost"
        aria-label="Rename campaign"
        disabled={!current}
        onClick={() => current && setNaming({ mode: "rename", id: current.id, name: current.name })}
      >
        <Pencil />
      </Button>
      {naming && (
        <CampaignNameDialog
          mode={naming.mode}
          initial={naming.mode === "rename" ? naming.name : ""}
          onClose={() => setNaming(null)}
          onSave={async (name) => {
            if (naming.mode === "rename") {
              await api.renameCampaign(naming.id, name)
            } else {
              // Created even if opening fails (e.g. a job is running); the picker lists it either way.
              const created = await api.createCampaign(name)
              void open(created.id)
            }
          }}
        />
      )}
    </div>
  )
}

function CampaignNameDialog({
  mode,
  initial,
  onClose,
  onSave,
}: {
  mode: "create" | "rename"
  initial: string
  onClose: () => void
  onSave: (name: string) => Promise<void>
}) {
  const [name, setName] = useState(initial)
  const [saving, setSaving] = useState(false)

  const submit = async () => {
    setSaving(true)
    try {
      await onSave(name.trim())
      onClose()
    } catch (e) {
      toast.error((e as Error).message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="sm:max-w-sm">
        <DialogHeader>
          <DialogTitle>{mode === "create" ? "New campaign" : "Rename campaign"}</DialogTitle>
          {mode === "create" && (
            <DialogDescription>
              It gets its own notes and sessions. Players from your other campaigns keep their voice training.
            </DialogDescription>
          )}
        </DialogHeader>
        <form
          id="campaign-name"
          onSubmit={(e) => {
            e.preventDefault()
            void submit()
          }}
        >
          <Input
            aria-label="Campaign name"
            autoFocus
            maxLength={60}
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Sunken Crown"
          />
        </form>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" form="campaign-name" disabled={saving || !name.trim()}>
            {mode === "create" ? "Create and open" : "Save"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
