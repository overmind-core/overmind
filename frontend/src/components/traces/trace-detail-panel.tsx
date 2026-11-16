import { DetailErrorState } from "@/components/route-error";
import { TraceDataProvider } from "@/components/traces/contexts/TraceDataContext";
import { TraceSelectionProvider } from "@/components/traces/contexts/TraceSelectionContext";
import { TraceLayoutDesktop } from "@/components/traces/TraceLayoutDesktop";
import { TraceLoadingState } from "@/components/traces/TraceLoadingState";
import { TracePanelDetail } from "@/components/traces/TracePanelDetail";
import { TracePanelNavigation } from "@/components/traces/TracePanelNavigation";
import { TracePanelNavigationHeader } from "@/components/traces/TracePanelNavigationHeader";
import { Button } from "@/components/ui/button";
import { useIsMobile } from "@/hooks/use-mobile";
import { useProjectsList } from "@/hooks/use-projects";
import type { SpanRow } from "@/hooks/use-traces";
import { useTraceDetail } from "@/hooks/use-traces";

export function TraceDetailPanel({
  traceId,
  projectId,
  detailExpanded,
  onClose,
  onToggleExpanded,
}: {
  traceId: string;
  projectId?: string;
  detailExpanded: boolean;
  onClose: () => void;
  onToggleExpanded: () => void;
}) {
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
            <Button onClick={onClose} size="sm" variant="secondary">
              Back to traces
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
          <TracePanelNavigationHeader
            detailExpanded={detailExpanded}
            onClose={onClose}
            onToggleExpanded={onToggleExpanded}
          />
          <div className="min-h-0 flex-1">
            {isLoading ? <TraceLoadingState /> : <TraceLayoutContent />}
          </div>
        </div>
      </TraceSelectionProvider>
    </TraceDataProvider>
  );
}

function TraceLayoutContent() {
  const isMobile = useIsMobile();
  if (isMobile)
    return (
      <div className="flex h-full min-h-0 flex-col">
        <div className="h-[30%] min-h-36 shrink-0 border-b border-border">
          <TracePanelNavigation />
        </div>
        <div className="min-h-0 flex-1">
          <TracePanelDetail />
        </div>
      </div>
    );
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
