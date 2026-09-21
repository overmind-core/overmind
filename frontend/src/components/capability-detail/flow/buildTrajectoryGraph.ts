import type { Edge, Node } from "@xyflow/react";
import ELK from "elkjs/lib/elk.bundled.js";

import type { CapabilityFlow, CapabilityFlowTrajectoryStep } from "@/openapi";

export type EntryNodeData = {
  kind: "entry";
  capabilityName: string;
  task: string;
  pathCount: number;
};

export type StepCapability = {
  tool: string;
  when: string;
  purpose: string;
  sideEffect: string;
};

/** The In/Action/Out facets of a model-invocation backbone step. */
type InvocationContract = { input: string; action: string; output: string };
type ModelContext = Pick<StepNodeData, "model" | "systemPrompt" | "promptIsExcerpt">;

export type StepNodeData = {
  kind: "step";
  label: string;
  pathNames: string[];
  inputs: string[];
  outputs: { label: string; condition: string }[];
  condition: string;
  actor: "agent" | "model";
  model: string;
  systemPrompt: string;
  promptIsExcerpt: boolean;
  /** Set on model invocations; null on agent steps. */
  contract: InvocationContract | null;
  /** Conditional capability slots of a backbone step. */
  mayUse: StepCapability[];
  /**
   * Set on a decision-surface model invocation: the route from here is chosen
   * at runtime by user intent over the clustered option space, not by code.
   */
  decisionNote: string;
};

export type CapabilitiesNodeData = {
  kind: "capabilities";
  /** The model-invocation step whose output selects these tools. */
  forLabel: string;
  tools: StepCapability[];
};

type ClusterTool = { label: string; purpose: string; sideEffect: string };

/**
 * A semantic capability cluster in a decision surface's option space: tools
 * of similar function that serve similar tasks, grouped at extraction time.
 */
export type ClusterNodeData = {
  kind: "cluster";
  name: string;
  /** The model-invocation step whose runtime choice selects into this cluster. */
  forLabel: string;
  tools: ClusterTool[];
  /** e.g. "12 read · 3 write" — the trust mix at a glance. */
  sideEffectMix: string;
};

export type TerminalNodeData = {
  kind: "terminal";
  pathId: string;
  pathName: string;
  /** code_path | declared_task | decision_surface. */
  claim: string;
  /** declared_task citation into prompt/tool-purpose text. */
  promptQuote: string;
  /** Chassis-verified: anchors call-graph reachable from the entry anchor. */
  verified: boolean;
  routing: string;
  terminalKind: string;
  terminalDescription: string;
  tools: string[];
  provenance: string[];
};

type TrajectoryNodeData =
  | EntryNodeData
  | StepNodeData
  | CapabilitiesNodeData
  | ClusterNodeData
  | TerminalNodeData;
export type TrajectoryNode = Node<TrajectoryNodeData>;
export type TrajectoryGraph = {
  /** Unpositioned (0,0) — run `relayout` for placement. */
  nodes: TrajectoryNode[];
  edges: Edge[];
  /** ELK placement (async). Uses `node.measured.height` when present, else
   * `estimateHeight` — call once with estimates for first paint, again after
   * React Flow measures the cards. Returns fresh nodes; input untouched. */
  relayout: (measuredNodes: TrajectoryNode[]) => Promise<TrajectoryNode[]>;
};

const ROW_GAP = 56;
/** Gap between wrapped lanes of a satellite group. */
const LANE_GAP = 48;
/** A satellite lane taller than this wraps into side-by-side lanes instead
 * of growing into an unreadable tower. */
const MAX_LANE_HEIGHT = 1000;

const NODE_WIDTH: Record<TrajectoryNodeData["kind"], number> = {
  capabilities: 300,
  cluster: 300,
  entry: 240,
  step: 280,
  terminal: 300,
};

