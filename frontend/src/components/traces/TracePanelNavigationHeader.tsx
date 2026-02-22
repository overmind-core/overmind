import { useMemo } from "react";

import { FoldVertical, Timer, UnfoldVertical } from "lucide-react";

import { Button } from "@/components/ui/button";
import { useTraceData } from "./contexts/TraceDataContext";
import { useTraceSelection } from "./contexts/TraceSelectionContext";
import { buildSpanTree, collectAllNodeIds } from "./lib/tree-building";

export function TracePanelNavigationHeader() {
  const { spans } = useTraceData();
  const { collapsedNodes, expandAll, collapseAll, viewMode, setViewMode } = useTraceSelection();

  const roots = useMemo(() => buildSpanTree(spans), [spans]);
  const allNodeIds = useMemo(() => collectAllNodeIds(roots), [roots]);
  const isEverythingCollapsed = roots.length > 0 && roots.every((r) => collapsedNodes.has(r.id));

  return (
    <div className="flex shrink-0 flex-col gap-2 border-b border-border p-2">
      <div className="flex items-center gap-2">
        <Button
          className="h-7 w-7"
          onClick={() => (isEverythingCollapsed ? expandAll() : collapseAll(allNodeIds))}
          size="icon"
          title={isEverythingCollapsed ? "Expand all" : "Collapse all"}
          variant="ghost"
        >
          {isEverythingCollapsed ? (
            <UnfoldVertical className="h-3.5 w-3.5" />
          ) : (
            <FoldVertical className="h-3.5 w-3.5" />
          )}
        </Button>
        <Button
          className="h-7 px-2 text-xs"
          onClick={() => setViewMode(viewMode === "timeline" ? "tree" : "timeline")}
          size="sm"
          title="Toggle timeline view"
          variant={viewMode === "timeline" ? "default" : "ghost"}
        >
          <Timer className="h-3.5 w-3.5" />
        </Button>
      </div>
    </div>
  );
}
