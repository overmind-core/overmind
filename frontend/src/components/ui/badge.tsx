import type * as React from "react";

import { cva, type VariantProps } from "class-variance-authority";

import { cn } from "@/lib/utils";

const badgeVariants = cva(
  "inline-flex items-center rounded-md border px-2.5 py-0.5 text-xs font-semibold transition-colors focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2",
  {
    defaultVariants: {
      variant: "default",
    },
    variants: {
      variant: {
        default: "border-transparent bg-primary text-primary-foreground",
        destructive: "border-transparent bg-destructive text-destructive-foreground",
        outline: "text-foreground",
        secondary: "border-transparent bg-secondary text-secondary-foreground",
        success: "border-transparent bg-green-500/20 text-green-600 dark:text-green-400",
        warning: "border-transparent bg-amber-500/20 text-amber-600 dark:text-amber-400",
      },
    },
  }
);

function Badge({
  className,
  variant,
  ...props
}: React.ComponentProps<"div"> & VariantProps<typeof badgeVariants>) {
  return <div className={cn(badgeVariants({ variant }), className)} data-slot="badge" {...props} />;
}

export { Badge, badgeVariants };