// Estimates for column packing: overestimate (extra air) rather than
// underestimate (overlap).
const estimateHeight = (data: TrajectoryNodeData): number => {
  switch (data.kind) {
    case "entry":
      return 110;
    case "step":
      return (
        104 +
        (data.pathNames.length > 1 ? 30 : 18) +
        (data.contract
          ? 3 * 48
          : data.inputs.length * 20 +
            data.outputs.reduce((sum, o) => sum + 20 + (o.condition ? 28 : 0), 0)) +
        (data.condition ? 52 : 0) +
        (data.actor === "model" ? 80 : 0) +
        // The rendered may-use list is capped (max-h-56 + scroll).
        (data.mayUse.length > 0 ? Math.min(28 + data.mayUse.length * 36, 252) : 0)
      );
    case "capabilities":
      // Rendered list is capped (max-h-72 + scroll).
      return Math.min(96 + data.tools.length * 44, 420);
    case "cluster":
      // Header + mix line + capped example list (max-h-40 + scroll).
      return Math.min(128 + data.tools.length * 36, 320);
    case "terminal":
      return 120 + (data.routing ? 52 : 0) + data.tools.length * 8 + data.provenance.length * 26;
  }
};

const norm = (value: string): string => value.trim().toLowerCase().replace(/\s+/g, " ");

const slug = (value: string): string =>
  value
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/(^-|-$)/g, "") || "x";

const ENTRY_ID = "entry";

// Word sequence, code punctuation flattened: "execute_tool per call" and
// "execute_tool (any gated tool)" both compare as plain words.
const words = (value: string): string =>
  value
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, " ")
    .trim();

// Card tool entries carry annotations — "execute_tool (any gated/mutation
// tool)" names the identifier before the parenthetical.
const toolIdentifier = (name: string): string => name.split("(")[0].trim();

const baseEdgeStyle = { stroke: "var(--muted-foreground)", strokeOpacity: 0.5, strokeWidth: 1.5 };

// mayUse: tool → when clause; first path's clause/contract wins on shared steps.
type StepMeta = {
  id: string;
  label: string;
  pathIds: Set<string>;
  actor: "agent" | "model";
  contract: InvocationContract | null;
  modelContexts: ModelContext[];
  mayUse: Map<string, string>;
  /** Model invocation on a decision_surface path: option space = whole tool_spec. */
  decisionSurface: boolean;
};
type EdgeMeta = { source: string; target: string; pathIds: Set<string> };

const capturedContext = (model = "", prompt = "", excerpt = ""): ModelContext => ({
  model: model.trim(),
  promptIsExcerpt: !prompt.trim() && !!excerpt.trim(),
  systemPrompt: prompt.trim() || excerpt.trim(),
});

// Shared calls and unbound modes may not have one model or prompt. Never pick
// the first worker's configuration as if it applied to every invocation.
const commonContext = (contexts: ModelContext[]): ModelContext => ({
  model: contexts.every((context) => context.model === contexts[0]?.model)
    ? (contexts[0]?.model ?? "")
    : "",
  promptIsExcerpt: contexts.some((context) => context.promptIsExcerpt),
  systemPrompt: contexts.every((context) => context.systemPrompt === contexts[0]?.systemPrompt)
    ? (contexts[0]?.systemPrompt ?? "")
    : "",
});

/**
 * One graph reflecting the backbone execution topology: entry fans out into
 * the per-path step chains and each path lands on its terminal. Steps shared
 * across paths (by normalized label) dedupe into shared fan-in/fan-out nodes;
 * path-specific ones render as per-path cards with in/out/condition detail.
 * Model invocations carry their In/Action/Out contract and hang their may_use
 * tools off a capability satellite; decision-surface invocations loop through
 * the tool_spec's semantic clusters instead. Routing clauses surface on the
 * card that owns the branch: a shared node's output row, a unique card fed
 * straight from entry, or the terminal when a path has no card of its own.
 *
 * Pure and deterministic — the caller memoizes on the flow.
 */
