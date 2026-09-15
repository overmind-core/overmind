import { TraceExecutionFlow } from "@/components/traces/trace-execution-flow";
import { ScoreConflictMarker, ScoreReasonChip } from "@/components/traces/trace-score-chips";
import { Icon } from "@/components/ui/icons";
import {
  Sheet,
  SheetBody,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { Spinner } from "@/components/ui/spinner";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import {
  anchorTail,
  type ExecutionStepResult,
  executionIntent,
  executionRouteFlags,
  executionStepResults,
  hasCapabilityHandoff,
  isUnboundExecution,
  scoredStepResults,
  scoreRationale,
  stepActionLabel,
  unboundReason,
  useTaskExecutionDetail,
  useTraceExecutions,
} from "@/hooks/use-task-executions";
import { useTracesList } from "@/hooks/use-traces";
import { scoreTone, TONE_CHIP, TONE_FILL } from "@/lib/colors";
import { formatCost, formatDuration, formatNumber } from "@/lib/formatters";
import { sentenceCase } from "@/lib/label-case";
import { cn, scorePct } from "@/lib/utils";
import type { TaskExecutionList } from "@/openapi";

const CHIP = "chip-label inline-flex h-5 items-center rounded-sm border px-1.5 text-xs font-medium";
const SECTION_LABEL = "text-xs font-medium text-muted-foreground";

function verdictName(verdict: ExecutionStepResult): string {
  const displayName = (verdict.display_name ?? "").trim();
  if (displayName) return displayName;
  // Without display_name the humanized label beats the raw slug only when behaviour-step shaped.
  const label = stepActionLabel(verdict);
  if (label && label !== "Step") return label;
  return verdict.evaluator || "evaluator";
}

function verdictTone(verdict: ExecutionStepResult) {
  if (verdict.score != null) return scoreTone(scorePct(verdict.score));
  if (verdict.passed === true) return "success" as const;
  if (verdict.passed === false) return "error" as const;
  return "neutral" as const;
}

function verdictLabel(verdict: ExecutionStepResult): string {
  if (verdict.score != null) return `${scorePct(verdict.score)}%`;
  if (verdict.passed === true) return "Pass";
  if (verdict.passed === false) return "Fail";
  return "—";
}

function VerdictChip({ verdict }: { verdict: ExecutionStepResult }) {
  return (
    <ScoreReasonChip
      label={verdictLabel(verdict)}
      rationale={verdict.rationale}
      tone={verdictTone(verdict)}
    />
  );
}

export function ExecutionDetailSheet({
  execution,
  capabilityName,
  capabilityNameById,
  behaviourName,
  projectId,
  onOpenTrace,
  onSelectExecution,
  onClose,
}: {
  execution: TaskExecutionList | null;
  capabilityName?: string;
  capabilityNameById: Map<string, string>;
  behaviourName?: string;
  projectId: string;
  onOpenTrace: (traceId: string) => void;
  onSelectExecution: (row: TaskExecutionList) => void;
  onClose: () => void;
}) {
  return (
    <Sheet
      modal={false}
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
      open={!!execution}
    >
      <SheetContent
        onEscapeKeyDown={onClose}
        // Picking another row switches the panel instead of dismissing it.
        onInteractOutside={(e) => e.preventDefault()}
        showOverlay={false}
        side="right"
        size="md"
      >
        {execution && (
          <ExecutionDetailBody
            behaviourName={behaviourName}
            capabilityName={capabilityName}
            capabilityNameById={capabilityNameById}
            execution={execution}
            onOpenTrace={onOpenTrace}
            onSelectExecution={onSelectExecution}
            projectId={projectId}
          />
        )}
      </SheetContent>
    </Sheet>
  );
}

function ExecutionDetailBody({
  execution,
  capabilityName,
  capabilityNameById,
  behaviourName,
  projectId,
  onOpenTrace,
  onSelectExecution,
}: {
  execution: TaskExecutionList;
  capabilityName?: string;
  capabilityNameById: Map<string, string>;
  behaviourName?: string;
  projectId: string;
  onOpenTrace: (traceId: string) => void;
  onSelectExecution: (row: TaskExecutionList) => void;
}) {
  const { data: detail } = useTaskExecutionDetail(execution.id);
  const { rows: traceExecutions } = useTraceExecutions(execution.traceId, projectId);
  const { data: tracesData, isFetched: tracesFetched } = useTracesList({
    filters: { all_spans: "false", trace_id: execution.traceId },
    pageSize: 10,
    project_id: projectId,
  });

  const unbound = isUnboundExecution(execution);
  const taskName = unbound ? unboundReason(execution) : behaviourName || execution.behaviourKey;
  const steps = executionStepResults(detail?.stepResults);
  const verdicts = scoredStepResults(steps);
  const flow = detail?.flow ?? { steps: [], terminal: { kind: "", verdict: null } };
  const intent = executionIntent(detail?.userIntent);
  const flags = executionRouteFlags(execution.routeFlags);
  const route = (detail?.observedRoute ?? {}) as {
    matched_anchors?: unknown;
    cluster_occupancy?: unknown;
  };
  const matchedAnchors = Array.isArray(route.matched_anchors)
    ? route.matched_anchors.filter((a): a is string => typeof a === "string")
    : [];
  const occupancy =
    route.cluster_occupancy && typeof route.cluster_occupancy === "object"
      ? Object.entries(route.cluster_occupancy as Record<string, unknown>).filter(
          ([, tools]) => Array.isArray(tools) && tools.length > 0
        )
      : [];
  const traces = tracesData?.results ?? [];
  const score = execution.successScore;
  const conflict = detail?.conflict ?? null;
  const skippedMembers = detail?.skippedMembers ?? [];
  const stepsByEvaluator = new Map(steps.map((s) => [s.evaluator ?? "", s]));

  return (
    <>
      <SheetHeader>
        <SheetTitle className="truncate">{taskName}</SheetTitle>
        <SheetDescription className="truncate">
          {capabilityName || "Task execution"}
        </SheetDescription>
      </SheetHeader>
      <SheetBody className="flex flex-col gap-5">
        <div className="flex flex-wrap items-center gap-1">
          <span className={cn(CHIP, unbound ? TONE_CHIP.warning : TONE_CHIP.neutral)}>
            {sentenceCase((execution.bindingSource ?? "unbound").replaceAll("_", " "))}
          </span>
          <span
            className={cn(
              CHIP,
              execution.status === "error"
                ? TONE_CHIP.error
                : execution.status === "interrupted"
                  ? TONE_CHIP.warning
                  : TONE_CHIP.success
            )}
          >
            {execution.status === "error"
              ? "Error"
              : execution.status === "interrupted"
                ? "Interrupted"
                : "Completed"}
          </span>
          {flags.map((flag) => (
            <span className={cn(CHIP, TONE_CHIP.neutral)} key={flag}>
              {sentenceCase(flag.replaceAll("_", " "))}
            </span>
          ))}
        </div>

        <div className="flex flex-col gap-1">
          <span className={SECTION_LABEL}>Score</span>
          {score == null ? (
            <span className="flex items-center gap-2 text-sm text-muted-foreground" role="status">
              {execution.scoringPending && <Spinner size="sm" />}
              {execution.scoringPending ? "Scoring" : "Not scored"}
            </span>
          ) : (
            <div className="flex flex-wrap items-center gap-2">
              <ScoreReasonChip
                className="h-6 px-2 text-sm"
                label={`${scorePct(score)}%`}
                rationale={scoreRationale(steps)}
                tone={scoreTone(scorePct(score))}
              />
              {conflict && <ScoreConflictMarker className="h-6 px-1.5" conflict={conflict} />}
            </div>
          )}
        </div>

        {intent && (
          <div className="flex flex-col gap-1">
            <div className="flex items-center gap-2">
              <span className={SECTION_LABEL}>Intent</span>
              {intent.source && (
                <span className={cn(CHIP, TONE_CHIP.neutral)}>
                  {sentenceCase(intent.source.replaceAll("_", " "))}
                </span>
              )}
            </div>
            <p className="whitespace-pre-wrap break-words text-sm">{intent.text}</p>
            {intent.current && intent.current !== intent.text && (
              <p className="text-xs text-muted-foreground">This turn: {intent.current}</p>
            )}
          </div>
        )}

        {hasCapabilityHandoff(traceExecutions) && (
          <div className="flex flex-col gap-1">
            <span className={SECTION_LABEL}>Trace flow</span>
            <TraceExecutionFlow
              capabilityNameById={capabilityNameById}
              currentId={execution.id}
              executions={traceExecutions}
              onSelect={onSelectExecution}
            />
          </div>
        )}

        {(flow.steps.length > 0 || flow.terminal.kind) && (
          <div className="flex flex-col gap-1">
            <span className={SECTION_LABEL}>Steps</span>
            <ol className="flex flex-col">
              {flow.steps.map((step, i) => (
                <li className="relative flex gap-3 pb-3" key={`${step.anchor}-${i}`}>
                  <div className="flex flex-col items-center">
                    <span
                      aria-hidden
                      className={cn(
                        "mt-1.5 size-2 shrink-0 rounded-xs",
                        step.verdict ? TONE_FILL[verdictTone(step.verdict)] : TONE_FILL.neutral
                      )}
                    />
                    <span aria-hidden className="w-px flex-1 bg-border" />
                  </div>
                  <div className="flex min-w-0 flex-1 items-center gap-2">
                    <span className="min-w-0 flex-1 truncate font-mono text-sm" title={step.anchor}>
                      {anchorTail(step.anchor)}
                    </span>
                    {step.matched && (
                      <TooltipProvider>
                        <Tooltip>
                          <TooltipTrigger asChild>
                            <Icon.success
                              aria-label="Matched contract anchor"
                              className="size-3.5 shrink-0 text-muted-foreground"
                            />
                          </TooltipTrigger>
                          <TooltipContent className="text-xs" side="left">
                            Matched contract anchor
                          </TooltipContent>
                        </Tooltip>
                      </TooltipProvider>
                    )}
                    {step.verdict && <VerdictChip verdict={step.verdict} />}
                  </div>
                </li>
              ))}
              <li className="flex gap-3">
                <span
                  aria-hidden
                  className={cn(
                    "mt-1.5 size-2 shrink-0 rounded-xs",
                    flow.terminal.verdict
                      ? TONE_FILL[verdictTone(flow.terminal.verdict)]
                      : TONE_FILL.neutral
                  )}
                />
                <div className="flex min-w-0 flex-1 items-center gap-2">
                  <span className="min-w-0 flex-1 truncate font-mono text-sm font-medium">
                    {flow.terminal.kind || "terminal"}
                  </span>
                  <span className={cn(CHIP, TONE_CHIP.neutral, "shrink-0")}>Terminal</span>
                  {flow.terminal.verdict && <VerdictChip verdict={flow.terminal.verdict} />}
                </div>
              </li>
            </ol>
          </div>
        )}

        {(verdicts.length > 0 || skippedMembers.length > 0) && (
          <div className="flex flex-col gap-2">
            <span className={SECTION_LABEL}>Evaluator verdicts</span>
            <ul className="flex flex-col gap-2">
              {verdicts.map((verdict, i) => (
                <li
                  className="flex flex-col gap-1 rounded-sm border border-border/70 px-3 py-2"
                  key={`${verdict.evaluator ?? "verdict"}-${i}`}
                >
                  <div className="flex items-center gap-2">
                    <span className="min-w-0 flex-1 truncate text-xs" title={verdict.evaluator}>
                      {verdictName(verdict)}
                    </span>
                    {verdict.role && (
                      <span className="shrink-0 text-xs text-muted-foreground">{verdict.role}</span>
                    )}
                    <VerdictChip verdict={verdict} />
                  </div>
                  {verdict.rationale && (
                    <p className="line-clamp-2 text-xs text-muted-foreground">
                      {verdict.rationale}
                    </p>
                  )}
                </li>
              ))}
              {skippedMembers.map((name) => {
                const step = stepsByEvaluator.get(name);
                const displayName = (step?.display_name ?? "").trim() || name;
                return (
                  <li
                    className="flex flex-col gap-1 rounded-sm border border-border/60 px-3 py-2 text-muted-foreground"
                    key={`skipped-${name}`}
                  >
                    <div className="flex items-center gap-2">
                      <span className="min-w-0 flex-1 truncate text-xs" title={name}>
                        {displayName}
                      </span>
                      <span className={cn(CHIP, TONE_CHIP.neutral, "shrink-0")}>Skipped</span>
                    </div>
                    <p className="text-xs">
                      {step?.outcome === "segment_not_run"
                        ? "Step segment did not run in this unit."
                        : "Not run for this unit. Retryable."}
                    </p>
                  </li>
                );
              })}
            </ul>
          </div>
        )}

        {occupancy.length > 0 && (
          <div className="flex flex-col gap-1">
            <span className={SECTION_LABEL}>Cluster occupancy</span>
            <ul className="flex flex-col gap-0.5">
              {occupancy.map(([cluster, tools]) => (
                <li className="text-sm" key={cluster}>
                  <span className="font-medium">{cluster}</span>
                  <span className="text-muted-foreground">
                    {" "}
                    {(tools as unknown[]).map(String).join(", ")}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        )}

        {matchedAnchors.length > 0 && (
          <div className="flex flex-col gap-1">
            <span className={SECTION_LABEL}>Matched anchors</span>
            <div className="flex flex-wrap items-center gap-1">
              {matchedAnchors.map((anchor) => (
                <span
                  className={cn(CHIP, TONE_CHIP.neutral, "font-mono")}
                  key={anchor}
                  title={anchor}
                >
                  {anchorTail(anchor)}
                </span>
              ))}
            </div>
          </div>
        )}

        <div className="flex flex-col gap-1">
          <span className={SECTION_LABEL}>
            {tracesFetched ? `${traces.length} trace${traces.length === 1 ? "" : "s"}` : "Traces"}
          </span>
          {!tracesFetched ? (
            <Skeleton className="h-8 w-full" />
          ) : traces.length === 0 ? (
            <span className="text-sm text-muted-foreground">
              No spans ingested for trace {execution.traceId}
            </span>
          ) : (
            <ul className="flex flex-col gap-1">
              {traces.map((trace) => (
                <li key={trace.spanId || trace.traceId}>
                  <button
                    aria-label={`Open trace ${trace.applicationName ?? trace.traceId}`}
                    className="flex w-full items-center gap-3 rounded-sm border border-border/70 bg-background px-3 py-1.5 text-left transition-colors hover:bg-accent/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                    onClick={() => onOpenTrace(trace.traceId)}
                    type="button"
                  >
                    <span className="min-w-0 flex-1 truncate font-mono text-sm">
                      {trace.applicationName ?? trace.traceGroup ?? trace.traceId.slice(0, 12)}
                    </span>
                    <span className={cn(CHIP, trace.error ? TONE_CHIP.error : TONE_CHIP.success)}>
                      {trace.error ? "Error" : "OK"}
                    </span>
                    <span className="whitespace-nowrap text-xs tabular-nums text-muted-foreground">
                      {trace.durationNs != null
                        ? formatDuration(trace.durationNs / 1_000_000)
                        : "—"}
                    </span>
                    <span className="whitespace-nowrap text-xs tabular-nums text-muted-foreground">
                      {trace.totalTokens != null ? `${formatNumber(trace.totalTokens)} tok` : "—"}
                    </span>
                    <span className="whitespace-nowrap text-xs tabular-nums text-muted-foreground">
                      {trace.totalCost != null ? formatCost(trace.totalCost) : "—"}
                    </span>
                    <Icon.chevronRight className="size-4 shrink-0 text-muted-foreground" />
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </SheetBody>
    </>
  );
}
