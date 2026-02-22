import { createFileRoute, Link } from "@tanstack/react-router";
import { ArrowLeft, Expand, Loader2, Shrink } from "lucide-react";

import { TraceDataProvider } from "@/components/traces/contexts/TraceDataContext";
import type { SpanRow } from "@/hooks/use-traces";
import { TraceSelectionProvider } from "@/components/traces/contexts/TraceSelectionContext";
import { TraceLayoutDesktop } from "@/components/traces/TraceLayoutDesktop";
import { TracePanelDetail } from "@/components/traces/TracePanelDetail";
import { TracePanelNavigation } from "@/components/traces/TracePanelNavigation";
import { Button } from "@/components/ui/button";
import { useProjectsList } from "@/hooks/use-projects";
import { useTraceDetail } from "@/hooks/use-traces";
import { tracesSearchSchema } from "@/lib/schemas";

export const Route = createFileRoute("/_auth/projects/$projectId/traces/$traceId")({
  component: TraceDetailPage,
  validateSearch: tracesSearchSchema,
});

function TraceDetailPage() {
  const { traceId, projectId: projectIdParam } = Route.useParams();
  const navigate = Route.useNavigate();
  const { projectId: projectIdSearch, detailExpanded } = Route.useSearch();

  const { data: projectsData } = useProjectsList();
  const firstProjectId = projectsData?.projects?.[0]?.projectId;
  const selectedProject = projectIdParam ?? projectIdSearch ?? firstProjectId ?? "";

  const { data: traceQueryData, isLoading } = useTraceDetail(traceId, selectedProject);
  const spanList = (traceQueryData?.spans ?? []) as SpanRow[];

  const traceData = {
    isLoading,
    projectId: selectedProject,
    spans: spanList,
    traceId,
  };

  return (
    <TraceDataProvider value={traceData}>
      <TraceSelectionProvider>
        <div className="flex h-full min-h-0 flex-col">
          {/* Header - Langfuse-style: breadcrumb, back, expand */}
          <div className="flex shrink-0 items-center gap-2 border-b border-border px-4 py-3">
            <Button asChild size="sm" variant="ghost">
              <Link search={(prev) => prev} to=".." resetScroll={false}>
                <ArrowLeft className="size-4" />
              </Link>
            </Button>
            <Button
              className="cursor-pointer"
              onClick={() =>
                navigate({
                  search: (prev) => ({ ...prev, detailExpanded: !prev.detailExpanded }),
                })
              }
              size="sm"
              title={detailExpanded ? "Collapse panel" : "Expand panel"}
              variant="ghost"
            >
              {detailExpanded ? <Shrink className="size-4" /> : <Expand className="size-4" />}
            </Button>
          </div>

          {/* Main content - resizable Nav | Detail layout */}
          <div className="min-h-0 flex-1">
            {isLoading ? (
              <div className="flex h-full items-center justify-center">
                <Loader2 className="size-8 animate-spin text-muted-foreground" />
              </div>
            ) : (
              <TraceLayoutContent />
            )}
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
