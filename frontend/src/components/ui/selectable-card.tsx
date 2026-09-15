/** A card that embeds its own controls can't nest inside the `<button>`; those call
 *  sites apply `selectableCardVariants` to their own element instead. */

import type * as React from "react";

import { cva, type VariantProps } from "class-variance-authority";

import { cn } from "@/lib/utils";

const selectableCardVariants = cva(
  "rounded-md border text-left outline-none transition-colors duration-150 focus-visible:ring-2 focus-visible:ring-ring/60 disabled:pointer-events-none disabled:opacity-50",
  {
    defaultVariants: { selected: false },
    variants: {
      selected: {
        false:
          "border-border/60 bg-card text-foreground hover:border-primary/40 hover:bg-wash-subtle",
        true: "border-primary/40 bg-primary/10 text-primary",
      },
    },
  }
);

interface SelectableCardProps
  extends Omit<React.ComponentProps<"button">, "onSelect">,
    VariantProps<typeof selectableCardVariants> {
  selected: boolean;
  onSelect: () => void;
  /** One option among siblings inside a `SelectableCardGroup` — omit for a standalone toggle. */
  role?: "radio";
}

function SelectableCard({
  className,
  selected,
  onSelect,
  role,
  disabled,
  children,
  ...props
}: SelectableCardProps) {
  return (
    <button
      aria-checked={role === "radio" ? selected : undefined}
      aria-pressed={role === "radio" ? undefined : selected}
      className={cn(selectableCardVariants({ selected }), className)}
      data-selected={selected ? "" : undefined}
      disabled={disabled}
      onClick={onSelect}
      role={role}
      type="button"
      {...props}
    >
      {children}
    </button>
  );
}

function SelectableCardGroup({ className, ...props }: React.ComponentProps<"div">) {
  return <div className={className} role="radiogroup" {...props} />;
}

export { SelectableCard, SelectableCardGroup, selectableCardVariants };
