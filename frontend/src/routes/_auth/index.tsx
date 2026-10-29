import { useEffect, useState } from "react";

import { useQuery } from "@tanstack/react-query";
import { createFileRoute } from "@tanstack/react-router";

import desktopMonitorIcon from "@/assets/desktop-monitor.svg";
import apiClient from "@/client";
import { CapabilityBrowser } from "@/components/capability-grid";
import { CodingAgentIcon } from "@/components/coding-agent-icon";
import { CreateProjectDialog } from "@/components/create-project";
import {
  NoProjectsEmptyState,
  ProjectRequiredEmptyState,
} from "@/components/project-required-empty-state";
import { QuickstartEmbed } from "@/components/quickstart/quickstart-embed";
import { RepositorySnapshot } from "@/components/repository-snapshot";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Icon } from "@/components/ui/icons";
import { PageHeader } from "@/components/ui/page-header";
import { PageShell } from "@/components/ui/page-shell";
import { LoadingState } from "@/components/ui/spinner";
import { useAuthContext } from "@/contexts/auth-context";
import { useOnboardingMilestones } from "@/hooks/use-activity";
import { setStoredProjectId } from "@/hooks/use-project-search-sync";
import { useProjectsList } from "@/hooks/use-projects";
import { getGuestProjectId } from "@/lib/guest";
import { errorMessage } from "@/lib/notify";
import { cn } from "@/lib/utils";
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

function OnboardingStrip({ projectId }: { projectId: string }) {
  const storageKey = `onboarding-dismissed:${projectId}`;
  const [dismissed, setDismissed] = useState(() => {
    try {
      return localStorage.getItem(storageKey) === "1";
    } catch {
      return false;
    }
  });
  const onboarding = useOnboardingMilestones(projectId, "agent");
  const steps = onboarding.data ?? [];
  const completed = steps.filter((step) => step.completedAt).length;
  const current = steps.find((step) => !step.completedAt);
  const reporter = current?.actorName ? current : steps.find((step) => step.actorName);
  if (dismissed || !completed || !current) return null;
  return (
    <section
      aria-label="Onboarding status"
      className="flex min-h-10 items-center gap-3 border border-border bg-card py-1 pr-1 pl-3 text-xs"
    >
      {reporter?.actorKind && reporter.actorName ? (
        <span className="inline-flex shrink-0 items-center gap-1.5 text-muted-foreground">
          <CodingAgentIcon kind={reporter.actorKind} />
          <span className="hidden sm:inline">{reporter.actorName}</span>
        </span>
      ) : null}
      <span className="min-w-0 truncate">Onboarding · {current.label}</span>
      <span className="ml-auto flex shrink-0 items-center gap-3">
        <span
          aria-label="Onboarding progress"
          aria-valuemax={steps.length}
          aria-valuemin={0}
          aria-valuenow={completed}
          aria-valuetext={`${completed} of ${steps.length} stages complete`}
          className="hidden w-40 gap-0.5 border border-border bg-background p-0.5 sm:flex"
          role="progressbar"
        >
          {steps.map((step) => (
            <span
              aria-hidden="true"
              className={cn("h-2 flex-1", step.completedAt ? "bg-primary" : "bg-muted")}
              key={step.kind}
            />
          ))}
        </span>
        <span className="text-muted-foreground tabular-nums">
          {completed} of {steps.length}
        </span>
        <Button
          aria-label="Dismiss onboarding progress"
          onClick={() => {
            try {
              localStorage.setItem(storageKey, "1");
            } catch {}
            setDismissed(true);
          }}
          size="icon-sm"
          variant="ghost"
        >
          <Icon.close />
        </Button>
      </span>
    </section>
  );
}

function CapabilitiesSection({
  graph,
  isLoading,
  error,
  projectId,
  projectName,
}: {
  graph?: AgentGraph;
  isLoading: boolean;
  error: Error | null;
  projectId: string;
  projectName?: string;
}) {
  const capabilities: CapabilityList[] = graph?.capabilities ?? [];
  if (isLoading) return <LoadingState fullPage label="Loading agent" />;
  if (error && !graph)
    return <Alert variant="destructive">Failed to load capabilities: {error.message}</Alert>;
  if (!capabilities.length)
    return (
      <div className="grid min-w-0 flex-1 place-items-center">
        <QuickstartEmbed projectId={projectId} projectName={projectName} />
      </div>
    );
  return (
    <div className="min-w-0 space-y-4">
      <OnboardingStrip projectId={projectId} />
      <CapabilityBrowser capabilities={capabilities} projectId={projectId} />
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
  const header = <AgentHeader graph={graphQuery.data} />;

  if (projectsLoading) {
    return (
      <PageShell header={header} variant="scroll">
        <LoadingState fullPage label="Loading projects" />
      </PageShell>
    );
  }

  if (projectsError && !projectsData) {
    return (
      <PageShell header={header} variant="scroll">
        <Alert variant="destructive">
          {errorMessage(projectsError, "Couldn't load your projects.")}
        </Alert>
      </PageShell>
    );
  }

  if (projects.length === 0) {
    return (
      <PageShell
        className="flex min-h-full flex-col gap-6 space-y-0"
        header={header}
        variant="scroll"
      >
        <div className="grid min-w-0 flex-1 place-items-center">
          <NoProjectsEmptyState />
        </div>
        {createProjectDialog}
      </PageShell>
    );
  }

  if (!search.projectId) {
    return (
      <PageShell header={header} variant="scroll">
        <ProjectRequiredEmptyState />
        {createProjectDialog}
      </PageShell>
    );
  }

  return (
    <PageShell
      className="flex min-h-full flex-col gap-6 space-y-0"
      header={header}
      variant="scroll"
    >
      <CapabilitiesSection
        error={graphQuery.error}
        graph={graphQuery.data}
        isLoading={graphQuery.isLoading}
        key={search.projectId}
        projectId={search.projectId}
        projectName={projects.find((project) => project.id === search.projectId)?.name}
      />
      {createProjectDialog}
    </PageShell>
  );
}
