import { useState } from "react";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ChevronDown, ChevronUp, MessageText as MessageSquare, ThumbsDown, ThumbsUp } from "pixelarticons/react";

import type { SuggestionOut } from "@/api";
import apiClient from "@/client";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import type { SuggestionItem } from "@/types/agent";

export type { SuggestionItem };

interface SuggestionScores {
  avg_correctness_new?: number;
  avg_correctness_old?: number;
  spans_tested?: number;
  spans_scored?: number;
  total_cost_new?: number;
  total_cost_old?: number;
  avg_latency_ms_new?: number;
  avg_latency_ms_old?: number;
}

function DeltaBadge({ pct, lowerIsBetter = false }: { pct: number; lowerIsBetter?: boolean }) {
  if (pct === 0) return null;
  const positive = lowerIsBetter ? pct < 0 : pct > 0;
  const sign = pct > 0 ? "+" : "";
  return (
    <span
      className={cn(
        "ml-1.5 rounded px-1 py-0.5 text-[10px] font-semibold",
        positive ? "bg-green-500/15 text-green-600" : "bg-red-500/15 text-red-500"
      )}
    >
      {sign}
      {pct.toFixed(1)}%
    </span>
  );
}

function MetricCell({
  label,
  valueNew,
  valueOld,
  delta,
  lowerIsBetter,
}: {
  label: string;
  valueNew: string;
  valueOld?: string;
  delta?: number;
  lowerIsBetter?: boolean;
}) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
        {label}
      </span>
      <div className="flex items-center gap-1.5">
        {valueOld != null && (
          <>
            <span className="text-sm text-muted-foreground line-through decoration-muted-foreground/50">
              {valueOld}
            </span>
            <span className="text-muted-foreground/50 text-xs">→</span>
          </>
        )}
        <span className="text-sm font-semibold">{valueNew}</span>
        {delta != null && <DeltaBadge lowerIsBetter={lowerIsBetter} pct={delta} />}
      </div>
    </div>
  );
}

