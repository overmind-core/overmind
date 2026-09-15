import type { SpanRow } from "@/hooks/use-traces";

export interface SpanTreeNode {
  id: string;
  span: SpanRow;
  children: SpanTreeNode[];
}

export function buildSpanTree(spans: SpanRow[]): SpanTreeNode[] {
  if (!spans || spans.length === 0) return [];

  const idMap = new Map<string, SpanTreeNode>();

  for (const span of spans) {
    const id = span.spanId;
    if (!id) continue;
    idMap.set(id, { children: [], id, span });
  }

  const roots: SpanTreeNode[] = [];
  for (const span of spans) {
    const id = span.spanId;
    if (!id) continue;
    const node = idMap.get(id)!;
    const parentId = span.parentSpanId;
    if (!parentId || !idMap.has(parentId)) {
      roots.push(node);
    } else {
      const parent = idMap.get(parentId)!;
      parent.children.push(node);
    }
  }

  const sortByStart = (nodes: SpanTreeNode[]) => {
    nodes.sort((a, b) => a.span.startTimeUnixNano - b.span.startTimeUnixNano);
    for (const n of nodes) {
      sortByStart(n.children);
    }
  };
  sortByStart(roots);

  return roots;
}

export interface SpanTreeRollup {
  totalTokens: number | null;
  totalCost: number | null;
  hasError: boolean;
}

/**
 * A node's own attribute always wins (the SDK already aggregated when it set the
 * field); children are only summed when the node itself carries no value.
 */
export function rollupSubtree(node: SpanTreeNode): SpanTreeRollup {
  const ownTokens = node.span.totalTokens;
  const ownCost = node.span.totalCost;
  let childTokens = 0;
  let childCost = 0;
  let sawChildToken = false;
  let sawChildCost = false;
  let hasError = node.span.statusCode === 2;

  for (const child of node.children) {
    const rollup = rollupSubtree(child);
    if (rollup.totalTokens != null) {
      childTokens += rollup.totalTokens;
      sawChildToken = true;
    }
    if (rollup.totalCost != null) {
      childCost += rollup.totalCost;
      sawChildCost = true;
    }
    if (rollup.hasError) hasError = true;
  }

  return {
    hasError,
    totalCost: ownCost ?? (sawChildCost ? childCost : null),
    totalTokens: ownTokens ?? (sawChildToken ? childTokens : null),
  };
}

export function collectAllNodeIds(nodes: SpanTreeNode[]): string[] {
  const ids: string[] = [];
  function walk(n: SpanTreeNode) {
    ids.push(n.id);
    n.children.forEach(walk);
  }
  nodes.forEach(walk);
  return ids;
}
