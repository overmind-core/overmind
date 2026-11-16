import { type MetricResult, metricResults } from "@/components/evaluations/metric-list";
import { FinetuningModelChip } from "@/components/finetuning/finetuning-model-chip";
import { useTraceData } from "@/components/traces/contexts/TraceDataContext";
import { InspectorSection as Section } from "@/components/traces/inspector-section";
import {
  AvailableToolsBlock,
  collectFieldEntries,
  groupSpanAttributes,
  JsonBlock,
  KeyValueRows,
  MetricExplanation,
  MetricScore,
  MODEL_USAGE_FIELDS,
  RETRIEVAL_FIELDS,
  type ToolDefinition,
} from "@/components/traces/SpanDetailView";
import { TraceErrorDetailsView } from "@/components/traces/trace-error-details";
import { Badge } from "@/components/ui/badge";
import { Icon } from "@/components/ui/icons";
import { type SpanRow, spanStatusLabel } from "@/hooks/use-traces";
import { formatCost, formatDuration, formatNumber } from "@/lib/formatters";
import { cn } from "@/lib/utils";
import { KIND_ICON, stepKind } from "./run-steps";

const FAIL_BELOW = 0.5;

const CONTENT_LABELS = {
  llm: ["Prompt", "Response"],
  other: ["Input", "Output"],
  retrieval: ["Query", "Results"],
  tool: ["Arguments", "Result"],
} as const;

const KIND_LABEL = {
  llm: "Model call",
  other: "Step",
  retrieval: "Retrieval",
  tool: "Tool call",
};

function failed(metric: MetricResult) {
  return (
    !!metric.error || metric.verdict === "no" || (metric.score != null && metric.score < FAIL_BELOW)
  );
}

function Check({ name, metric }: { name: string; metric: MetricResult }) {
  return (
    <details className="group border-b border-border/70 last:border-b-0">
      <summary className="flex cursor-pointer list-none items-center gap-3 px-3 py-2 text-xs hover:bg-wash-subtle">
        <Icon.chevronRight
          aria-hidden
          className="size-3 shrink-0 text-muted-foreground group-open:rotate-90"
        />
        <span className="min-w-0 flex-1 truncate text-foreground">
          {metric.expectation || name}
        </span>
        <MetricScore metric={metric} />
      </summary>
      <div className="px-3 pt-1 pb-3">
        <MetricExplanation metric={metric} />
      </div>
    </details>
  );
}

function Checks({ span }: { span: SpanRow }) {
  const entries = metricResults(span.evalMetrics);
  const failing = entries.filter(([, metric]) => failed(metric));
  const passing = entries.filter(([, metric]) => !failed(metric));
  if (!entries.length && !span.trajectory) return null;
  return (
    <Section label="Checks">
      <div className="overflow-hidden rounded-md border border-border bg-wash-subtle">
        {span.trajectory && (
          <Check
            metric={{
              expectation: "The whole run achieves the intent.",
              score: span.trajectoryScore,
            }}
            name="Trajectory"
          />
        )}
        {failing.map(([name, metric]) => (
          <Check key={name} metric={metric} name={name} />
        ))}
        {passing.map(([name, metric]) => (
          <Check key={name} metric={metric} name={name} />
        ))}
      </div>
    </Section>
  );
}

/** One step, most telling first: what broke, what was judged, then what went in and came out.
 *  Everything recorded stays reachable under the fold. */
