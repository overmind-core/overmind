import { useMemo, useState } from "react";

import { useQuery } from "@tanstack/react-query";
import type { AgentOut } from "@/api";
import { createFileRoute, Outlet } from "@tanstack/react-router";
import {
  AlertTriangle,
  ArrowDown,
  ArrowUp,
  CheckCircle,
  ChevronsUpDown,
  Clock,
  EyeOff,
  Loader2,
  XCircle,
} from "lucide-react";

import type { JobStatus, JobType, ListJobsApiV1JobsGetRequest } from "@/api";
import apiClient from "@/client";
import { CreateJobDialog } from "@/components/create-job-dialog";
import { TracesTablePagination } from "@/components/traces/traces-table-pagination";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";

import { jobsSearchSchema } from "@/lib/schemas";
import type { JobsSearch } from "@/lib/schemas";
import { cn, formatDate } from "@/lib/utils";

type SortField = NonNullable<JobsSearch["sortBy"]>;

function SortableHead({
  field,
  label,
  sortBy,
  sortDirection,
  onSort,
  onHide,
  className,
}: {
  field: SortField;
  label: string;
  sortBy: SortField;
  sortDirection: "asc" | "desc";
  onSort: (field: SortField) => void;
  onHide: (field: SortField) => void;
  className?: string;
}) {
  const isActive = sortBy === field;
  const isRight = className?.includes("text-right");
  return (
    <TableHead className={className}>
      <div className={cn("group flex items-center gap-0.5", isRight && "justify-end")}>
        <Button
          className="-ml-3 h-8 gap-1"
          onClick={() => onSort(field)}
          size="sm"
          variant="ghost"
        >
          {label}
          {isActive ? (
            sortDirection === "asc" ? (
              <ArrowUp className="size-3.5" />
            ) : (
              <ArrowDown className="size-3.5" />
            )
          ) : (
            <ChevronsUpDown className="size-3.5 text-muted-foreground/60" />
          )}
        </Button>
        <TooltipProvider>
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                className="size-6 opacity-0 transition-opacity group-hover:opacity-100"
                onClick={() => onHide(field)}
                size="icon"
                variant="ghost"
              >
                <EyeOff className="size-3.5 text-muted-foreground/60" />
              </Button>
            </TooltipTrigger>
            <TooltipContent>Hide column</TooltipContent>
          </Tooltip>
        </TooltipProvider>
      </div>
    </TableHead>
  );
}

export const Route = createFileRoute("/_auth/jobs")({
  component: JobsPage,
  validateSearch: jobsSearchSchema,
});

const STATUS_CONFIG: Record<
  string,
  {
    variant: "default" | "secondary" | "destructive" | "success" | "warning";
    icon: React.ReactNode;
    label: string;
  }
> = {
  completed: { icon: <CheckCircle className="size-3.5" />, label: "Completed", variant: "success" },
  failed: { icon: <XCircle className="size-3.5" />, label: "Failed", variant: "destructive" },
  partially_completed: { icon: <AlertTriangle className="size-3.5" />, label: "Partial", variant: "warning" },
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
  prompt_tuning: "Prompt Tuning",
  model_backtesting: "Model Backtesting",
};

function humanSlug(slug?: string): string {
  if (!slug) return "—";
  return slug.replace(/-/g, " ").replace(/_/g, " ");
}

