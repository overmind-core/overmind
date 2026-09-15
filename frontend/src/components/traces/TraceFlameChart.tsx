import { useMemo } from "react";

import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import type { SpanRow } from "@/hooks/use-traces";
import { cn } from "@/lib/utils";

interface TraceFlameChartProps {
  spans: SpanRow[] | undefined;
  onSpanClick?: (span: SpanRow) => void;
  selectedSpanId?: string | null;
  height?: number;
}

export function TraceFlameChart({ spans, onSpanClick, selectedSpanId }: TraceFlameChartProps) {
  const { rows, totalDuration } = useMemo(() => {
    if (!spans || spans.length === 0) {
      return { rows: [], totalDuration: 0 };
    }

    const getSpanData = (s: SpanRow) => ({
      duration: s.durationNano,
      id: s.spanId,
      name: s.name || s.scopeName || "Unnamed",
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

  const labelWidth = 250;

  return (
    <TooltipProvider>
      <div className="w-full overflow-auto rounded-md border border-border bg-card font-mono text-xs h-full">
        <div className="sticky top-0 z-10 flex border-b border-border/70 bg-background">
          <div
            className="flex shrink-0 items-center border-r border-border/70 px-3 py-2 text-xs font-semibold text-muted-foreground"
            style={{ width: labelWidth }}
          >
            Span name
          </div>
          <div className="flex flex-1 items-center justify-between px-3 py-2 text-xs text-muted-foreground">
            <span>0ms</span>
            <span>{totalDuration.toFixed(0)}ms</span>
          </div>
        </div>

        {rows.map((row, index) => {
          const leftPercent = totalDuration > 0 ? (row.startMs / totalDuration) * 100 : 0;
          const widthPercent =
            totalDuration > 0 ? Math.max((row.durationMs / totalDuration) * 100, 0.5) : 100;
          const isSelected = selectedSpanId && row.id === selectedSpanId;

          return (
            <div
              className={cn(
                "flex h-7 cursor-pointer items-center border-b border-border/70 transition-colors hover:bg-wash-raised",
                isSelected && "bg-primary/10 hover:bg-primary/15",
                !isSelected && index % 2 === 1 && "bg-wash-subtle"
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
              <Tooltip>
                <TooltipTrigger asChild>
                  <div
                    className="flex shrink-0 items-center overflow-hidden border-r border-border/70 px-3"
                    style={{ width: labelWidth }}
                  >
                    <span
                      className="truncate text-xs text-foreground"
                      style={{
                        fontWeight: row.depth === 0 ? 600 : 400,
                        paddingLeft: row.depth * 12,
                      }}
                    >
                      {row.depth > 0 && <span className="mr-1 text-muted-foreground">└</span>}
                      {row.original.name}
                    </span>
                  </div>
                </TooltipTrigger>
                <TooltipContent side="right">{row.name}</TooltipContent>
              </Tooltip>

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
