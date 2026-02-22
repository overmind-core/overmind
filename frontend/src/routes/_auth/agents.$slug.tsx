import { useEffect, useMemo, useRef, useState } from "react";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import {
  ArrowLeft,
  BarChart3,
  Check,
  CheckCircle,
  Clock,
  ClipboardCheck,
  FlaskConical,
  Loader2,
  Pencil,
  Play,
  Plus,
  Sparkles,
  X,
  XCircle,
} from "lucide-react";
import z from "zod";

import {
  type HourlyBucket,
  type JobOut,
  type PromptVersionOut,
  ResponseError,
  type SuggestionOut,
} from "@/api";
import apiClient from "@/client";
import { AgentCriteriaCard } from "@/components/agent-review/AgentCriteriaCard";
import { AgentCriteriaReviewDialog } from "@/components/agent-review/AgentCriteriaReviewDialog";
import { SpanFeedbackDialog } from "@/components/agent-review/SpanFeedbackDialog";
import { MiniStat } from "@/components/mini-stat";
import { SuggestionCard } from "@/components/suggestion-card";
import { Alert } from "@/components/ui/alert";
import { DismissibleAlert } from "@/components/ui/dismissible-alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useAgentDetailQuery } from "@/hooks/use-query";
import { cn, formatDate } from "@/lib/utils";

function formatShortDate(iso?: string | null): string {
  if (!iso) return "—";
  try {
    const d = new Date(iso);
    return Number.isNaN(d.getTime())
      ? "—"
      : d.toLocaleDateString(undefined, { day: "numeric", month: "short" });
  } catch {
    return "—";
  }
}

export const Route = createFileRoute("/_auth/agents/$slug")({
  component: AgentDetailPage,
  validateSearch: z.object({
    tab: z.enum(["suggestions", "jobs", "versions"]).optional().default("suggestions"),
    projectId: z.string().optional(),
  }),
});

type AnalyticsRange = "past24h" | "past7d" | "past14d" | "past1m";
type AnalyticsAggregation = "hour" | "day";

function getGradeFromScore(score: number | null): { grade: string; color: string } {
  if (score == null) return { color: "text-muted-foreground", grade: "—" };
  const pct = score * 100;
  if (pct >= 90) return { color: "text-emerald-600 dark:text-emerald-400", grade: "A" };
  if (pct >= 80) return { color: "text-green-600 dark:text-green-400", grade: "B" };
  if (pct >= 70) return { color: "text-amber-600 dark:text-amber-400", grade: "C" };
  if (pct >= 60) return { color: "text-orange-600 dark:text-orange-400", grade: "D" };
  return { color: "text-red-600 dark:text-red-400", grade: "F" };
}

function clampBuckets(buckets: HourlyBucket[], range: AnalyticsRange): HourlyBucket[] {
  const now = Date.now();
  const rangeMs =
    range === "past24h"
      ? 24 * 60 * 60 * 1000
      : range === "past7d"
        ? 7 * 24 * 60 * 60 * 1000
        : range === "past14d"
          ? 14 * 24 * 60 * 60 * 1000
          : 30 * 24 * 60 * 60 * 1000; // past1m
  const min = now - rangeMs;
  return buckets.filter((b) => {
    const ts = Date.parse(b.hour);
    return Number.isFinite(ts) ? ts >= min : true;
  });
}

