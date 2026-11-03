import { useEffect, useMemo, useRef } from "react";

import { DetailErrorState } from "@/components/route-error";
import { TraceDataProvider, useTraceData } from "@/components/traces/contexts/TraceDataContext";
import {
  TraceSelectionProvider,
  useTraceSelection,
} from "@/components/traces/contexts/TraceSelectionContext";
import { buildSpanTree } from "@/components/traces/lib/tree-building";
import { TraceLoadingState } from "@/components/traces/TraceLoadingState";
import { Button } from "@/components/ui/button";
import { Icon } from "@/components/ui/icons";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useTraceIssues } from "@/hooks/use-issues";
import { useIsMobile } from "@/hooks/use-mobile";
import { type SpanRow, useTraceDetail } from "@/hooks/use-traces";
import { formatCost, formatDuration, formatNumber } from "@/lib/formatters";
import { cn } from "@/lib/utils";
import type { TraceIssue } from "@/openapi";
import { formatRate, SEVERITY_RULE, SEVERITY_TEXT, stateLabel } from "./issue-parts";
import { RunStepDetail } from "./run-step-detail";
import { RunSteps } from "./run-steps";

type Verdict = { label: string; tone: string; rule: string };

const VERDICTS: Record<string, Verdict> = {
  completed: { label: "Completed", rule: "border-t-border", tone: "text-foreground" },
  failed: { label: "Failed", rule: "border-t-destructive", tone: "text-destructive" },
  interrupted: { label: "Interrupted", rule: "border-t-warning", tone: "text-warning" },
  passed: { label: "Passed", rule: "border-t-success", tone: "text-success" },
  running: { label: "Running", rule: "border-t-info", tone: "text-info" },
};

function verdictOf(spans: SpanRow[], status: string | undefined, issues: TraceIssue[]): Verdict {
  if (status === "live") return VERDICTS.running;
  if (status === "interrupted") return VERDICTS.interrupted;
  const root = buildSpanTree(spans)[0]?.span;
  if (
    spans.some((span) => span.statusCode === 2) ||
    issues.some((issue) => issue.cause !== "data") ||
    root?.trajectory?.passed === false
  ) {
    return VERDICTS.failed;
  }
  const score = root?.trajectoryScore ?? root?.evalScore;
  return score != null ? VERDICTS.passed : VERDICTS.completed;
}

function Measure({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="text-sm tabular-nums text-foreground">{value}</dd>
    </div>
  );
}

function RunHeader({
  issues,
  detailExpanded,
  onClose,
  onToggleExpanded,
  onOpenIssue,
  onTrain,
}: {
  issues: TraceIssue[];
  detailExpanded: boolean;
  onClose: () => void;
  onToggleExpanded: () => void;
  onOpenIssue: (id: string) => void;
  onTrain: (capability: string) => void;
}) {
  const { spans, traceId, traceStatus, usage, isLoading } = useTraceData();
  const root = useMemo(() => buildSpanTree(spans)[0]?.span, [spans]);
  const verdict = verdictOf(spans, traceStatus, issues);
  const score = root?.trajectoryScore ?? root?.evalScore;
  const repeating = issues.filter((issue) => issue.repeating);
  const trainable = repeating.find((issue) => issue.cause === "model" && issue.capability);
  const lead = repeating[0];

  return (
    <header
      className={cn(
        "shrink-0 border-t-2 border-b border-b-border/70 bg-card",
        isLoading ? "border-t-border" : lead ? SEVERITY_RULE[lead.severity] : verdict.rule
      )}
    >
      <div className="flex items-start gap-3 px-4 pt-3 pb-3">
        <Tooltip>
          <TooltipTrigger asChild>
            <Button aria-label="Close trace" onClick={onClose} size="icon-sm" variant="ghost">
              <Icon.close />
            </Button>
          </TooltipTrigger>
          <TooltipContent>Close trace</TooltipContent>
        </Tooltip>
        <div className="flex min-w-0 flex-1 flex-col gap-0.5">
          <h2 className="truncate text-lg text-foreground">
            {isLoading ? "Loading run" : root?.name || "Run"}
            {root?.capabilityName && (
              <span className="text-muted-foreground"> in {root.capabilityName}</span>
            )}
          </h2>
          <p className="truncate font-mono text-xs text-muted-foreground" title={traceId}>
            {traceId}
          </p>
        </div>
        {!isLoading && (
          <dl className="hidden shrink-0 items-start gap-6 md:flex">
            <div className="flex flex-col">
              <dt className="text-xs text-muted-foreground">Result</dt>
              <dd className={cn("text-sm", verdict.tone)}>{verdict.label}</dd>
            </div>
            <Measure label="Score" value={score != null ? score.toFixed(2) : "Not scored"} />
            <Measure
              label="Duration"
              value={
                root && root.durationNano > 0 ? formatDuration(root.durationNano / 1_000_000) : "—"
              }
            />
            <Measure
              label="Tokens"
              value={usage?.totalTokens != null ? formatNumber(usage.totalTokens) : "—"}
            />
            <Measure label="Cost" value={formatCost(usage?.totalCost)} />
          </dl>
        )}
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
      {repeating.length > 0 ? (
        <div className="flex items-center gap-4 border-t border-border/70 bg-wash-subtle px-4 py-2">
          <ul className="flex min-w-0 flex-1 flex-col">
            {repeating.slice(0, 3).map((issue) => (
              <li key={issue.id}>
                <button
                  className="flex w-full min-w-0 items-baseline gap-3 rounded-sm py-1 text-left text-sm hover:underline focus-visible:outline-2 focus-visible:outline-ring"
                  onClick={() => onOpenIssue(issue.id)}
                  type="button"
                >
                  <span className={cn("w-14 shrink-0 tabular-nums", SEVERITY_TEXT[issue.severity])}>
                    {formatRate(issue.rate)}
                  </span>
                  <span className="min-w-0 truncate text-foreground">{issue.title}</span>
                  <span className="shrink-0 text-xs text-muted-foreground">
                    {stateLabel(issue.state)}, {formatNumber(issue.traces)} traces in 7 days
                  </span>
                </button>
              </li>
            ))}
          </ul>
          {trainable && (
            <Button onClick={() => onTrain(trainable.capability as string)}>
              <Icon.training />
              Train a model
            </Button>
          )}
        </div>
      ) : (
        issues.length > 0 && (
          <p className="border-t border-border/70 bg-wash-subtle px-4 py-2 text-xs text-muted-foreground">
            This failure has not repeated.
          </p>
        )
      )}
    </header>
  );
}

