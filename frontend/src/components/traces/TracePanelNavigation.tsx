import { useTraceData } from "./contexts/TraceDataContext";
import { useTraceSelection } from "./contexts/TraceSelectionContext";
import { SpanTree } from "./SpanTree";
import { TraceFlameChart } from "./TraceFlameChart";
import { TracePanelNavigationHeader } from "./TracePanelNavigationHeader";

export function TracePanelNavigation() {
  const { spans } = useTraceData();
  const { viewMode, selectedSpanId, setSelectedSpanId } = useTraceSelection();

  return (
    <div className="flex h-full flex-col border-r border-border">
      <TracePanelNavigationHeader />
      <div className="flex-1 overflow-hidden">
        {viewMode === "tree" ? (
          <div className="h-full overflow-auto">
            <SpanTree />
          </div>
        ) : (
          <div className="h-full overflow-auto p-2">
            <TraceFlameChart
              height={400}
              onSpanClick={(span) => {
                const id = span.spanId ?? span.SpanId ?? span.span_id ?? "";
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
