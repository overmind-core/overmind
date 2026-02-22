import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createFileRoute } from "@tanstack/react-router";
import { ExternalLink, Loader2, RefreshCw } from "lucide-react";
import { useState } from "react";

import { ResponseError, type AgentOut } from "@/api";
import apiClient from "@/client";
import { AgentGrid } from "@/components/agent-grid";
import { useProjectsList } from "@/hooks/use-projects";
import { Alert } from "@/components/ui/alert";
import { DismissibleAlert } from "@/components/ui/dismissible-alert";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

export const Route = createFileRoute("/_auth/agents/")({
  component: AgentsPage,
});

function EmptyState() {
  return (
    <div className="flex w-full flex-col items-center py-12 text-center">
      <div className="mb-4 flex size-16 items-center justify-center rounded-full border border-border bg-amber-500/10">
        <svg
          className="size-10"
          fill="none"
          stroke="currentColor"
          strokeWidth={1.4}
          viewBox="0 0 24 24"
        >
          <circle cx="12" cy="12" r="9.2" stroke="currentColor" />
          <path
            d="M9.5 13.85l2.23 2.05 3.94-5.2"
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth={1.5}
          />
        </svg>
      </div>
      <p className="mb-2 text-base font-medium">No agents detected yet</p>
      <p className="mx-auto mb-4 max-w-sm text-sm text-muted-foreground">
        Connect your LLM application and ingest traces, then extract templates to see your agents
        here.
      </p>
      <a
        className="inline-flex items-center rounded-lg bg-amber-600 px-4 py-2 text-sm font-semibold text-black hover:bg-amber-500"
        href="https://docs.overmindlab.ai/guides/integrate"
        rel="noopener noreferrer"
        target="_blank"
      >
        Integration Guide <ExternalLink className="ml-1.5 size-4" />
      </a>
    </div>
  );
}

function AgentsPage() {
  const queryClient = useQueryClient();
  const { data: projectsData } = useProjectsList();
  const projects = projectsData?.projects ?? [];

  const [selectedProjectId, setSelectedProjectId] = useState<string | undefined>(undefined);

  const activeProjectId = selectedProjectId ?? projects[0]?.projectId;

  const { data, isLoading, error } = useQuery<{ data: AgentOut[] }>({
    queryFn: async () => {
      const res = await apiClient.agents.listAgentsApiV1AgentsGet({
        projectId: activeProjectId,
      });
      return { data: res.data ?? [] };
    },
    queryKey: ["agents", activeProjectId],
    refetchInterval: 15_000,
    enabled: !!activeProjectId,
  });

  const extractMutation = useMutation({
    mutationFn: () =>
      apiClient.jobs
        .createTemplateExtractionApiV1JobsExtractTemplatesPost()
        .catch(async (error) => {
          if (error instanceof ResponseError) {
            const r = await error.response.json();
            throw new Error(r.detail ?? "Extraction failed");
          }
          throw error;
        }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["agents"] }),
  });

  const agents = data?.data ?? ([] as AgentOut[]);

  const projectFilter = projects.length > 1 && (
    <Select onValueChange={setSelectedProjectId} value={activeProjectId}>
      <SelectTrigger size="sm">
        <SelectValue placeholder="All projects" />
      </SelectTrigger>
      <SelectContent>
        {projects.map((p) => (
          <SelectItem key={p.projectId} value={p.projectId}>
            {p.name}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );

  if (isLoading) {
    return (
      <div className="page-wrapper">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <h1 className="text-2xl font-bold">Agents</h1>
            {projectFilter}
          </div>
        </div>
        <Loader2 className="size-8 animate-spin text-muted-foreground" />
      </div>
    );
  }
  if (error) {
    return (
      <div className="page-wrapper">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <h1 className="text-2xl font-bold">Agents</h1>
            {projectFilter}
          </div>
        </div>
        <Alert className="mb-4" variant="destructive">
          Failed to load agents: {(error as Error).message}
        </Alert>
      </div>
    );
  }
  if (!agents || agents.length === 0) {
    return (
      <div className="page-wrapper">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <h1 className="text-2xl font-bold">Agents</h1>
            {projectFilter}
          </div>
        </div>
        <EmptyState />
      </div>
    );
  }
  return (
    <div className="page-wrapper">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <h1 className="text-2xl font-bold">Agents</h1>
          {projectFilter}
        </div>
        <Button
          disabled={extractMutation.isPending}
          onClick={() => extractMutation.mutate()}
          size="sm"
          variant="outline"
        >
          {extractMutation.isPending ? (
            <Loader2 className="size-3.5 animate-spin" />
          ) : (
            <RefreshCw className="size-3.5" />
          )}
          {extractMutation.isPending ? "Extracting…" : "Extract Templates"}
        </Button>
      </div>

      <DismissibleAlert
        className="mb-4"
        error={extractMutation.isError ? (extractMutation.error as Error) : null}
        fallback="Extraction failed"
        variant="warning"
      />
      {extractMutation.isSuccess && (
        <Alert className="mb-4" variant="success">
          Template extraction started — results will appear shortly.
        </Alert>
      )}

      <AgentGrid agents={agents} projectId={activeProjectId} />
    </div>
  );
}
