import type * as React from "react";

import { cva, type VariantProps } from "class-variance-authority";

import { cn } from "@/lib/utils";

// `box-content` + `py-0.5` gives the 16px glyph a 20px box, exactly the `text-sm` line
// box beside it, so `items-start` lands it on the copy's first line.
const alertVariants = cva(
  "flex w-full items-start gap-2 rounded-md border px-4 py-3 text-sm *:min-w-0 [&>svg]:box-content [&>svg]:shrink-0 [&>svg]:py-0.5 [&>svg]:text-foreground [&>svg:not([class*='size-'])]:size-4",
  {
    defaultVariants: {
      variant: "default",
    },
    variants: {
      variant: {
        default: "bg-background text-foreground border-border",
        destructive: "border-destructive/50 text-destructive [&>svg]:text-destructive",
        info: "border-info/50 text-info [&>svg]:text-info",
        success: "border-success/50 text-success [&>svg]:text-success",
        warning: "border-warning/50 text-warning [&>svg]:text-warning",
      },
    },
  }
);

function Alert({
  className,
  variant,
  ...props
}: React.ComponentProps<"div"> & VariantProps<typeof alertVariants>) {
  return (
    <div
      className={cn(alertVariants({ variant }), className)}
      data-slot="alert"
      role="alert"
      {...props}
    />
  );
}

export { Alert, alertVariants };
