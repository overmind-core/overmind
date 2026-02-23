import { ArrowRight } from "lucide-react";
import { Link } from "@tanstack/react-router";

import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { RawResultAccordion } from "./RawResultAccordion";

interface ComparisonMetrics {
  old_prompt: { avg_score: number; span_count: number; total_cost: number; avg_latency_ms: number };
  new_prompt: { avg_score: number; span_count: number; total_cost: number; avg_latency_ms: number };
  improvement: {
    score_delta: number;
    score_delta_pct: number;
    cost_delta: number;
    cost_delta_pct: number;
    latency_delta_ms: number;
    latency_delta_pct: number;
  };
}

interface PromptTuningData {
  status?: string;
  reason?: string;
  error?: string;
  suggestion_id?: string;
  new_version?: number;
  scored_count?: number;
  comparison_test?: {
    spans_tested?: number;
    metrics?: ComparisonMetrics;
  };
}

function formatScore(n: number) {
  return `${(n * 100).toFixed(1)}%`;
}

function formatDelta(n: number, unit = "", plus = true) {
  const sign = n >= 0 && plus ? "+" : "";
  return `${sign}${n.toFixed(1)}${unit}`;
}

function DeltaBadge({ pct, label }: { pct: number; label?: string }) {
  const color =
    pct > 0 ? "text-green-600 bg-green-500/10" : pct < 0 ? "text-red-600 bg-red-500/10" : "text-amber-600 bg-amber-500/10";
  return (
    <span className={`rounded px-2 py-0.5 text-xs font-semibold ${color}`}>
      {label ?? formatDelta(pct, "%")}
    </span>
  );
}

interface MetricChipProps {
  label: string;
  value: string;
  delta?: string;
  deltaColor?: "green" | "red" | "amber" | "neutral";
}

function MetricChip({ label, value, delta, deltaColor = "neutral" }: MetricChipProps) {
  const deltaClass = {
    green: "text-green-600",
    red: "text-red-600",
    amber: "text-amber-600",
    neutral: "text-muted-foreground",
  }[deltaColor];

  return (
    <div className="flex flex-col gap-1 rounded-lg border border-border bg-muted/30 px-4 py-3 text-center">
      <span className="text-sm font-medium tabular-nums">{value}</span>
      {delta && <span className={`text-xs font-medium ${deltaClass}`}>{delta}</span>}
      <span className="text-xs text-muted-foreground">{label}</span>
    </div>
  );
}

interface PromptTuningResultProps {
  result: Record<string, unknown>;
  promptSlug?: string | null;
}

export function PromptTuningResult({ result, promptSlug }: PromptTuningResultProps) {
  const data = result as PromptTuningData;

  // Cancelled / error states
  const errorMsg = data.reason ?? data.error;
  if (
    errorMsg &&
    data.status !== "improved" &&
    data.status !== "no_improvement"
  ) {
    return (
      <div className="space-y-4">
        <Alert variant="warning">{errorMsg}</Alert>
        <RawResultAccordion result={result} />
      </div>
    );
  }

  const metrics = data.comparison_test?.metrics;
  const imp = metrics?.improvement;
  const isImproved = data.status === "improved";

  return (
    <div className="space-y-5">
      {/* View Suggestion button */}
      {isImproved && data.suggestion_id && promptSlug && (
        <div className="flex justify-end">
          <Button asChild size="sm" variant="outline">
            <Link
              params={{ slug: promptSlug }}
              search={{ tab: "suggestions" }}
              to="/agents/$slug"
            >
              View Suggestion
              <ArrowRight className="ml-1.5 size-3.5" />
            </Link>
          </Button>
        </div>
      )}

      {/* Progress arrow visualization */}
      {metrics ? (
        <div className="flex items-center gap-4 rounded-lg border border-border bg-muted/20 px-6 py-5">
          {/* Old score */}
          <div className="text-center">
            <div className="text-2xl font-bold tabular-nums text-muted-foreground">
              {formatScore(metrics.old_prompt.avg_score)}
            </div>
            <div className="mt-0.5 text-xs text-muted-foreground">Current Score</div>
          </div>

          {/* Arrow + delta */}
          <div className="flex flex-1 flex-col items-center gap-1">
            <div className="flex w-full items-center gap-1">
              <div className="h-0.5 flex-1 bg-border" />
              <ArrowRight className="size-4 shrink-0 text-muted-foreground" />
            </div>
            {imp && (
              <DeltaBadge
                label={isImproved ? formatDelta(imp.score_delta_pct, "%") : "No improvement"}
                pct={imp.score_delta_pct}
              />
            )}
          </div>

          {/* New score */}
          <div className="text-center">
            <div
              className={`text-2xl font-bold tabular-nums ${isImproved ? "text-green-600" : "text-muted-foreground"}`}
            >
              {formatScore(metrics.new_prompt.avg_score)}
            </div>
            <div className="mt-0.5 text-xs text-muted-foreground">
              {isImproved ? `New Score (v${data.new_version})` : "Test Score"}
            </div>
          </div>
        </div>
      ) : null}

      {/* Secondary metric chips */}
      {imp && (
        <div className="flex flex-wrap gap-3">
          {data.comparison_test?.spans_tested != null && (
            <MetricChip
              label="Spans Tested"
              value={String(data.comparison_test.spans_tested)}
            />
          )}
          <MetricChip
            delta={formatDelta(imp.cost_delta_pct, "%")}
            deltaColor={imp.cost_delta > 0 ? "red" : "green"}
            label="Cost Delta"
            value={imp.cost_delta >= 0 ? `+$${imp.cost_delta.toFixed(5)}` : `-$${Math.abs(imp.cost_delta).toFixed(5)}`}
          />
          <MetricChip
            delta={formatDelta(imp.latency_delta_pct, "%")}
            deltaColor={imp.latency_delta_ms > 0 ? "amber" : "green"}
            label="Latency Delta"
            value={`${imp.latency_delta_ms >= 0 ? "+" : ""}${imp.latency_delta_ms.toFixed(0)} ms`}
          />
        </div>
      )}

      <RawResultAccordion result={result} />
    </div>
  );
}
