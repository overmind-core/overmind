import { EmptyState } from "@/components/ui/empty-state";
import { Icon } from "@/components/ui/icons";
import { TITLE } from "@/lib/typography";
import { useTraceData } from "./contexts/TraceDataContext";
import { useTraceSelection } from "./contexts/TraceSelectionContext";
import { SpanDetailView } from "./SpanDetailView";

export function TracePanelDetail() {
  const { traceId, spans, isLoading } = useTraceData();
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
      <EmptyState
        className="h-full"
        description="This trace has no spans, or they haven't finished processing yet."
        icon={Icon.observability}
        size="section"
        title="No trace data found"
      />
    );
  }

  return (
    <div className="flex h-full flex-col overflow-hidden">
      {selectedSpan ? (
        <div className="min-h-0 flex-1 overflow-hidden p-4">
          <SpanDetailView span={selectedSpan} />
        </div>
      ) : (
        <div className="flex flex-1 flex-col items-center justify-center p-8 text-center">
          <h3 className={TITLE.card}>Trace overview</h3>
          <p className="mt-2 text-sm text-muted-foreground">
            Trace: <code className="font-mono">{traceId}</code>
          </p>
          <p className="mt-1 text-sm text-muted-foreground">
            {spans.length} span{spans.length !== 1 ? "s" : ""}
          </p>
          <p className="mt-6 text-sm text-muted-foreground">
            Select a span from the navigation panel to view details.
          </p>
        </div>
      )}
    </div>
  );
}