function aggregateBuckets(
  buckets: HourlyBucket[],
  aggregation: AnalyticsAggregation
): HourlyBucket[] {
  if (aggregation === "hour") return buckets;
  const byDay = new Map<
    string,
    {
      key: string;
      span_count: number;
      cost: number;
      scoreWeightedSum: number;
      latencyWeightedSum: number;
      scoreWeight: number;
      latencyWeight: number;
    }
  >();

  for (const b of buckets) {
    const d = new Date(b.hour);
    const key = Number.isFinite(d.getTime())
      ? `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`
      : b.hour;
    const prev = byDay.get(key) ?? {
      cost: 0,
      key,
      latencyWeight: 0,
      latencyWeightedSum: 0,
      scoreWeight: 0,
      scoreWeightedSum: 0,
      span_count: 0,
    };

    const spanCount = b.spanCount ?? 0;
    const score = b.avgScore;
    const latency = b.avgLatencyMs;

    byDay.set(key, {
      ...prev,
      cost: prev.cost + (b.estimatedCost ?? 0),
      latencyWeight: prev.latencyWeight + (latency != null ? spanCount : 0),
      latencyWeightedSum: prev.latencyWeightedSum + (latency != null ? latency * spanCount : 0),
      scoreWeight: prev.scoreWeight + (score != null ? spanCount : 0),
      scoreWeightedSum: prev.scoreWeightedSum + (score != null ? score * spanCount : 0),
      span_count: prev.span_count + spanCount,
    });
  }

  return Array.from(byDay.values())
    .sort((a, b) => a.key.localeCompare(b.key))
    .map((g) => ({
      avgLatencyMs: g.latencyWeight > 0 ? g.latencyWeightedSum / g.latencyWeight : null,
      avgScore: g.scoreWeight > 0 ? g.scoreWeightedSum / g.scoreWeight : null,
      estimatedCost: g.cost,
      hour: g.key,
      spanCount: g.span_count,
    }));
}

/** Segmented bar for Report Card metrics (Score, Latency, Cost) */
function ReportCardMetricBar({
  label,
  value,
  valueFormatter,
  maxValue,
}: {
  label: string;
  value: number | null | undefined;
  valueFormatter: (v: number | null) => string;
  maxValue: number;
}) {
  const fillRatio = value != null && maxValue > 0 ? Math.min(1, value / maxValue) : 0;
  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between text-sm">
        <span className="font-medium text-muted-foreground">{label}</span>
        <span className="font-semibold">{valueFormatter(value ?? null)}</span>
      </div>
      <div className="h-2 w-full overflow-hidden rounded-full bg-muted">
        <div
          className="h-full rounded-full bg-foreground transition-all"
          style={{ width: `${fillRatio * 100}%` }}
        />
      </div>
    </div>
  );
}

/** Bar chart for Requests (Live) - spans over time */
function RequestsBarChart({ buckets }: { buckets: HourlyBucket[] }) {
  const series = buckets.slice(-48);
  const maxCount = Math.max(...series.map((b) => b.spanCount ?? 0), 1);
  const height = 120;

  return (
    <div className="flex h-[120px] items-end gap-1 px-1">
      {series.map((b, i) => {
        const count = b.spanCount ?? 0;
        const h = maxCount > 0 ? Math.max(4, (count / maxCount) * (height - 8)) : 4;
        return (
          <div
            className="flex-1 min-w-[3px] max-w-[12px] rounded-t bg-emerald-500 transition-all hover:bg-emerald-600"
            key={b.hour ?? i}
            style={{ height: h }}
            title={`${count} • ${b.hour}`}
          />
        );
      })}
    </div>
  );
}

/** Mini bar chart for Average Latency in summary */
function LatencyMiniChart({ buckets }: { buckets: HourlyBucket[] }) {
  const series = buckets.slice(-24);
  const vals = series.map((b) => b.avgLatencyMs).filter((v): v is number => v != null);
  const max = Math.max(...vals, 1);

  return (
    <div className="flex h-8 items-end gap-px">
      {series.map((b, i) => {
        const v = b.avgLatencyMs;
        const h = v != null ? Math.max(2, (v / max) * 24) : 2;
        return (
          <div
            className="min-w-[3px] flex-1 rounded-t bg-muted-foreground/60"
            key={b.hour ?? i}
            style={{ height: h }}
          />
        );
      })}
    </div>
  );
}

