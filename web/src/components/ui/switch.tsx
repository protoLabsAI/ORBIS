import * as React from "react"
import { Switch as SwitchPrimitive } from "radix-ui"

import { cn } from "@/lib/utils"

/**
 * The ORBIS pill toggle — the single source for on/off switches. Radix for
 * a11y (role=switch, keyboard, focus management); ORBIS design tokens for
 * colour (brand when on, raised/edge when off, fg thumb).
 *
 * The thumb is anchored with `left` (left-0.5 ↔ left-4), not translate-x, so
 * it's pinned a fixed inset from each end and physically can't slide past the
 * track edge at any size.
 */
function Switch({
  className,
  ...props
}: React.ComponentProps<typeof SwitchPrimitive.Root>) {
  return (
    <SwitchPrimitive.Root
      data-slot="switch"
      className={cn(
        "relative inline-flex h-5 w-9 shrink-0 cursor-pointer items-center rounded-full border outline-none transition-colors",
        "focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50",
        "disabled:cursor-not-allowed disabled:opacity-50",
        "data-[state=checked]:border-brand data-[state=checked]:bg-brand/80",
        "data-[state=unchecked]:border-edge data-[state=unchecked]:bg-raised",
        className
      )}
      {...props}
    >
      <SwitchPrimitive.Thumb
        data-slot="switch-thumb"
        className={cn(
          "pointer-events-none absolute top-1/2 size-3.5 -translate-y-1/2 rounded-full bg-fg shadow-sm transition-[left] duration-150",
          "data-[state=checked]:left-4 data-[state=unchecked]:left-0.5"
        )}
      />
    </SwitchPrimitive.Root>
  )
}

export { Switch }
