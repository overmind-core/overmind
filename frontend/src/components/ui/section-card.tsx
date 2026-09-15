import type * as React from "react";

import { Card } from "@/components/ui/card";
import { PROSE } from "@/lib/typography";
import { cn } from "@/lib/utils";

// Body font, not the display face: a section label reads as UI, not a headline.
function SectionLabel({ children, icon }: { children: React.ReactNode; icon?: React.ReactNode }) {
  return (
    <p className="inline-flex items-center gap-1.5 text-sm font-semibold text-foreground">
      {icon}
      {children}
    </p>
  );
}

// No shadow — depth is border/surface (DESIGN.md "Elevation & Depth").
export function SectionCard({
  title,
  description,
  icon,
  headerEnd,
  children,
  className,
  contentClassName,
}: {
  title: React.ReactNode;
  description?: React.ReactNode;
  icon?: React.ReactNode;
  headerEnd?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
  contentClassName?: string;
}) {
  return (
    <Card className={cn("overflow-hidden border-border bg-wash-subtle", className)}>
      <div
        className={cn(
          "flex justify-between gap-3 border-b border-border/70 bg-wash-raised px-4 py-3",
          description ? "items-start" : "items-center"
        )}
      >
        <div className="min-w-0">
          <SectionLabel icon={icon}>{title}</SectionLabel>
          {description ? (
            <p className={cn(PROSE, "mt-1 text-sm leading-snug text-muted-foreground")}>
              {description}
            </p>
          ) : null}
        </div>
        {headerEnd ? <div className="min-w-0 shrink-0">{headerEnd}</div> : null}
      </div>
      <div
        className={cn("bg-card/95 p-4 text-sm leading-relaxed dark:bg-card/80", contentClassName)}
      >
        {children}
      </div>
    </Card>
  );
}
