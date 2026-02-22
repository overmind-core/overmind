import { useMemo } from "react";

import { ChevronDown, ChevronRight } from "lucide-react";

import { cn } from "@/lib/utils";
import { useTraceData } from "./contexts/TraceDataContext";
import { useTraceSelection } from "./contexts/TraceSelectionContext";
import { buildSpanTree, collectAllNodeIds, type SpanTreeNode } from "./lib/tree-building";

function SpanTreeNodeRow({ node, depth }: { node: SpanTreeNode; depth: number }) {
  const { selectedSpanId, setSelectedSpanId, collapsedNodes, toggleCollapsed } =
    useTraceSelection();
  const name = node.span.scopeName || node.span.name || "Unnamed";
  const hasChildren = node.children.length > 0;
  const isCollapsed = collapsedNodes.has(node.id);
  const isSelected = selectedSpanId === node.id;

  return (
    <div className="flex flex-col">
      <div
        className={cn(
          "flex cursor-pointer items-center gap-1 py-1.5 px-2 text-xs font-mono hover:bg-muted/50",
          isSelected && "bg-primary/10"
        )}
        onClick={() => setSelectedSpanId(isSelected ? null : node.id)}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            setSelectedSpanId(isSelected ? null : node.id);
          }
        }}
        role="button"
        style={{ paddingLeft: 8 + depth * 16 }}
        tabIndex={0}
      >
        <button
          className="flex shrink-0 items-center justify-center p-0.5 hover:bg-muted/50 rounded"
          onClick={(e) => {
            e.stopPropagation();
            if (hasChildren) toggleCollapsed(node.id);
          }}
          type="button"
        >
          {hasChildren ? (
            isCollapsed ? (
              <ChevronRight className="h-3.5 w-3.5" />
            ) : (
              <ChevronDown className="h-3.5 w-3.5" />
            )
          ) : (
            <span className="w-3.5" />
          )}
        </button>
        <span className="truncate">{name}</span>
      </div>
      {hasChildren && !isCollapsed && (
        <div>
          {node.children.map((child) => (
            <SpanTreeNodeRow depth={depth + 1} key={child.id} node={child} />
          ))}
        </div>
      )}
    </div>
  );
}

export function SpanTree() {
  const { spans } = useTraceData();
  const roots = useMemo(() => buildSpanTree(spans), [spans]);

  if (roots.length === 0) {
    return (
      <div className="flex flex-1 items-center justify-center p-4 text-center text-sm text-muted-foreground">
        No spans to display
      </div>
    );
  }

  return (
    <div className="overflow-auto">
      {roots.map((node) => (
        <SpanTreeNodeRow depth={0} key={node.id} node={node} />
      ))}
    </div>
  );
}

export { collectAllNodeIds };
