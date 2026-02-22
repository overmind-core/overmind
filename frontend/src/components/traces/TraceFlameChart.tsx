import { useMemo } from "react";

import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";
import type { SpanRow } from "@/hooks/use-traces";

interface TraceFlameChartProps {
  spans: SpanRow[] | undefined;
  onSpanClick?: (span: SpanRow) => void;
  selectedSpanId?: string | null;
  height?: number;
}

/**
 * Waterfall timeline chart for trace spans.
 * Each span gets its own row, positioned by start time.
 */
export function TraceFlameChart({
  spans,
  onSpanClick,
  selectedSpanId,
  height = 400,
}: TraceFlameChartProps) {
  const { rows, totalDuration } = useMemo(() => {
    if (!spans || spans.length === 0) {
      return { rows: [], totalDuration: 0 };
    }

    const getSpanData = (s: SpanRow) => ({
      duration: s.durationNano,
      id: s.spanId,
      name: s.scopeName || s.name || "Unnamed",
      original: s,
      parentId: s.parentSpanId,
      start: s.startTimeUnixNano,
      status: s.statusCode,
    });

    const allSpanData = spans.map(getSpanData);
    const minStartTime = Math.min(...allSpanData.map((s) => s.start));
    const maxEndTime = Math.max(...allSpanData.map((s) => s.start + s.duration));
    const totalDur = (maxEndTime - minStartTime) / 1_000_000;

    const spanMap = new Map(allSpanData.map((s) => [s.id, s]));

    const getDepth = (spanData: (typeof allSpanData)[0]) => {
      let depth = 0;
      let current = spanData;
      const seen = new Set<string>();
      while (current.parentId && spanMap.has(current.parentId) && !seen.has(current.parentId)) {
        seen.add(current.parentId);
        depth++;
        current = spanMap.get(current.parentId)!;
      }
      return depth;
    };

    allSpanData.sort((a, b) => a.start - b.start);

    const rows = allSpanData.map((spanData) => {
      const startMs = (spanData.start - minStartTime) / 1_000_000;
      const durationMs = Math.max(spanData.duration / 1_000_000, 1);
      const depth = getDepth(spanData);
      const isError = spanData.status === 2;

      return {
        depth,
        durationMs,
        id: spanData.id,
        isError,
        name: spanData.name,
        original: spanData.original,
        startMs,
      };
    });

    return { rows, totalDuration: totalDur };
  }, [spans]);

  if (rows.length === 0) {
    return (
      <div className="flex items-center justify-center p-8 text-center text-sm text-muted-foreground">
        No spans to display
      </div>
    );
  }

  const rowHeight = 28;
  const labelWidth = 250;

  return (
    <TooltipProvider>
      <div
        className="w-full overflow-auto rounded-lg border border-border bg-card font-mono text-xs"
        style={{ maxHeight: height }}
      >
        {/* Header with time scale */}
        <div className="sticky top-0 z-10 flex border-b border-border bg-background">
          <div
            className="flex shrink-0 items-center border-r border-border px-3 py-2 text-[0.7rem] font-semibold uppercase text-muted-foreground"
            style={{ width: labelWidth }}
          >
            Span Name
          </div>
          <div className="flex flex-1 items-center justify-between px-3 py-2 text-[0.7rem] text-muted-foreground">
            <span>0ms</span>
            <span>{totalDuration.toFixed(0)}ms</span>
          </div>
        </div>

        {/* Span rows */}
        {rows.map((row, index) => {
          const leftPercent = totalDuration > 0 ? (row.startMs / totalDuration) * 100 : 0;
          const widthPercent =
            totalDuration > 0 ? Math.max((row.durationMs / totalDuration) * 100, 0.5) : 100;
          const isSelected = selectedSpanId && row.id === selectedSpanId;

          return (
            <div
              className={cn(
                "flex h-7 cursor-pointer items-center border-b border-border/60 transition-colors hover:bg-muted/50",
                isSelected && "bg-primary/10 hover:bg-primary/15",
                !isSelected && index % 2 === 1 && "bg-muted/30"
              )}
              key={row.id}
              onClick={() => onSpanClick?.(row.original)}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  onSpanClick?.(row.original);
                }
              }}
              role="button"
              tabIndex={0}
            >
              {/* Span name with depth indentation */}
              <Tooltip>
                <TooltipTrigger asChild>
                  <div
                    className="flex shrink-0 items-center overflow-hidden border-r border-border/60 px-3"
                    style={{ width: labelWidth }}
                  >
                    <span
                      className="truncate text-[0.7rem] text-foreground"
                      style={{
                        fontWeight: row.depth === 0 ? 600 : 400,
                        paddingLeft: row.depth * 12,
                      }}
                    >
                      {row.depth > 0 && <span className="mr-1 text-muted-foreground">└</span>}
                      {row.name}
                    </span>
                  </div>
                </TooltipTrigger>
                <TooltipContent side="right">{row.name}</TooltipContent>
              </Tooltip>

              {/* Timeline bar area */}
              <div className="relative flex flex-1 items-center bg-transparent px-1">
                <Tooltip>
                  <TooltipTrigger asChild>
                    <div
                      className={cn(
                        "absolute h-4 rounded-sm border transition-all hover:brightness-110",
                        row.isError
                          ? "border-destructive/80 bg-destructive"
                          : row.depth === 0
                            ? "border-primary/80 bg-primary"
                            : "border-primary/50 bg-primary/60"
                      )}
                      style={{
                        left: `${leftPercent}%`,
                        minWidth: 4,
                        width: `${widthPercent}%`,
                      }}
                    />
                  </TooltipTrigger>
                  <TooltipContent>
                    <pre className="whitespace-pre-wrap text-left text-xs">
                      {`${row.name}
Start: ${row.startMs.toFixed(2)}ms
Duration: ${row.durationMs.toFixed(2)}ms`}
                    </pre>
                  </TooltipContent>
                </Tooltip>
              </div>
            </div>
          );
        })}
      </div>
    </TooltipProvider>
  );
}
