import { useMemo } from "react";

import { getRouteApi, Link } from "@tanstack/react-router";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Icon } from "@/components/ui/icons";
import { Skeleton } from "@/components/ui/skeleton";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { formatCost, formatNumber } from "@/lib/formatters";
import { useTraceData } from "./contexts/TraceDataContext";
import { buildSpanTree } from "./lib/tree-building";

const traceRouteApi = getRouteApi("/_auth/observability/$traceId");

export function TracePanelNavigationHeader() {
  const { spans, traceStatus, usage, isLoading } = useTraceData();
  const navigate = traceRouteApi.useNavigate();
  const { traceId } = traceRouteApi.useParams();
  const { detailExpanded } = traceRouteApi.useSearch();
  const roots = useMemo(() => buildSpanTree(spans), [spans]);
  const rootName = roots[0]?.span.name?.trim() || "Trace";
  return (
    <header className="flex shrink-0 items-center gap-3 border-b border-border/70 bg-card px-3 py-3">
      <Tooltip>
        <TooltipTrigger asChild>
          <Button asChild size="icon-sm" variant="ghost">
            <Link aria-label="Close trace" resetScroll={false} search={(prev) => prev} to="..">
              <Icon.panelLeftOpen />
            </Link>
          </Button>
        </TooltipTrigger>
        <TooltipContent>Close trace</TooltipContent>
      </Tooltip>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          {isLoading ? (
            <Skeleton aria-label="Loading trace name" className="h-4 w-48 max-w-full" />
          ) : (
            <h2 className="truncate text-sm font-medium" title={rootName}>
              {rootName}
            </h2>
          )}
          {traceStatus === "live" && <Badge variant="info">Live</Badge>}
          {traceStatus === "interrupted" && <Badge variant="warning">Interrupted</Badge>}
        </div>
        <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-xs tabular-nums text-muted-foreground">
          <span className="font-mono" title={traceId}>
            {traceId.slice(0, 8)}…
          </span>
          {isLoading ? (
            <Skeleton aria-label="Loading span count" className="h-3 w-16" />
          ) : (
            <span>{spans.length} spans</span>
          )}
          {usage?.totalTokens != null && <span>{formatNumber(usage.totalTokens)} tokens</span>}
          {usage?.totalCost != null && <span>{formatCost(usage.totalCost)}</span>}
        </div>
      </div>
      <div className="flex shrink-0 items-center gap-1">
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              aria-label={detailExpanded ? "Exit full screen" : "Full screen"}
              onClick={() =>
                navigate({ search: (prev) => ({ ...prev, detailExpanded: !prev.detailExpanded }) })
              }
              size="icon-sm"
              variant="ghost"
            >
              <Icon.aspectRatio />
            </Button>
          </TooltipTrigger>
          <TooltipContent>{detailExpanded ? "Exit full screen" : "Full screen"}</TooltipContent>
        </Tooltip>
        <Tooltip>
          <TooltipTrigger asChild>
            <Button asChild size="icon-sm" variant="ghost">
              <Link aria-label="Close" resetScroll={false} search={(prev) => prev} to="..">
                <Icon.close />
              </Link>
            </Button>
          </TooltipTrigger>
          <TooltipContent>Close trace</TooltipContent>
        </Tooltip>
      </div>
    </header>
  );
}
