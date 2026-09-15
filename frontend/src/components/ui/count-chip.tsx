import type * as React from "react";

import { cn } from "@/lib/utils";

function CountChip({
  count,
  className,
  ...props
}: React.ComponentProps<"span"> & { count: number }) {
  return (
    <span
      className={cn(
        "inline-flex h-5 min-w-5 items-center justify-center rounded-sm border border-border bg-muted px-1 text-xs leading-none tabular-nums text-muted-foreground",
        className
      )}
      data-slot="count-chip"
      {...props}
    >
      {count}
    </span>
  );
}

export { CountChip };
