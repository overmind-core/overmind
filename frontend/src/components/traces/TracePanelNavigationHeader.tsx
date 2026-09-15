import { useMemo } from "react";

import { getRouteApi, Link } from "@tanstack/react-router";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Icon } from "@/components/ui/icons";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { TONE_CHIP } from "@/lib/colors";
import { formatCost, formatNumber } from "@/lib/formatters";
import { useTraceData } from "./contexts/TraceDataContext";
import { useTraceSelection } from "./contexts/TraceSelectionContext";
import { buildSpanTree, collectAllNodeIds } from "./lib/tree-building";

const traceRouteApi = getRouteApi("/_auth/observability/$traceId");

export function TracePanelNavigationHeader() {
  const { spans, traceStatus, usage } = useTraceData();
  const { collapsedNodes, expandAll, collapseAll, viewMode, setViewMode } = useTraceSelection();
  const navigate = traceRouteApi.useNavigate();
  const { traceId } = traceRouteApi.useParams();
  const { detailExpanded } = traceRouteApi.useSearch();

  const roots = useMemo(() => buildSpanTree(spans), [spans]);
  const rootName = roots[0]?.span.name?.trim() || null;
  const allNodeIds = useMemo(() => collectAllNodeIds(roots), [roots]);
  const isEverythingCollapsed = roots.length > 0 && roots.every((r) => collapsedNodes.has(r.id));

  return (
    // `pr-16` keeps the trailing badges clear of the Sheet's absolutely
    // positioned close button (right-9, size-7 ⇒ occupies 36px–64px from the edge).
    <div className="flex shrink-0 items-center gap-2 border-b border-border/70 py-1.5 pr-16 pl-3">
      <TooltipProvider>
        <div className="flex items-center gap-1">
          <Tooltip>
            <TooltipTrigger asChild>
              <Button asChild className="shrink-0" size="icon-sm" variant="secondary">
                <Link aria-label="Close trace" resetScroll={false} search={(prev) => prev} to="..">
                  <Icon.panelLeftOpen />
                </Link>
              </Button>
            </TooltipTrigger>
            <TooltipContent side="bottom">Close trace</TooltipContent>
          </Tooltip>

          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                aria-label={detailExpanded ? "Exit full screen" : "Full screen"}
                className="shrink-0 cursor-pointer"
                onClick={() =>
                  navigate({
                    search: (prev) => ({ ...prev, detailExpanded: !prev.detailExpanded }),
                  })
                }
                size="icon-sm"
                variant="secondary"
              >
                <Icon.aspectRatio />
              </Button>
            </TooltipTrigger>
            <TooltipContent side="bottom">
              {detailExpanded ? "Exit full screen" : "Full screen"}
            </TooltipContent>
          </Tooltip>

          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                aria-label={isEverythingCollapsed ? "Expand all" : "Collapse all"}
                className="shrink-0"
                onClick={() => (isEverythingCollapsed ? expandAll() : collapseAll(allNodeIds))}
                size="icon-sm"
                variant="secondary"
              >
                <Icon.listBox />
              </Button>
            </TooltipTrigger>
            <TooltipContent side="bottom">
              {isEverythingCollapsed ? "Expand all" : "Collapse all"}
            </TooltipContent>
          </Tooltip>

          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                aria-label="Toggle timeline view"
                className="shrink-0"
                onClick={() => setViewMode(viewMode === "timeline" ? "tree" : "timeline")}
                size="icon-sm"
                variant={viewMode === "timeline" ? "default" : "outline"}
              >
                <Icon.job />
              </Button>
            </TooltipTrigger>
            <TooltipContent side="bottom">Toggle timeline view</TooltipContent>
          </Tooltip>
        </div>
      </TooltipProvider>

      <div className="flex min-w-0 flex-1 items-center gap-2 pl-1">
        {rootName ? (
          <span className="truncate text-sm font-medium text-foreground" title={rootName}>
            {rootName}
          </span>
        ) : null}
        <span className="shrink-0 font-mono text-xs text-muted-foreground" title={traceId}>
          {traceId.slice(0, 8)}
        </span>
        {traceStatus === "live" && (
          <span
            className={`chip-label inline-flex h-6 shrink-0 items-center rounded-sm border px-2 text-xs font-medium ${TONE_CHIP.info}`}
          >
            Live
          </span>
        )}
        {traceStatus === "interrupted" && (
          <span
            className={`chip-label inline-flex h-6 shrink-0 items-center rounded-sm border px-2 text-xs font-medium ${TONE_CHIP.warning}`}
          >
            Interrupted
          </span>
        )}
      </div>

      {spans.length > 0 && (
        <div className="flex min-w-0 shrink-0 flex-wrap items-center justify-end gap-1.5">
          <Badge className="h-6 gap-1 px-1.5 font-mono tabular-nums" variant="neutral">
            <Icon.list className="size-3" />
            {spans.length} span{spans.length === 1 ? "" : "s"}
          </Badge>
          {usage?.totalTokens != null && (
            <Badge
              className="h-6 gap-1 px-1.5 font-mono tabular-nums"
              title={`${usage.totalTokens} tokens across all spans`}
              variant="neutral"
            >
              <Icon.hash className="size-3" />
              {formatNumber(usage.totalTokens)} tokens
            </Badge>
          )}
          {usage?.totalCost != null && (
            <Badge
              className="h-6 gap-1 px-1.5 font-mono tabular-nums"
              title={`$${usage.totalCost.toFixed(6)} across all spans`}
              variant="neutral"
            >
              <Icon.cost className="size-3" />
              {formatCost(usage.totalCost)}
            </Badge>
          )}
        </div>
      )}
    </div>
  );
}
