import { useMemo, useState } from "react";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { createFileRoute, Link } from "@tanstack/react-router";
import {
  Analytics as Activity,
  ArrowLeft,
  Check,
  ClipboardNote as ClipboardCheck,
  Loader as Loader2,
  Play,
  Sparkles,
  Cancel as X,
} from "pixelarticons/react";
import z from "zod";

import type { PromptVersionOut, SuggestionOut } from "@/api";
import { ResponseError } from "@/api";
import apiClient from "@/client";
import { AgentNameEditor } from "@/components/agent-detail/AgentNameEditor";
import { AgentTagsEditor } from "@/components/agent-detail/AgentTagsEditor";
import { DateRangePicker } from "@/components/agent-detail/DateRangePicker";
import { JobsTab } from "@/components/agent-detail/JobsTab";
import { ReportMetricRow } from "@/components/agent-detail/ReportCard";
import { SparklineChart, SummaryStat } from "@/components/agent-detail/SparklineChart";
import { SuggestionsTab } from "@/components/agent-detail/SuggestionsTab";
import { VersionsTab } from "@/components/agent-detail/VersionsTab";
import { AgentCriteriaCard } from "@/components/agent-review/AgentCriteriaCard";
import { AgentCriteriaReviewDialog } from "@/components/agent-review/AgentCriteriaReviewDialog";
import { SpanFeedbackDialog } from "@/components/agent-review/SpanFeedbackDialog";
import { BacktestConfigDialog, type ModelSuggestion } from "@/components/BacktestConfigDialog";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { DismissibleAlert } from "@/components/ui/dismissible-alert";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useAgentDetailQuery } from "@/hooks/use-query";
import {
  type AnalyticsRange,
  aggregateBuckets,
  aggregationForRange,
  clampBuckets,
} from "@/lib/analytics";
import { formatDate } from "@/lib/utils";

// ─── Route ───────────────────────────────────────────────────────────────────

export const Route = createFileRoute("/_auth/agents/$slug")({
  component: AgentDetailPage,
  validateSearch: z.object({
    projectId: z.string().optional(),
    tab: z.enum(["suggestions", "jobs", "versions"]).optional().default("suggestions"),
  }),
});

// ─── Main Page ───────────────────────────────────────────────────────────────

