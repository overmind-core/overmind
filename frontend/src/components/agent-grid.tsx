import { useState } from "react";

import { Link } from "@tanstack/react-router";
import { useQueryClient } from "@tanstack/react-query";
import { Activity, ClipboardCheck, Clock, DollarSign, Target } from "lucide-react";

import { cn } from "@/lib/utils";
import type { AgentOut, HourlyBucket } from "@/api";
import { Badge } from "@/components/ui/badge";
import { AgentCriteriaReviewDialog } from "@/components/agent-review/AgentCriteriaReviewDialog";
import { SpanFeedbackDialog } from "@/components/agent-review/SpanFeedbackDialog";

export type { AgentOut, HourlyBucket };

// ─── Helpers ─────────────────────────────────────────────────────────────────

function hasAnyData(analytics: AgentOut["analytics"]): boolean {
  return (
    analytics.avgScore != null ||
    (analytics.totalSpans ?? 0) > 0 ||
    analytics.avgLatencyMs != null ||
    (analytics.totalEstimatedCost ?? 0) > 0
  );
}

// ─── Metric Row ─────────────────────────────────────────────────────────────

function MetricRow({
  icon,
  label,
  value,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
}) {
  return (
    <div className="flex items-center gap-2.5 py-1.5">
      <span className="flex size-6 shrink-0 items-center justify-center rounded-full bg-muted text-muted-foreground">
        {icon}
      </span>
      <span className="flex-1 text-xs text-muted-foreground">{label}</span>
      <span className="text-sm font-semibold text-foreground">{value}</span>
    </div>
  );
}

// ─── Agent Card ──────────────────────────────────────────────────────────────

function AgentCard({ agent }: { agent: AgentOut }) {
  const { analytics } = agent;
  const populated = hasAnyData(analytics);

  const accuracy =
    analytics.avgScore != null ? `${(analytics.avgScore * 100).toFixed(0)}%` : "—";
  const scored =
    (analytics.totalSpans ?? 0) > 0
      ? `${analytics.scoredSpans ?? 0} / ${analytics.totalSpans ?? 0}`
      : "—";
  const latency =
    analytics.avgLatencyMs != null ? `${analytics.avgLatencyMs.toFixed(0)} ms` : "—";
  const cost = `$${(analytics.totalEstimatedCost ?? 0).toFixed(4)}`;

  return (
    <div
      className={cn(
        "flex flex-1 cursor-pointer flex-col rounded-lg border border-border bg-card px-5 pb-4 pt-4 transition-all hover:border-[var(--accent-warm)]",
        !populated && "opacity-60",
      )}
    >
      <div className="mb-3 flex items-center justify-between gap-2">
        <h3 className="text-base font-semibold capitalize text-foreground">{agent.name}</h3>
        <Badge className="shrink-0 bg-muted font-medium text-foreground" variant="secondary">
          v{agent.version}
        </Badge>
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

      <div className="divide-y divide-border/60">
        <MetricRow
          icon={<Target className="size-3.5" strokeWidth={1.5} />}
          label="Accuracy"
          value={accuracy}
        />
        <MetricRow
          icon={<Activity className="size-3.5" strokeWidth={1.5} />}
          label="Spans scored"
          value={scored}
        />
        <MetricRow
          icon={<Clock className="size-3.5" strokeWidth={1.5} />}
          label="Avg latency"
          value={latency}
        />
        <MetricRow
          icon={<DollarSign className="size-3.5" strokeWidth={1.5} />}
          label="Est. cost"
          value={cost}
        />
      </div>
    </div>
  );
}

// ─── Review flow ─────────────────────────────────────────────────────────────
// State machine managed here so dialogs stay mounted while grid re-renders.

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
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
        {agents.map((agent) => (
          <div className="relative flex" key={agent.slug}>
            <Link
              className="flex flex-1"
              params={{ slug: agent.slug }}
              search={projectId ? { projectId } : undefined}
              to="/agents/$slug"
            >
              <AgentCard agent={agent} />
            </Link>
            {agent.readyForReview && (
              <button
                className="absolute right-3 top-3 flex items-center gap-1.5 rounded-md border border-amber-400/60 bg-amber-400/15 px-2.5 py-1 text-xs font-medium text-amber-700 transition-colors hover:bg-amber-400/30 dark:text-amber-400"
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
          onClose={() => setReviewingAgent(null)}
          onConfirm={handleCriteriaConfirmed}
          projectId={projectId}
        />
      )}

      {reviewingAgent && reviewStep === "spans" && (
        <SpanFeedbackDialog
          agent={reviewingAgent}
          onClose={() => setReviewingAgent(null)}
          onComplete={handleReviewComplete}
          projectId={projectId}
        />
      )}
    </>
  );
}
