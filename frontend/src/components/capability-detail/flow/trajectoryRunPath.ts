import type { Span } from "@/openapi";
import { matchEvaluationAnchor } from "./bindTrajectoryEvaluations";
import type { TrajectoryGraph, TrajectoryNode } from "./buildTrajectoryGraph";

type RunSpan = Pick<
  Span,
  | "spanId"
  | "parentSpanId"
  | "name"
  | "operation"
  | "attributes"
  | "startTimeNs"
  | "endTimeNs"
  | "capability"
>;

function capturedOutput(span: RunSpan): unknown {
  const attrs = span.attributes ?? {};
  const output =
    attrs["overmind.output_data"] ??
    attrs["overmind.output.data"] ??
    attrs.outputs ??
    attrs["traceloop.entity.output"];
  if (typeof output !== "string") return output;
  try {
    return JSON.parse(output);
  } catch {
    return output;
  }
}

const outputText = (output: unknown) =>
  typeof output === "string" ? output : JSON.stringify(output, null, 2);

export function trajectoryRunPath(
  graph: TrajectoryGraph,
  spans: RunSpan[],
  capability: string,
  completed = false
) {
  graph = { ...graph, edges: [...graph.edges], nodes: [...graph.nodes] };
  const targets = graph.nodes.flatMap(({ id, data }) => {
    if (data.kind === "entry") return [{ anchors: data.entrypoint ? [data.entrypoint] : [], id }];
    if (data.kind === "step") return [{ anchors: data.anchors ?? [], id }];
    if (data.kind === "action") return [{ anchors: [data.tool.name], id }];
    return [];
  });
  const nodesById = new Map(graph.nodes.map((node) => [node.id, node]));
  const toolTargets = targets.filter(({ id }) => nodesById.get(id)?.data.kind === "action");
  const nodeIds = new Set<string>();
  const edgeIds = new Set<string>();
  const transitions = new Set<string>();
  const matches = new Map<string, string>();
  const recorded = spans.filter((span) => !span.capability || span.capability === capability);
  const spansById = new Map(recorded.map((span) => [span.spanId, span]));
  for (const span of recorded) {
    const attrs = span.attributes ?? {};
    const qualified = [attrs["code.namespace"], attrs["code.function.name"]]
      .filter((value) => typeof value === "string" && value)
      .join(".");
    const codeMatch = qualified ? matchEvaluationAnchor(qualified, targets) : undefined;
    const candidates = new Set(
      [attrs["tool.name"], span.name, span.operation]
        .filter((value): value is string => typeof value === "string" && !!value)
        .map((name) => matchEvaluationAnchor(name, targets))
        .filter((id): id is string => !!id)
    );
    const toolMatch =
      typeof attrs["tool.name"] === "string"
        ? matchEvaluationAnchor(attrs["tool.name"], toolTargets)
        : undefined;
    const id = toolMatch ?? codeMatch ?? (candidates.size === 1 ? [...candidates][0] : undefined);
    if (id) {
      matches.set(span.spanId, id);
      nodeIds.add(id);
    }
  }

  // An outcome needs its own named runtime span under the matching tool. A
  // branch's prose condition alone is never evidence that it executed.
  const outcomes = graph.nodes.filter((node) => node.data.kind === "outcome");
  for (const span of recorded) {
    if (matches.has(span.spanId)) continue;
    const visited = new Set<string>([span.spanId]);
    let parent = span.parentSpanId ? spansById.get(span.parentSpanId) : undefined;
    let actionId: string | undefined;
    while (parent && !visited.has(parent.spanId)) {
      visited.add(parent.spanId);
      const id = matches.get(parent.spanId);
      if (id && nodesById.get(id)?.data.kind === "action") {
        actionId = id;
        break;
      }
      parent = parent.parentSpanId ? spansById.get(parent.parentSpanId) : undefined;
    }
    if (!actionId) continue;
    const candidates = outcomes.filter(
      ({ data }) =>
        data.kind === "outcome" &&
        data.actionId === actionId &&
        !!data.branch?.action &&
        [span.name, span.operation].includes(data.branch.action)
    );
    if (candidates.length === 1) {
      matches.set(span.spanId, candidates[0].id);
      nodeIds.add(candidates[0].id);
    }
  }

  const captured = new Map<string, { output: unknown; time: number; count: number }>();
  for (const span of recorded) {
    const actionId = matches.get(span.spanId);
    if (!actionId || nodesById.get(actionId)?.data.kind !== "action") continue;
    const output = capturedOutput(span);
    if (output == null) continue;
    const previous = captured.get(actionId);
    captured.set(actionId, {
      count: (previous?.count ?? 0) + 1,
      output: previous && previous.time > span.startTimeNs ? previous.output : output,
      time: Math.max(previous?.time ?? 0, span.startTimeNs),
    });
  }
  for (const [actionId, result] of captured) {
    // Show the actual result when the trace does not identify a source branch;
    // never guess which prose condition was true from a successful tool call.
    if (
      outcomes.some(
        ({ id, data }) => data.kind === "outcome" && data.actionId === actionId && nodeIds.has(id)
      )
    )
      continue;
    const generic = outcomes.filter(
      ({ data }) => data.kind === "outcome" && data.actionId === actionId && data.branch === null
    );
    const action = nodesById.get(actionId)!;
    if (action.data.kind !== "action") continue;
    const id = generic.length === 1 ? generic[0].id : `outcome:${actionId}:recorded`;
    const outcome: TrajectoryNode = {
      data: {
        actionId,
        branch: null,
        groupId: action.data.groupId,
        kind: "outcome",
        recordedInvocations: result.count,
        returns: outputText(result.output),
      },
      id,
      position: { x: 0, y: 0 },
      type: "outcome",
      width: 320,
    };
    graph.nodes = graph.nodes.filter((node) => node.id !== id);
    graph.nodes.push(outcome);
    nodesById.set(id, outcome);
    const existing = outcomes.findIndex((node) => node.id === id);
    if (existing >= 0) outcomes[existing] = outcome;
    else outcomes.push(outcome);
    if (!generic.length) {
      graph.edges.push({
        data: { relation: "tool-outcome" },
        id: `${actionId}->${id}`,
        source: actionId,
        sourceHandle: "outcomes",
        target: id,
        type: "condition",
      });
    }
    graph.edges = graph.edges.map((edge) =>
      edge.source === actionId && edge.target === id ? { ...edge, label: undefined } : edge
    );
    nodeIds.add(id);
    transitions.add(`${actionId}->${id}`);
  }

  // Only adjacent observed invocations establish a transition; never fill gaps
  // with a shortest path through steps that have no runtime evidence.
  const ordered = recorded
    .filter((span) => matches.has(span.spanId))
    .sort((a, b) => a.startTimeNs - b.startTimeNs);
  for (let index = 1; index < ordered.length; index += 1) {
    const previous = ordered[index - 1];
    const current = ordered[index];
    if (previous.endTimeNs > current.startTimeNs && current.parentSpanId !== previous.spanId)
      continue;
    const source = matches.get(ordered[index - 1].spanId)!;
    const target = matches.get(ordered[index].spanId)!;
    if (source !== target) transitions.add(`${source}->${target}`);
  }

  // Result cards represent the entrypoint's return value, not a separate call.
  // Require a finished run, a captured return, and a unique observed exit.
  if (completed) {
    for (const entry of recorded) {
      if (nodesById.get(matches.get(entry.spanId) ?? "")?.data.kind !== "entry") continue;
      const output = capturedOutput(entry);
      if (output == null || entry.endTimeNs <= entry.startTimeNs) continue;
      const exits = graph.edges.filter(
        (edge) => nodesById.get(edge.target)?.data.kind === "terminal"
      );
      const candidates = ordered
        .flatMap((span) => {
          if (span.endTimeNs > entry.endTimeNs || span.startTimeNs < entry.startTimeNs) return [];
          let parent: RunSpan | undefined = span;
          const seen = new Set<string>();
          while (parent && parent.spanId !== entry.spanId && !seen.has(parent.spanId)) {
            seen.add(parent.spanId);
            parent = parent.parentSpanId ? spansById.get(parent.parentSpanId) : undefined;
          }
          if (parent?.spanId !== entry.spanId) return [];
          return exits
            .filter((edge) => edge.source === matches.get(span.spanId))
            .map((edge) => ({ edge, end: span.endTimeNs }));
        })
        .sort((a, b) => b.end - a.end);
      const latest = candidates.filter((candidate) => candidate.end === candidates[0]?.end);
      const destinations = new Set(latest.map(({ edge }) => edge.target));
      if (destinations.size !== 1) continue;
      const terminal = latest[0].edge.target;
      const response =
        typeof output === "object" &&
        output !== null &&
        "final_response" in output &&
        typeof output.final_response === "string"
          ? output.final_response
          : outputText(output);
      nodeIds.add(terminal);
      for (const { edge } of latest) transitions.add(`${edge.source}->${terminal}`);
      graph.nodes = graph.nodes.map((node) =>
        node.id === terminal ? { ...node, data: { ...node.data, recordedOutput: response } } : node
      );
    }
  }

  for (const node of graph.nodes) {
    if (node.data.kind !== "action" || !nodeIds.has(node.id)) continue;
    const groupId = node.data.groupId;
    nodeIds.add(groupId);
    const group = nodesById.get(groupId);
    if (group?.data.kind === "actionGroup" && nodeIds.has(group.data.ownerId))
      transitions.add(`${group.data.ownerId}->${groupId}`);
    transitions.add(`${groupId}->${node.id}`);
    for (const transition of [...transitions]) {
      if (transition.startsWith(`${node.id}->`)) {
        const target = transition.slice(node.id.length + 2);
        transitions.add(`${groupId}->${target}`);
      }
    }
  }

  for (const outcome of outcomes) {
    if (outcome.data.kind !== "outcome" || !nodeIds.has(outcome.id)) continue;
    const action = nodesById.get(outcome.data.actionId);
    if (action?.data.kind !== "action") continue;
    transitions.add(`${action.id}->${outcome.id}`);
    for (const transition of [...transitions]) {
      if (transition.startsWith(`${action.id}->`) && transition !== `${action.id}->${outcome.id}`)
        transitions.add(`${outcome.id}->${transition.slice(action.id.length + 2)}`);
      if (transition.startsWith(`${outcome.id}->`))
        transitions.add(`${action.data.groupId}->${transition.slice(outcome.id.length + 2)}`);
    }
    if (outcome.data.recordedInvocations) {
      for (const target of graph.nodes) {
        if (!transitions.has(`${action.id}->${target.id}`)) continue;
        const sourceReturn = graph.edges.find(
          (edge) =>
            edge.target === target.id &&
            edge.data?.relation === "continuation" &&
            nodesById.get(edge.source)?.data.kind === "outcome" &&
            outcomes.some(
              (candidate) =>
                candidate.id === edge.source &&
                candidate.data.kind === "outcome" &&
                candidate.data.actionId === action.id
            )
        );
        if (!sourceReturn) continue;
        outcome.data.continues = true;
        if (graph.edges.some((edge) => edge.source === outcome.id && edge.target === target.id))
          continue;
        graph.edges.push({
          ...sourceReturn,
          id: `return:${outcome.id}->${target.id}`,
          label: undefined,
          source: outcome.id,
        });
      }
    }
  }

  // A run reveals only its executed tool family members, not every available
  // tool in an expanded category. Keep the main map for route context.
  for (const edge of [...graph.edges]) {
    if (edge.data?.relation !== "continuation" || nodeIds.has(edge.source)) continue;
    const outcome = nodesById.get(edge.source);
    if (outcome?.data.kind !== "outcome") continue;
    const actionId = outcome.data.actionId;
    if (!transitions.has(`${actionId}->${edge.target}`)) continue;
    if (
      graph.edges.some((candidate) => {
        const source = nodesById.get(candidate.source);
        return (
          candidate.target === edge.target &&
          nodeIds.has(candidate.source) &&
          (candidate.source === actionId ||
            (source?.data.kind === "outcome" && source.data.actionId === actionId))
        );
      })
    )
      continue;
    graph.edges.push({
      ...edge,
      data: { ...edge.data, summary: true },
      id: `return:${actionId}->${edge.target}`,
      label: undefined,
      source: actionId,
      sourceHandle: "return",
    });
  }
  graph.nodes = graph.nodes.filter(
    ({ id, data }) => !["actionGroup", "action", "outcome"].includes(data.kind) || nodeIds.has(id)
  );
  const visible = new Set(graph.nodes.map(({ id }) => id));
  graph.edges = graph.edges.filter((edge) => visible.has(edge.source) && visible.has(edge.target));

  const expandedIds = new Set<string>();
  const incoming = new Map<string, string[]>();
  for (const edge of graph.edges) {
    if (edge.data?.relation !== "continuation")
      incoming.set(edge.target, [...(incoming.get(edge.target) ?? []), edge.source]);
  }
  const queue = [...nodeIds];
  const seen = new Set<string>();
  while (queue.length) {
    const id = queue.pop()!;
    if (seen.has(id)) continue;
    seen.add(id);
    for (const parent of incoming.get(id) ?? []) {
      expandedIds.add(parent);
      queue.push(parent);
    }
  }

  const byPair = new Map<string, typeof graph.edges>();
  for (const edge of graph.edges) {
    const pair = `${edge.source}->${edge.target}`;
    byPair.set(pair, [...(byPair.get(pair) ?? []), edge]);
  }
  for (const [pair, edges] of byPair) {
    // Two conditions with the same endpoints cannot be distinguished by spans.
    if (edges.length === 1 && transitions.has(pair)) edgeIds.add(edges[0].id);
  }
  return {
    edgeIds,
    expandedIds,
    graph,
    mappedSpans: matches.size,
    nodeIds,
    totalSpans: recorded.length,
    transitions,
  };
}
