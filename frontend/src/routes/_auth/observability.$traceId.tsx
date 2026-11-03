import { createFileRoute } from "@tanstack/react-router";

import { TraceDetailPanel } from "@/components/traces/trace-detail-panel";
import { tracesSearchSchema } from "@/lib/schemas";

export const Route = createFileRoute("/_auth/observability/$traceId")({
  component: TraceDetailPage,
  validateSearch: tracesSearchSchema,
});

function TraceDetailPage() {
  const { traceId } = Route.useParams();
  const { projectId, detailExpanded } = Route.useSearch();
  const navigate = Route.useNavigate();

  return (
    <TraceDetailPanel
      detailExpanded={detailExpanded}
      onClose={() => navigate({ resetScroll: false, search: (prev) => prev, to: "/observability" })}
      onToggleExpanded={() =>
        navigate({ search: (prev) => ({ ...prev, detailExpanded: !prev.detailExpanded }) })
      }
      projectId={projectId}
      traceId={traceId}
    />
  );
}
