import { Fragment, useEffect } from "react";

import { useQuery } from "@tanstack/react-query";
import { createFileRoute, Link, Navigate, Outlet, useRouterState } from "@tanstack/react-router";

import apiClient from "@/client";
import { OutOfCreditsDialog } from "@/components/billing/out-of-credits-dialog";
import { BreadcrumbSwitcher } from "@/components/breadcrumb-switcher";
import { CreateAccountDialog } from "@/components/guest/create-account-dialog";
import { HeaderCredits } from "@/components/header-credits";
import { ProjectSelector } from "@/components/project-selector";
import { Button } from "@/components/ui/button";
import { Icon } from "@/components/ui/icons";
import { Spinner } from "@/components/ui/spinner";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { useAuthContext } from "@/contexts/auth-context";
import { useFinetuningRunJobsQuery } from "@/hooks/use-finetuning";
import { useOnboardingStatus } from "@/hooks/use-query";
import { emitGuestUpgrade, getGuestProjectId, isGuestAllowedPath } from "@/lib/guest";
import { sentenceCase } from "@/lib/label-case";
import { projectIdSearchSchema } from "@/lib/schemas";
import { AppSidebar } from "../components/app-sidebar";
import { CommandPalette } from "../components/command-palette";
import { RunningJobsButton } from "../components/running-jobs-button";
import { SettingsMenuButton } from "../components/settings-menu";
import { SidebarInset, SidebarProvider, useSidebar } from "../components/ui/sidebar";
import { useCommandPalette } from "../hooks/use-command-palette";
import { useProjectSearchSync } from "../hooks/use-project-search-sync";
import { cn } from "../lib/utils";
export const Route = createFileRoute("/_auth")({
  component: RouteComponent,
  validateSearch: projectIdSearchSchema,
});

type Crumb = { label: string; path: string };

const DYNAMIC_PARENTS = new Set([
  "capabilities",
  "projects",
  "jobs",
  "observability",
  "datasets",
  "optimiser",
]);

const HIDDEN_BREADCRUMB_PATHS = new Set(["/evaluations/runs"]);

// Not a CSS `capitalize` class: that title-cases every word and cannot keep
// acronyms and product names canonical (Trace ID, Langfuse).
function prettifySegment(seg: string): string {
  return sentenceCase(decodeURIComponent(seg).replace(/[-_]/g, " "));
}

function detectDynamicResource(segments: string[]): {
  kind: string;
  slug: string;
  slugIdx: number;
} | null {
  if (segments.length >= 2 && DYNAMIC_PARENTS.has(segments[0])) {
    return { kind: segments[0], slug: segments[1], slugIdx: 1 };
  }
  return null;
}

function useCachedName(kind: string | undefined, slug: string | undefined): string | undefined {
  const capabilityNameQuery = useQuery({
    enabled: kind === "capabilities" && !!slug,
    queryFn: () => apiClient.capabilities.capabilitiesRetrieve({ id: slug ?? "" }),
    queryKey: ["capability-detail", slug],
    select: (capability) => capability.name,
  });

  const projectNameQuery = useQuery({
    enabled: kind === "projects" && !!slug,
    queryFn: () => apiClient.projects.projectsRetrieve({ id: slug ?? "" }),
    queryKey: ["project", slug],
    select: (project) => project.name,
  });

  const datasetNameQuery = useQuery({
    enabled: kind === "datasets" && !!slug,
    queryFn: () => apiClient.datasets.datasetsRetrieve({ id: slug ?? "" }),
    queryKey: ["dataset", slug],
    select: (dataset) => dataset.name?.trim() || `Dataset ${dataset.id.slice(0, 8)}`,
  });

  const experimentNameQuery = useQuery({
    enabled: kind === "optimiser" && !!slug,
    queryFn: () => apiClient.optimizerExperiments.optimizerExperimentsRetrieve({ id: slug ?? "" }),
    // Same key as the run page so the label rides the existing cache.
    queryKey: ["optimizer-experiment", slug],
    select: (experiment) => `${experiment.capabilityName || "Run"} · ${experiment.id.slice(0, 8)}`,
  });

  if (kind === "capabilities") return capabilityNameQuery.data;
  if (kind === "projects") return projectNameQuery.data;
  if (kind === "datasets") return datasetNameQuery.data;
  if (kind === "optimiser") return experimentNameQuery.data;
  return undefined;
}

interface BreadcrumbInfo {
  crumbs: Crumb[];
  // Drives the leaf quick-switcher; null unless the slug is the last segment.
  leaf: { kind: string; slug: string } | null;
}

