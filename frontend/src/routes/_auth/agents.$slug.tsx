import { useMemo, useState } from "react";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { createFileRoute, Link } from "@tanstack/react-router";
import {
  Activity,
  ArrowLeft,
  ClipboardCheck,
  FlaskConical,
  Loader2,
  Play,
  Sparkles,
} from "lucide-react";
import z from "zod";

import { ResponseError } from "@/api";
import apiClient from "@/client";
import { AgentCriteriaCard } from "@/components/agent-review/AgentCriteriaCard";
import { AgentCriteriaReviewDialog } from "@/components/agent-review/AgentCriteriaReviewDialog";
import { SpanFeedbackDialog } from "@/components/agent-review/SpanFeedbackDialog";
import { AgentNameEditor } from "@/components/agent-detail/AgentNameEditor";
import { AgentTagsEditor } from "@/components/agent-detail/AgentTagsEditor";
import { DateRangePicker } from "@/components/agent-detail/DateRangePicker";
import { JobsTab } from "@/components/agent-detail/JobsTab";
import { ReportMetricRow } from "@/components/agent-detail/ReportCard";
import { SparklineChart, SummaryStat } from "@/components/agent-detail/SparklineChart";
import { SuggestionsTab } from "@/components/agent-detail/SuggestionsTab";
import { VersionsTab } from "@/components/agent-detail/VersionsTab";
import { Alert } from "@/components/ui/alert";
import { DismissibleAlert } from "@/components/ui/dismissible-alert";
import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useAgentDetailQuery } from "@/hooks/use-query";
import {
  type AnalyticsRange,
  aggregateBuckets,
  aggregationForRange,
  clampBuckets,
} from "@/lib/analytics";
import { cn, formatDate } from "@/lib/utils";

// ─── Route ───────────────────────────────────────────────────────────────────

export const Route = createFileRoute("/_auth/agents/$slug")({
  component: AgentDetailPage,
  validateSearch: z.object({
    tab: z.enum(["suggestions", "jobs", "versions"]).optional().default("suggestions"),
    projectId: z.string().optional(),
  }),
});

// ─── Main Page ───────────────────────────────────────────────────────────────