function RunBody({ issues }: { issues: TraceIssue[] }) {
  const { spans, isLoading, traceId } = useTraceData();
  const { selectedSpanId, setSelectedSpanId } = useTraceSelection();
  const isMobile = useIsMobile();
  const reasons = useMemo(
    () => new Map(issues.map((issue) => [issue.spanId, issue.reason])),
    [issues]
  );

  // A failed run opens on what failed, once per trace, so a later click isn't overridden.
  const landed = useRef<string | null>(null);
  useEffect(() => {
    if (landed.current === traceId || !spans.length) return;
    const ids = new Set(spans.map((span) => span.spanId));
    const firstIssue = issues.find((issue) => ids.has(issue.spanId));
    const errored = spans
      .filter((span) => span.statusCode === 2 && span.parentSpanId)
      .sort((a, b) => a.startTimeUnixNano - b.startTimeUnixNano)[0];
    const target = errored?.spanId ?? firstIssue?.spanId;
    if (target) {
      landed.current = traceId;
      setSelectedSpanId(target);
    }
  }, [issues, setSelectedSpanId, spans, traceId]);

  if (isLoading) return <TraceLoadingState />;
  if (!spans.length) {
    return (
      <p className="p-6 text-center text-sm text-muted-foreground">
        This trace has no spans, or they haven't finished processing.
      </p>
    );
  }
  const selected = spans.find((span) => span.spanId === selectedSpanId);
  return (
    <div
      className={cn(
        "grid min-h-0 flex-1",
        isMobile ? "grid-rows-[35%_minmax(0,1fr)]" : "grid-cols-[minmax(20rem,5fr)_minmax(0,7fr)]"
      )}
    >
      <div className="min-h-0 min-w-0 border-border/70 bg-wash-subtle md:border-r">
        <RunSteps reasons={reasons} />
      </div>
      <div className="min-h-0 min-w-0">
        {selected ? (
          <RunStepDetail
            key={selected.spanId}
            reason={reasons.get(selected.spanId)}
            span={selected}
          />
        ) : (
          <p className="p-6 text-center text-sm text-muted-foreground">
            Select a step to inspect it.
          </p>
        )}
      </div>
    </div>
  );
}

/** One run, read from the top down: did it fail, does the failure repeat, and what to do. */
export function TraceRun({
  traceId,
  projectId,
  detailExpanded,
  onClose,
  onToggleExpanded,
  onOpenIssue,
  onTrain,
}: {
  traceId: string;
  projectId: string;
  detailExpanded: boolean;
  onClose: () => void;
  onToggleExpanded: () => void;
  onOpenIssue: (id: string) => void;
  onTrain: (capability: string) => void;
}) {
  const { data, isLoading, error } = useTraceDetail(traceId, projectId);
  const issues = useTraceIssues(projectId, traceId).data?.issues ?? [];
  const spans = (data?.transformedSpans ?? []) as SpanRow[];

  if (error && !data) {
    return (
      <div className="p-6">
        <DetailErrorState
          action={
            <Button onClick={onClose} size="sm" variant="secondary">
              Back
            </Button>
          }
          error={error}
          fallback="Couldn't load this trace."
        />
      </div>
    );
  }

  return (
    <TraceDataProvider
      value={{
        isLoading,
        projectId,
        spans,
        traceId,
        traceStatus: data?.traceStatus,
        usage: data?.usage,
      }}
    >
      <TraceSelectionProvider>
        <div className="flex h-full min-h-0 flex-col">
          <RunHeader
            detailExpanded={detailExpanded}
            issues={issues}
            onClose={onClose}
            onOpenIssue={onOpenIssue}
            onToggleExpanded={onToggleExpanded}
            onTrain={onTrain}
          />
          <RunBody issues={issues} />
        </div>
      </TraceSelectionProvider>
    </TraceDataProvider>
  );
}