// The training monitor is search-param driven on `/training`, not a nested path.
// Same run query as the monitor page, so the label rides its cache.
function useTrainingMonitorCrumb(
  groupId: string | undefined,
  jobId: string | undefined
): string | undefined {
  const { data: jobs } = useFinetuningRunJobsQuery(groupId);
  if (!jobs) return undefined;
  if (jobs.length === 0) return "Training run";
  if (jobs.length > 1) return `${jobs.length} experiments`;
  const selected = (jobId && jobs.find((j) => j.id === jobId)) || jobs[0];
  return selected?.name || selected?.baseModel || "Training run";
}

function useBreadcrumbs(): BreadcrumbInfo {
  const { location } = useRouterState();
  const { pathname } = location;
  const search = location.search as {
    groupId?: string;
    jobId?: string;
    projectId?: string;
    train?: boolean;
  };
  const segments = pathname.split("/").filter(Boolean);

  const dynamic = detectDynamicResource(segments);
  const cachedName = useCachedName(dynamic?.kind, dynamic?.slug);

  const ftGroupId = pathname === "/training" ? search.groupId : undefined;
  const ftMonitorLabel = useTrainingMonitorCrumb(ftGroupId, search.jobId);
  // The create wizard is the same route with `train=true`. It is a modal, so it
  // gets no crumb, but the flag still suppresses the leaf switcher below.
  const onTrainWizard = pathname === "/training" && search.train === true;

  if (pathname === "/") return { crumbs: [{ label: "Home", path: "/" }], leaf: null };

  const crumbs: Crumb[] = [{ label: "Home", path: "/" }];
  let builtPath = "";

  for (let i = 0; i < segments.length; i++) {
    const seg = segments[i];
    builtPath += `/${seg}`;
    if (HIDDEN_BREADCRUMB_PATHS.has(builtPath)) continue;

    const isDynamicSlug = dynamic && i === dynamic.slugIdx;
    const label = isDynamicSlug && cachedName ? cachedName : prettifySegment(seg);

    crumbs.push({ label, path: builtPath });
  }

  if (ftGroupId) {
    crumbs.push({
      label: ftMonitorLabel ?? "Training run",
      path: `${builtPath}#${ftGroupId}`,
    });
  }

  const slugIsLast = dynamic ? dynamic.slugIdx === segments.length - 1 : false;
  return {
    crumbs,
    leaf:
      dynamic && slugIsLast && !ftGroupId && !onTrainWizard
        ? { kind: dynamic.kind, slug: dynamic.slug }
        : null,
  };
}

