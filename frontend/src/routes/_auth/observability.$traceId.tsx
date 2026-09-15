import { createFileRoute, Link } from "@tanstack/react-router";

import { DetailErrorState } from "@/components/route-error";
import { TraceDataProvider } from "@/components/traces/contexts/TraceDataContext";
import { TraceSelectionProvider } from "@/components/traces/contexts/TraceSelectionContext";
import { TraceLayoutDesktop } from "@/components/traces/TraceLayoutDesktop";
import { TracePanelDetail } from "@/components/traces/TracePanelDetail";
import { TracePanelNavigation } from "@/components/traces/TracePanelNavigation";
import { TracePanelNavigationHeader } from "@/components/traces/TracePanelNavigationHeader";
import { Button } from "@/components/ui/button";
import { LoadingState } from "@/components/ui/spinner";
import { useProjectsList } from "@/hooks/use-projects";
import type { SpanRow } from "@/hooks/use-traces";
import { useTraceDetail } from "@/hooks/use-traces";
import { tracesSearchSchema } from "@/lib/schemas";

export const Route = createFileRoute("/_auth/observability/$traceId")({
  component: TraceDetailPage,
  validateSearch: tracesSearchSchema,
});

function TraceDetailPage() {
  const { traceId } = Route.useParams();
  const { projectId } = Route.useSearch();

  const { data: projectsData } = useProjectsList();
  const firstProjectId = projectsData?.projects?.[0]?.projectId;
  const selectedProject = projectId ?? firstProjectId ?? "";

  const { data: traceQueryData, isLoading, error } = useTraceDetail(traceId, selectedProject);
  const spanList = (traceQueryData?.transformedSpans ?? []) as SpanRow[];

  if (error && !traceQueryData) {
    return (
      <div className="p-6">
        <DetailErrorState
          action={
            <Button asChild size="sm" variant="secondary">
              <Link search={{ projectId }} to="/observability">
                Back to traces
              </Link>
            </Button>
          }
          error={error}
          fallback="Couldn't load this trace."
        />
      </div>
    );
  }

  const traceData = {
    isLoading,
    projectId: selectedProject,
    spans: spanList,
    traceId: traceId ?? "",
    traceStatus: traceQueryData?.traceStatus,
    usage: traceQueryData?.usage,
  };

  return (
    <TraceDataProvider value={traceData}>
      <TraceSelectionProvider>
        <div className="flex h-full min-h-0 flex-col">
          {/* Rendered while loading too: the panel's only close affordance. */}
          <TracePanelNavigationHeader />
          <div className="min-h-0 flex-1">
            {isLoading ? <LoadingState /> : <TraceLayoutContent />}
          </div>
        </div>
      </TraceSelectionProvider>
    </TraceDataProvider>
  );
}

function TraceLayoutContent() {
  return (
    <TraceLayoutDesktop>
      <TraceLayoutDesktop.NavigationPanel>
        <TracePanelNavigation />
      </TraceLayoutDesktop.NavigationPanel>
      <TraceLayoutDesktop.ResizeHandle />
      <TraceLayoutDesktop.DetailPanel>
        <TracePanelDetail />
      </TraceLayoutDesktop.DetailPanel>
    </TraceLayoutDesktop>
  );
}
