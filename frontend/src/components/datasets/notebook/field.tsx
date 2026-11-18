import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

/** A labelled value, drawn like the version chip in the cell header. */
export function Field({
  label,
  title,
  tone = "text-foreground",
  children,
}: {
  label: string;
  title?: string;
  tone?: string;
  children: ReactNode;
}) {
  return (
    <span
      className="inline-flex h-6 min-w-0 items-center overflow-hidden rounded-sm border border-border/70 bg-card text-xs"
      title={title}
    >
      <span className="pixel-label flex h-full shrink-0 items-center border-r border-border/70 px-2 text-muted-foreground">
        {label}
      </span>
      <span className={cn("flex min-w-0 items-center gap-1.5 px-2", tone)}>{children}</span>
    </span>
  );
}
