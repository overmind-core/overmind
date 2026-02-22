import { useTraceData } from "./contexts/TraceDataContext";
import { useTraceSelection } from "./contexts/TraceSelectionContext";
import { SpanDetailView } from "./SpanDetailView";

export function TracePanelDetail() {
  const { traceId, projectId, spans, isLoading } = useTraceData();
  const { selectedSpanId } = useTraceSelection();

  const selectedSpan = selectedSpanId ? spans.find((s) => s.spanId === selectedSpanId) : null;

  if (isLoading) {
    return (
      <div className="flex h-full items-center justify-center p-8">
        <p className="text-sm text-muted-foreground">Loading trace...</p>
      </div>
    );
  }

  if (spans.length === 0) {
    return (
      <div className="flex h-full items-center justify-center p-8">
        <p className="text-sm text-muted-foreground">No trace data found.</p>
      </div>
    );
  }

  if (selectedSpan) {
    return (
      <div className="h-full overflow-y-auto p-6">
        <SpanDetailView queryKey={["trace", traceId, projectId]} span={selectedSpan} />
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col items-center justify-center p-8 text-center">
      <h3 className="text-base font-semibold">Trace Overview</h3>
      <p className="mt-2 text-sm text-muted-foreground">
        Trace: <code className="font-mono">{traceId}</code>
      </p>
      <p className="mt-1 text-sm text-muted-foreground">
        {spans.length} span{spans.length !== 1 ? "s" : ""}
      </p>
      <p className="mt-4 text-sm text-muted-foreground">
        Select a span from the navigation panel to view details.
      </p>
    </div>
  );
}