export function SuggestionCard({ suggestion }: { suggestion: SuggestionOut }) {
  const queryClient = useQueryClient();
  const [feedbackExpanded, setFeedbackExpanded] = useState(false);
  const [feedbackText, setFeedbackText] = useState(suggestion.feedback ?? "");
  const [expanded, setExpanded] = useState<string | null>(null);

  const feedbackMutation = useMutation({
    mutationFn: ({ vote, feedback }: { vote: number; feedback?: string }) =>
      apiClient.suggestions.addSuggestionFeedbackApiV1SuggestionsSuggestionIdFeedbackPost({
        suggestionFeedbackRequest: { feedback, vote },
        suggestionId: suggestion.id,
      }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["agents"] }),
  });

  const handleVote = (vote: 1 | -1) => {
    feedbackMutation.mutate({
      feedback: feedbackText.trim() || undefined,
      vote,
    });
    setFeedbackExpanded(false);
  };

  const currentVote = suggestion.vote ?? 0;
  const scores = suggestion.scores as SuggestionScores | undefined;

  const correctnessNew = scores?.avg_correctness_new;
  const correctnessOld = scores?.avg_correctness_old;
  const latencyNew = scores?.avg_latency_ms_new;
  const latencyOld = scores?.avg_latency_ms_old;
  const costNew = scores?.total_cost_new;
  const costOld = scores?.total_cost_old;
  const spansTested = scores?.spans_tested;
  const spansScored = scores?.spans_scored;

  const pctDelta = (next: number, prev: number) => (prev !== 0 ? ((next - prev) / prev) * 100 : 0);

  const improvementPct =
    correctnessNew != null && correctnessOld != null
      ? pctDelta(correctnessNew, correctnessOld)
      : undefined;
  const latencyDeltaPct =
    latencyNew != null && latencyOld != null ? pctDelta(latencyNew, latencyOld) : undefined;
  const costDeltaPct = costNew != null && costOld != null ? pctDelta(costNew, costOld) : undefined;

  const hasMetrics = correctnessNew != null || latencyNew != null || costNew != null;

  return (
    <button
      className={cn(
        "border border-border bg-card p-4 transition-colors hover:border-[var(--accent-warm)] text-left w-full",
        "focus:outline-none focus:ring-2 focus:ring-amber-500/30"
      )}
      onClick={() => setExpanded(expanded ? null : suggestion.id)}
      onKeyDown={(e) => e.key === "Enter" && setExpanded(expanded ? null : suggestion.id)}
      type="button"
    >
      {/* Header row */}
      <div className="flex items-center justify-between">
        <span className="text-base font-semibold capitalize">{suggestion.title}</span>
        <div className="flex items-center gap-1">
          {suggestion.status === "pending" && <Badge variant="secondary">New</Badge>}
          <Button
            className={cn(currentVote === 1 && "text-amber-600")}
            disabled={feedbackMutation.isPending}
            onClick={(e) => {
              e.stopPropagation();
              handleVote(1);
            }}
            size="icon-sm"
            variant="ghost"
          >
            <ThumbsUp className="size-3.5" />
          </Button>
          <Button
            className={cn(currentVote === -1 && "text-destructive")}
            disabled={feedbackMutation.isPending}
            onClick={(e) => {
              e.stopPropagation();
              handleVote(-1);
            }}
            size="icon-sm"
            variant="ghost"
          >
            <ThumbsDown className="size-3.5" />
          </Button>
        </div>
      </div>

      {/* Description */}
      <p className="mt-1 text-sm text-muted-foreground">{suggestion.description}</p>

      {hasMetrics && (
        <div className="mt-3 flex flex-wrap gap-x-6 gap-y-2">
          {correctnessNew != null && (
            <MetricCell
              delta={improvementPct}
              label="Correctness"
              valueNew={`${(correctnessNew * 100).toFixed(1)}%`}
              valueOld={
                correctnessOld != null ? `${(correctnessOld * 100).toFixed(1)}%` : undefined
              }
            />
          )}
          {latencyNew != null && (
            <MetricCell
              delta={latencyDeltaPct}
              label="Latency"
              lowerIsBetter
              valueNew={`${Math.round(latencyNew).toLocaleString()} ms`}
              valueOld={
                latencyOld != null ? `${Math.round(latencyOld).toLocaleString()} ms` : undefined
              }
            />
          )}
          {costNew != null && (
            <MetricCell
              delta={costDeltaPct}
              label="Cost"
              lowerIsBetter
              valueNew={`$${costNew.toFixed(4)}`}
              valueOld={costOld != null ? `$${costOld.toFixed(4)}` : undefined}
            />
          )}
          {spansTested != null && (
            <MetricCell label="Spans Tested" valueNew={String(spansTested)} />
          )}
          {spansScored != null && (
            <MetricCell label="Spans Scored" valueNew={String(spansScored)} />
          )}
        </div>
      )}

      {/* Feedback + expanded prompt */}
      <div className="mt-2">
        <Button
          className="h-auto p-0 text-xs text-muted-foreground hover:text-foreground"
          onClick={(e) => {
            e.stopPropagation();
            setFeedbackExpanded(!feedbackExpanded);
          }}
          size="sm"
          variant="ghost"
        >
          <MessageSquare className="mr-1 size-3" />
          {feedbackExpanded ? "Hide" : "Add"} feedback
          {feedbackExpanded ? (
            <ChevronUp className="ml-1 size-3" />
          ) : (
            <ChevronDown className="ml-1 size-3" />
          )}
        </Button>
        {feedbackExpanded && (
          <div className="mt-2 flex gap-2">
            <Input
              className="flex-1 text-sm"
              onChange={(e) => setFeedbackText(e.target.value)}
              onClick={(e) => e.stopPropagation()}
              placeholder="Optional: explain why..."
              value={feedbackText}
            />
            <Button
              disabled={feedbackMutation.isPending || !feedbackText.trim()}
              onClick={(e) => {
                e.stopPropagation();
                feedbackText.trim() && handleVote((currentVote === -1 ? -1 : 1) as 1 | -1);
              }}
              size="sm"
              variant="outline"
            >
              {feedbackMutation.isPending ? "..." : "Save"}
            </Button>
          </div>
        )}
        {suggestion.feedback && (
          <p className="mt-1 text-xs italic text-muted-foreground">
            Your feedback: {suggestion.feedback}
          </p>
        )}

        {expanded === suggestion.id && suggestion.newPromptText && (
          <div className="mt-4 space-y-2">
            <p className="text-sm font-semibold">New Prompt Text</p>
            <pre className="max-h-[300px] overflow-y-auto rounded-lg border border-green-500/30 bg-green-500/10 p-3 font-mono text-xs leading-relaxed whitespace-pre-wrap wrap-break-word">
              {suggestion.newPromptText}
            </pre>
          </div>
        )}
      </div>
    </button>
  );
}
