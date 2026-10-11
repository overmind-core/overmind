import type { ReactNode } from "react";

import { HelpTip } from "@/components/finetuning/finetuning-chrome";
import type { MetricSeries } from "@/components/finetuning/loss-chart";
import { type MetricKind, MetricSummary } from "@/components/finetuning/metric-summary";
import { Card } from "@/components/ui/card";
import { Icon } from "@/components/ui/icons";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

export function MonitorChartCard({
  title,
  subtitle,
  children,
  emptyHint,
  help,
  isLoading,
  className,
  summary,
}: {
  title: string;
  subtitle?: string;
  children: ReactNode;
  emptyHint: string;
  help: string;
  isLoading?: boolean;
  className?: string;
  summary?: { series: MetricSeries[]; kind: MetricKind; terminal?: boolean };
}) {
  const hasContent = children != null && children !== false && children !== true;
  return (
    <Card className={cn("flex min-w-0 flex-col", className)}>
      <div className="flex min-h-11 items-center gap-2 border-b border-border/70 px-3 py-2.5">
        <Icon.chart className="size-4 shrink-0 text-muted-foreground" />
        <h3 className="shrink-0 text-sm leading-none">{title}</h3>
        {subtitle && (
          <span className="truncate text-xs leading-none text-muted-foreground">{subtitle}</span>
        )}
        <span className="ml-auto flex shrink-0 items-center">
          <HelpTip label={title} text={help} />
        </span>
      </div>
      {isLoading ? (
        <Skeleton className="h-52 rounded-none" />
      ) : hasContent ? (
        children
      ) : (
        <div className="flex h-52 items-center justify-center px-3 text-center text-sm text-muted-foreground">
          {emptyHint}
        </div>
      )}
      {summary && !isLoading && <MetricSummary {...summary} />}
    </Card>
  );
}