function SidebarToggle() {
  const { toggleSidebar, state } = useSidebar();
  const isCollapsed = state === "collapsed";
  const label = isCollapsed ? "Expand sidebar" : "Collapse sidebar";

  return (
    <TooltipProvider delayDuration={150}>
      <Tooltip>
        <TooltipTrigger asChild>
          <Button
            aria-label={label}
            className="shrink-0 text-muted-foreground hover:text-foreground"
            onClick={toggleSidebar}
            size="icon"
            variant="ghost"
          >
            {isCollapsed ? <Icon.panelLeftOpen /> : <Icon.panelLeftClose />}
          </Button>
        </TooltipTrigger>
        <TooltipContent side="bottom" sideOffset={6}>
          {label}
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

function Breadcrumb() {
  const { crumbs, leaf } = useBreadcrumbs();
  const projectId = useRouterState({
    select: (s) => (s.location.search as { projectId?: string }).projectId,
  });

  // `contents` on the nav keeps toggle, chevrons and crumbs siblings in one h-8
  // flex row. `-translate-y-px`: with the 80/20 ascent/descent overrides
  // NeueBit's cap-ink centre lands ~1px below the row centre at 22px.
  const crumbClass =
    "inline-flex h-8 min-w-0 items-center leading-none -translate-y-px text-muted-foreground transition-colors hover:text-foreground";
  const leafClass =
    "inline-flex h-8 min-w-0 items-center leading-none -translate-y-px font-bold text-foreground";

  return (
    <>
      <div className="crumb-label flex h-8 min-w-0 flex-1 items-center gap-1.5">
        <SidebarToggle />
        <nav aria-label="Breadcrumb" className="contents">
          {crumbs.map((crumb, i) => {
            const isLast = i === crumbs.length - 1;
            return (
              <Fragment key={crumb.path}>
                {i > 0 && (
                  <span
                    aria-hidden
                    className="inline-flex h-8 shrink-0 items-center justify-center"
                  >
                    <Icon.chevronRight className="block size-3.5 text-muted-foreground" />
                  </span>
                )}
                {isLast ? (
                  leaf ? (
                    <BreadcrumbSwitcher
                      currentSlug={leaf.slug}
                      kind={leaf.kind}
                      label={crumb.label}
                      projectId={projectId}
                    />
                  ) : (
                    <span className={leafClass}>{crumb.label}</span>
                  )
                ) : (
                  <Link
                    className={crumbClass}
                    search={(prev) => {
                      // Drop the monitor/wizard params so the Training parent
                      // shows the jobs list again.
                      if (crumb.path !== "/training") return prev;
                      const {
                        groupId: _g,
                        jobId: _j,
                        train: _t,
                        capabilityId: _a,
                        datasetId: _d,
                        ...rest
                      } = prev as {
                        capabilityId?: string;
                        datasetId?: string;
                        groupId?: string;
                        jobId?: string;
                        train?: boolean;
                      };
                      return rest;
                    }}
                    to={crumb.path}
                  >
                    {crumb.label}
                  </Link>
                )}
              </Fragment>
            );
          })}
        </nav>
      </div>
      <HeaderActions hideProjects={crumbs[1]?.path === "/projects"} />
    </>
  );
}

function HeaderActions({ hideProjects }: { hideProjects: boolean }) {
  const { isGuest } = useAuthContext();
  return (
    <div className="flex h-8 shrink-0 items-center gap-2">
      {isGuest ? null : <HeaderCredits />}
      {hideProjects ? null : <ProjectSelector />}
      <SettingsMenuButton />
      {isGuest ? null : <RunningJobsButton />}
    </div>
  );
}

function RootLayout() {
  useProjectSearchSync();
  const { open: cmdOpen, setOpen: setCmdOpen } = useCommandPalette();
  // `?flowFull=true` hides the header chrome so the flow canvas can fill the card bezel.
  const flowFull = useRouterState({
    select: (s) => (s.location.search as { flowFull?: boolean }).flowFull === true,
  });

  return (
    <SidebarProvider>
      <AppSidebar collapsible="icon" onSearchOpen={() => setCmdOpen(true)} />
      <CommandPalette onOpenChange={setCmdOpen} open={cmdOpen} />
      <SidebarInset className="min-w-0 bg-sidebar">
        <div className="flex h-screen p-2">
          {/* border-sidebar-border: hairline in the sidebar black family (not --border grey). */}
          <div className="flex min-w-0 flex-1 flex-col overflow-clip rounded-md border border-sidebar-border bg-card">
            <header
              className={cn(
                // pr-3 matches the 12px the 32px-tall controls leave above and
                // below them, so the corner control sits square.
                "flex h-14 shrink-0 items-center gap-3 border-b border-border/70 pl-3 pr-3",
                flowFull && "hidden"
              )}
            >
              <Breadcrumb />
            </header>
            {/* relative: absolute descendants (e.g. sr-only spans in pagination
                  buttons) must resolve their containing block inside this clipper —
                  otherwise they escape to the ICB, extend the hidden-overflow body,
                  and any focus/scrollIntoView shoves the whole shell off screen. */}
            <div
              className={cn(
                "relative flex min-h-0 flex-1 flex-col",
                flowFull ? "overflow-hidden p-0" : "overflow-y-auto p-4 md:p-6"
              )}
            >
              <Outlet />
            </div>
          </div>
        </div>
      </SidebarInset>
    </SidebarProvider>
  );
}

function RouteComponent() {
  const { isSignedIn, isLoaded, isGuest } = useAuthContext();
  const onboardingQuery = useOnboardingStatus(isSignedIn && isLoaded && !isGuest);
  const projectId = useRouterState({
    select: (s) => (s.location.search as { projectId?: string }).projectId,
  });
  // The committed match, not the pending location: while the router leaves for
  // a public route the pending pathname would read as gated and bounce back.
  const settledPathname = useRouterState({
    select: (s) => s.matches[s.matches.length - 1]?.pathname ?? s.location.pathname,
  });
  const gated = isGuest && !isGuestAllowedPath(settledPathname);

  // Home is hidden from guests, so landing there is a redirect, not a gate.
  useEffect(() => {
    if (gated && settledPathname !== "/") emitGuestUpgrade();
  }, [gated, settledPathname]);

  if (!isLoaded) {
    return (
      <div className="flex h-screen items-center justify-center">
        <Spinner size="lg" />
      </div>
    );
  }

  if (!isSignedIn) return <Navigate to="/login" />;

  if (gated) {
    return (
      <Navigate
        replace
        search={{ projectId: projectId ?? getGuestProjectId() ?? undefined }}
        to="/"
      />
    );
  }

  if (onboardingQuery.isLoading) {
    return (
      <div className="flex h-screen items-center justify-center">
        <Spinner size="lg" />
      </div>
    );
  }

  // Gated on `data` so a failed auth-me fetch never traps an existing user in
  // the onboarding flow.
  if (onboardingQuery.data !== undefined && !onboardingQuery.data.hasCompletedOnboarding) {
    return <Navigate to="/onboarding" />;
  }

  return (
    <>
      <RootLayout />
      <CreateAccountDialog />
      {isGuest || !onboardingQuery.data?.billingEnabled ? null : <OutOfCreditsDialog />}
    </>
  );
}
