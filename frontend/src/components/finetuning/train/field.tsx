import type { ReactNode } from "react";

import { Icon } from "@/components/ui/icons";
import { Label } from "@/components/ui/label";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { PROSE } from "@/lib/typography";
import { cn } from "@/lib/utils";

export function Field({
  label,
  htmlFor,
  hint,
  className,
  children,
}: {
  label: string;
  htmlFor?: string;
  /** What the platform does with this choice that the page cannot show. */
  hint?: string;
  className?: string;
  children: ReactNode;
}) {
  return (
    <div className={cn("flex min-w-0 flex-col gap-1.5", className)}>
      <Label className="gap-1.5 text-xs text-muted-foreground" htmlFor={htmlFor}>
        {label}
        {hint && (
          <Tooltip>
            <TooltipTrigger asChild>
              <button
                aria-label={`About ${label.toLowerCase()}`}
                className="inline-flex rounded-sm text-muted-foreground/70 outline-none transition-colors hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/60"
                type="button"
              >
                <Icon.info className="size-3.5" />
              </button>
            </TooltipTrigger>
            <TooltipContent className={cn(PROSE, "max-w-64 text-xs")} side="bottom">
              {hint}
            </TooltipContent>
          </Tooltip>
        )}
      </Label>
      <div className="flex min-w-0 flex-col gap-2">{children}</div>
    </div>
  );
}

export function SetupGrid({ children }: { children: ReactNode }) {
  return (
    <div className="grid grid-cols-1 items-start gap-x-5 gap-y-3 sm:grid-cols-2 lg:grid-cols-3">
      {children}
    </div>
  );
}

export function Column({
  label,
  meta,
  children,
  className,
}: {
  label?: string;
  meta?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={cn("flex flex-col gap-3", className)}>
      {(label || meta) && (
        <header className="flex h-6 shrink-0 items-center justify-between gap-3">
          {label ? (
            <h3 className="pixel-label shrink-0 text-xs text-muted-foreground">{label}</h3>
          ) : null}
          {meta}
        </header>
      )}
      {/* No inner scroller: its scrollbar narrows the content against the header
          above it, and the two stop lining up. The dialog body scrolls instead. */}
      <div className="flex flex-col gap-2">{children}</div>
    </section>
  );
}

export function EmptyField({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-8 items-center rounded-sm border border-dashed border-border px-3 py-1.5 text-xs leading-snug text-muted-foreground">
      {children}
    </div>
  );
}

/** Middot-joined facts; nullish entries drop out. */
export function FactLine({
  items,
  className,
}: {
  items: (ReactNode | null | false | undefined)[];
  className?: string;
}) {
  const shown = items.filter(Boolean);
  if (shown.length === 0) return null;
  return (
    <p
      className={cn("flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground", className)}
    >
      {shown.map((item, i) => (
        <span className="flex items-center gap-1.5" key={i}>
          {i > 0 && <span className="text-border">·</span>}
          {item}
        </span>
      ))}
    </p>
  );
}

export interface Segment {
  label: string;
  value: number;
  className: string;
}

/** Proportional bar and its legend on one line; segments under 2% still draw a sliver. */
export function SplitBar({ segments }: { segments: Segment[] }) {
  const total = segments.reduce((sum, s) => sum + s.value, 0);
  if (total <= 0) return null;
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
      <span className="flex h-1.5 w-24 shrink-0 overflow-hidden rounded-xs bg-border/60">
        {segments.map((s) => (
          <span
            className={s.className}
            key={s.label}
            style={{ width: `${Math.max(2, (s.value / total) * 100)}%` }}
          />
        ))}
      </span>
      {segments.map((s) => (
        <span className="flex items-center gap-1.5 text-xs text-muted-foreground" key={s.label}>
          <span aria-hidden className={cn("size-1.5 rounded-xs", s.className)} />
          {s.label}
          <span className="tabular-nums text-foreground">{s.value.toLocaleString()}</span>
        </span>
      ))}
    </div>
  );
}
