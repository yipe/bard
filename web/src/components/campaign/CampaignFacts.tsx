import { ScrollText } from "lucide-react"
import { api } from "@/lib/api"
import { mutateCampaignFacts } from "@/lib/campaignStore"
import { FactList } from "./FactList"

/** Campaign-wide facts (not about any one entry). */
export function CampaignFacts({ facts }: { facts: string[] }) {
  return (
    <section className="space-y-2 rounded-xl border bg-card p-3">
      <h3 className="flex items-center gap-2 text-sm font-semibold">
        <ScrollText className="size-4" /> Campaign facts
      </h3>
      <FactList
        facts={facts}
        empty="Nothing yet — e.g. the party's goal, the calendar, house rules."
        addPlaceholder="Add a campaign fact…"
        addLabel="New campaign fact"
        onEdit={(fact, next) =>
          mutateCampaignFacts((latest) => api.setCampaignFacts(latest.map((f) => (f === fact ? next : f))))
        }
        onDelete={(fact) =>
          mutateCampaignFacts((latest) => api.setCampaignFacts(latest.filter((f) => f !== fact)))
        }
        onAdd={(text) => mutateCampaignFacts(() => api.addCampaignFact(text))}
      />
    </section>
  )
}
