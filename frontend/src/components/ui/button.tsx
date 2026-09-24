import type * as React from "react";

import { cva, type VariantProps } from "class-variance-authority";
import { Slot } from "radix-ui";

import { cn } from "@/lib/utils";

// Icon↔label spacing is the primitive's job: every size sets its own `gap`, so a call
// site must NOT add `mr-*` to the icon. `border border-transparent` on the base gives
// every variant the same content box as `outline`, the one variant with a visible edge.
const buttonVariants = cva(
  "inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-sm border border-transparent text-sm font-medium transition-colors duration-150 disabled:pointer-events-none disabled:opacity-50 [&_svg]:pointer-events-none [&_svg:not([class*='size-'])]:size-4 shrink-0 [&_svg]:shrink-0 outline-none focus-visible:ring-2 focus-visible:ring-ring/60",
  {
    defaultVariants: {
      size: "default",
      variant: "default",
    },
    variants: {
      size: {
        default: "h-8 px-3 has-[>svg]:px-2.5",
        icon: "size-8",
        "icon-lg": "size-9",
        "icon-sm": "size-7",
        "icon-xs": "size-6 [&_svg:not([class*='size-'])]:size-3",
        lg: "h-9 px-5 has-[>svg]:px-3.5",
        sm: "h-7 gap-1.5 px-2.5 has-[>svg]:px-2",
        xs: "h-6 gap-1 px-2 text-xs has-[>svg]:px-1.5 [&_svg:not([class*='size-'])]:size-3",
      },
      variant: {
        default: "bg-primary text-primary-foreground hover:bg-primary/85",
        destructive: "bg-destructive text-white hover:bg-destructive/90",
        // Icon-only affordances. A labelled button takes a filled variant instead
        // (`check:controls` enforces this).
        ghost: "hover:bg-accent/60 hover:text-accent-foreground",
        link: "text-primary underline underline-offset-4",
        // For controls on an already-filled surface, or carrying their own accent
        // border. Not the default neutral button.
        outline: "border-border bg-transparent hover:bg-accent/50",
        // The neutral control face — a fill, not an outline.
        secondary: "bg-control text-foreground hover:bg-control-hover",
        warning: "bg-warning text-warning-foreground hover:bg-warning/90",
      },
    },
  }
);

function Button({
  className,
  variant = "default",
  size = "default",
  asChild = false,
  ...props
}: React.ComponentProps<"button"> &
  VariantProps<typeof buttonVariants> & {
    asChild?: boolean;
  }) {
  const Comp = asChild ? Slot.Root : "button";

  return (
    <Comp
      className={cn(buttonVariants({ className, size, variant }))}
      data-size={size}
      data-slot="button"
      data-variant={variant}
      {...props}
    />
  );
}

export { Button, buttonVariants };
