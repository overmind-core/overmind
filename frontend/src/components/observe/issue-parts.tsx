import type { ReactNode } from "react";

import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import type { IssueRow } from "@/openapi";

const SEVERITY = {
  critical: { label: "Critical", variant: "destructive" },
  high: { label: "High", variant: "error" },
  low: { label: "Low", variant: "neutral" },
  medium: { label: "Medium", variant: "warning" },
} as const;

const STATE: Record<IssueRow["state"], string> = {
  escalating: "Escalating",
  muted: "Muted",
  new: "New",
  ongoing: "Ongoing",
  regressed: "Regressed",
  resolved: "Resolved",
};

// Colour carries severity and nothing else on this page's issue surfaces.
export const SEVERITY_TEXT: Record<IssueRow["severity"], string> = {
  critical: "text-destructive",
  high: "text-destructive",
  low: "text-muted-foreground",
  medium: "text-warning",
};

export const SEVERITY_RULE: Record<IssueRow["severity"], string> = {
  critical: "border-t-destructive",
  high: "border-t-destructive",
  low: "border-t-border",
  medium: "border-t-warning",
};

export function SeverityBadge({ severity }: { severity: IssueRow["severity"] }) {
  const { label, variant } = SEVERITY[severity];
  return (
    <Badge className="w-[4.5rem] justify-center" size="chip" variant={variant}>
      {label}
    </Badge>
  );
}

export function stateLabel(state: IssueRow["state"]) {
  return STATE[state];
}

export function formatRate(rate: number | null | undefined) {
  if (rate == null) return "—";
  if (rate > 0 && rate < 0.001) return "<0.1%";
  return `${(rate * 100).toFixed(rate < 0.1 ? 2 : 1)}%`;
}

export function SectionHeading({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  return <h2 className={cn("mb-3 text-sm text-foreground", className)}>{children}</h2>;
}

const SPARK_BARS = 28;

// Long ranges collapse into bars wide enough to read at table-row height.
function binned(values: number[]) {
  if (values.length <= SPARK_BARS) return values;
  const bins = new Array<number>(SPARK_BARS).fill(0);
  values.forEach((value, index) => {
    bins[Math.floor((index * SPARK_BARS) / values.length)] += value;
  });
  return bins;
}

export function Sparkline({ values }: { values: number[] }) {
  const bars = binned(values);
  const peak = Math.max(...bars, 1);
  return (
    <span aria-hidden="true" className="flex h-5 w-full items-end gap-px">
      {bars.map((count, index) => (
        <span
          className={count ? "flex-1 rounded-xs bg-foreground/70" : "flex-1 bg-border"}
          key={index}
          style={{ height: count ? `${Math.max(20, (count / peak) * 100)}%` : "1px" }}
        />
      ))}
    </span>
  );
}
