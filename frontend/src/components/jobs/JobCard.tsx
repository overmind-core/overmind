import { useState } from "react";
import { CheckCircle, ChevronDown, Clock, Loader2, XCircle } from "lucide-react";

import type { JobOut } from "@/api";
import { Badge } from "@/components/ui/badge";
import { cn, formatDate } from "@/lib/utils";

const STATUS_ICON: Record<string, React.ReactNode> = {
  completed: <CheckCircle className="size-4 text-emerald-500" />,
  failed: <XCircle className="size-4 text-destructive" />,
  pending: <Clock className="size-4 text-amber-500" />,
  running: <Loader2 className="size-4 animate-spin text-blue-500" />,
};

const JOB_TYPE_LABELS: Record<string, string> = {
  agent_discovery: "Agent Discovery",
  judge_scoring: "LLM Judge Scoring",
  model_backtesting: "Model Backtesting",
  prompt_tuning: "Prompt Tuning",
  scoring: "LLM Judge Scoring",
  template_extraction: "Template Extraction",
};

function getVariantByStatus(status: string) {
  if (status === "completed") return "success";
  if (status === "running") return "secondary";
  if (status === "failed") return "destructive";
  return "warning";
}

export function JobMetricColumn({
  label,
  newVal,
  oldVal,
  format,
  invertColor = false,
}: {
  label: string;
  newVal: number | null | undefined;
  oldVal: number | null | undefined;
  format: "percent" | "ms" | "cost";
  invertColor?: boolean;
}) {
  const fmt = (v: number | null | undefined): string => {
    if (v == null) return "—";
    switch (format) {
      case "percent":
        return `${(v * 100).toFixed(1)}%`;
      case "ms":
        return `${v.toLocaleString(undefined, { maximumFractionDigits: 0 })} ms`;
      case "cost":
        return `$${v.toFixed(4)}`;
    }
  };

  const delta =
    newVal != null && oldVal != null && oldVal !== 0
      ? ((newVal - oldVal) / Math.abs(oldVal)) * 100
      : null;

  const isPositive = delta != null && delta > 0;
  const isGood = delta != null ? (invertColor ? !isPositive : isPositive) : null;

  return (
    <div className="flex flex-col gap-1">
      <span className="text-[0.68rem] font-medium uppercase tracking-wider text-muted-foreground">
        {label}
      </span>
      <div className="flex items-center gap-1.5">
        {oldVal != null && (
          <>
            <span className="text-sm text-muted-foreground">{fmt(oldVal)}</span>
            <span className="text-xs text-muted-foreground">→</span>
          </>
        )}
        <span className="text-sm font-bold text-foreground">{fmt(newVal)}</span>
        {delta != null && delta !== 0 && (
          <span
            className={cn(
              "inline-flex items-center gap-0.5 rounded-sm px-1.5 py-0.5 text-[0.68rem] font-semibold",
              isGood
                ? "bg-emerald-500/10 text-emerald-600 dark:bg-emerald-400/10 dark:text-emerald-400"
                : "bg-red-500/10 text-red-600 dark:bg-red-400/10 dark:text-red-400"
            )}
          >
            {isPositive ? "+" : ""}
            {delta.toFixed(1)}%
          </span>
        )}
      </div>
    </div>
  );
}

function ComparisonTestCard({ data }: { data: Record<string, unknown> }) {
  const metrics = data.metrics as Record<string, Record<string, number>> | undefined;
  const spansTested = data.spans_tested as number | undefined;
  const spansCreated = data.spans_created as number | undefined;

  const newPrompt = metrics?.new_prompt;
  const oldPrompt = metrics?.old_prompt;

  if (!metrics) return null;

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-end gap-6">
        <JobMetricColumn
          format="percent"
          label="Performance"
          newVal={newPrompt?.avg_score}
          oldVal={oldPrompt?.avg_score}
        />
        <JobMetricColumn
          format="ms"
          invertColor
          label="Latency"
          newVal={newPrompt?.avg_latency_ms}
          oldVal={oldPrompt?.avg_latency_ms}
        />
        <JobMetricColumn
          format="cost"
          invertColor
          label="Cost"
          newVal={newPrompt?.total_cost}
          oldVal={oldPrompt?.total_cost}
        />
        {spansTested != null && (
          <div className="flex flex-col gap-1">
            <span className="text-[0.68rem] font-medium uppercase tracking-wider text-muted-foreground">
              Spans Tested
            </span>
            <span className="text-sm font-bold text-foreground">{spansTested}</span>
          </div>
        )}
        {spansCreated != null && (
          <div className="flex flex-col gap-1">
            <span className="text-[0.68rem] font-medium uppercase tracking-wider text-muted-foreground">
              Spans Scored
            </span>
            <span className="text-sm font-bold text-foreground">{spansCreated}</span>
          </div>
        )}
      </div>
    </div>
  );
}

const VERDICT_LABELS: Record<string, string> = {
  current_is_best: "Current model is best",
  switch_recommended: "Switch recommended",
};

const CANDIDATE_KEYS: { key: string; label: string }[] = [
  { key: "best_overall", label: "Best Overall" },
  { key: "top_performer", label: "Top Performer" },
  { key: "fastest", label: "Fastest" },
  { key: "cheapest", label: "Cheapest" },
];

