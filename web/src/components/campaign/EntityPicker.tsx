import { NativeSelect } from "@/components/ui/native-select"
import type { Entity } from "@/lib/types"

/** Pick a glossary entry by name (sorted), excluding `exclude`. */
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
  const options = entities
    .filter((e) => e.name !== exclude)
    .sort((a, b) => a.name.localeCompare(b.name, undefined, { sensitivity: "base" }))
  return (
    <NativeSelect
      aria-label={label}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className={className}
    >
      <option value="">Choose an entry…</option>
      {options.map((e) => (
        <option key={e.name} value={e.name}>
          {e.name}
        </option>
      ))}
    </NativeSelect>
  )
}
