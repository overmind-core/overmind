import { EmptyState } from "@/components/ui/empty-state";
import { Icon } from "@/components/ui/icons";
import { LoadingState } from "@/components/ui/spinner";
import { useTraceData } from "./contexts/TraceDataContext";
import { useTraceSelection } from "./contexts/TraceSelectionContext";
import { SpanDetailView } from "./SpanDetailView";

export function TracePanelDetail() {
  const { spans, isLoading } = useTraceData();
  const { selectedSpanId } = useTraceSelection();

  const selectedSpan = selectedSpanId ? spans.find((s) => s.spanId === selectedSpanId) : null;

  if (isLoading) {
    return <LoadingState label="Loading trace" />;
  }

  if (spans.length === 0) {
    return (
      <EmptyState
        className="h-full"
        description="This trace has no spans, or they haven't finished processing yet."
        icon={Icon.observability}
        size="compact"
        title="No trace data found"
      />
    );
  }

  return (
    <div className="flex h-full flex-col overflow-hidden">
      {selectedSpan ? (
        <div className="min-h-0 flex-1 overflow-hidden">
          <SpanDetailView key={selectedSpan.spanId} span={selectedSpan} />
        </div>
      ) : (
        <EmptyState
          description="Choose a span to inspect its input, output and recorded details."
          icon={Icon.observability}
          size="compact"
          title="Select a span"
        />
      )}
    </div>
  );
}
