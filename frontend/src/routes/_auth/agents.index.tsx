import { useMemo } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createFileRoute } from "@tanstack/react-router";
import { Loader2, RefreshCw, Search } from "lucide-react";
import { useState } from "react";

import { ResponseError, type AgentOut } from "@/api";
import apiClient from "@/client";
import { AgentGrid } from "@/components/agent-grid";
import { NoAgentsEmptyState } from "@/components/NoAgentsEmptyState";
import { useProjectsList } from "@/hooks/use-projects";
import { Alert } from "@/components/ui/alert";
import { DismissibleAlert } from "@/components/ui/dismissible-alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
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


function AgentsPage() {
  const queryClient = useQueryClient();
  const { data: projectsData } = useProjectsList();
  const projects = projectsData?.projects ?? [];

  const [selectedProjectId, setSelectedProjectId] = useState<string | undefined>(undefined);
  const [search, setSearch] = useState("");

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

  const filteredAgents = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return agents;
    return agents.filter(
      (a) =>
        a.name.toLowerCase().includes(q) ||
        (a.tags ?? []).some((t) => t.toLowerCase().includes(q)),
    );
  }, [agents, search]);

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
            {projectFilter}
          </div>
        </div>
        <Alert className="mb-4" variant="destructive">
          Failed to load agents: {(error as Error).message}
        </Alert>
      </div>
    );
  }
  return (
    <div className="page-wrapper">
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          {projectFilter}
          <div className="relative">
            <Search className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
            <Input
              className="h-8 w-56 pl-8 text-sm"
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search by name or tag..."
              value={search}
            />
          </div>
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

      {!agents || agents.length === 0 ? (
        <NoAgentsEmptyState />
      ) : filteredAgents.length === 0 && search.trim() ? (
        <p className="py-8 text-center text-sm text-muted-foreground">
          No agents matching &ldquo;{search.trim()}&rdquo;
        </p>
      ) : (
        <AgentGrid agents={filteredAgents} projectId={activeProjectId} />
      )}
    </div>
  );
}
