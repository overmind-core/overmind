import { getModelProviderInfo } from "@/components/model-provider";
import {
  collectAncestors,
  type TrajectoryGraph,
  type TrajectoryNode,
} from "./buildTrajectoryGraph";

export function projectTrajectory(
  graph: TrajectoryGraph,
  collapsed: ReadonlySet<string>,
  focusId?: string | null
): TrajectoryGraph {
  const forced = focusId ? collectAncestors(graph, focusId) : null;
  const structure = graph.edges.filter((edge) => edge.data?.relation !== "continuation");
  const incoming = new Set(structure.map((edge) => edge.target));
  const outgoing = new Map<string, typeof graph.edges>();
  for (const edge of structure)
    outgoing.set(edge.source, [...(outgoing.get(edge.source) ?? []), edge]);
  const visible = new Set<string>();
  const visibleEdges = new Set<string>();
  const queue = graph.nodes
    .filter((node) => node.id === "entry" || !incoming.has(node.id))
    .map((node) => node.id);
  while (queue.length) {
    const id = queue.pop()!;
    if (visible.has(id)) continue;
    visible.add(id);
    for (const edge of outgoing.get(id) ?? []) {
      if (collapsed.has(id) && !forced?.edgeIds.has(edge.id)) continue;
      visibleEdges.add(edge.id);
      queue.push(edge.target);
    }
  }
  const returns = new Map<string, (typeof graph.edges)[number]>();
  for (const edge of graph.edges.filter((edge) => edge.data?.relation === "continuation")) {
    if (!visible.has(edge.target)) continue;
    const sources = new Set<string>();
    const pending = [edge.source];
    const seen = new Set<string>();
    while (pending.length) {
      const id = pending.pop()!;
      if (seen.has(id)) continue;
      seen.add(id);
      if (visible.has(id)) sources.add(id);
      else
        for (const parent of structure.filter((candidate) => candidate.target === id))
          pending.push(parent.source);
    }
    for (const source of sources) {
      const summary = source !== edge.source;
      if (source === edge.target && summary) continue;
      const kind = graph.nodes.find((node) => node.id === source)?.data.kind;
      const id = summary ? `return:${source}->${edge.target}` : edge.id;
      returns.set(id, {
        ...edge,
        data: { ...edge.data, summary },
        id,
        label: summary ? "Tool results" : edge.label,
        source,
        sourceHandle: summary
          ? kind === "action" || kind === "actionGroup"
            ? "return"
            : undefined
          : edge.sourceHandle,
      });
    }
  }
  const edges = [...structure.filter((edge) => visibleEdges.has(edge.id)), ...returns.values()];
  return {
    ...graph,
    edges,
    nodes: graph.nodes
      .filter((node) => visible.has(node.id))
      .map((node) => ({
        ...node,
        data: {
          ...node.data,
          childCount: outgoing.get(node.id)?.length ?? 0,
          collapsed:
            collapsed.has(node.id) &&
            !(outgoing.get(node.id) ?? []).some((edge) => visibleEdges.has(edge.id)),
          ...(node.data.kind === "action"
            ? { expanded: (outgoing.get(node.id) ?? []).some((edge) => visibleEdges.has(edge.id)) }
            : {}),
        },
      })),
    relayout: (nodes) => graph.relayout(nodes, edges),
  };
}

function searchable(node: TrajectoryNode): { label: string; detail: string } {
  const d = node.data;
  switch (d.kind) {
    case "entry":
      return { detail: `${d.task} ${d.entrypoint ?? ""}`, label: d.capabilityName };
    case "step":
      return {
        detail: `${d.actor === "model" ? `Model call ${d.model} ${getModelProviderInfo(d.model).providerLabel}` : ""} ${d.contract?.action ?? ""} ${d.contract?.input ?? ""} ${d.contract?.output ?? ""} ${(d.anchors ?? []).join(" ")}`,
        label: d.label,
      };
    case "actionGroup":
      return { detail: "", label: d.label };
    case "action":
      return { detail: `${d.tool.purpose} ${d.when}`, label: d.tool.name };
    case "outcome":
      return {
        detail: `${d.branch?.condition ?? ""} ${d.branch?.outcome ?? d.returns ?? ""} ${d.branch?.next ?? ""}`,
        label: d.branch?.action || "Local outcome",
      };
    case "terminal":
      return { detail: `${d.terminalDescription} ${d.routing}`, label: d.pathName || "Result" };
    case "evaluations":
      return { detail: "", label: "Other evaluation scopes" };
  }
}

export function searchTrajectory(
  graph: TrajectoryGraph,
  query: string
): { id: string; label: string }[] {
  const q = query.trim().toLowerCase();
  if (!q) return [];
  return graph.nodes
    .map((node) => ({ id: node.id, ...searchable(node) }))
    .filter((node) => `${node.label} ${node.detail}`.toLowerCase().includes(q))
    .sort(
      (a, b) =>
        Number(b.label.toLowerCase() === q) - Number(a.label.toLowerCase() === q) ||
        Number(b.label.toLowerCase().includes(q)) - Number(a.label.toLowerCase().includes(q))
    )
    .map(({ id, label }) => ({ id, label }));
}