function VersionsTab({
  agentName,
  projectId,
  versions,
}: {
  agentName: string;
  projectId: string;
  versions: PromptVersionOut[];
}) {
  const [expanded, setExpanded] = useState<number | null>(versions[0]?.version ?? null);
  const navigate = useNavigate();

  if (versions.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center rounded-xl border border-dashed border-border py-16">
        <BarChart3 className="mb-3 size-12 text-muted-foreground/50" />
        <p className="text-sm italic text-muted-foreground">No prompt versions found.</p>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {versions.map((v) => {
        const isExpanded = expanded === v.version;
        const { grade, color } = getGradeFromScore(v.avgScore ?? null);
        return (
          <Card className="overflow-hidden border-border" key={v.version}>
            <CardHeader className="flex flex-row flex-wrap items-center justify-between gap-4 bg-muted/20 pb-4">
              <div className="flex flex-wrap items-center gap-3">
                <div
                  className={cn(
                    "flex size-14 items-center justify-center rounded-xl border-2 font-black text-2xl",
                    color,
                    "border-current bg-background"
                  )}
                >
                  {grade}
                </div>
                <div>
                  <div className="flex items-center gap-2">
                    <span className="font-bold capitalize">{agentName}</span>
                    <Badge variant="outline">v{v.version}</Badge>
                    <Badge variant="secondary">{v.slug}</Badge>
                  </div>
                  <p className="mt-0.5 text-xs text-muted-foreground">
                    {formatDate(v.createdAt ?? "")}
                  </p>
                </div>
              </div>
              <Button
                onClick={() => setExpanded(isExpanded ? null : v.version)}
                size="sm"
                variant="outline"
              >
                {isExpanded ? "Hide" : "Show"} template
              </Button>
            </CardHeader>
            <CardContent className="pt-4">
              <div className="mb-4 flex flex-wrap gap-4">
                <MiniStat label="Spans" value={v.totalSpans?.toLocaleString() ?? "—"} />
                <MiniStat
                  label="Scored"
                  value={`${v.scoredSpans?.toLocaleString() ?? "—"} / ${v.totalSpans?.toLocaleString() ?? "—"}`}
                />
                <MiniStat
                  label="Avg Score"
                  value={v.avgScore != null ? `${(v.avgScore * 100).toFixed(1)}%` : "—"}
                />
                <MiniStat
                  label="Avg Latency"
                  value={v.avgLatencyMs != null ? `${v.avgLatencyMs.toFixed(0)} ms` : "—"}
                />
                <MiniStat label="Hash" value={`${v.hash.slice(0, 10)}…`} />
              </div>
              <div className="mb-4 flex flex-wrap gap-2">
                <Button
                  onClick={() =>
                    navigate({
                      params: { projectId },
                      search: { agent: v.promptId },
                      to: "/projects/$projectId/traces",
                    })
                  }
                  size="sm"
                  variant="outline"
                >
                  View spans
                </Button>
                <Button
                  onClick={() =>
                    navigate({
                      params: { projectId },
                      search: { agent: v.promptId, sortBy: "judgeScore" },
                      to: "/projects/$projectId/traces",
                    })
                  }
                  size="sm"
                  variant="ghost"
                >
                  Best by judge
                </Button>
                <Button
                  onClick={() =>
                    navigate({
                      params: { projectId },
                      search: { agent: v.promptId, sortBy: "duration" },
                      to: "/projects/$projectId/traces",
                    })
                  }
                  size="sm"
                  variant="ghost"
                >
                  Slowest
                </Button>
                <Button
                  onClick={() =>
                    navigate({
                      params: { projectId },
                      search: { promptSlug: v.slug, promptVersion: v.version.toString() },
                      to: "/projects/$projectId/traces",
                    })
                  }
                  size="sm"
                  variant="ghost"
                >
                  Most expensive
                </Button>
              </div>
              {isExpanded && (
                <pre className="max-h-[260px] overflow-y-auto rounded-lg border border-border bg-muted/30 p-4 font-mono text-xs whitespace-pre-wrap">
                  {v.promptText ?? ""}
                </pre>
              )}
            </CardContent>
          </Card>
        );
      })}
    </div>
  );
}

