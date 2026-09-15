import type { ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { Icon } from "@/components/ui/icons";
import { PROSE } from "@/lib/typography";

/** Fixed bottom-center next-action bar. Presentational — callers own visibility. */
export function FloatingNudge({
  icon,
  title,
  description,
  cta,
  onDismiss,
}: {
  icon: ReactNode;
  title: string;
  description: string;
  cta: ReactNode;
  onDismiss: () => void;
}) {
  return (
    <div className="pointer-events-none fixed bottom-6 left-1/2 z-30 -translate-x-1/2">
      <div className="pointer-events-auto flex items-center gap-4 rounded-md border border-border bg-popover px-5 py-3.5">
        <div className="shrink-0 text-foreground">{icon}</div>
        <div className="flex flex-col">
          <span className="text-sm font-semibold text-foreground">{title}</span>
          <span className={`${PROSE} text-xs text-muted-foreground`}>{description}</span>
        </div>
        {cta}
        <Button aria-label="Dismiss" onClick={onDismiss} size="icon-sm" variant="ghost">
          <Icon.close />
        </Button>
      </div>
    </div>
  );
}
