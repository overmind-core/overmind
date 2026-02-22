import { useState } from "react";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { Activity, BarChart3, Clock, DollarSign, Loader2, Play, Sparkles, Zap } from "lucide-react";

import { ResponseError } from "@/api";
import apiClient from "@/client";
import { SuggestionCard } from "@/components/suggestion-card";
import { DismissibleAlert } from "@/components/ui/dismissible-alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { Agent, HourlyBucket } from "@/types/agent";

function StatPill({ icon, label, value }: { icon: React.ReactNode; label: string; value: string }) {
  return (
    <div className="inline-flex items-center gap-2 rounded-lg border border-border bg-card px-3 py-1.5">
      {icon}
      <div>
        <span className="text-xs font-semibold text-muted-foreground">{label}</span>
        <span className="ml-1 text-sm font-bold">{value}</span>
      </div>
    </div>
  );
}

function MiniBarChart({ buckets }: { buckets: HourlyBucket[] }) {
  const recent = buckets.slice(-24);
  const maxCount = Math.max(...recent.map((b) => b.span_count), 1);

  return (
    <div>
      <p className="mb-2 text-xs font-semibold text-muted-foreground">Spans per hour (recent)</p>
      <div className="flex h-12 items-end gap-0.5">
        {recent.map((b, i) => {
          const h = Math.max(4, (b.span_count / maxCount) * 44);
          const scoreColor =
            b.avg_score == null
              ? "bg-muted"
              : b.avg_score >= 0.7
                ? "bg-green-500"
                : b.avg_score >= 0.4
                  ? "bg-amber-500"
                  : "bg-destructive";
          return (
            <div
              className={cn("min-w-1 max-w-[18px] flex-1 rounded-t transition-all", scoreColor)}
              key={b.hour ?? i}
              style={{ height: h }}
              title={`${b.span_count} spans | score: ${b.avg_score != null ? `${(b.avg_score * 100).toFixed(0)}%` : "—"} | ${b.hour?.slice(11, 16) ?? ""}`}
            />
          );
        })}
      </div>
    </div>
  );
}

export function AgentListItem({ agent }: { agent: Agent }) {
  const queryClient = useQueryClient();
  const { analytics } = agent;
  const runningJobs = agent.jobs.filter((j) => j.status === "running");
  const [tuneSuccessKey, setTuneSuccessKey] = useState(0);

  const scoreMutation = useMutation({
    mutationFn: () =>
      apiClient.jobs
        .createPromptScoringJobApiV1JobsPromptSlugScorePost({ promptSlug: agent.slug })
        .catch(async (error) => {
          if (error instanceof ResponseError) {
            const r = await error.response.json();
            throw new Error(r.detail ?? "Scoring trigger failed");
          }
          throw error;
        }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["jobs-score", agent.slug] }),
  });

  const tuneMutation = useMutation({
    mutationFn: () =>
      apiClient.jobs
        .createPromptTuningJobApiV1JobsPromptSlugTunePost({ promptSlug: agent.slug })
        .catch(async (error) => {
          if (error instanceof ResponseError) {
            const r = await error.response.json();
            throw new Error(r.detail ?? "Tuning trigger failed");
          }
          throw error;
        }),
    onSuccess: () => {
      setTuneSuccessKey((k) => k + 1);
      queryClient.invalidateQueries({ queryKey: ["jobs-tune", agent.slug] });
    },
  });

  return (
    <div className="rounded-xl border border-border bg-card p-6">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <Link
            className="text-lg font-extrabold text-amber-600 capitalize hover:underline"
            params={{ slug: agent.slug }}
            to="/agents/$slug"
          >
            {agent.name}
          </Link>
          <Badge variant="outline">v{agent.version}</Badge>
          {runningJobs.length > 0 && (
            <Badge className="gap-1" variant="secondary">
              <Loader2 className="size-3 animate-spin" />
              {runningJobs.length} job{runningJobs.length > 1 ? "s" : ""} running
            </Badge>
          )}
        </div>
        <div className="flex gap-2">
          <Button
            disabled={scoreMutation.isPending}
            onClick={() => scoreMutation.mutate()}
            size="sm"
            variant="outline"
          >
            {scoreMutation.isPending ? (
              <Loader2 className="size-3 animate-spin" />
            ) : (
              <Play className="size-3" />
            )}
            Score
          </Button>
          <Button
            disabled={tuneMutation.isPending}
            onClick={() => tuneMutation.mutate()}
            size="sm"
            variant="outline"
          >
            {tuneMutation.isPending ? (
              <Loader2 className="size-3 animate-spin" />
            ) : (
              <Sparkles className="size-3" />
            )}
            Tune
          </Button>
        </div>
      </div>

      <DismissibleAlert
        className="mb-3"
        error={scoreMutation.isError ? (scoreMutation.error as Error) : null}
        fallback="Scoring trigger failed"
        variant="warning"
      />
      <DismissibleAlert
        className="mb-3"
        error={tuneMutation.isError ? (tuneMutation.error as Error) : null}
        fallback="Tuning trigger failed"
        variant="warning"
      />
      <DismissibleAlert
        className="mb-3"
        message="Prompt tuning has been queued. Analysis will run in the background."
        messageKey={tuneSuccessKey}
        variant="success"
      />

      <div className="flex flex-col gap-4 md:flex-row">
        <div className="flex flex-1 flex-col gap-4 rounded-xl border border-border bg-amber-500/5 p-4 md:flex-[0_0_70%]">
          <div className="flex flex-wrap gap-2">
            <StatPill
              icon={<Activity className="size-3.5" />}
              label="Spans"
              value={analytics.total_spans.toLocaleString()}
            />
            <StatPill
              icon={<BarChart3 className="size-3.5" />}
              label="Avg Score"
              value={
                analytics.avg_score != null ? `${(analytics.avg_score * 100).toFixed(1)}%` : "—"
              }
            />
            <StatPill
              icon={<Clock className="size-3.5" />}
              label="Avg Latency"
              value={
                analytics.avg_latency_ms != null ? `${analytics.avg_latency_ms.toFixed(0)} ms` : "—"
              }
            />
            <StatPill
              icon={<DollarSign className="size-3.5" />}
              label="Est. Cost"
              value={`$${analytics.total_estimated_cost.toFixed(4)}`}
            />
            <StatPill
              icon={<Zap className="size-3.5" />}
              label="Scored"
              value={`${analytics.scored_spans} / ${analytics.total_spans}`}
            />
          </div>
          {analytics.hourly.length > 0 ? (
            <MiniBarChart buckets={analytics.hourly} />
          ) : (
            <p className="text-sm italic text-muted-foreground">
              No hourly data yet — scores will appear after evaluation.
            </p>
          )}
        </div>
        <div className="flex flex-col gap-2 md:flex-[0_0_30%]">
          {agent.suggestions.length === 0 && (
            <p className="py-4 text-sm italic text-muted-foreground">
              No suggestions yet — tune the prompt to generate suggestions.
            </p>
          )}
          {agent.suggestions.map((s) => (
            <SuggestionCard key={s.id} suggestion={s} />
          ))}
        </div>
      </div>
    </div>
  );
}