export function RunStepDetail({ span, reason }: { span: SpanRow; reason?: string }) {
  const { projectId } = useTraceData();
  const kind = stepKind(span);
  const KindIcon = KIND_ICON[kind];
  const [inputLabel, outputLabel] = CONTENT_LABELS[kind];
  const errored = span.statusCode === 2;
  const tools = span.spanAttributes?.available_tools;
  const usage = collectFieldEntries(span.spanAttributes, MODEL_USAGE_FIELDS);
  const retrieval = collectFieldEntries(span.spanAttributes, RETRIEVAL_FIELDS);
  const { groups, rest } = groupSpanAttributes(span.spanAttributes, [
    span.inputAttribute,
    span.outputAttribute,
  ]);
  const identity: Array<[string, unknown]> = [
    ["Span ID", span.spanId],
    ["Parent span ID", span.parentSpanId ?? "—"],
    ["Scope", span.scopeName || "—"],
    ["Status", spanStatusLabel(span.statusCode)],
  ];

  return (
    <div className="flex h-full min-w-0 flex-col overflow-hidden bg-card">
      <header className="flex shrink-0 flex-col gap-1.5 border-b border-border/70 px-4 py-3">
        <div className="flex min-w-0 items-center gap-2">
          <KindIcon aria-hidden className="size-4 shrink-0 text-muted-foreground" />
          <h3 className="min-w-0 truncate text-sm text-foreground">
            {span.name || span.scopeName || "Unnamed span"}
          </h3>
          <span className="shrink-0 text-xs text-muted-foreground">{KIND_LABEL[kind]}</span>
          {errored && (
            <Badge className="ml-auto shrink-0" size="chip" variant="error">
              Failed
            </Badge>
          )}
        </div>
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs tabular-nums text-muted-foreground">
          {span.model && <FinetuningModelChip compact model={span.model} projectId={projectId} />}
          {span.durationNano > 0 && <span>{formatDuration(span.durationNano / 1_000_000)}</span>}
          {span.totalTokens != null && <span>{formatNumber(span.totalTokens)} tokens</span>}
          {span.totalCost != null && <span>{formatCost(span.totalCost)}</span>}
          {span.startTimeUnixNano > 0 && (
            <time dateTime={new Date(span.startTimeUnixNano / 1_000_000).toISOString()}>
              {new Date(span.startTimeUnixNano / 1_000_000).toLocaleString()}
            </time>
          )}
        </div>
      </header>
      <div className={cn("min-h-0 flex-1 space-y-2 overflow-auto px-4 py-3")}>
        {reason && !span.errorDetails && !span.statusMessage && (
          <p className="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-xs text-destructive">
            {reason}
          </p>
        )}
        {span.errorDetails ? (
          <TraceErrorDetailsView details={span.errorDetails} />
        ) : (
          errored &&
          span.statusMessage && (
            <p className="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-xs text-destructive">
              {span.statusMessage}
            </p>
          )
        )}
        <Checks span={span} />
        <JsonBlock title={inputLabel} value={span.inputs} />
        <JsonBlock title={outputLabel} value={span.outputs} />
        {retrieval.length > 0 && (
          <Section label="Retrieval">
            <KeyValueRows entries={retrieval} />
          </Section>
        )}
        {span.events.length > 0 && (
          <Section label={`Events (${span.events.length})`}>
            <div className="space-y-3">
              {span.events.map((event, index) => (
                <div key={index}>
                  <p className="mb-1 text-xs text-foreground">{event.name}</p>
                  <KeyValueRows entries={Object.entries(event.attributes ?? {})} />
                </div>
              ))}
            </div>
          </Section>
        )}
        {Array.isArray(tools) && tools.length > 0 && (
          <Section defaultOpen={false} label={`Tools offered (${tools.length})`}>
            <AvailableToolsBlock tools={tools as ToolDefinition[]} />
          </Section>
        )}
        {usage.length > 0 && (
          <Section defaultOpen={false} label="Model and usage">
            <KeyValueRows entries={usage} />
          </Section>
        )}
        {groups.map((group) => (
          <Section defaultOpen={false} key={group.title} label={group.title}>
            <KeyValueRows entries={group.entries} />
          </Section>
        ))}
        <Section defaultOpen={false} label="All recorded attributes">
          <KeyValueRows entries={[...identity, ...rest]} />
        </Section>
      </div>
    </div>
  );
}