function AgentDetailPage() {
  const { slug } = Route.useParams();
  const queryClient = useQueryClient();
  const { tab, projectId: projectIdParam } = Route.useSearch();
  const navigate = Route.useNavigate();
  const setTab = (v: string) =>
    navigate({
      replace: true,
      resetScroll: false,
      search: (prev) => ({ ...prev, tab: v as "suggestions" | "jobs" | "versions" }),
    });

  const [range, setRange] = useState<AnalyticsRange>("past7d");
  const [reviewStep, setReviewStep] = useState<"criteria" | "spans" | null>(null);

  const { data, isLoading, error } = useAgentDetailQuery(slug, projectIdParam);

  // Once loaded, the detail response is the authoritative source for projectId.
  // The URL param is only used as a hint for the initial fetch.
  const projectId = data?.projectId ?? projectIdParam;

  const scoreMutation = useMutation({
    mutationFn: () =>
      apiClient.jobs
        .createPromptScoringJobApiV1JobsPromptSlugScorePost({
          projectId: projectId!,
          promptSlug: slug,
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
          projectId,
          promptSlug: slug,
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

  const updateMetadataMutation = useMutation({
    mutationFn: (req: { name?: string; tags?: string[] }) =>
      apiClient.agents
        .updateAgentMetadataApiV1AgentsPromptSlugMetadataPut({
          projectId,
          promptSlug: slug,
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

  const acceptSuggestionMutation = useMutation({
    mutationFn: (suggestionId: string) =>
      apiClient.suggestions
        .acceptSuggestionApiV1SuggestionsSuggestionIdAcceptPost({ suggestionId })
        .catch(async (error) => {
          if (error instanceof ResponseError) {
            const r = await error.response.json();
            throw new Error(r.detail ?? "Accept failed");
          }
          throw error;
        }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["agent-detail", slug] }),
  });

  const dismissSuggestionMutation = useMutation({
    mutationFn: (suggestionId: string) =>
      apiClient.suggestions
        .dismissSuggestionApiV1SuggestionsSuggestionIdDismissPost({ suggestionId })
        .catch(async (error) => {
          if (error instanceof ResponseError) {
            const r = await error.response.json();
            throw new Error(r.detail ?? "Dismiss failed");
          }
          throw error;
        }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["agent-detail", slug] }),
  });

  const rejectVersionMutation = useMutation({
    mutationFn: (version: number) =>
      apiClient.agents
        .rejectPromptVersionApiV1AgentsPromptSlugRejectVersionPost({
          projectId,
          promptSlug: slug,
          rejectVersionRequest: { version },
        })
        .catch(async (error) => {
          if (error instanceof ResponseError) {
            const r = await error.response.json();
            throw new Error(r.detail ?? "Reject failed");
          }
          throw error;
        }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["agent-detail", slug] }),
  });

  const acceptVersionMutation = useMutation({
    mutationFn: (version: number) =>
      apiClient.agents
        .acceptPromptVersionApiV1AgentsPromptSlugAcceptVersionPost({
          acceptVersionRequest: { version },
          projectId,
          promptSlug: slug,
        })
        .catch(async (error) => {
          if (error instanceof ResponseError) {
            const r = await error.response.json();
            throw new Error(r.detail ?? "Accept version failed");
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
  // Use status field from each version when available; fall back to version number comparison
  const activeVersion =
    allVersionsSorted.find((v) => v.status === "active") ??
    allVersionsSorted.find((v) => v.version === agent.activeVersion) ??
    allVersionsSorted[0];
  const pendingVersionData = allVersionsSorted.find((v) => v.status === "pending") ?? null;
  const lastEvaluated = activeVersion?.createdAt;
  const pendingVersionNumber = pendingVersionData?.version ?? null;
  const pendingSuggestion =
    pendingVersionNumber != null
      ? ((agent.suggestions ?? []).find(
          (s) => s.newPromptVersion === pendingVersionNumber && s.status === "pending"
        ) ?? null)
      : null;

  const agentForReview = {
    analytics: agent.analytics,
    name: agent.name,
    promptId: activeVersion?.promptId ?? "",
    slug: agent.slug,
    version: agent.activeVersion,
  };

  const isPeriodicReview = Boolean(
    (agent.agentDescription as Record<string, unknown> | null)?.initialReviewCompleted
  );

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
          promptId={activeVersion?.promptId ?? ""}
        />
      </div>

      {/* Review pending banner */}
      {agent.readyForReview && reviewStep === null && (
        <div className="flex items-center justify-between rounded-lg border border-amber-400/60 bg-amber-400/10 px-4 py-3">
          <div className="flex items-center gap-2 text-sm text-amber-700">
            <ClipboardCheck className="size-4 shrink-0" />
            <span>
              {isPeriodicReview
                ? "Time for a periodic review — confirm the description, criteria, and updated span scores."
                : "This agent is ready for initial review - confirm the description, criteria, and span scores to start the review process and improve the agent."}
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

      {/* New version available banner */}
      {pendingVersionData && pendingVersionNumber != null && (
        <NewVersionBanner
          isAccepting={acceptSuggestionMutation.isPending || acceptVersionMutation.isPending}
          isDismissing={dismissSuggestionMutation.isPending || rejectVersionMutation.isPending}
          onAccept={() => {
            if (pendingSuggestion) {
              acceptSuggestionMutation.mutate(pendingSuggestion.id);
            } else {
              acceptVersionMutation.mutate(pendingVersionNumber);
            }
          }}
          onDismiss={() => {
            if (pendingSuggestion) {
              // Dismissing a suggestion also marks its prompt version as rejected server-side
              dismissSuggestionMutation.mutate(pendingSuggestion.id);
            } else {
              // No linked suggestion — reject the version directly
              rejectVersionMutation.mutate(pendingVersionNumber);
            }
          }}
          pendingVersion={pendingVersionNumber}
          pendingVersionData={pendingVersionData}
          suggestion={pendingSuggestion}
        />
      )}

      {/* Version row: VERSION N + Active/Pending badges + Score/Tune/Backtest */}
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div className="flex items-center gap-2">
          <span className="text-sm font-semibold uppercase tracking-wider text-muted-foreground">
            Version {agent.activeVersion}
          </span>
          <Badge
            className="bg-emerald-500/15 text-emerald-700 dark:text-emerald-400"
            variant="secondary"
          >
            Active
          </Badge>
          {agent.pendingVersion != null && (
            <Badge
              className="border-amber-400/60 text-amber-600 dark:text-amber-400"
              variant="outline"
            >
              v{agent.pendingVersion} pending
            </Badge>
          )}
        </div>
        <div className="flex flex-wrap gap-2">
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
          {allVersionsSorted[0]?.promptId && (
            <BacktestConfigDialog
              onSuccess={() => queryClient.invalidateQueries({ queryKey: ["agent-detail", slug] })}
              promptId={allVersionsSorted[0].promptId}
              recommendations={
                agent.backtestModelSuggestions?.recommendations as ModelSuggestion[] | undefined
              }
            />
          )}
        </div>
      </div>

      <DismissibleAlert
        error={scoreMutation.isError ? scoreMutation.error : null}
        variant="warning"
      />
      <DismissibleAlert
        error={tuneMutation.isError ? tuneMutation.error : null}
        variant="warning"
      />
      <DismissibleAlert
        message="Prompt tuning has been queued. Analysis will run in the background."
        messageKey={tuneSuccessKey}
        variant="success"
      />

      {/* Featured Active Version Card */}
      {activeVersion && (
        <div className="overflow-hidden rounded-lg border border-border/60 bg-card shadow-sm">
          <div className="flex items-center justify-between border-b border-border/40 px-5 py-3">
            <div className="flex items-center gap-3">
              <span className="text-[0.72rem] font-bold uppercase tracking-widest text-foreground">
                Version {activeVersion.version}
              </span>
              <Badge
                className="bg-emerald-500/15 text-emerald-700 dark:text-emerald-400"
                variant="secondary"
              >
                Active
              </Badge>
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
                {lastEvaluated
                  ? `Last evaluated ${formatDate(lastEvaluated)}`
                  : "Not yet evaluated"}
              </p>
              <div className="space-y-3">
                <ReportMetricRow
                  label="Accuracy"
                  progress={activeVersion.avgScore != null ? activeVersion.avgScore * 100 : 0}
                  value={
                    activeVersion.avgScore != null
                      ? `${(activeVersion.avgScore * 100).toFixed(0)}%`
                      : "—"
                  }
                />
                <ReportMetricRow
                  label="Scored"
                  progress={
                    (activeVersion.totalSpans ?? 0) > 0
                      ? ((activeVersion.scoredSpans ?? 0) / (activeVersion.totalSpans ?? 1)) * 100
                      : 0
                  }
                  value={
                    (activeVersion.totalSpans ?? 0) > 0
                      ? `${(((activeVersion.scoredSpans ?? 0) / (activeVersion.totalSpans ?? 1)) * 100).toFixed(0)}%`
                      : "—"
                  }
                />
                <ReportMetricRow
                  label="Latency"
                  progress={
                    activeVersion.avgLatencyMs != null
                      ? Math.min(100, (activeVersion.avgLatencyMs / 10000) * 100)
                      : 0
                  }
                  value={
                    activeVersion.avgLatencyMs != null
                      ? `${activeVersion.avgLatencyMs.toFixed(0)} ms`
                      : "—"
                  }
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
                    <SummaryStat
                      label="Total Spans"
                      value={(analytics.totalSpans ?? 0).toLocaleString()}
                    />
                    <SummaryStat
                      label="Scored"
                      value={(analytics.scoredSpans ?? 0).toLocaleString()}
                    />
                    <SummaryStat label="Total Errors" value="0" />
                    <SummaryStat
                      label="Avg Latency"
                      value={
                        analytics.avgLatencyMs != null
                          ? `${analytics.avgLatencyMs.toFixed(0)} ms`
                          : "—"
                      }
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
          projectId={agent.projectId}
        />
      )}

      {reviewStep === "spans" && (
        <SpanFeedbackDialog
          agent={agentForReview}
          isPeriodicReview={isPeriodicReview}
          onClose={() => {
            setReviewStep(null);
            queryClient.invalidateQueries({ queryKey: ["agent-detail", slug] });
          }}
          onComplete={() => {
            setReviewStep(null);
            queryClient.invalidateQueries({ queryKey: ["agent-detail", slug] });
          }}
          projectId={agent.projectId}
          scoredSpanCount={agent.analytics.scoredSpans}
        />
      )}
    </div>
  );
}

// ─── New Version Banner + Dialog ──────────────────────────────────────────────

function NewVersionBanner({
  pendingVersion,
  pendingVersionData,
  suggestion,
  isAccepting,
  isDismissing,
  onAccept,
  onDismiss,
}: {
  pendingVersion: number;
  pendingVersionData: PromptVersionOut;
  suggestion: SuggestionOut | null;
  isAccepting: boolean;
  isDismissing: boolean;
  onAccept: () => void;
  onDismiss: () => void;
}) {
  const [dialogOpen, setDialogOpen] = useState(false);
  const scores = suggestion?.scores as Record<string, number> | null | undefined;

  return (
    <>
      <div className="flex items-center justify-between rounded-lg border border-amber-400/60 bg-amber-400/10 px-4 py-3">
        <div className="flex items-center gap-2 text-sm text-amber-700 dark:text-amber-400">
          <Sparkles className="size-4 shrink-0" />
          <span>
            A new prompt version <strong>v{pendingVersion}</strong> is available.
            {suggestion?.description ? ` ${suggestion.description}` : ""}
          </span>
        </div>
        <div className="ml-4 flex shrink-0 gap-2">
          <Button
            className="border-amber-400/60 text-amber-700 hover:bg-amber-400/20 dark:text-amber-400"
            onClick={() => setDialogOpen(true)}
            size="sm"
            variant="outline"
          >
            Review
          </Button>
          <Button disabled={isDismissing} onClick={onDismiss} size="sm" variant="ghost">
            {isDismissing ? <Loader2 className="size-3 animate-spin" /> : <X className="size-3" />}
          </Button>
        </div>
      </div>

      <Dialog onOpenChange={setDialogOpen} open={dialogOpen}>
        <DialogContent
          className="sm:max-w-lg"
          onEscapeKeyDown={() => {
            onDismiss();
            setDialogOpen(false);
          }}
          onInteractOutside={() => {
            onDismiss();
            setDialogOpen(false);
          }}
        >
          <DialogHeader>
            <DialogTitle>New Version Available — v{pendingVersion}</DialogTitle>
            {suggestion?.title && <DialogDescription>{suggestion.title}</DialogDescription>}
          </DialogHeader>

          <div className="space-y-4 py-2">
            {suggestion?.description && (
              <p className="text-sm text-muted-foreground">{suggestion.description}</p>
            )}

            {!suggestion && (
              <p className="text-sm text-muted-foreground">
                A newer prompt template (v{pendingVersion}) has been generated. Accept it to make it
                the active version.
              </p>
            )}

            {scores && (
              <div className="grid grid-cols-2 gap-3">
                {scores.avg_correctness_old != null && scores.avg_correctness_new != null && (
                  <ComparisonStat
                    improved={scores.avg_correctness_new > scores.avg_correctness_old}
                    label="Correctness"
                    newValue={`${(scores.avg_correctness_new * 100).toFixed(1)}%`}
                    oldValue={`${(scores.avg_correctness_old * 100).toFixed(1)}%`}
                  />
                )}
                {scores.avg_latency_ms_old != null && scores.avg_latency_ms_new != null && (
                  <ComparisonStat
                    improved={scores.avg_latency_ms_new < scores.avg_latency_ms_old}
                    label="Latency"
                    newValue={`${scores.avg_latency_ms_new.toFixed(0)} ms`}
                    oldValue={`${scores.avg_latency_ms_old.toFixed(0)} ms`}
                  />
                )}
                {scores.total_cost_old != null && scores.total_cost_new != null && (
                  <ComparisonStat
                    improved={scores.total_cost_new < scores.total_cost_old}
                    label="Cost"
                    newValue={`$${scores.total_cost_new.toFixed(4)}`}
                    oldValue={`$${scores.total_cost_old.toFixed(4)}`}
                  />
                )}
                {scores.spans_tested != null && (
                  <div className="rounded-md border border-border/60 p-3">
                    <p className="text-xs text-muted-foreground">Spans Tested</p>
                    <p className="text-sm font-semibold">{scores.spans_tested}</p>
                  </div>
                )}
              </div>
            )}

            {pendingVersionData.totalSpans != null && pendingVersionData.totalSpans > 0 && (
              <p className="text-xs text-muted-foreground">
                v{pendingVersion} already has {pendingVersionData.totalSpans} production span(s) —
                it will be auto-accepted once confirmed by the system.
              </p>
            )}
          </div>

          <DialogFooter>
            <Button
              disabled={isAccepting || isDismissing}
              onClick={() => {
                onDismiss();
                setDialogOpen(false);
              }}
              variant="outline"
            >
              {isDismissing ? (
                <Loader2 className="size-3 animate-spin" />
              ) : (
                <X className="size-3" />
              )}
              Dismiss
            </Button>
            <Button
              disabled={isAccepting || isDismissing}
              onClick={() => {
                onAccept();
                setDialogOpen(false);
              }}
            >
              {isAccepting ? (
                <Loader2 className="size-3 animate-spin" />
              ) : (
                <Check className="size-3" />
              )}
              Accept v{pendingVersion}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}

function ComparisonStat({
  label,
  oldValue,
  newValue,
  improved,
}: {
  label: string;
  oldValue: string;
  newValue: string;
  improved: boolean;
}) {
  return (
    <div className="rounded-md border border-border/60 p-3">
      <p className="text-xs text-muted-foreground">{label}</p>
      <div className="mt-1 flex items-center gap-2 text-sm">
        <span className="text-muted-foreground">{oldValue}</span>
        <span className="text-muted-foreground">→</span>
        <span
          className={
            improved
              ? "font-semibold text-emerald-600 dark:text-emerald-400"
              : "font-semibold text-red-600 dark:text-red-400"
          }
        >
          {newValue}
        </span>
      </div>
    </div>
  );
}