export const buildTrajectoryGraph = (
  flow: CapabilityFlow,
  capabilityName: string
): TrajectoryGraph => {
  const paths = (flow.trajectoryMap ?? []).filter((p) => p.id);
  const byId = new Map(paths.map((p) => [p.id, p]));
  const entryLabel = (flow.task ?? "").trim() || "agent input";
  const capabilityContext = capturedContext(
    flow.model,
    flow.systemPrompt,
    flow.systemPromptExcerpt
  );
  const modelContextFor = (step: CapabilityFlowTrajectoryStep): ModelContext => {
    const modes = flow.modes ?? [];
    const matchingModes = modes.filter((mode) =>
      [mode.entrypointFn, mode.promptBuilder].some(
        (anchor) => !!anchor && step.anchors?.includes(anchor)
      )
    );
    const contexts = (matchingModes.length ? matchingModes : modes).map((mode) =>
      capturedContext(mode.model || flow.model, mode.prompt, mode.promptExcerpt)
    );
    return commonContext(matchingModes.length ? contexts : [capabilityContext, ...contexts]);
  };

  const backbones = new Map<string, CapabilityFlowTrajectoryStep[]>();
  for (const p of paths) {
    backbones.set(
      p.id,
      (p.steps ?? []).filter((s) => (s.step ?? "").trim())
    );
  }

  const sharedKeyPaths = new Map<string, Set<string>>();
  for (const p of paths) {
    for (const step of backbones.get(p.id)!) {
      const key = norm((step.step ?? "").trim());
      const bucket = sharedKeyPaths.get(key) ?? new Set();
      bucket.add(p.id);
      sharedKeyPaths.set(key, bucket);
    }
  }

  const stepMetas = new Map<string, StepMeta>();
  const chains = new Map<string, string[]>();

  const stepIdFor = (pathId: string, label: string, index: number): string => {
    const key = norm(label);
    const shared = (sharedKeyPaths.get(key)?.size ?? 0) > 1;
    return shared ? `shared:${slug(key)}` : `step:${pathId}:${index}`;
  };

  for (const path of paths) {
    const chain = [ENTRY_ID];
    const extend = (id: string) => {
      if (chain[chain.length - 1] !== id) chain.push(id);
    };
    backbones.get(path.id)!.forEach((step, index) => {
      const label = (step.step ?? "").trim();
      const id = stepIdFor(path.id, label, index);
      const isModel = step.kind === "model_invocation";
      const meta: StepMeta = stepMetas.get(id) ?? {
        actor: isModel ? ("model" as const) : ("agent" as const),
        contract: null,
        decisionSurface: false,
        id,
        label,
        mayUse: new Map<string, string>(),
        modelContexts: [],
        pathIds: new Set<string>(),
      };
      if (isModel) meta.modelContexts.push(modelContextFor(step));
      if (isModel && !meta.contract) {
        meta.actor = "model";
        meta.contract = {
          action: step.action ?? "",
          input: step.input ?? "",
          output: step.output ?? "",
        };
      }
      if (isModel && (path.claim ?? "") === "decision_surface") meta.decisionSurface = true;
      for (const cap of step.mayUse ?? []) {
        if (cap.tool && !meta.mayUse.has(cap.tool)) meta.mayUse.set(cap.tool, cap.when ?? "");
      }
      meta.pathIds.add(path.id);
      stepMetas.set(id, meta);
      extend(id);
    });
    chain.push(`terminal-${path.id}`);
    chains.set(path.id, chain);
  }

  const nodePathIds = new Map<string, Set<string>>();
  nodePathIds.set(ENTRY_ID, new Set(paths.map((p) => p.id)));
  for (const meta of stepMetas.values()) nodePathIds.set(meta.id, meta.pathIds);
  for (const path of paths) nodePathIds.set(`terminal-${path.id}`, new Set([path.id]));

  const edgeMetas = new Map<string, EdgeMeta>();
  for (const path of paths) {
    const chain = chains.get(path.id)!;
    for (let i = 0; i < chain.length - 1; i++) {
      const key = `${chain[i]}->${chain[i + 1]}`;
      const meta = edgeMetas.get(key) ?? {
        pathIds: new Set(),
        source: chain[i],
        target: chain[i + 1],
      };
      meta.pathIds.add(path.id);
      edgeMetas.set(key, meta);
    }
  }

  const displayLabel = (id: string): string => {
    if (id === ENTRY_ID) return entryLabel;
    const meta = stepMetas.get(id);
    if (meta) return meta.label;
    const path = byId.get(id.replace(/^terminal-/, ""));
    return path ? path.terminal.description.trim() || path.terminal.kind : id;
  };

  const isBranchEdge = (meta: EdgeMeta): boolean =>
    meta.pathIds.size === 1 && meta.pathIds.size < (nodePathIds.get(meta.source)?.size ?? 0);

  const edges: Edge[] = [];
  for (const meta of edgeMetas.values()) {
    const routing = isBranchEdge(meta) ? byId.get([...meta.pathIds][0])!.routing.trim() : "";
    edges.push({
      id: `${meta.source}->${meta.target}`,
      ...(routing && {
        label: routing.length > 44 ? `${routing.slice(0, 43)}…` : routing,
        labelBgPadding: [4, 2] as [number, number],
        labelBgStyle: { fill: "var(--card)", fillOpacity: 0.9 },
        labelStyle: { fill: "var(--muted-foreground)", fontSize: 10 },
      }),
      source: meta.source,
      style: baseEdgeStyle,
      target: meta.target,
      type: "smoothstep",
    });
  }

  // Divergences the fan-out does not already express become dashed edges from
  // the diverging path's last shared node (its final decision point).
  for (const path of paths) {
    const chain = chains.get(path.id)!;
    let source = ENTRY_ID;
    for (let i = chain.length - 2; i >= 1; i--) {
      if ((nodePathIds.get(chain[i])?.size ?? 0) > 1) {
        source = chain[i];
        break;
      }
    }
    for (const siblingId of path.divergences) {
      const siblingChain = chains.get(siblingId);
      if (!siblingChain) continue;
      let common = 0;
      while (
        common < chain.length &&
        common < siblingChain.length &&
        chain[common] === siblingChain[common]
      ) {
        common++;
      }
      const target = siblingChain[common];
      if (!target || target === source || edgeMetas.has(`${source}->${target}`)) continue;
      const id = `diverge:${source}->${target}`;
      if (edges.some((e) => e.id === id)) continue;
      edges.push({
        id,
        label: "diverges",
        labelBgPadding: [4, 2] as [number, number],
        labelBgStyle: { fill: "var(--card)", fillOpacity: 0.9 },
        labelStyle: { fill: "var(--muted-foreground)", fontSize: 10 },
        source,
        style: { ...baseEdgeStyle, strokeDasharray: "4 4", strokeOpacity: 0.35 },
        target,
        type: "smoothstep",
      });
    }
  }

  const inputsOf = new Map<string, string[]>();
  const outputsOf = new Map<string, { label: string; condition: string }[]>();
  for (const meta of edgeMetas.values()) {
    const ins = inputsOf.get(meta.target) ?? [];
    const sourceLabel = displayLabel(meta.source);
    if (!ins.includes(sourceLabel)) ins.push(sourceLabel);
    inputsOf.set(meta.target, ins);

    const outs = outputsOf.get(meta.source) ?? [];
    outs.push({
      condition: isBranchEdge(meta) ? byId.get([...meta.pathIds][0])!.routing.trim() : "",
      label: displayLabel(meta.target),
    });
    outputsOf.set(meta.source, outs);
  }

  // The path's own routing clause lands on its first unique card when that
  // card is fed straight from entry — a shared predecessor already shows it
  // on an output row.
  const cardCondition = new Map<string, string>();
  const pathsWithUnique = new Set<string>();
  for (const path of paths) {
    const chain = chains.get(path.id)!;
    const index = chain.findIndex((id) => id.startsWith(`step:${path.id}:`));
    if (index === -1) continue;
    pathsWithUnique.add(path.id);
    if (chain[index - 1] === ENTRY_ID) {
      cardCondition.set(chain[index], path.routing.trim());
    }
  }

  const nodes: TrajectoryNode[] = [];
  const push = (id: string, data: TrajectoryNodeData) =>
    nodes.push({
      data,
      draggable: true,
      id,
      position: { x: 0, y: 0 },
      type: data.kind,
      width: NODE_WIDTH[data.kind],
    });

  push(ENTRY_ID, {
    capabilityName,
    kind: "entry",
    pathCount: paths.length,
    task: flow.task ?? "",
  });

  const specByName = new Map((flow.toolSpec ?? []).map((t) => [t.name, t]));
  const capabilitiesOf = (meta: StepMeta): StepCapability[] =>
    [...meta.mayUse].map(([tool, when]) => ({
      purpose: specByName.get(tool)?.purpose ?? "",
      sideEffect: (specByName.get(tool)?.sideEffect ?? "").trim(),
      tool,
      when,
    }));

  // The decision surface's option space: the whole tool_spec grouped by
  // semantic cluster (unclustered tools pool under "other capabilities").
  const clusters = new Map<string, ClusterTool[]>();
  for (const spec of flow.toolSpec ?? []) {
    const identifier = toolIdentifier(spec.name);
    if (!words(identifier) || /llm/i.test(identifier)) continue;
    const name = (spec.cluster ?? "").trim() || "other capabilities";
    const bucket = clusters.get(name) ?? [];
    bucket.push({
      label: identifier,
      purpose: spec.purpose ?? "",
      sideEffect: (spec.sideEffect ?? "").trim(),
    });
    clusters.set(name, bucket);
  }
  const clusterCount = clusters.size;
  const toolCount = [...clusters.values()].reduce((sum, bucket) => sum + bucket.length, 0);
  const decisionNoteFor = (meta: StepMeta): string =>
    meta.decisionSurface && toolCount > 0
      ? `The route from here is decided at runtime by the user's intent — the model chooses from ${toolCount} capabilities across ${clusterCount} cluster${clusterCount === 1 ? "" : "s"}. The task mix is not enumerable from code.`
      : "";

  for (const meta of stepMetas.values()) {
    push(meta.id, {
      actor: meta.actor,
      condition: cardCondition.get(meta.id) ?? "",
      contract: meta.contract,
      decisionNote: decisionNoteFor(meta),
      inputs: inputsOf.get(meta.id) ?? [],
      kind: "step",
      label: meta.label,
      mayUse: capabilitiesOf(meta),
      ...commonContext(meta.modelContexts),
      outputs: outputsOf.get(meta.id) ?? [],
      pathNames: paths.filter((p) => meta.pathIds.has(p.id)).map((p) => p.name || p.id),
    });
  }

  // A model invocation's may_use tools hang off its output as a capability
  // satellite; the dashed return edge is the results feeding back into the
  // same invocation for another round.
  const loopLabelProps = {
    labelBgPadding: [4, 2] as [number, number],
    labelBgStyle: { fill: "var(--card)", fillOpacity: 0.9 },
    labelStyle: { fill: "var(--muted-foreground)", fontSize: 10 },
  };
  for (const meta of stepMetas.values()) {
    if (meta.actor !== "model" || meta.mayUse.size === 0) continue;
    const capsId = `caps:${meta.id}`;
    nodePathIds.set(capsId, new Set(meta.pathIds));
    push(capsId, { forLabel: meta.label, kind: "capabilities", tools: capabilitiesOf(meta) });
    edges.push({
      id: `${meta.id}->${capsId}`,
      label: "tool_calls",
      ...loopLabelProps,
      source: meta.id,
      style: baseEdgeStyle,
      target: capsId,
      type: "smoothstep",
    });
    edges.push({
      id: `${capsId}->${meta.id}`,
      label: "results",
      ...loopLabelProps,
      source: capsId,
      style: { ...baseEdgeStyle, strokeDasharray: "4 4" },
      target: meta.id,
      type: "smoothstep",
    });
  }

  // A decision-surface invocation's option space is the WHOLE tool_spec: one
  // SHARED satellite per semantic cluster (the option space belongs to the
  // agent's tool surface, not to any single invocation), with every decision
  // invocation looping tool_calls → results through it. Code binds no subset
  // here, so there is no may_use.
  const mixOf = (tools: ClusterTool[]): string => {
    const counts = new Map<string, number>();
    for (const tool of tools) {
      const effect = tool.sideEffect || "none";
      counts.set(effect, (counts.get(effect) ?? 0) + 1);
    }
    return [...counts].map(([effect, count]) => `${count} ${effect}`).join(" · ");
  };
  const decisionSteps = [...stepMetas.values()].filter((m) => m.decisionSurface);
  if (decisionSteps.length > 0) {
    for (const [name, tools] of clusters) {
      const clusterId = `cluster:${slug(name)}`;
      nodePathIds.set(clusterId, new Set(decisionSteps.flatMap((m) => [...m.pathIds])));
      push(clusterId, {
        forLabel: decisionSteps[0].label,
        kind: "cluster",
        name,
        sideEffectMix: mixOf(tools),
        tools,
      });
      for (const meta of decisionSteps) {
        edges.push({
          id: `${meta.id}->${clusterId}`,
          label: "tool_calls",
          ...loopLabelProps,
          source: meta.id,
          style: baseEdgeStyle,
          target: clusterId,
          type: "smoothstep",
        });
        edges.push({
          id: `${clusterId}->${meta.id}`,
          label: "results",
          ...loopLabelProps,
          source: clusterId,
          style: { ...baseEdgeStyle, strokeDasharray: "4 4" },
          target: meta.id,
          type: "smoothstep",
        });
      }
    }
  }

  for (const path of paths) {
    push(`terminal-${path.id}`, {
      claim: path.claim ?? "code_path",
      kind: "terminal",
      pathId: path.id,
      pathName: path.name || path.id,
      promptQuote: path.promptQuote ?? "",
      provenance: path.provenance,
      routing: pathsWithUnique.has(path.id) ? "" : path.routing,
      terminalDescription: path.terminal.description,
      terminalKind: path.terminal.kind,
      tools: path.tools,
      verified: path.verified ?? false,
    });
  }

  // Only strictly forward chain edges constrain the layout: back-edges (a
  // node re-entered later in a chain) still render but would form cycles
  // that scramble ELK's layering.
  const forward = new Set<string>();
  for (const chain of chains.values()) {
    const firstIndex = new Map<string, number>();
    chain.forEach((id, i) => {
      if (!firstIndex.has(id)) firstIndex.set(id, i);
    });
    for (let i = 0; i < chain.length - 1; i++) {
      if (firstIndex.get(chain[i + 1])! > firstIndex.get(chain[i])!) {
        forward.add(`${chain[i]}->${chain[i + 1]}`);
      }
    }
  }

  return {
    edges,
    nodes,
    relayout: async (measuredNodes) => {
      const next = measuredNodes.map((node) => ({ ...node }));
      await layout(next, edges, forward);
      return next;
    },
  };
};

