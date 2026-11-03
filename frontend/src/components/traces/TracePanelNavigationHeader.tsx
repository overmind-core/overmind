import { useMemo } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Icon } from "@/components/ui/icons";
import { Skeleton } from "@/components/ui/skeleton";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { formatCost, formatDuration, formatNumber } from "@/lib/formatters";
import { useTraceData } from "./contexts/TraceDataContext";
import { buildSpanTree } from "./lib/tree-building";

export function TracePanelNavigationHeader({
  detailExpanded,
  onClose,
  onToggleExpanded,
}: {
  detailExpanded: boolean;
  onClose: () => void;
  onToggleExpanded: () => void;
}) {
  const { spans, traceStatus, usage, isLoading, traceId } = useTraceData();
  const roots = useMemo(() => buildSpanTree(spans), [spans]);
  const root = roots[0]?.span;
  const rootName = root?.name?.trim() || "Trace";
  return (
    <header className="flex shrink-0 items-center gap-2 border-b border-border/70 bg-card px-3 py-2">
      <Tooltip>
        <TooltipTrigger asChild>
          <Button aria-label="Close trace" onClick={onClose} size="icon-sm" variant="ghost">
            <Icon.close />
          </Button>
        </TooltipTrigger>
        <TooltipContent>Close trace</TooltipContent>
      </Tooltip>
      <div className="flex min-w-0 flex-1 flex-wrap items-center gap-x-4 gap-y-2">
        <div className="flex min-w-0 items-center gap-2">
          {isLoading ? (
            <Skeleton aria-label="Loading trace name" className="h-4 w-40 max-w-full" />
          ) : (
            <h2 className="max-w-64 truncate text-sm font-medium" title={rootName}>
              {rootName}
            </h2>
          )}
          <span
            className="max-w-32 truncate font-mono text-xs text-muted-foreground"
            title={traceId}
          >
            {traceId}
          </span>
        </div>
        <div className="flex flex-wrap items-center gap-1.5 tabular-nums">
          {isLoading ? (
            <Skeleton aria-label="Loading span count" className="h-6 w-20" />
          ) : (
            <>
              {root?.parentSpanId === null && root.durationNano > 0 && (
                <Badge
                  aria-label="Root duration"
                  className="gap-1.5"
                  size="chip"
                  variant="secondary"
                >
                  <Icon.history aria-hidden className="size-3.5 text-muted-foreground" />
                  {formatDuration(root.durationNano / 1_000_000)}
                </Badge>
              )}
              <Badge className="gap-1.5" size="chip" variant="secondary">
                <Icon.gitBranch aria-hidden className="size-3.5 text-muted-foreground" />
                {spans.length} {spans.length === 1 ? "span" : "spans"}
              </Badge>
            </>
          )}
          {usage?.totalTokens != null && (
            <Badge size="chip" variant="secondary">
              {formatNumber(usage.totalTokens)} tokens
            </Badge>
          )}
          {usage?.totalCost != null && (
            <Badge aria-label="Total cost" className="gap-1.5" size="chip" variant="secondary">
              <Icon.cost aria-hidden className="size-3.5 text-muted-foreground" />
              {formatCost(usage.totalCost)}
            </Badge>
          )}
          {traceStatus === "live" && (
            <Badge size="chip" variant="info">
              Live
            </Badge>
          )}
          {traceStatus === "interrupted" && (
            <Badge size="chip" variant="warning">
              Interrupted
            </Badge>
          )}
        </div>
      </div>
      <div className="flex shrink-0 items-center gap-1">
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              aria-label={detailExpanded ? "Exit full screen" : "Full screen"}
              onClick={onToggleExpanded}
              size="icon-sm"
              variant="ghost"
            >
              <Icon.aspectRatio />
            </Button>
          </TooltipTrigger>
          <TooltipContent>{detailExpanded ? "Exit full screen" : "Full screen"}</TooltipContent>
        </Tooltip>
      </div>
    </header>
  );
}