function SuggestionsTab({ suggestions }: { suggestions: SuggestionOut[] }) {
  if (suggestions.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center rounded-xl border border-dashed border-border py-16">
        <Sparkles className="mb-3 size-12 text-muted-foreground/50" />
        <p className="text-center text-sm italic text-muted-foreground">
          No suggestions yet — tune the prompt to generate suggestions.
        </p>
      </div>
    );
  }
  return (
    <div className="space-y-4">
      {suggestions.map((e) => (
        <SuggestionCard key={e.id} suggestion={e} />
      ))}
    </div>
  );
}

const STATUS_ICON: Record<string, React.ReactNode> = {
  completed: <CheckCircle className="size-4 text-emerald-500" />,
  failed: <XCircle className="size-4 text-destructive" />,
  pending: <Clock className="size-4 text-amber-500" />,
  running: <Loader2 className="size-4 animate-spin text-blue-500" />,
};

const JOB_TYPE_LABELS: Record<string, string> = {
  prompt_tuning: "Prompt Tuning",
  scoring: "LLM Judge Scoring",
  template_extraction: "Template Extraction",
};

const getVariantByStatus = (status: string) => {
  if (status === "completed") return "success";
  if (status === "running") return "secondary";
  if (status === "failed") return "destructive";
  return "warning";
};
function JobsTab({ jobs }: { jobs: JobOut[] }) {
  if (jobs.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center rounded-xl border border-dashed border-border py-16">
        <Loader2 className="mb-3 size-12 text-muted-foreground/50" />
        <p className="max-w-sm text-center text-sm italic text-muted-foreground">
          No jobs have been run for this agent yet.
          <br />
          Use one of the available actions above to start a job.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {jobs.map((j) => (
        <JobCard job={j} key={j.jobId} />
      ))}
    </div>
  );
}

function RenderJson({ result }: { result: Record<string, unknown> | null | undefined }) {
  if (!result || Object.keys(result).length < 0) return null;
  return (
    <div className="border-t border-border bg-muted/30 px-4 py-3">
      <div className="flex flex-wrap gap-6">
        {Object.entries(result)
          .filter(([k]) => k !== "status" && k !== "raw")
          .slice(0, 6)
          .map(([k, v]) => {
            let display = "—";
            if (typeof v === "object") {
              display = JSON.stringify(v, null, 2);
            } else {
              display = String(v ?? "—");
              if (display.length > 60) display = `${display.slice(0, 57)}…`;
            }
            return (
              <div className="min-w-[80px]" key={k}>
                <span className="text-xs font-medium capitalize text-muted-foreground">
                  {k.replace(/_/g, " ")}
                </span>
                <pre
                  className="mt-0.5 max-h-[80px] overflow-y-auto break-all font-mono text-xs"
                  title={typeof display === "string" && display.length > 30 ? display : undefined}
                >
                  {display}
                </pre>
              </div>
            );
          })}
      </div>
    </div>
  );
}

