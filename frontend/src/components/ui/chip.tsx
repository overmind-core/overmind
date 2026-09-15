/** The interactive pill, carrying a selected state. Not for static metadata — a
 *  non-interactive tag is a `Badge`. */

import type * as React from "react";

import { cva, type VariantProps } from "class-variance-authority";

import { cn } from "@/lib/utils";

const chipVariants = cva(
  "chip-label inline-flex shrink-0 cursor-pointer items-center gap-1.5 rounded-sm border border-transparent px-2.5 text-xs whitespace-nowrap transition-colors duration-150 outline-none focus-visible:ring-2 focus-visible:ring-ring/60 disabled:pointer-events-none disabled:opacity-50 [&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-3",
  {
    defaultVariants: { selected: false, size: "default" },
    variants: {
      selected: {
        false: "bg-control text-muted-foreground hover:bg-control-hover hover:text-foreground",
        // Selected fills like a primary button; `SelectableCard` tints instead. Those
        // two are the whole set of selected treatments.
        true: "bg-primary text-primary-foreground hover:bg-primary/85",
      },
      size: {
        default: "h-7 px-2.5",
        // 32px — matches Button `default` / `icon`, for rows that mix the two.
        lg: "h-8 px-3",
        sm: "h-6 px-2",
      },
    },
  }
);

function Chip({
  className,
  selected = false,
  size = "default",
  ...props
}: Omit<React.ComponentProps<"button">, "size"> & VariantProps<typeof chipVariants>) {
  return (
    <button
      aria-pressed={selected ?? false}
      className={cn(chipVariants({ className, selected, size }))}
      data-selected={selected ? "" : undefined}
      data-slot="chip"
      type="button"
      {...props}
    />
  );
}

export { Chip };