const isSatellite = (node: TrajectoryNode): boolean =>
  node.data.kind === "capabilities" || node.data.kind === "cluster";

const elk = new ELK();

const LAYOUT_OPTIONS = {
  "elk.algorithm": "layered",
  "elk.direction": "RIGHT",
  // Keep layers/rows following path insertion order instead of re-shuffling.
  "elk.layered.considerModelOrder.strategy": "NODES_AND_EDGES",
  "elk.layered.spacing.nodeNodeBetweenLayers": "120",
  "elk.spacing.nodeNode": String(ROW_GAP),
};

/** ELK layered layout, left→right, fed a pure DAG: forward chain edges plus
 * one anchor edge per satellite group. Satellites (capabilities, clusters)
 * are grouped per owner set and pre-packed into wrapped lanes, then fed to
 * ELK as one box per group: ELK places the box one layer right of its
 * owner(s) and a big orbit wraps instead of growing into a tower. Loop
 * returns and diverges annotations render over the result untouched. */
const layout = async (
  nodes: TrajectoryNode[],
  edges: Edge[],
  forward: Set<string>
): Promise<void> => {
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const heightOf = (n: TrajectoryNode) => n.measured?.height ?? estimateHeight(n.data);
  const widthOf = (n: TrajectoryNode) => n.width ?? NODE_WIDTH[n.data.kind];

  const owners = new Map<string, string[]>();
  for (const edge of edges) {
    const source = byId.get(edge.source);
    const target = byId.get(edge.target);
    if (!source || !target || !isSatellite(target) || isSatellite(source)) continue;
    const list = owners.get(target.id) ?? [];
    if (!list.includes(source.id)) list.push(source.id);
    owners.set(target.id, list);
  }
  const groupOf = new Map<string, string>();
  const groups = new Map<string, TrajectoryNode[]>();
  for (const node of nodes) {
    if (!isSatellite(node)) continue;
    const key = `sat:${[...(owners.get(node.id) ?? [node.id])].sort().join("|")}`;
    groupOf.set(node.id, key);
    const bucket = groups.get(key) ?? [];
    bucket.push(node);
    groups.set(key, bucket);
  }

  type Pack = { offsets: Map<string, { dx: number; dy: number }>; width: number; height: number };
  const packs = new Map<string, Pack>();
  for (const [key, sats] of groups) {
    const offsets = new Map<string, { dx: number; dy: number }>();
    let dx = 0;
    let dy = 0;
    let laneWidth = 0;
    let height = 0;
    for (const sat of sats) {
      const h = heightOf(sat);
      if (dy > 0 && dy + h > MAX_LANE_HEIGHT) {
        dx += laneWidth + LANE_GAP;
        dy = 0;
        laneWidth = 0;
      }
      offsets.set(sat.id, { dx, dy });
      laneWidth = Math.max(laneWidth, widthOf(sat));
      dy += h + ROW_GAP;
      height = Math.max(height, dy - ROW_GAP);
    }
    packs.set(key, { height, offsets, width: dx + laneWidth });
  }

  const seen = new Set<string>();
  const elkEdges: { id: string; sources: string[]; targets: string[] }[] = [];
  for (const edge of edges) {
    const source = byId.get(edge.source);
    const target = byId.get(edge.target);
    if (!source || !target || isSatellite(source)) continue;
    if (!isSatellite(target) && !forward.has(edge.id)) continue;
    const targetId = groupOf.get(target.id) ?? target.id;
    if (source.id === targetId) continue;
    const id = `${source.id}->${targetId}`;
    if (seen.has(id)) continue;
    seen.add(id);
    elkEdges.push({ id, sources: [source.id], targets: [targetId] });
  }

  const laid = await elk.layout({
    children: [
      ...nodes
        .filter((n) => !isSatellite(n))
        .map((n) => ({ height: heightOf(n), id: n.id, width: widthOf(n) })),
      ...[...packs].map(([key, pack]) => ({ height: pack.height, id: key, width: pack.width })),
    ],
    edges: elkEdges,
    id: "root",
    layoutOptions: LAYOUT_OPTIONS,
  });

  for (const child of laid.children ?? []) {
    const pack = packs.get(child.id);
    if (pack) {
      for (const [id, { dx, dy }] of pack.offsets) {
        byId.get(id)!.position = { x: (child.x ?? 0) + dx, y: (child.y ?? 0) + dy };
      }
    } else {
      byId.get(child.id)!.position = { x: child.x ?? 0, y: child.y ?? 0 };
    }
  }
};

/** Upstream subgraph of `focusId`: every node and edge on a chain that leads
 * into it (the focused node included). */
export const collectAncestors = (
  graph: TrajectoryGraph,
  focusId: string
): { nodeIds: Set<string>; edgeIds: Set<string> } => {
  const incoming = new Map<string, Edge[]>();
  for (const edge of graph.edges) {
    const bucket = incoming.get(edge.target) ?? [];
    bucket.push(edge);
    incoming.set(edge.target, bucket);
  }
  const nodeIds = new Set([focusId]);
  const edgeIds = new Set<string>();
  const queue = [focusId];
  while (queue.length) {
    for (const edge of incoming.get(queue.pop()!) ?? []) {
      edgeIds.add(edge.id);
      if (!nodeIds.has(edge.source)) {
        nodeIds.add(edge.source);
        queue.push(edge.source);
      }
    }
  }
  return { edgeIds, nodeIds };
};
