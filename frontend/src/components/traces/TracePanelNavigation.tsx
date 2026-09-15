import { useTraceData } from "./contexts/TraceDataContext";
import { useTraceSelection } from "./contexts/TraceSelectionContext";
import { SpanTree } from "./SpanTree";
import { TraceFlameChart } from "./TraceFlameChart";

export function TracePanelNavigation() {
  const { spans } = useTraceData();
  const { viewMode, selectedSpanId, setSelectedSpanId } = useTraceSelection();

  return (
    <div className="flex h-full min-w-0 flex-col border-r border-border/70">
      <div className="min-w-0 flex-1 overflow-hidden">
        {viewMode === "tree" ? (
          <div className="h-full min-w-0 overflow-auto">
            <SpanTree />
          </div>
        ) : (
          <div className="h-full min-w-0 overflow-auto p-2">
            <TraceFlameChart
              onSpanClick={(span) => {
                const id = span.spanId ?? "";
                setSelectedSpanId(id);
              }}
              selectedSpanId={selectedSpanId}
              spans={spans}
            />
          </div>
        )}
      </div>
    </div>
  );
}
