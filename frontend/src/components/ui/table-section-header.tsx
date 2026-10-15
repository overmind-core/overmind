import type { ReactNode } from "react";

import { CountChip } from "@/components/ui/count-chip";
import { TITLE } from "@/lib/typography";

export function TableSectionHeader({
  title,
  description,
  icon,
  count,
  actions,
  toolbar,
}: {
  title: string;
  description: string;
  icon: ReactNode;
  count: number;
  actions?: ReactNode;
  toolbar: ReactNode;
}) {
  return (
    <div className="overflow-hidden rounded-md border border-border bg-card">
      <div className="flex flex-wrap items-start justify-between gap-4 px-4 py-3">
        <div className="flex min-w-0 items-start gap-3">
          <span className="mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-sm border border-border bg-wash-raised text-foreground">
            {icon}
          </span>
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <h2 className={TITLE.card}>{title}</h2>
              <CountChip count={count} />
            </div>
            <p className="mt-1 max-w-2xl text-sm text-muted-foreground">{description}</p>
          </div>
        </div>
        {actions ? <div className="flex items-center gap-2">{actions}</div> : null}
      </div>
      <div className="border-t border-border/70 px-4 py-3">{toolbar}</div>
    </div>
  );
}
