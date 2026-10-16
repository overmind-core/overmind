import { type CSSProperties, useMemo } from "react";

import { EmptyState } from "@/components/ui/empty-state";
import { Icon } from "@/components/ui/icons";
import { formatCost, formatDuration, formatNumber } from "@/lib/formatters";
import { cn } from "@/lib/utils";
import { useTraceData } from "./contexts/TraceDataContext";
import { useTraceSelection } from "./contexts/TraceSelectionContext";
import { buildSpanTree, rollupSubtree, type SpanTreeNode } from "./lib/tree-building";

function SpanTreeNodeRow({
  node,
  depth,
  visible,
  isLast = true,
}: {
  node: SpanTreeNode;
  depth: number;
  visible: Set<string> | null;
  isLast?: boolean;
}) {
  const { selectedSpanId, setSelectedSpanId, collapsedNodes, toggleCollapsed } =
    useTraceSelection();
  const name = node.span.name || node.span.scopeName || "Unnamed span";
  const hasChildren = node.children.length > 0;
  const isCollapsed = !visible && collapsedNodes.has(node.id);
  const isSelected = selectedSpanId === node.id;
  const rollup = useMemo(() => rollupSubtree(node), [node]);
  const TypeIcon =
    node.span.spanType === "tool_call"
      ? Icon.tool
      : node.span.spanType === "llm_call"
        ? Icon.model
        : Icon.code;
  if (visible && !visible.has(node.id)) return null;
  const children = node.children.filter((child) => !visible || visible.has(child.id));
  const branchX = 16 + (depth - 1) * 20;

  return (
    <div className="relative min-w-0">
      {depth > 0 && (
        <>
          {!isLast && (
            <span
              aria-hidden
              className="pointer-events-none absolute inset-y-0 z-10 w-0.5 bg-[var(--sidebar-elbow)]"
              style={{ left: branchX }}
            />
          )}
          <span
            aria-hidden
            className="sidebar-elbow pointer-events-none absolute top-0 z-10 h-9 w-3"
            style={{ "--elbow-h": "18px", left: branchX + 10 } as CSSProperties}
          />
        </>
      )}
      <div
        className={cn(
          "relative flex min-w-0 items-start pr-2 hover:bg-wash-raised",
          isSelected && "bg-wash-raised"
        )}
        style={{ paddingLeft: 8 + depth * 20 }}
      >
        {children.length > 0 && !isCollapsed && (
          <span
            aria-hidden
            className="pointer-events-none absolute top-7 bottom-0 w-0.5 bg-[var(--sidebar-elbow)]"
            style={{ left: 16 + depth * 20 }}
          />
        )}
        {hasChildren ? (
          <button
            aria-expanded={!isCollapsed}
            aria-label={`${isCollapsed ? "Expand" : "Collapse"} ${name}`}
            className="mt-2.5 flex size-4 shrink-0 items-center justify-center rounded-sm text-muted-foreground focus-visible:ring-2 focus-visible:ring-ring"
            disabled={!!visible}
            onClick={() => toggleCollapsed(node.id)}
            type="button"
          >
            {isCollapsed ? (
              <Icon.chevronRight className="size-3" />
            ) : (
              <Icon.chevronDown className="size-3" />
            )}
          </button>
        ) : (
          <span className="w-4 shrink-0" />
        )}
        <button
          aria-pressed={isSelected}
          className="min-w-0 flex-1 rounded-sm px-1.5 py-2 text-left focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
          onClick={() => setSelectedSpanId(node.id)}
          type="button"
        >
          <span className="flex items-center gap-2 text-xs">
            <TypeIcon
              aria-hidden
              className={cn(
                "size-3.5 shrink-0",
                node.span.spanType === "tool_call"
                  ? "text-cat-2"
                  : node.span.spanType === "llm_call"
                    ? "text-cat-1"
                    : "text-muted-foreground"
              )}
            />
            <span
              className={cn("truncate", isSelected && "font-medium text-foreground")}
              title={name}
            >
              {name}
            </span>
            {rollup.hasError && (
              <Icon.failed
                aria-label="Span or descendant has an error"
                className="ml-auto size-3 shrink-0 text-destructive"
              />
            )}
          </span>
          <span className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 pl-5.5 font-mono text-xs tabular-nums text-muted-foreground">
            <span>
              {node.span.durationNano > 0
                ? formatDuration(node.span.durationNano / 1_000_000)
                : "—"}
            </span>
            {rollup.totalTokens != null && <span>{formatNumber(rollup.totalTokens)} tok</span>}
            {rollup.totalCost != null && <span>{formatCost(rollup.totalCost)}</span>}
            {node.span.evalScore != null && (
              <span title="Mean span score">{Math.round(node.span.evalScore * 100)}% score</span>
            )}
          </span>
        </button>
      </div>
      {hasChildren &&
        !isCollapsed &&
        children.map((child, index) => (
          <SpanTreeNodeRow
            depth={depth + 1}
            isLast={index === children.length - 1}
            key={child.id}
            node={child}
            visible={visible}
          />
        ))}
    </div>
  );
}

export function SpanTree({ search = "" }: { search?: string }) {
  const { spans } = useTraceData();
  const roots = useMemo(() => buildSpanTree(spans), [spans]);
  const visible = useMemo(() => {
    const query = search.trim().toLowerCase();
    if (!query) return null;
    const ids = new Set<string>();
    const visit = (node: SpanTreeNode): boolean => {
      const childrenMatch = node.children.map(visit).some(Boolean);
      const matches = [node.span.name, node.span.scopeName, node.id, node.span.model].some(
        (value) => value?.toLowerCase().includes(query)
      );
      if (matches || childrenMatch) ids.add(node.id);
      return matches || childrenMatch;
    };
    roots.forEach(visit);
    return ids;
  }, [roots, search]);
  if (!roots.length || visible?.size === 0)
    return (
      <EmptyState
        description={search ? "Try a span name, model or ID." : "Captured spans will appear here."}
        icon={Icon.search}
        size="compact"
        title={search ? "No matching spans" : "No spans"}
      />
    );
  return (
    <div className="min-w-0 py-1">
      {roots.map((node) => (
        <SpanTreeNodeRow depth={0} key={node.id} node={node} visible={visible} />
      ))}
    </div>
  );
}
