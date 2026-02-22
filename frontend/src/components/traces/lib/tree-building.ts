import type { SpanRow } from "@/hooks/use-traces";

export interface SpanTreeNode {
  id: string;
  span: SpanRow;
  children: SpanTreeNode[];
}

/**
 * Build a tree from flat spans using parentSpanId relationships.
 */
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
    nodes.forEach((n) => sortByStart(n.children));
  };
  sortByStart(roots);

  return roots;
}

/** Collect all node IDs from a tree (for collapse all). */
export function collectAllNodeIds(nodes: SpanTreeNode[]): string[] {
  const ids: string[] = [];
  function walk(n: SpanTreeNode) {
    ids.push(n.id);
    n.children.forEach(walk);
  }
  nodes.forEach(walk);
  return ids;
}
