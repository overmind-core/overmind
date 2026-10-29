import { useCallback, useEffect, useState } from "react";

import { useQuery, useQueryClient } from "@tanstack/react-query";
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
import { Button } from "@/components/ui/button";
import { PageHeader } from "@/components/ui/page-header";
import { PageShell } from "@/components/ui/page-shell";
import { LoadingState } from "@/components/ui/spinner";
import { Switch } from "@/components/ui/switch";
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

function AgentHeader({
  graph,
  previewNewUser,
  onPreviewNewUserChange,
}: {
  graph?: AgentGraph;
  previewNewUser: boolean;
  onPreviewNewUserChange: (checked: boolean) => void;
}) {
  return (
    <header className="space-y-3">
      <PageHeader
        actions={
          <>
            {graph && <RepositorySnapshot graph={graph} />}
            {import.meta.env.DEV && (
              <label className="flex items-center gap-2 text-sm text-muted-foreground">
                <Switch
                  checked={previewNewUser}
                  onCheckedChange={onPreviewNewUserChange}
                  size="sm"
                />
                Dev: new user
              </label>
            )}
          </>
        }
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
  projectName,
  devPreview = false,
}: {
  graph?: AgentGraph;
  isLoading: boolean;
  error: Error | null;
  projectId: string;
  projectName?: string;
  devPreview?: boolean;
}) {
  const capabilities: CapabilityList[] = graph?.capabilities ?? [];
  const onboarding = useOnboardingMilestones(projectId, "agent");
  const onboardingActive =
    !!onboarding.data?.[0]?.completedAt && !onboarding.data?.every((step) => step.completedAt);
  const [started, setStarted] = useState(false);
  const [finished, setFinished] = useState(false);
  const [exiting, setExiting] = useState(false);
  const [revealed, setRevealed] = useState(false);
  const [replay, setReplay] = useState(0);
  const queryClient = useQueryClient();
  const onComplete = useCallback(() => setFinished(true), []);
  useEffect(() => {
    if (onboardingActive) setStarted(true);
  }, [onboardingActive]);
  useEffect(() => {
    if (finished && !devPreview)
      void queryClient.invalidateQueries({ queryKey: ["agent-graph", projectId] });
  }, [finished, devPreview, projectId, queryClient]);
  useEffect(() => {
    if (!finished || !capabilities.length) return;
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const pause = window.setTimeout(() => setExiting(true), 1100);
    const reveal = window.setTimeout(() => setRevealed(true), reduced ? 1100 : 1340);
    return () => {
      window.clearTimeout(pause);
      window.clearTimeout(reveal);
    };
  }, [finished, capabilities.length]);

  const showOnboarding =
    !revealed && (devPreview || onboardingActive || started || finished || !capabilities.length);
  if (isLoading && !graph && !devPreview && !started)
    return <LoadingState fullPage label="Loading agent" />;
  if (error && !graph && !devPreview && !started)
    return <Alert variant="destructive">Failed to load capabilities: {error.message}</Alert>;
  if (showOnboarding)
    return (
      <div
        className={cn(
          "grid min-w-0 flex-1 place-items-center transition-opacity duration-200 motion-reduce:transition-none",
          exiting && "opacity-0"
        )}
      >
        <QuickstartEmbed
          autoPlay={replay > 0}
          devPreview={devPreview}
          key={replay}
          onComplete={onComplete}
          projectId={projectId}
          projectName={projectName}
        />
        {finished && error && (
          <Alert variant="destructive">Couldn’t load your capabilities. Retrying…</Alert>
        )}
      </div>
    );
  return (
    <div
      className={cn(
        "min-w-0 space-y-4",
        revealed &&
          "motion-safe:animate-in motion-safe:fade-in-0 motion-safe:slide-in-from-bottom-2 motion-safe:duration-300"
      )}
    >
      {devPreview && (
        <div className="flex items-center justify-between gap-3 text-xs text-muted-foreground">
          <span>Preview complete · showing this project’s capabilities</span>
          <Button
            onClick={() => {
              setFinished(false);
              setExiting(false);
              setRevealed(false);
              setReplay((value) => value + 1);
            }}
            size="xs"
            variant="secondary"
          >
            Replay onboarding
          </Button>
        </div>
      )}
      <CapabilityBrowser capabilities={capabilities} projectId={projectId} />
    </div>
  );
}

function AgentPage() {
  const navigate = Route.useNavigate();
  const search = Route.useSearch();
  const [previewNewUser, setPreviewNewUser] = useState(false);
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
  const header = (
    <AgentHeader
      graph={previewNewUser ? undefined : graphQuery.data}
      onPreviewNewUserChange={setPreviewNewUser}
      previewNewUser={previewNewUser}
    />
  );

  if (projectsLoading && !previewNewUser) {
    return (
      <PageShell header={header} variant="scroll">
        <LoadingState fullPage label="Loading projects" />
      </PageShell>
    );
  }

  if (projectsError && !projectsData && !previewNewUser) {
    return (
      <PageShell header={header} variant="scroll">
        <Alert variant="destructive">
          {errorMessage(projectsError, "Couldn't load your projects.")}
        </Alert>
      </PageShell>
    );
  }

  if (previewNewUser || projects.length === 0) {
    return (
      <PageShell
        className="flex min-h-full flex-col gap-6 space-y-0"
        header={header}
        variant="scroll"
      >
        {previewNewUser && search.projectId ? (
          <CapabilitiesSection
            devPreview
            error={graphQuery.error}
            graph={graphQuery.data}
            isLoading={graphQuery.isLoading}
            key={`preview:${search.projectId}`}
            projectId={search.projectId}
            projectName={projects.find((project) => project.id === search.projectId)?.name}
          />
        ) : (
          <div className="grid min-w-0 flex-1 place-items-center">
            <NoProjectsEmptyState />
          </div>
        )}
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
