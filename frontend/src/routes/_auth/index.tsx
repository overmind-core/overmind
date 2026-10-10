import { useEffect, useState } from "react";

import { useQuery } from "@tanstack/react-query";
import { createFileRoute } from "@tanstack/react-router";

import desktopMonitorIcon from "@/assets/desktop-monitor.svg";
import apiClient from "@/client";
import { CapabilityBrowser } from "@/components/capability-grid";
import { CreateProjectDialog } from "@/components/create-project";
import {
  NoProjectsEmptyState,
  ProjectRequiredEmptyState,
} from "@/components/project-required-empty-state";
import { QuickstartEmbed } from "@/components/quickstart/quickstart-embed";
import { RepositorySnapshot } from "@/components/repository-snapshot";
import { Alert } from "@/components/ui/alert";
import { PageHeader } from "@/components/ui/page-header";
import { PageShell } from "@/components/ui/page-shell";
import { Skeleton } from "@/components/ui/skeleton";
import { useAuthContext } from "@/contexts/auth-context";
import { setStoredProjectId } from "@/hooks/use-project-search-sync";
import { useProjectsList } from "@/hooks/use-projects";
import { getGuestProjectId } from "@/lib/guest";
import { errorMessage } from "@/lib/notify";
import type { AgentGraph, CapabilityList } from "@/openapi";

export const Route = createFileRoute("/_auth/")({
  component: AgentPage,
});

function AgentHeader({ graph }: { graph?: AgentGraph }) {
  return (
    <header className="space-y-3">
      <PageHeader
        actions={graph && <RepositorySnapshot graph={graph} />}
        className="flex-col sm:flex-row sm:flex-wrap [&>div:last-child]:max-w-full [&>div:last-child]:min-w-0"
        description="Capabilities, prompts, tools and evals."
        icon={
          <img
            alt=""
            aria-hidden="true"
            className="size-6 shrink-0 [image-rendering:pixelated] invert"
            src={desktopMonitorIcon}
          />
        }
        title="Agent"
      />
    </header>
  );
}

function CapabilitiesSection({
  graph,
  isLoading,
  error,
  projectId,
}: {
  graph?: AgentGraph;
  isLoading: boolean;
  error: Error | null;
  projectId: string;
}) {
  const capabilities: CapabilityList[] = graph?.capabilities ?? [];

  if (isLoading) {
    return (
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
        {Array.from({ length: 6 }).map((_, i) => (
          <Skeleton className="h-40 rounded-md" key={i} />
        ))}
      </div>
    );
  }

  if (error) {
    return (
      <Alert variant="destructive">Failed to load capabilities: {(error as Error).message}</Alert>
    );
  }

  if (!projectId) return null;

  return (
    <div className="min-w-0 space-y-4">
      {capabilities.length === 0 ? (
        <QuickstartEmbed projectId={projectId} />
      ) : (
        <CapabilityBrowser capabilities={capabilities} projectId={projectId} />
      )}
    </div>
  );
}

function AgentPage() {
  const navigate = Route.useNavigate();
  const search = Route.useSearch();
  const graphQuery = useQuery({
    enabled: !!search.projectId,
    queryFn: () => apiClient.agent.agentRetrieve({ project: search.projectId! }),
    queryKey: ["agent-graph", search.projectId],
    refetchInterval: 30_000,
  });
  const { isGuest } = useAuthContext();
  const {
    data: projectsData,
    isLoading: projectsLoading,
    error: projectsError,
  } = useProjectsList({ pollWhileEmpty: true });
  const projects = projectsData?.projects ?? [];
  const [createOpen, setCreateOpen] = useState(false);

  const { createProject } = search;

  useEffect(() => {
    if (projects.length !== 1 || search.projectId) return;
    const id = projects[0].id;
    setStoredProjectId(id);
    void navigate({
      replace: true,
      search: (prev) => ({ ...prev, projectId: id }),
    });
  }, [navigate, projects, search.projectId]);
  useEffect(() => {
    if (!isGuest || search.projectId) return;
    const guestProjectId = getGuestProjectId();
    if (!guestProjectId) return;
    void navigate({
      replace: true,
      search: (prev) => ({ ...prev, projectId: guestProjectId }),
    });
  }, [isGuest, navigate, search.projectId]);

  useEffect(() => {
    if (!createProject) {
      return;
    }
    setCreateOpen(true);
    navigate({
      replace: true,
      search: (prev) => ({
        ...prev,
        createProject: undefined,
        message: undefined,
      }),
      to: "/",
    });
  }, [createProject, navigate]);

  const createProjectDialog = (
    <CreateProjectDialog onOpenChange={setCreateOpen} open={createOpen} />
  );

  if (projectsLoading) {
    return (
      <PageShell header={<AgentHeader />} variant="scroll">
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton className="h-40 rounded-md" key={i} />
          ))}
        </div>
      </PageShell>
    );
  }

  if (projectsError && !projectsData) {
    return (
      <PageShell header={<AgentHeader />} variant="scroll">
        <Alert variant="destructive">
          {errorMessage(projectsError, "Couldn't load your projects.")}
        </Alert>
      </PageShell>
    );
  }

  if (projects.length === 0) {
    return (
      <PageShell header={<AgentHeader />} variant="scroll">
        <NoProjectsEmptyState />
        {createProjectDialog}
      </PageShell>
    );
  }

  if (!search.projectId) {
    return (
      <PageShell header={<AgentHeader />} variant="scroll">
        <ProjectRequiredEmptyState />
        {createProjectDialog}
      </PageShell>
    );
  }

  return (
    <PageShell header={<AgentHeader graph={graphQuery.data} />} variant="scroll">
      <CapabilitiesSection
        error={graphQuery.error}
        graph={graphQuery.data}
        isLoading={graphQuery.isLoading}
        projectId={search.projectId}
      />
      {createProjectDialog}
    </PageShell>
  );
}
