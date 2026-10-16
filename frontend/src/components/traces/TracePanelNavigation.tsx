import { useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import { Icon } from "@/components/ui/icons";
import { SearchInput } from "@/components/ui/search-input";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useTraceData } from "./contexts/TraceDataContext";
import { useTraceSelection } from "./contexts/TraceSelectionContext";
import { buildSpanTree, collectAllNodeIds } from "./lib/tree-building";
import { SpanTree } from "./SpanTree";
import { TraceFlameChart } from "./TraceFlameChart";

export function TracePanelNavigation() {
  const { spans } = useTraceData();
  const {
    viewMode,
    setViewMode,
    selectedSpanId,
    setSelectedSpanId,
    collapsedNodes,
    expandAll,
    collapseAll,
  } = useTraceSelection();
  const [search, setSearch] = useState("");
  const roots = useMemo(() => buildSpanTree(spans), [spans]);
  const collapsed = roots.length > 0 && roots.every((root) => collapsedNodes.has(root.id));
  return (
    <div className="flex h-full min-w-0 flex-col bg-wash-subtle">
      <div className="flex h-11 shrink-0 items-center justify-between gap-2 border-b border-border/70 px-2">
        <Tabs onValueChange={(value) => setViewMode(value as "tree" | "timeline")} value={viewMode}>
          <TabsList aria-label="Span navigation" className="h-7 bg-transparent p-0">
            <TabsTrigger
              className="px-2 text-xs data-[state=active]:bg-muted data-[state=active]:text-foreground data-[state=active]:hover:text-foreground"
              value="tree"
            >
              Tree
            </TabsTrigger>
            <TabsTrigger
              className="px-2 text-xs data-[state=active]:bg-muted data-[state=active]:text-foreground data-[state=active]:hover:text-foreground"
              value="timeline"
            >
              Timeline
            </TabsTrigger>
          </TabsList>
        </Tabs>
        {viewMode === "tree" && (
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                aria-label={collapsed ? "Expand all spans" : "Collapse all spans"}
                disabled={!!search.trim()}
                onClick={() => (collapsed ? expandAll() : collapseAll(collectAllNodeIds(roots)))}
                size="icon-sm"
                variant="ghost"
              >
                <Icon.listBox />
              </Button>
            </TooltipTrigger>
            <TooltipContent>{collapsed ? "Expand all spans" : "Collapse all spans"}</TooltipContent>
          </Tooltip>
        )}
      </div>
      {viewMode === "tree" && (
        <div className="shrink-0 border-b border-border/70 px-2 py-1.5">
          <SearchInput
            label="Search spans"
            onChange={(e) => setSearch(e.target.value)}
            onClear={() => setSearch("")}
            placeholder="Search spans…"
            size="sm"
            value={search}
            variant="subtle"
          />
        </div>
      )}
      <div className="min-h-0 min-w-0 flex-1 overflow-auto">
        {viewMode === "tree" ? (
          <SpanTree search={search} />
        ) : (
          <div className="p-2">
            <TraceFlameChart
              onSpanClick={(span) => setSelectedSpanId(span.spanId)}
              selectedSpanId={selectedSpanId}
              spans={spans}
            />
          </div>
        )}
      </div>
    </div>
  );
}