function AgentDetailPage() {
  const { slug } = Route.useParams();
  const queryClient = useQueryClient();
  const { tab, projectId } = Route.useSearch();
  const navigate = Route.useNavigate();
  const setTab = (v: string) =>
    navigate({
      replace: true,
      resetScroll: false,
      search: (prev) => ({ ...prev, tab: v as "suggestions" | "jobs" | "versions" }),
    });

  const [range, setRange] = useState<AnalyticsRange>("past7d");
  const [reviewStep, setReviewStep] = useState<"criteria" | "spans" | null>(null);

  const { data, isLoading, error } = useAgentDetailQuery(slug, projectId);

  const scoreMutation = useMutation({
    mutationFn: () =>
      apiClient.jobs
        .createPromptScoringJobApiV1JobsPromptSlugScorePost({
          promptSlug: slug,
          projectId,
        })
        .catch(async (error) => {
          if (error instanceof ResponseError) {
            const r = await error.response.json();
            throw new Error(r.detail ?? "Scoring trigger failed");
          }
          throw error;
        }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["agent-detail", slug] }),
  });

  const [tuneSuccessKey, setTuneSuccessKey] = useState(0);

  const tuneMutation = useMutation({
    mutationFn: () =>
      apiClient.jobs
        .createPromptTuningJobApiV1JobsPromptSlugTunePost({
          promptSlug: slug,
          projectId,
        })
        .catch(async (error) => {
          if (error instanceof ResponseError) {
            const r = await error.response.json();
            throw new Error(r.detail ?? "Tuning trigger failed");
          }
          throw error;
        }),
    onSuccess: () => {
      setTuneSuccessKey((k) => k + 1);
      queryClient.invalidateQueries({ queryKey: ["agent-detail", slug] });
    },
  });

  const backtestMutation = useMutation({
    mutationFn: (promptId: string) =>
      apiClient.jobs
        .createJobFromUserApiV1JobsPost({
          jobCreateRequest: {
            jobType: "model_backtesting",
            promptId,
          },
        })
        .catch(async (error) => {
          if (error instanceof ResponseError) {
            const r = await error.response.json();
            throw new Error(r.detail ?? "Backtesting trigger failed");
          }
          throw error;
        }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["agent-detail", slug] }),
  });

  const updateMetadataMutation = useMutation({
    mutationFn: (req: { name?: string; tags?: string[] }) =>
      apiClient.agents
        .updateAgentMetadataApiV1AgentsPromptSlugMetadataPut({
          promptSlug: slug,
          projectId,
          updateAgentMetadataRequest: req,
        })
        .catch(async (error) => {
          if (error instanceof ResponseError) {
            const r = await error.response.json();
            throw new Error(r.detail ?? "Update failed");
          }
          throw error;
        }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["agent-detail", slug] });
      queryClient.invalidateQueries({ queryKey: ["agents"] });
    },
  });

  const allVersionsSorted = useMemo(() => {
    if (!data?.versions) return [];
    return [...data.versions].sort((a, b) => b.version - a.version);
  }, [data?.versions]);

  const trendBuckets = useMemo(() => {
    const hourly = data?.analytics?.hourly ?? [];
    const clamped = clampBuckets(hourly, range);
    return aggregateBuckets(clamped, aggregationForRange(range));
  }, [data?.analytics?.hourly, range]);

  if (isLoading) {
    return (
      <div className="flex min-h-[400px] items-center justify-center">
        <Loader2 className="size-10 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className="space-y-4">
        <Alert variant="destructive">{(error as Error)?.message || "Agent not found"}</Alert>
        <Button asChild size="sm" variant="ghost">
          <Link search={(prev) => prev} to="..">
            <ArrowLeft className="size-4" />
          </Link>
        </Button>
      </div>
    );
  }

  const agent = data;
  const { analytics } = agent;
  const latestVersion = allVersionsSorted[0];
  const lastEvaluated = latestVersion?.createdAt;

  const agentForReview = {
    slug: agent.slug,
    name: agent.name,
    promptId: allVersionsSorted[0]?.promptId ?? "",
    version: agent.latestVersion,
    analytics: agent.analytics,
  };

  return (
    <div className="space-y-5 pb-12">
      {/* Title + Criteria side-by-side */}
      <div className="grid gap-6 lg:grid-cols-[1fr,340px] items-start">
        <div className="space-y-3">
          <AgentNameEditor
            initialName={agent.name}
            isSaving={updateMetadataMutation.isPending}
            onSave={(name) => updateMetadataMutation.mutate({ name })}
          />
          <AgentTagsEditor
            initialTags={agent.tags ?? []}
            isSaving={updateMetadataMutation.isPending}
            onSave={(tags) => updateMetadataMutation.mutate({ tags })}
          />
          {agent.agentDescription?.description && (
            <p className="text-sm text-muted-foreground leading-relaxed">
              {String(agent.agentDescription.description)}
            </p>
          )}
          <DismissibleAlert
            error={updateMetadataMutation.isError ? (updateMetadataMutation.error as Error) : null}
            fallback="Failed to update agent"
            variant="warning"
          />
        </div>
        <AgentCriteriaCard
          agentSlug={agent.slug}
          projectId={projectId}
          promptId={allVersionsSorted[0]?.promptId ?? ""}
        />
      </div>

      {/* Review pending banner */}
      {agent.readyForReview && reviewStep === null && (
        <div className="flex items-center justify-between rounded-lg border border-amber-400/60 bg-amber-400/10 px-4 py-3">
          <div className="flex items-center gap-2 text-sm text-amber-700">
            <ClipboardCheck className="size-4 shrink-0" />
            <span>
              This agent is ready for initial review — confirm the description, criteria, and span
              scores.
            </span>
          </div>
          <Button
            className="ml-4 shrink-0 border-amber-400/60 text-amber-700 hover:bg-amber-400/20"
            onClick={() => setReviewStep("criteria")}
            size="sm"
            variant="outline"
          >
            Start Review
          </Button>
        </div>
      )}

      <DismissibleAlert error={scoreMutation.isError ? scoreMutation.error : null} variant="warning" />
      <DismissibleAlert error={tuneMutation.isError ? tuneMutation.error : null} variant="warning" />
      <DismissibleAlert
        message="Prompt tuning has been queued. Analysis will run in the background."
        messageKey={tuneSuccessKey}
        variant="success"
      />
      <DismissibleAlert error={backtestMutation.isError ? backtestMutation.error : null} variant="warning" />

      {/* Featured Latest Version Card */}
      {latestVersion && (
        <div className="overflow-hidden rounded-lg border border-border/60 bg-card shadow-sm">
          <div className="flex items-center justify-between border-b border-border/40 px-5 py-3">
            <div className="flex items-center gap-3">
              <span className="text-[0.72rem] font-bold uppercase tracking-widest text-foreground">
                Version {latestVersion.version}
              </span>
              <span className="rounded bg-foreground px-2 py-0.5 text-[0.65rem] font-bold uppercase tracking-wider text-card">
                Latest
              </span>
            </div>
            <Button
              onClick={() => {
                const pid = projectId ?? agent.projectId;
                if (pid) {
                  navigate({
                    params: { projectId: pid },
                    search: {
                      promptSlug: agent.slug,
                    },
                    to: "/projects/$projectId/traces",
                  });
                }
              }}
              size="sm"
              variant="outline"
            >
              <Activity className="mr-1.5 size-3.5" />
              View Traces
            </Button>
          </div>

          <div className="flex flex-col md:flex-row">
            {/* Left: Report Card */}
            <div className="flex-[0_0_25%] border-b border-border/40 p-5 md:border-b-0 md:border-r">
              <p className="mb-0.5 text-[0.92rem] font-semibold text-foreground">Report Card</p>
              <p className="mb-5 text-[0.75rem] text-muted-foreground">
                {lastEvaluated ? `Last evaluated ${formatDate(lastEvaluated)}` : "Not yet evaluated"}
              </p>
              <div className="space-y-3">
                <ReportMetricRow
                  label="Accuracy"
                  progress={latestVersion.avgScore != null ? latestVersion.avgScore * 100 : 0}
                  value={latestVersion.avgScore != null ? `${(latestVersion.avgScore * 100).toFixed(0)}%` : "—"}
                />
                <ReportMetricRow
                  label="Scored"
                  progress={
                    (latestVersion.totalSpans ?? 0) > 0
                      ? ((latestVersion.scoredSpans ?? 0) / (latestVersion.totalSpans ?? 1)) * 100
                      : 0
                  }
                  value={
                    (latestVersion.totalSpans ?? 0) > 0
                      ? `${(((latestVersion.scoredSpans ?? 0) / (latestVersion.totalSpans ?? 1)) * 100).toFixed(0)}%`
                      : "—"
                  }
                />
                <ReportMetricRow
                  label="Latency"
                  progress={
                    latestVersion.avgLatencyMs != null
                      ? Math.min(100, (latestVersion.avgLatencyMs / 10000) * 100)
                      : 0
                  }
                  value={latestVersion.avgLatencyMs != null ? `${latestVersion.avgLatencyMs.toFixed(0)} ms` : "—"}
                />
              </div>
            </div>

            {/* Right: Activity / Sparklines */}
            <div className="flex flex-1 flex-col p-5">
              <div className="mb-4 flex items-center justify-between">
                <p className="text-[0.92rem] font-semibold text-foreground">Activity</p>
                <DateRangePicker onChange={setRange} value={range} />
              </div>

              {trendBuckets.length > 0 ? (
                <>
                  <div className="mb-4 flex-1">
                    <SparklineChart buckets={trendBuckets} />
                  </div>
                  <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
                    <SummaryStat label="Total Spans" value={(analytics.totalSpans ?? 0).toLocaleString()} />
                    <SummaryStat label="Scored" value={(analytics.scoredSpans ?? 0).toLocaleString()} />
                    <SummaryStat label="Total Errors" value="0" />
                    <SummaryStat
                      label="Avg Latency"
                      value={analytics.avgLatencyMs != null ? `${analytics.avgLatencyMs.toFixed(0)} ms` : "—"}
                    />
                  </div>
                </>
              ) : (
                <div className="flex flex-1 items-center justify-center py-6 text-sm text-muted-foreground">
                  No activity data yet. Traces will appear here once spans are recorded.
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Suggestions & Jobs & Versions Tabs */}
      <div className="overflow-hidden rounded-lg border border-border/60 bg-card shadow-sm">
        <Tabs onValueChange={setTab} value={tab}>
          <div className="flex items-center justify-between border-b border-border/40 px-2">
            <TabsList className="mb-0 h-11 justify-start bg-transparent p-0">
              <TabsTrigger className="px-4" value="suggestions">
                Suggestions ({agent.suggestions?.length ?? 0})
              </TabsTrigger>
              <TabsTrigger className="px-4" value="versions">
                Versions ({agent.versions?.length ?? 0})
              </TabsTrigger>
              <TabsTrigger className="px-4" value="jobs">
                Jobs ({agent.jobs?.length ?? 0})
              </TabsTrigger>
            </TabsList>
            <div className="flex items-center gap-2">
              <Button
                disabled={scoreMutation.isPending}
                onClick={() => scoreMutation.mutate()}
                size="sm"
                variant="outline"
              >
                <Play className="mr-1.5 size-3.5" />
                Score Spans
              </Button>
              <Button
                disabled={tuneMutation.isPending}
                onClick={() => tuneMutation.mutate()}
                size="sm"
                variant="outline"
              >
                <Sparkles className="mr-1.5 size-3.5" />
                Tune Prompt
              </Button>
              <Button
                disabled={backtestMutation.isPending}
                onClick={() => {
                  const promptId = allVersionsSorted[0]?.promptId;
                  if (promptId) backtestMutation.mutate(promptId);
                }}
                size="sm"
                variant="outline"
              >
                <FlaskConical className="mr-1.5 size-3.5" />
                Backtest
              </Button>
            </div>
          </div>
          <div className="p-4 md:p-5">
            <TabsContent className="mt-0" value="suggestions">
              <SuggestionsTab suggestions={agent.suggestions ?? []} />
            </TabsContent>
            <TabsContent className="mt-0" value="jobs">
              <JobsTab jobs={agent.jobs ?? []} />
            </TabsContent>
            <TabsContent className="mt-0" value="versions">
              <VersionsTab
                agentName={agent.name}
                projectId={agent.projectId}
                versions={allVersionsSorted}
              />
            </TabsContent>
          </div>
        </Tabs>
      </div>

      {/* Review Dialogs */}
      {reviewStep === "criteria" && (
        <AgentCriteriaReviewDialog
          agent={agentForReview}
          onClose={() => setReviewStep(null)}
          onConfirm={() => setReviewStep("spans")}
          projectId={projectId}
        />
      )}

      {reviewStep === "spans" && (
        <SpanFeedbackDialog
          agent={agentForReview}
          onClose={() => {
            setReviewStep(null);
            queryClient.invalidateQueries({ queryKey: ["agent-detail", slug] });
          }}
          onComplete={() => {
            setReviewStep(null);
            queryClient.invalidateQueries({ queryKey: ["agent-detail", slug] });
          }}
          projectId={projectId}
        />
      )}
    </div>
  );
}
