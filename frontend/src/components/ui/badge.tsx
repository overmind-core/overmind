import type * as React from "react";

import { cva, type VariantProps } from "class-variance-authority";

import { cn } from "@/lib/utils";

const badgeVariants = cva(
  // items-center + leading-none + non-shrinking icon slots keep glyph and label on one
  // centre line: the pixel `chip-label` face otherwise drifts the text box off the
  // icon's cross-axis centre. Focus matches Button — 2px ring, reduced opacity, no offset.
  "chip-label inline-flex items-center leading-none rounded-sm border transition-colors outline-none focus-visible:ring-2 focus-visible:ring-ring/60 [&>svg]:shrink-0",
  {
    defaultVariants: {
      size: "default",
      variant: "default",
    },
    variants: {
      size: {
        // `chip` is the Traces-table chip metric (status/score/count cells).
        chip: "h-6 px-2 py-0 text-xs font-medium",
        default: "px-2.5 py-0.5 text-xs font-semibold",
      },
      variant: {
        default: "border-transparent bg-primary text-primary-foreground",
        destructive: "border-transparent bg-destructive text-destructive-foreground",
        error: "border-destructive/40 bg-destructive/10 text-destructive",
        info: "border-info/40 bg-info/10 text-info",
        neutral: "border-muted-foreground/50 bg-wash-raised text-muted-foreground",
        outline: "text-foreground",
        secondary: "border-transparent bg-secondary text-secondary-foreground",
        // Tokens flip per theme (--success/--warning/--info in styles.css), so no `dark:`.
        success: "border-success/40 bg-success/10 text-success",
        warning: "border-warning/40 bg-warning/10 text-warning",
      },
    },
  }
);

function Badge({
  className,
  variant,
  size,
  ...props
}: React.ComponentProps<"div"> & VariantProps<typeof badgeVariants>) {
  return (
    <div className={cn(badgeVariants({ size, variant }), className)} data-slot="badge" {...props} />
  );
}

export { Badge, badgeVariants };
