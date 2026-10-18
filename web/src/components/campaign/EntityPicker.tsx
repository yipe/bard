import { useMemo, useState } from "react"
import { ChevronsUpDown } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Command, CommandEmpty, CommandInput, CommandItem, CommandList } from "@/components/ui/command"
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover"
import { TYPE_LABEL } from "@/lib/campaign"
import { cn } from "@/lib/utils"
import type { Entity } from "@/lib/types"

/**
 * Pick a glossary entry by name, excluding `exclude`. Type to filter fuzzily; aliases and "heard as"
 * spellings match too, so "Ellara" finds the entry named Elara.
 */
export function EntityPicker({
  entities,
  value,
  onChange,
  exclude,
  label,
  className,
}: {
  entities: Entity[]
  value: string
  onChange: (name: string) => void
  exclude?: string
  label: string
  className?: string
}) {
  const [open, setOpen] = useState(false)
  const options = useMemo(
    () =>
      entities
        .filter((e) => e.name !== exclude)
        .sort((a, b) => a.name.localeCompare(b.name, undefined, { sensitivity: "base" })),
    [entities, exclude],
  )
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger
        render={
          <Button
            variant="outline"
            size="sm"
            role="combobox"
            aria-expanded={open}
            aria-label={label}
            className={cn("justify-between font-normal", className)}
          />
        }
      >
        <span className={cn("truncate", !value && "text-muted-foreground")}>
          {value || "Choose an entry…"}
        </span>
        <ChevronsUpDown className="opacity-50" />
      </PopoverTrigger>
      <PopoverContent align="start" className="w-64 p-0">
        <Command label={`Search: ${label}`}>
          <CommandInput placeholder="Search entries…" />
          <CommandList>
            <CommandEmpty>No matching entry.</CommandEmpty>
            {options.map((e) => (
              <CommandItem
                key={e.name}
                value={e.name}
                keywords={[...e.aliases, ...e.heard_as]}
                data-checked={e.name === value}
                onSelect={() => {
                  onChange(e.name)
                  setOpen(false)
                }}
              >
                <span className="truncate">{e.name}</span>
                <span className="text-xs text-muted-foreground">{TYPE_LABEL[e.type]}</span>
              </CommandItem>
            ))}
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  )
}