function ModelCard({
  label,
  model,
  currentModel,
}: {
  label: string;
  model: Record<string, unknown>;
  currentModel?: Record<string, unknown>;
}) {
  return (
    <div className="rounded border border-border bg-card p-3 space-y-2">
      <div className="flex items-center justify-between">
        <span className="text-[0.68rem] font-semibold uppercase tracking-widest text-muted-foreground">
          {label}
        </span>
        <span className="text-sm font-bold text-foreground">
          {String(model.model ?? model.name ?? "—")}
        </span>
      </div>
      {model.reason && (
        <p className="text-xs text-muted-foreground leading-relaxed">{String(model.reason)}</p>
      )}
      <div className="flex flex-wrap gap-4">
        <JobMetricColumn
          format="percent"
          label="Avg Score"
          newVal={model.avg_eval_score as number | undefined}
          oldVal={currentModel?.avg_eval_score as number | undefined}
        />
        <JobMetricColumn
          format="ms"
          invertColor
          label="Avg Latency"
          newVal={model.avg_latency_ms as number | undefined}
          oldVal={currentModel?.avg_latency_ms as number | undefined}
        />
        <JobMetricColumn
          format="cost"
          invertColor
          label="Avg Cost"
          newVal={model.avg_cost_per_request as number | undefined}
          oldVal={currentModel?.avg_cost_per_request as number | undefined}
        />
      </div>
    </div>
  );
}

export function BacktestRecommendations({ data }: { data: Record<string, unknown> }) {
  const summary = data.summary as string | undefined;
  const verdict = data.verdict as string | undefined;
  const currentModel = data.current_model as Record<string, unknown> | undefined;

  const candidates = CANDIDATE_KEYS.map(({ key, label }) => ({
    data: data[key] as Record<string, unknown> | undefined,
    label,
  })).filter((c) => c.data && typeof c.data === "object");

  return (
    <div className="space-y-3">
      {summary && <p className="text-sm text-foreground leading-relaxed">{summary}</p>}
      {verdict && (
        <div className="flex items-center gap-2">
          <span className="text-[0.68rem] font-medium uppercase tracking-wider text-muted-foreground">
            Verdict
          </span>
          <Badge variant={verdict === "current_is_best" ? "success" : "warning"}>
            {VERDICT_LABELS[verdict] ?? verdict.replace(/_/g, " ")}
          </Badge>
        </div>
      )}
      {currentModel && (
        <ModelCard currentModel={undefined} label="Current Model" model={currentModel} />
      )}
      {candidates.length > 0 && (
        <div className="grid gap-2 sm:grid-cols-2">
          {candidates.map((c) => (
            <ModelCard currentModel={currentModel} key={c.label} label={c.label} model={c.data!} />
          ))}
        </div>
      )}
    </div>
  );
}

const COMPLEX_KEYS = new Set(["status", "raw", "comparison_test", "recommendations"]);

function RenderJson({ result }: { result: Record<string, unknown> | null | undefined }) {
  if (!result || Object.keys(result).length < 1) return null;

  const comparisonTest = result.comparison_test as Record<string, unknown> | undefined;
  const hasComparison =
    comparisonTest && typeof comparisonTest === "object" && comparisonTest.metrics;

  const recommendations = result.recommendations as Record<string, unknown> | undefined;
  const hasRecommendations =
    recommendations && typeof recommendations === "object" && recommendations.summary;

  const simpleEntries = Object.entries(result).filter(([k]) => !COMPLEX_KEYS.has(k));

  return (
    <div className="border-t border-border bg-muted/30 px-4 py-3 space-y-3">
      {simpleEntries.length > 0 && (
        <div className="flex flex-wrap gap-6">
          {simpleEntries.slice(0, 8).map(([k, v]) => {
            let display: string;
            if (v == null) {
              display = "—";
            } else if (Array.isArray(v)) {
              display = String(v.length);
            } else if (typeof v === "object") {
              display = JSON.stringify(v);
              if (display.length > 40) display = `${display.slice(0, 37)}…`;
            } else {
              display = String(v);
              if (display.length > 60) display = `${display.slice(0, 57)}…`;
            }
            return (
              <div className="min-w-[80px]" key={k}>
                <span className="text-[0.68rem] font-medium uppercase tracking-wider text-muted-foreground">
                  {k.replace(/_/g, " ")}
                </span>
                <p className="mt-0.5 text-sm font-semibold text-foreground">{display}</p>
              </div>
            );
          })}
        </div>
      )}

      {hasRecommendations && (
        <div className="rounded border border-border bg-card p-3">
          <p className="mb-2 text-[0.72rem] font-semibold uppercase tracking-widest text-muted-foreground">
            Recommendations
          </p>
          <BacktestRecommendations data={recommendations} />
        </div>
      )}

      {hasComparison && (
        <div className="rounded border border-border bg-card p-3">
          <p className="mb-2 text-[0.72rem] font-semibold uppercase tracking-widest text-muted-foreground">
            Comparison Test
          </p>
          <ComparisonTestCard data={comparisonTest} />
        </div>
      )}
    </div>
  );
}

export function JobCard({ job: j }: { job: JobOut }) {
  const [open, setOpen] = useState(false);

  return (
    <div className="overflow-hidden border border-border bg-card">
      <button
        className="flex w-full items-center gap-3 p-4 text-left transition-colors hover:bg-muted/30"
        onClick={() => setOpen(!open)}
        type="button"
      >
        {STATUS_ICON[j.status] ?? STATUS_ICON.pending}
        <span className="flex-1 font-semibold">{JOB_TYPE_LABELS[j.jobType] ?? j.jobType}</span>
        <Badge variant={getVariantByStatus(j.status)}>
          {j.status.charAt(0).toUpperCase() + j.status.slice(1)}
        </Badge>
        <span className="text-xs text-muted-foreground">{formatDate(j.createdAt ?? "")}</span>
        <ChevronDown
          className={cn("size-4 text-muted-foreground transition-transform", open && "rotate-180")}
          strokeWidth={1.5}
        />
      </button>
      {open && <RenderJson result={j.result} />}
    </div>
  );
}
