import type { ReactNode } from "react";

import { CountChip } from "@/components/ui/count-chip";
import { PROSE, TITLE } from "@/lib/typography";
import { cn } from "@/lib/utils";

// `icon` takes an element, not a name: a page mark is the raster/two-tone variant, not
// the sidebar's nav glyph for the same section, so the call site owns it. `[&_svg]`
// sizes a plain registry glyph without touching a raster one.
function PageHeader({
  title,
  description,
  actions,
  count,
  icon,
  meta,
  className,
}: {
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  count?: number;
  icon?: ReactNode;
  /** Chips and stats under the title. Inside the title column, so it indents with it. */
  meta?: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn("flex items-start justify-between gap-4", className)}
      data-slot="page-header"
    >
      <div className="min-w-0 space-y-1">
        <div className="flex items-center gap-2">
          {icon ? <span className="flex shrink-0 items-center [&_svg]:size-6">{icon}</span> : null}
          {/* Plain strings only: `truncate` sets `overflow: hidden`, which clips a node
              title whose own decoration bleeds outside the text box (the editable
              Capabilities title's hover pad). */}
          <h1
            className={cn(TITLE.page, "text-foreground", typeof title === "string" && "truncate")}
          >
            {title}
          </h1>
          {typeof count === "number" ? <CountChip count={count} /> : null}
        </div>
        {description ? (
          <p className={cn(PROSE, "max-w-2xl text-sm text-muted-foreground")}>{description}</p>
        ) : null}
        {meta ? (
          <div className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
            {meta}
          </div>
        ) : null}
      </div>
      {actions ? <div className="flex shrink-0 items-center gap-2">{actions}</div> : null}
    </div>
  );
}

export { PageHeader };
