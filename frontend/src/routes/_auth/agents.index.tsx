import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createFileRoute } from "@tanstack/react-router";
import { ExternalLink, Key, Loader2, RefreshCw, Search } from "lucide-react";
import { useMemo, useState } from "react";

import { ResponseError, type AgentOut } from "@/api";
import apiClient from "@/client";
import { AgentGrid } from "@/components/agent-grid";
import { CreateApiKeyDialog } from "@/components/create-api-key-dialog";
import { ProjectSelector } from "@/components/project-selector";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { DismissibleAlert } from "@/components/ui/dismissible-alert";
import { Input } from "@/components/ui/input";
import { useProjectsList } from "@/hooks/use-projects";

export const Route = createFileRoute("/_auth/agents/")({
  component: AgentsPage,
});

interface EmptyStateProps {
  projectId?: string;
  organisationId?: string;
}

function EmptyState({ projectId, organisationId }: EmptyStateProps) {
  const [showCreateKey, setShowCreateKey] = useState(false);

  return (
    <>
      <div className="flex w-full flex-col items-center py-12 text-center">
        <p className="mb-2 font-display text-4xl font-medium">No agents detected yet</p>
        <p className="mx-auto mb-4 max-w-sm text-sm text-muted-foreground">
          Connect your LLM application and ingest traces, then extract templates to see your agents
          here.
        </p>
        <div className="flex items-center gap-3">
          <a
            className="inline-flex items-center rounded-lg bg-black px-4 py-2 text-sm font-semibold text-white hover:bg-black/80 dark:bg-white dark:text-black dark:hover:bg-white/80"
            href="https://docs.overmindlab.ai/"
            rel="noopener noreferrer"
            target="_blank"
          >
            Integration Guide <ExternalLink className="ml-1.5 size-4" />
          </a>
          {projectId && organisationId && (
            <Button
              aria-label="Create API Key"
              onClick={() => setShowCreateKey(true)}
              variant="outline"
            >
              <Key className="mr-1.5 size-4" />
              Create API Key
            </Button>
          )}
        </div>
      </div>

      {projectId && organisationId && (
        <CreateApiKeyDialog
          defaultRole="project_admin"
          onCreated={() => { }}
          onOpenChange={setShowCreateKey}
          open={showCreateKey}
          organisationId={organisationId}
          projectId={projectId}
        />
      )}
    </>
  );
}

function AgentsPage() {
  const queryClient = useQueryClient();
  const { data: projectsData } = useProjectsList();
  const projects = projectsData?.projects ?? [];

  const [selectedProjectId, setSelectedProjectId] = useState<string | undefined>(undefined);
  const [search, setSearch] = useState("");

  const activeProjectId = selectedProjectId ?? projects[0]?.projectId;
  const activeProject = projects.find((p) => p.projectId === activeProjectId);

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
        a.name.toLowerCase().includes(q) || (a.tags ?? []).some((t) => t.toLowerCase().includes(q))
    );
  }, [agents, search]);


  const projectFilter = (
    <ProjectSelector selection={activeProjectId} setSelection={setSelectedProjectId} />
  );

  if (isLoading) {
    return (
      <div className="page-wrapper">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">{projectFilter}</div>
        </div>
        <Loader2 className="size-8 animate-spin text-muted-foreground" />
      </div>
    );
  }
  if (error) {
    return (
      <div className="page-wrapper">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">{projectFilter}</div>
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
        <EmptyState organisationId={activeProject?.organisationId} projectId={activeProjectId} />
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
