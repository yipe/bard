import * as React from "react"
import { ChevronDown } from "lucide-react"
import { cn } from "@/lib/utils"

/** A styled native <select>: keyboard- and screen-reader-friendly, no popup layer. */
function NativeSelect({ className, children, ...props }: React.ComponentProps<"select">) {
  return (
    <span data-slot="native-select" className={cn("relative inline-flex", className)}>
      <select
        className="h-8 w-full min-w-0 appearance-none rounded-lg border border-input bg-transparent py-1 pr-7 pl-2.5 text-sm transition-colors outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 disabled:cursor-not-allowed disabled:opacity-50 dark:bg-input/30 [&>option]:bg-popover"
        {...props}
      >
        {children}
      </select>
      <ChevronDown className="pointer-events-none absolute top-1/2 right-2 size-3.5 -translate-y-1/2 text-muted-foreground" />
    </span>
  )
}

export { NativeSelect }
