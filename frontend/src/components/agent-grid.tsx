import { useState } from "react";

import { Link } from "@tanstack/react-router";
import { useQueryClient } from "@tanstack/react-query";
import { Activity, BarChart3, Clock, DollarSign, Zap, ClipboardCheck } from "lucide-react";

import type { AgentOut, HourlyBucket } from "@/api";
import { cn } from "@/lib/utils";
import { AgentCriteriaReviewDialog } from "@/components/agent-review/AgentCriteriaReviewDialog";
import { SpanFeedbackDialog } from "@/components/agent-review/SpanFeedbackDialog";

export type { AgentOut, HourlyBucket };

function StatPill({ icon, label, value }: { icon: React.ReactNode; label: string; value: string }) {
  return (
    <div className="inline-flex items-center gap-2 rounded-lg border border-border bg-muted/30 px-3 py-1.5">
      {icon}
      <div>
        <span className="text-xs font-medium text-muted-foreground">{label}</span>
        <span className="ml-1 text-sm font-semibold">{value}</span>
      </div>
    </div>
  );
}

function MiniBarChart({ buckets }: { buckets: HourlyBucket[] }) {
  const recent = buckets.slice(-24);
  const maxCount = Math.max(...recent.map((b) => b.spanCount ?? 0), 1);

  return (
    <div>
      <p className="mb-2 text-xs font-medium text-muted-foreground">Spans per hour (recent)</p>
      <div className="flex h-12 items-end gap-0.5">
        {recent.map((b, i) => {
          const count = b.spanCount ?? 0;
          const h = Math.max(4, (count / maxCount) * 44);
          const scoreColor =
            b.avgScore == null
              ? "bg-muted"
              : b.avgScore >= 0.7
                ? "bg-green-500"
                : b.avgScore >= 0.4
                  ? "bg-amber-500"
                  : "bg-destructive";
          return (
            <div
              className={cn("min-w-1 max-w-[18px] flex-1 rounded-t transition-all", scoreColor)}
              key={b.hour ?? i}
              style={{ height: h }}
              title={`${count} spans | score: ${b.avgScore != null ? `${(b.avgScore * 100).toFixed(0)}%` : "—"} | ${b.hour?.slice(11, 16) ?? ""}`}
            />
          );
        })}
      </div>
    </div>
  );
}

function AgentCard({ agent }: { agent: AgentOut }) {
  const { analytics } = agent;

  return (
    <div className="flex flex-1 cursor-pointer flex-col rounded-lg border border-border bg-card p-6 shadow-sm transition-all hover:shadow-md">
      <div className="mb-1 flex items-center gap-2">
        <span className="text-lg font-semibold capitalize">{agent.name}</span>
        <span className="rounded-lg border border-border bg-muted/50 px-2 py-0.5 text-xs font-medium">
          v{agent.version}
        </span>
      </div>
      {(agent.tags ?? []).length > 0 && (
        <div className="mb-3 flex flex-wrap gap-1.5">
          {(agent.tags ?? []).map((tag) => (
            <span
              className="rounded-full border border-border bg-muted/60 px-2 py-0.5 text-xs font-medium text-muted-foreground"
              key={tag}
            >
              {tag}
            </span>
          ))}
        </div>
      )}
      {(agent.tags ?? []).length === 0 && <div className="mb-3" />}

      <div className="mb-4 flex flex-wrap gap-2">
        <StatPill
          icon={<Activity className="size-3.5" />}
          label="Spans"
          value={(analytics.totalSpans ?? 0).toLocaleString()}
        />
        <StatPill
          icon={<BarChart3 className="size-3.5" />}
          label="Avg Score"
          value={analytics.avgScore != null ? `${(analytics.avgScore * 100).toFixed(1)}%` : "—"}
        />
        <StatPill
          icon={<Clock className="size-3.5" />}
          label="Avg Latency"
          value={analytics.avgLatencyMs != null ? `${analytics.avgLatencyMs.toFixed(0)} ms` : "—"}
        />
        <StatPill
          icon={<DollarSign className="size-3.5" />}
          label="Est. Cost"
          value={`$${(analytics.totalEstimatedCost ?? 0).toFixed(4)}`}
        />
        <StatPill
          icon={<Zap className="size-3.5" />}
          label="Scored"
          value={`${analytics.scoredSpans ?? 0} / ${analytics.totalSpans ?? 0}`}
        />
      </div>

      <div className="flex-1">
        {(analytics.hourly ?? []).length > 0 ? (
          <MiniBarChart buckets={analytics.hourly ?? []} />
        ) : (
          <p className="text-sm italic text-muted-foreground">
            No hourly data yet — scores will appear after evaluation.
          </p>
        )}
      </div>

      {(agent.suggestions ?? []).length > 0 && (
        <p className="mt-3 text-xs font-medium text-muted-foreground">
          {(agent.suggestions ?? []).length} suggestion
          {(agent.suggestions ?? []).length !== 1 ? "s" : ""} available
        </p>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Review flow state machine managed here so dialogs stay mounted while grid
// re-renders from polling.
// ---------------------------------------------------------------------------

type ReviewStep = "criteria" | "spans";

export function AgentGrid({ agents, projectId }: { agents: AgentOut[]; projectId?: string }) {
  const queryClient = useQueryClient();
  const [reviewingAgent, setReviewingAgent] = useState<AgentOut | null>(null);
  const [reviewStep, setReviewStep] = useState<ReviewStep>("criteria");

  function openReview(agent: AgentOut) {
    setReviewingAgent(agent);
    setReviewStep("criteria");
  }

  function handleCriteriaConfirmed() {
    setReviewStep("spans");
  }

  function handleReviewComplete() {
    setReviewingAgent(null);
    queryClient.invalidateQueries({ queryKey: ["agents"] });
  }

  return (
    <>
      <div className="grid grid-cols-1 gap-5 md:grid-cols-2">
        {agents.map((agent) => (
          <div className="relative" key={agent.slug}>
            <Link
              className="block"
              params={{ slug: agent.slug }}
              search={projectId ? { projectId } : undefined}
              to="/agents/$slug"
            >
              <AgentCard agent={agent} />
            </Link>
            {agent.readyForReview && (
              <button
                className="absolute right-4 top-4 flex items-center gap-1.5 rounded-full border border-amber-400/60 bg-amber-400/15 px-2.5 py-1 text-xs font-medium text-amber-700 transition-colors hover:bg-amber-400/30"
                onClick={() => openReview(agent)}
                title="Review agent description and criteria"
                type="button"
              >
                <ClipboardCheck className="size-3.5" />
                Review Pending
              </button>
            )}
          </div>
        ))}
      </div>

      {reviewingAgent && reviewStep === "criteria" && (
        <AgentCriteriaReviewDialog
          agent={reviewingAgent}
          onConfirm={handleCriteriaConfirmed}
          projectId={projectId}
        />
      )}

      {reviewingAgent && reviewStep === "spans" && (
        <SpanFeedbackDialog
          agent={reviewingAgent}
          onComplete={handleReviewComplete}
          projectId={projectId}
        />
      )}
    </>
  );
}