function JobCard({ job: j }: { job: JobOut }) {
  return (
    <Card className="overflow-hidden border-border transition-shadow hover:shadow-sm" key={j.jobId}>
      <CardContent className="flex flex-col gap-3 p-4 md:flex-row md:items-center md:justify-between">
        <div className="flex flex-wrap items-center gap-3">
          {STATUS_ICON[j.status] ?? STATUS_ICON.pending}
          <span className="font-semibold">{JOB_TYPE_LABELS[j.jobType] ?? j.jobType}</span>
          <Badge variant={getVariantByStatus(j.status)}>
            {j.status.charAt(0).toUpperCase() + j.status.slice(1)}
          </Badge>
          <span className="text-xs text-muted-foreground">{formatDate(j.createdAt ?? "")}</span>
        </div>
      </CardContent>
      <RenderJson result={j.result} />
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Inline name editor
// ---------------------------------------------------------------------------

function AgentNameEditor({
  initialName,
  onSave,
  isSaving,
}: {
  initialName: string;
  onSave: (name: string) => void;
  isSaving: boolean;
}) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(initialName);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (editing) inputRef.current?.focus();
  }, [editing]);

  useEffect(() => {
    setValue(initialName);
  }, [initialName]);

  function handleSave() {
    const trimmed = value.trim();
    if (trimmed.length < 3) return;
    onSave(trimmed);
    setEditing(false);
  }

  function handleKeyDown(e: React.KeyboardEvent) {
    if (e.key === "Enter") handleSave();
    if (e.key === "Escape") {
      setValue(initialName);
      setEditing(false);
    }
  }

  if (editing) {
    return (
      <div className="flex items-center gap-2">
        <input
          ref={inputRef}
          className="rounded-md border border-border bg-background px-3 py-1.5 text-2xl font-bold tracking-tight focus:outline-none focus:ring-2 focus:ring-ring"
          disabled={isSaving}
          maxLength={255}
          onKeyDown={handleKeyDown}
          onChange={(e) => setValue(e.target.value)}
          value={value}
        />
        <button
          className="flex size-8 items-center justify-center rounded-md bg-emerald-500 text-white hover:bg-emerald-600 disabled:opacity-50"
          disabled={isSaving || value.trim().length < 3}
          onClick={handleSave}
          title="Save name"
          type="button"
        >
          {isSaving ? <Loader2 className="size-4 animate-spin" /> : <Check className="size-4" />}
        </button>
        <button
          className="flex size-8 items-center justify-center rounded-md border border-border hover:bg-muted"
          onClick={() => { setValue(initialName); setEditing(false); }}
          title="Cancel"
          type="button"
        >
          <X className="size-4" />
        </button>
      </div>
    );
  }

  return (
    <div className="group flex items-center gap-2">
      <h1 className="text-2xl font-bold capitalize tracking-tight">{initialName}</h1>
      <button
        className="flex size-7 items-center justify-center rounded-md text-muted-foreground opacity-0 transition-opacity hover:bg-muted hover:text-foreground group-hover:opacity-100"
        onClick={() => setEditing(true)}
        title="Edit name"
        type="button"
      >
        <Pencil className="size-3.5" />
      </button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Tags editor
// ---------------------------------------------------------------------------

function AgentTagsEditor({
  initialTags,
  onSave,
  isSaving,
}: {
  initialTags: string[];
  onSave: (tags: string[]) => void;
  isSaving: boolean;
}) {
  const [tags, setTags] = useState<string[]>(initialTags);
  const [input, setInput] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    setTags(initialTags);
  }, [initialTags]);

  function addTag() {
    const trimmed = input.trim();
    if (!trimmed || tags.includes(trimmed) || trimmed.length > 50) return;
    const newTags = [...tags, trimmed];
    setTags(newTags);
    setInput("");
    onSave(newTags);
  }

  function removeTag(tag: string) {
    const newTags = tags.filter((t) => t !== tag);
    setTags(newTags);
    onSave(newTags);
  }

  function handleKeyDown(e: React.KeyboardEvent) {
    if (e.key === "Enter" || e.key === ",") {
      e.preventDefault();
      addTag();
    }
  }

  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {tags.map((tag) => (
        <span
          className="group/tag inline-flex items-center gap-1 rounded-full border border-border bg-muted/60 px-2.5 py-0.5 text-xs font-medium text-muted-foreground"
          key={tag}
        >
          {tag}
          <button
            className="ml-0.5 rounded-full text-muted-foreground/60 hover:text-destructive disabled:opacity-50"
            disabled={isSaving}
            onClick={() => removeTag(tag)}
            title={`Remove tag "${tag}"`}
            type="button"
          >
            <X className="size-3" />
          </button>
        </span>
      ))}
      <div className="flex items-center gap-1">
        <input
          ref={inputRef}
          className="h-6 w-24 rounded-full border border-dashed border-border bg-transparent px-2.5 text-xs focus:outline-none focus:border-ring focus:w-32 transition-all"
          disabled={isSaving || tags.length >= 20}
          maxLength={50}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Add tag…"
          value={input}
        />
        {input.trim() && (
          <button
            className="flex size-5 items-center justify-center rounded-full bg-muted text-muted-foreground hover:bg-muted/80 disabled:opacity-50"
            disabled={isSaving}
            onClick={addTag}
            title="Add tag"
            type="button"
          >
            <Plus className="size-3" />
          </button>
        )}
      </div>
    </div>
  );
}

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
    const agg: AnalyticsAggregation = range === "past24h" || range === "past7d" ? "hour" : "day";
    return aggregateBuckets(clamped, agg);
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
  const lastEvaluated = allVersionsSorted[0]?.createdAt;
  const scoreMax = 1;
  const latencyMax = Math.max(analytics.avgLatencyMs ?? 0, 10000);
  const costMax = Math.max(analytics.totalEstimatedCost ?? 0, 0.1);

  // Build a minimal AgentOut-compatible object for the review dialogs
  const agentForReview = {
    slug: agent.slug,
    name: agent.name,
    promptId: allVersionsSorted[0]?.promptId ?? "",
    version: agent.latestVersion,
    analytics: agent.analytics,
  };

  const rangeLabels: Record<AnalyticsRange, string> = {
    past1m: "1M",
    past7d: "7D",
    past14d: "14D",
    past24h: "24H",
  };

  return (
    <div className="space-y-6 pb-12">
      {/* Breadcrumb */}
      <nav className="flex items-center gap-2 text-sm text-muted-foreground">
        <Link className="hover:text-foreground transition-colors" search={(prev) => prev} to="/agents">
          Agents
        </Link>
        <span>/</span>
        <span className="font-medium capitalize text-foreground">{agent.name}</span>
      </nav>

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

      {/* Version row: VERSION N + LATEST + Score/Tune/Backtest */}
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div className="flex items-center gap-2">
          <span className="text-sm font-semibold uppercase tracking-wider text-muted-foreground">
            Version {agent.latestVersion}
          </span>
          <Badge
            className="bg-emerald-500/15 text-emerald-700 dark:text-emerald-400"
            variant="secondary"
          >
            Latest
          </Badge>
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
          <Button
            disabled={backtestMutation.isPending}
            onClick={() => {
              const promptId = allVersionsSorted[0]?.promptId;
              if (promptId) backtestMutation.mutate(promptId);
            }}
            size="sm"
            variant="outline"
          >
            {backtestMutation.isPending ? (
              <Loader2 className="size-3 animate-spin" />
            ) : (
              <FlaskConical className="size-3" />
            )}
            Backtest
          </Button>
        </div>
      </div>

      <DismissibleAlert error={scoreMutation.isError ? scoreMutation.error : null} variant="warning" />
      <DismissibleAlert error={tuneMutation.isError ? tuneMutation.error : null} variant="warning" />
      <DismissibleAlert
        message="Prompt tuning has been queued. Analysis will run in the background."
        messageKey={tuneSuccessKey}
        variant="success"
      />
      <DismissibleAlert error={backtestMutation.isError ? backtestMutation.error : null} variant="warning" />

      <div className="grid gap-6 lg:grid-cols-2">
        {/* Report Card - Score, Latency, Cost (amended metrics) */}
        <Card className="overflow-hidden border-border">
          <CardHeader>
            <h2 className="text-base font-semibold">Report Card</h2>
            <p className="text-xs text-muted-foreground">
              Last evaluated {lastEvaluated ? formatDate(lastEvaluated) : "—"}
            </p>
          </CardHeader>
          <CardContent className="space-y-4">
            <ReportCardMetricBar
              label="Score"
              maxValue={scoreMax}
              value={analytics.avgScore ?? undefined}
              valueFormatter={(v) => (v != null ? `${(v * 100).toFixed(0)}%` : "—")}
            />
            <ReportCardMetricBar
              label="Latency"
              maxValue={latencyMax}
              value={analytics.avgLatencyMs ?? undefined}
              valueFormatter={(v) => (v != null ? `${Number(v).toLocaleString()} ms` : "—")}
            />
            <ReportCardMetricBar
              label="Cost"
              maxValue={costMax}
              value={analytics.totalEstimatedCost ?? undefined}
              valueFormatter={(v) => (v != null ? `$${v.toFixed(2)}` : "—")}
            />
          </CardContent>
        </Card>

        {/* Requests - bar chart + 24H/7D/14D/1M */}
        <Card className="overflow-hidden border-border">
          <CardHeader>
            <div className="flex flex-wrap items-center justify-between gap-4">
              <div>
                <h2 className="text-base font-semibold">Requests</h2>
                <p className="text-xs text-muted-foreground">
                  {trendBuckets.length > 0
                    ? `${formatShortDate(trendBuckets[0]?.hour ?? "")} - ${formatShortDate(trendBuckets[trendBuckets.length - 1]?.hour ?? "")}`
                    : "—"}
                </p>
              </div>
              <div className="flex gap-1 rounded-lg bg-muted/50 p-1">
                {(Object.keys(rangeLabels) as AnalyticsRange[]).map((r) => (
                  <button
                    className={cn(
                      "rounded px-2 py-1 text-xs font-medium transition-colors",
                      range === r
                        ? "bg-emerald-500 text-white"
                        : "text-muted-foreground hover:text-foreground"
                    )}
                    key={r}
                    onClick={() => setRange(r)}
                    type="button"
                  >
                    {rangeLabels[r]}
                  </button>
                ))}
              </div>
            </div>
          </CardHeader>
          <CardContent>
            {trendBuckets.length > 0 ? (
              <RequestsBarChart buckets={trendBuckets} />
            ) : (
              <div className="flex h-[120px] items-center justify-center rounded-lg border border-dashed border-border text-sm text-muted-foreground">
                No request data yet
              </div>
            )}

            {/* Summary metrics: Total Spans, Users, Total Errors, Average Latency */}
            <div className="mt-4 grid grid-cols-2 gap-4 sm:grid-cols-4">
              <div>
                <p className="text-xs font-medium text-muted-foreground">Total Spans</p>
                <p className="mt-0.5 text-lg font-semibold text-emerald-600">
                  {analytics.totalSpans?.toLocaleString() ?? "—"}
                </p>
              </div>
              <div>
                <p className="text-xs font-medium text-muted-foreground">Scored Spans</p>
                <p className="mt-0.5 text-lg font-semibold text-emerald-600">
                  {analytics.scoredSpans?.toLocaleString() ?? "—"}
                </p>
              </div>
              <div>
                <p className="text-xs font-medium text-muted-foreground">Total Errors</p>
                <p className="mt-0.5 text-lg font-semibold text-muted-foreground">0</p>
              </div>
              <div>
                <p className="text-xs font-medium text-muted-foreground">Average Latency</p>
                <p className="mt-0.5 text-lg font-semibold text-muted-foreground">
                  {analytics.avgLatencyMs != null
                    ? `${analytics.avgLatencyMs.toLocaleString()} ms`
                    : "—"}
                </p>
                {trendBuckets.length > 0 && (
                  <div className="mt-1">
                    <LatencyMiniChart buckets={trendBuckets} />
                  </div>
                )}
              </div>
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Tabs: Suggestions, Jobs, Versions */}
      <section>
        <Tabs onValueChange={setTab} value={tab}>
          <TabsList className="mb-4 h-11 w-full justify-start rounded-lg bg-muted/50 p-1 sm:w-auto">
            <TabsTrigger className="rounded-md px-4" value="suggestions">
              Suggestions ({agent.suggestions?.length ?? 0})
            </TabsTrigger>
            <TabsTrigger className="rounded-md px-4" value="versions">
              Versions ({agent.versions?.length ?? 0})
            </TabsTrigger>
            <TabsTrigger className="rounded-md px-4" value="jobs">
              Jobs ({agent.jobs?.length ?? 0})
            </TabsTrigger>
          </TabsList>
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
        </Tabs>
      </section>

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
