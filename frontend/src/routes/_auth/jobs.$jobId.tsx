import { useQuery } from "@tanstack/react-query";
import { createFileRoute, Link } from "@tanstack/react-router";
import { ArrowLeft, Cancel as XCircle, Check as CheckCircle, Clock, Loader as Loader2, WarningDiamond as AlertTriangle } from "pixelarticons/react";

import apiClient from "@/client";
import { BacktestRecommendations } from "@/components/jobs/JobCard";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { SheetWrapper } from "@/components/sheet-wrapper";

export const Route = createFileRoute("/_auth/jobs/$jobId")({
  component: () => (
    <SheetWrapper>
      <JobDetailPage />
    </SheetWrapper>
  ),
});

const STATUS_CONFIG: Record<
  string,
  {
    variant: "default" | "secondary" | "destructive" | "success" | "warning";
    icon: React.ReactNode;
    label: string;
  }
> = {
  cancelled: { icon: <XCircle className="size-3.5" />, label: "Cancelled", variant: "default" },
  completed: { icon: <CheckCircle className="size-3.5" />, label: "Completed", variant: "success" },
  failed: { icon: <XCircle className="size-3.5" />, label: "Failed", variant: "destructive" },
  pending: { icon: <Clock className="size-3.5" />, label: "Pending", variant: "warning" },
  running: {
    icon: <Loader2 className="size-3.5 animate-spin" />,
    label: "Running",
    variant: "secondary",
  },
  skipped: { icon: <AlertTriangle className="size-3.5" />, label: "Skipped", variant: "default" },
};

const JOB_TYPE_LABELS: Record<string, string> = {
  agent_discovery: "Agent Discovery",
  judge_scoring: "LLM Judge Scoring",
  model_backtesting: "Model Backtesting",
  prompt_tuning: "Prompt Tuning",
  scoring: "LLM Judge Scoring",
  template_extraction: "Template Extraction",
};

function formatDate(iso?: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString(undefined, {
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    month: "short",
    second: "2-digit",
    year: "numeric",
  });
}

function humanSlug(slug?: string | null): string {
  if (!slug) return "—";
  return slug.replace(/-/g, " ").replace(/_/g, " ");
}

function JobResultContent({ result }: { result: Record<string, unknown> }) {
  const recommendations = result.recommendations as Record<string, unknown> | undefined;
  const hasRecommendations =
    recommendations && typeof recommendations === "object" && recommendations.summary;

  const complexKeys = new Set(["status", "raw", "comparison_test", "recommendations"]);
  const simpleEntries = Object.entries(result).filter(([k]) => !complexKeys.has(k));

  return (
    <div className="space-y-4">
      {simpleEntries.length > 0 && (
        <div className="flex flex-wrap gap-6">
          {simpleEntries.map(([k, v]) => {
            let display: string;
            if (v == null) {
              display = "—";
            } else if (typeof v === "object") {
              display = JSON.stringify(v);
              if (display.length > 60) display = `${display.slice(0, 57)}…`;
            } else {
              display = String(v);
            }
            return (
              <div className="min-w-[80px]" key={k}>
                <span className="text-[0.68rem] font-medium uppercase tracking-wider text-muted-foreground">
                  {k.replace(/_/g, " ")}
                </span>
                <p className="mt-0.5 text-sm font-semibold text-foreground break-all">{display}</p>
              </div>
            );
          })}
        </div>
      )}

      {hasRecommendations && (
        <div className="rounded-md border border-border bg-muted/30 p-4 space-y-3">
          <p className="text-[0.72rem] font-semibold uppercase tracking-widest text-muted-foreground">
            Recommendations
          </p>
          <BacktestRecommendations data={recommendations} />
        </div>
      )}
    </div>
  );
}

function JobDetailPage() {
  const { jobId } = Route.useParams();

  const {
    data: job,
    isLoading,
    error,
  } = useQuery({
    queryFn: () => apiClient.jobs.getJobApiV1JobsJobIdGet({ jobId }),
    queryKey: ["job", jobId],
    refetchInterval: (query) => {
      const d = query.state.data;
      return d?.status === "running" ? 3000 : false;
    },
  });

  const cfg = job ? (STATUS_CONFIG[job.status] ?? STATUS_CONFIG.pending) : null;

  return (
    <div className="space-y-6 pb-8">
      <div className="flex items-center gap-4">
        <Button asChild size="sm" variant="ghost">
          <Link search={(prev) => prev} to="..">
            <ArrowLeft className="size-4" />
          </Link>
        </Button>
      </div>

      {isLoading && (
        <div className="flex items-center justify-center py-16">
          <Loader2 className="size-8 animate-spin text-muted-foreground" />
        </div>
      )}

      {error && <p className="text-destructive">Failed to load job: {(error as Error).message}</p>}

      {!isLoading && !error && job && (
        <div className="space-y-4">
          <Card>
            <CardHeader>
              <div className="flex flex-wrap items-center justify-between gap-4">
                <h2 className="text-base font-semibold">Job Details</h2>
                {cfg && (
                  <span className="inline-flex items-center gap-1 rounded-md border px-2 py-1 text-sm font-medium">
                    {cfg.icon}
                    {cfg.label}
                  </span>
                )}
              </div>
            </CardHeader>
            <CardContent>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-[35%]">Property</TableHead>
                    <TableHead>Value</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  <TableRow>
                    <TableCell className="font-mono text-xs text-muted-foreground">
                      Job ID
                    </TableCell>
                    <TableCell className="font-mono text-sm">{job.jobId}</TableCell>
                  </TableRow>
                  <TableRow>
                    <TableCell className="font-mono text-xs text-muted-foreground">Type</TableCell>
                    <TableCell>
                      {JOB_TYPE_LABELS[job.jobType ?? ""] ?? humanSlug(job.jobType ?? undefined)}
                    </TableCell>
                  </TableRow>
                  <TableRow>
                    <TableCell className="font-mono text-xs text-muted-foreground">Agent</TableCell>
                    <TableCell>{humanSlug(job.promptSlug ?? undefined) || "All agents"}</TableCell>
                  </TableRow>
                  <TableRow>
                    <TableCell className="font-mono text-xs text-muted-foreground">
                      Project ID
                    </TableCell>
                    <TableCell className="font-mono text-sm text-muted-foreground">
                      {job.projectId}
                    </TableCell>
                  </TableRow>
                  <TableRow>
                    <TableCell className="font-mono text-xs text-muted-foreground">
                      Started
                    </TableCell>
                    <TableCell className="text-sm">
                      {formatDate(job.createdAt ?? undefined)}
                    </TableCell>
                  </TableRow>
                  <TableRow>
                    <TableCell className="font-mono text-xs text-muted-foreground">
                      Updated
                    </TableCell>
                    <TableCell className="text-sm">
                      {formatDate(job.updatedAt ?? undefined)}
                    </TableCell>
                  </TableRow>
                  {job.celeryTaskId && (
                    <TableRow>
                      <TableCell className="font-mono text-xs text-muted-foreground">
                        Celery Task ID
                      </TableCell>
                      <TableCell className="font-mono break-all text-xs">
                        {job.celeryTaskId}
                      </TableCell>
                    </TableRow>
                  )}
                </TableBody>
              </Table>
            </CardContent>
          </Card>

          {job.result && Object.keys(job.result).length > 0 && (
            <Card>
              <CardHeader>
                <h2 className="text-base font-semibold">Result</h2>
              </CardHeader>
              <CardContent>
                <JobResultContent result={job.result as Record<string, unknown>} />
              </CardContent>
            </Card>
          )}
        </div>
      )}
    </div>
  );
}