function JobsPage() {
  const navigate = Route.useNavigate();
  const searchParams = Route.useSearch();
  const { job_type, status, page = 1, pageSize = 25, sortBy, sortDirection } = searchParams;
  const setSearch = (updates: Partial<typeof searchParams>) =>
    navigate({ resetScroll: false, search: (x) => ({ ...x, ...updates }) });

  const handleJobClick = (id: string) => {
    navigate({ params: { jobId: id }, resetScroll: false, search: (x) => x, to: "/jobs/$jobId" });
  };

  const [hiddenCols, setHiddenCols] = useState<Set<SortField>>(new Set());
  const handleSort = (field: SortField) => {
    if (sortBy === field) {
      setSearch({ sortDirection: sortDirection === "asc" ? "desc" : "asc" });
    } else {
      setSearch({ sortBy: field, sortDirection: "asc" });
    }
  };
  const handleHide = (field: SortField) => setHiddenCols((prev) => new Set([...prev, field]));
  const show = (field: SortField) => !hiddenCols.has(field);

  const offset = (page - 1) * pageSize;

  const { data, isLoading, error } = useQuery({
    queryFn: () => {
      const params: ListJobsApiV1JobsGetRequest = {};
      if (job_type && job_type !== "all") params.jobType = job_type as JobType;
      if (status && status !== "all") params.status = status as JobStatus;
      if (pageSize) params.limit = pageSize;
      if (offset) params.offset = offset;
      return apiClient.jobs.listJobsApiV1JobsGet(params);
    },
    queryKey: ["jobs", job_type, status, page, pageSize],
    refetchInterval: 10_000,
  });

  const { data: agentsData } = useQuery<{ data: AgentOut[] }>({
    queryFn: async () => {
      const res = await apiClient.agents.listAgentsApiV1AgentsGet();
      return { data: res.data ?? [] };
    },
    queryKey: ["agents"],
    staleTime: 60_000,
  });

  const agentNameBySlug = useMemo(() => {
    const map = new Map<string, string>();
    for (const agent of agentsData?.data ?? []) {
      map.set(agent.slug, agent.name);
    }
    return map;
  }, [agentsData]);

  const rawJobs = data?.jobs ?? [];
  const total = data?.total ?? 0;
  const showPagination = total > 0;

  const jobs = useMemo(() => {
    return [...rawJobs].sort((a, b) => {
      let cmp = 0;
      switch (sortBy) {
        case "status":
          cmp = (a.status ?? "").localeCompare(b.status ?? "");
          break;
        case "jobType":
          cmp = (a.jobType ?? "").localeCompare(b.jobType ?? "");
          break;
        case "promptSlug":
          cmp = (a.promptSlug ?? "").localeCompare(b.promptSlug ?? "");
          break;
        case "triggeredBy":
          cmp = (a.triggeredBy ?? "").localeCompare(b.triggeredBy ?? "");
          break;
        case "createdAt":
        default:
          cmp = (new Date(a.createdAt ?? 0).getTime()) - (new Date(b.createdAt ?? 0).getTime());
      }
      return sortDirection === "desc" ? -cmp : cmp;
    });
  }, [rawJobs, sortBy, sortDirection]);



  if (isLoading) {
    return (
      <div className="flex h-full flex-col gap-4">
        <JobsHeader />
        <div className="flex flex-1 items-center justify-center">
          <Loader2 className="size-8 animate-spin text-muted-foreground" />
        </div>
      </div>
    );
  }
  if (error) {
    return (
      <div className="flex h-full flex-col gap-4">
        <JobsHeader />
        <Alert variant="destructive">
          Failed to load jobs: {(error as Error).message}
        </Alert>
      </div>
    );
  }
  if (!jobs || jobs.length === 0) {
    return (
      <div className="flex h-full flex-col gap-4">
        <JobsHeader />
        <div className="flex flex-1 items-center justify-center rounded-md border border-dashed border-border text-center text-muted-foreground">
          <p>No jobs found. Trigger a job from the home page to see it here.</p>
        </div>
      </div>
    );
  }
  return (
    <div className="flex h-full flex-col gap-4">
      <JobsHeader />

      <div className="min-h-0 flex-1 overflow-hidden rounded-md border border-border">
        <div className="max-h-full overflow-auto">
          <Table>
            <TableHeader>
              <TableRow>
                {show("status") && (
                  <SortableHead
                    className="w-[120px]"
                    field="status"
                    label="Status"
                    onHide={handleHide}
                    onSort={handleSort}
                    sortBy={sortBy}
                    sortDirection={sortDirection}
                  />
                )}
                {show("jobType") && (
                  <SortableHead
                    field="jobType"
                    label="Job Type"
                    onHide={handleHide}
                    onSort={handleSort}
                    sortBy={sortBy}
                    sortDirection={sortDirection}
                  />
                )}
                {show("promptSlug") && (
                  <SortableHead
                    field="promptSlug"
                    label="Agent"
                    onHide={handleHide}
                    onSort={handleSort}
                    sortBy={sortBy}
                    sortDirection={sortDirection}
                  />
                )}
                {show("triggeredBy") && (
                  <SortableHead
                    className="w-[120px]"
                    field="triggeredBy"
                    label="Started By"
                    onHide={handleHide}
                    onSort={handleSort}
                    sortBy={sortBy}
                    sortDirection={sortDirection}
                  />
                )}
                {show("createdAt") && (
                  <SortableHead
                    className="w-[200px] text-right"
                    field="createdAt"
                    label="Started At"
                    onHide={handleHide}
                    onSort={handleSort}
                    sortBy={sortBy}
                    sortDirection={sortDirection}
                  />
                )}
              </TableRow>
            </TableHeader>
            <TableBody>
              {jobs.map((job) => {
                const cfg = STATUS_CONFIG[job.status] ?? STATUS_CONFIG.pending;
                return (
                  <TableRow
                    className="cursor-pointer hover:bg-muted/50"
                    key={job.jobId}
                    onClick={() => handleJobClick(job.jobId)}
                  >
                    {show("status") && (
                      <TableCell>
                        <Badge className="gap-1" variant={cfg.variant}>
                          {cfg.icon}
                          {cfg.label}
                        </Badge>
                      </TableCell>
                    )}
                    {show("jobType") && (
                      <TableCell className="font-medium">
                        {JOB_TYPE_LABELS[job.jobType] ?? job.jobType}
                      </TableCell>
                    )}
                    {show("promptSlug") && (
                      <TableCell className={job.promptSlug ? "" : "italic text-muted-foreground"}>
                        {job.promptSlug
                          ? (agentNameBySlug.get(job.promptSlug) ?? humanSlug(job.promptSlug))
                          : "All agents"}
                      </TableCell>
                    )}
                    {show("triggeredBy") && (
                      <TableCell
                        className={cn(
                          "text-sm",
                          job.triggeredByUserId ? "text-muted-foreground" : "text-primary"
                        )}
                      >
                        {job.triggeredBy === "scheduled" ? "System" : "User"}
                      </TableCell>
                    )}
                    {show("createdAt") && (
                      <TableCell className="text-right text-sm text-muted-foreground">
                        {formatDate(job.createdAt ?? undefined)}
                      </TableCell>
                    )}
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </div>
      </div>

      {showPagination && (
        <TracesTablePagination
          count={total}
          onPageChange={(p) => setSearch({ page: p })}
          onPageSizeChange={(s) => setSearch({ page: 1, pageSize: s })}
          page={page}
          pageSize={pageSize}
        />
      )}

      <Outlet />
    </div>
  );
}

const JobsHeader = () => {
  const navigate = Route.useNavigate();
  const searchParams = Route.useSearch();
  const { job_type, status } = searchParams;
  const typeFilter = job_type ?? "";
  const statusFilter = status ?? "";

  const setSearch = (updates: Partial<typeof searchParams>) =>
    navigate({ resetScroll: false, search: { ...searchParams, ...updates } });

  const setTypeFilter = (v: string) =>
    setSearch({
      job_type: v as "all" | "agent_discovery" | "judge_scoring" | "prompt_tuning" | "model_backtesting" | undefined,
      page: 1,
    });
  const setStatusFilter = (v: string) =>
    setSearch({
      page: 1,
      status: v as "all" | "running" | "completed" | "partially_completed" | "failed" | "pending" | undefined,
    });

  return (
    <div className="flex shrink-0 items-center gap-4">
      <CreateJobDialog />
      <Select onValueChange={setTypeFilter} value={typeFilter}>
        <SelectTrigger className="w-[160px]">
          <SelectValue placeholder="Job Type" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="all">All</SelectItem>
          <SelectItem value="agent_discovery">Agent Discovery</SelectItem>
          <SelectItem value="judge_scoring">Scoring</SelectItem>
          <SelectItem value="prompt_tuning">Prompt Tuning</SelectItem>
          <SelectItem value="model_backtesting">Model Backtesting</SelectItem>
        </SelectContent>
      </Select>
      <Select onValueChange={setStatusFilter} value={statusFilter}>
        <SelectTrigger className="w-[140px]">
          <SelectValue placeholder="Status" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="all">All</SelectItem>
          <SelectItem value="running">Running</SelectItem>
          <SelectItem value="completed">Completed</SelectItem>
          <SelectItem value="partially_completed">Partially Completed</SelectItem>
          <SelectItem value="failed">Failed</SelectItem>
          <SelectItem value="pending">Pending</SelectItem>
        </SelectContent>
      </Select>
    </div>
  );
};
