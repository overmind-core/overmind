import { useMemo } from "react";

import { TraceScoreChips } from "@/components/traces/trace-score-chips";
import { Icon } from "@/components/ui/icons";
import { formatCost, formatDuration, formatNumber } from "@/lib/formatters";
import { cn } from "@/lib/utils";
import { useTraceData } from "./contexts/TraceDataContext";
import { useTraceSelection } from "./contexts/TraceSelectionContext";
import { buildSpanTree, rollupSubtree, type SpanTreeNode } from "./lib/tree-building";

const TREE_INDENT_PX = 12;
const TREE_BASE_PAD_PX = 4;

function formatTreeTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M tok`;
  return `${formatNumber(n)} tok`;
}

function SpanTypeChip({ type }: { type: "llm_call" | "tool_call" | null }) {
  if (!type) return null;
  if (type === "llm_call") {
    return (
      <span className="pixel-label inline-flex h-4 w-[34px] shrink-0 items-center justify-center rounded-sm border border-cat-1/30 bg-cat-1/10 text-xs text-cat-1">
        LLM
      </span>
    );
  }
  return (
    <span className="pixel-label inline-flex h-4 w-[34px] shrink-0 items-center justify-center rounded-sm border border-cat-2/30 bg-cat-2/10 text-xs text-cat-2">
      Tool
    </span>
  );
}

function SpanTreeNodeRow({ node, depth }: { node: SpanTreeNode; depth: number }) {
  const { selectedSpanId, setSelectedSpanId, collapsedNodes, toggleCollapsed } =
    useTraceSelection();
  const name = node.span.name || node.span.scopeName || "Unnamed";
  const hasChildren = node.children.length > 0;
  const isCollapsed = collapsedNodes.has(node.id);
  const isSelected = selectedSpanId === node.id;

  const rollup = useMemo(() => rollupSubtree(node), [node]);
  const durationMs = node.span.durationNano > 0 ? node.span.durationNano / 1_000_000 : null;
  const hasScores = Object.keys(node.span.traceScores ?? {}).length > 0;

  return (
    <div className="flex min-w-0 flex-col">
      <div
        className={cn(
          "group flex min-w-0 cursor-pointer items-center gap-1 px-1 py-1.5 text-xs font-mono hover:bg-wash-raised",
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
        style={{ paddingLeft: TREE_BASE_PAD_PX + depth * TREE_INDENT_PX }}
        tabIndex={0}
      >
        <button
          aria-label={hasChildren ? (isCollapsed ? "Expand span" : "Collapse span") : undefined}
          className="flex size-4 shrink-0 items-center justify-center rounded-sm hover:bg-wash-raised"
          onClick={(e) => {
            e.stopPropagation();
            if (hasChildren) toggleCollapsed(node.id);
          }}
          type="button"
        >
          {hasChildren ? (
            isCollapsed ? (
              <Icon.chevronRight className="size-3.5" />
            ) : (
              <Icon.chevronDown className="size-3.5" />
            )
          ) : (
            <span aria-hidden className="size-3.5" />
          )}
        </button>

        <SpanTypeChip type={node.span.spanType} />

        <span className="min-w-0 flex-1 truncate" title={name}>
          {name}
        </span>

        <div className="flex shrink-0 items-center gap-2 text-xs tabular-nums text-muted-foreground">
          {hasScores && (
            <TraceScoreChips
              className="max-w-[160px]"
              empty="hidden"
              maxChips={2}
              scores={node.span.traceScores}
            />
          )}
          {durationMs != null && (
            <span className="w-[52px] text-right" title={`${durationMs.toFixed(2)}ms`}>
              {formatDuration(durationMs)}
            </span>
          )}
          {rollup.totalTokens != null && (
            <span className="w-[60px] text-right" title={`${rollup.totalTokens} tokens`}>
              {formatTreeTokens(rollup.totalTokens)}
            </span>
          )}
          {rollup.totalCost != null && (
            <span className="w-[56px] text-right" title={`$${rollup.totalCost.toFixed(6)}`}>
              {formatCost(rollup.totalCost)}
            </span>
          )}
          {rollup.hasError ? (
            <span
              aria-label="error"
              className="h-1.5 w-1.5 shrink-0 rounded-xs bg-destructive"
              role="img"
            />
          ) : (
            <span aria-hidden className="h-1.5 w-1.5 shrink-0 rounded-xs bg-transparent" />
          )}
        </div>
      </div>
      {hasChildren && !isCollapsed && (
        <div className="min-w-0">
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
    <div className="min-w-0 overflow-auto">
      {roots.map((node) => (
        <SpanTreeNodeRow depth={0} key={node.id} node={node} />
      ))}
    </div>
  );
}
