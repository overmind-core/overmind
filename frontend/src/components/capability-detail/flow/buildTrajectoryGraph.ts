import { type Edge, MarkerType, type Node } from "@xyflow/react";
import ELK, { type ElkNode } from "elkjs/lib/elk.bundled.js";

import type {
  CapabilityFlow,
  CapabilityFlowTool,
  CapabilityFlowToolBranch,
  CapabilityFlowTrajectoryStep,
  CapabilityFlowTransition,
  EvalMatrix,
} from "@/openapi";
import { bindTrajectoryEvaluations } from "./bindTrajectoryEvaluations";
import { packTrajectoryBlocks } from "./trajectoryPacking";
import {
  CONDITION_HEIGHT,
  CONDITION_SPACE,
  CONDITION_WIDTH,
  crossingPoints,
  outcomeRoute,
  routeAroundBoxes,
} from "./trajectoryRouting";

export type BranchControls = {
  collapsed?: boolean;
  childCount?: number;
  onToggle?: (id: string) => void;
};

export type EntryNodeData = BranchControls & {
  kind: "entry";
  capabilityName: string;
  task: string;
  pathCount: number;
  entrypoint?: string;
  evaluations?: EvalMatrix[];
};

type InvocationContract = { input: string; action: string; output: string };
type ModelContext = Pick<StepNodeData, "model" | "systemPrompt" | "promptIsExcerpt">;

export type StepNodeData = BranchControls & {
  kind: "step";
  label: string;
  pathNames: string[];
  inputs: string[];
  outputs: { label: string; condition: string }[];
  condition: string;
  actor: "agent" | "model";
  model: string;
  modelResolution?: CapabilityFlow["modelResolution"];
  systemPrompt: string;
  promptIsExcerpt: boolean;
  /** Null when the scan did not capture an execution contract. */
  contract: InvocationContract | null;
  hasActions: boolean;
  anchors?: string[];
  evaluations?: EvalMatrix[];
};

export type EvaluationScopesNodeData = {
  kind: "evaluations";
  broad: EvalMatrix[];
  unmapped: EvalMatrix[];
};

export type TerminalNodeData = {
  kind: "terminal";
  pathName: string;
  routing: string;
  terminalKind: string;
  terminalDescription: string;
  recordedOutput?: string;
};

export type ActionGroupNodeData = BranchControls & {
  kind: "actionGroup";
  label: string;
  ownerId: string;
  modelSelected?: boolean;
};
export type ActionNodeData = BranchControls & {
  kind: "action";
  tool: CapabilityFlowTool;
  groupId: string;
  modelSelected?: boolean;
  when: string;
  expanded: boolean;
  evaluations: EvalMatrix[];
};
export type OutcomeNodeData = {
  kind: "outcome";
  branch: CapabilityFlowToolBranch | null;
  actionId: string;
  groupId: string;
  returns?: string;
  recordedInvocations?: number;
  continues?: boolean;
};

export type TrajectoryRoute = {
  crossings?: { x: number; y: number }[];
  points: { x: number; y: number }[];
  label?: { x: number; y: number };
};

type TrajectoryNodeData = (
  | EntryNodeData
  | ActionGroupNodeData
  | ActionNodeData
  | OutcomeNodeData
  | StepNodeData
  | EvaluationScopesNodeData
  | TerminalNodeData
) & {
  routes?: Record<string, TrajectoryRoute>;
  layoutPorts?: Partial<
    Record<
      "in" | "out" | "return" | "retry" | "actions" | "outcomes" | "returnOut",
      { x: number; y: number }
    >
  >;
};
export type TrajectoryNode = Node<TrajectoryNodeData>;
export type TrajectoryGraph = {
  /** Unpositioned (0,0) — run `relayout` for placement. */
  nodes: TrajectoryNode[];
  edges: Edge[];
  /** ELK placement (async). Uses `node.measured.height` when present, else
   * `estimateHeight` — call once with estimates for first paint, again after
   * React Flow measures the cards. Returns fresh nodes; input untouched. */
  relayout: (measuredNodes: TrajectoryNode[], visibleEdges?: Edge[]) => Promise<TrajectoryNode[]>;
};

const ROW_GAP = 56;
const NODE_WIDTH = 320;

const evaluationHeight = (rows: EvalMatrix[] = []) => (rows.length ? 24 + rows.length * 58 : 28);
const estimateHeight = (data: TrajectoryNodeData): number => {
  switch (data.kind) {
    case "action":
      return 340;
    case "actionGroup":
      return 72;
    case "outcome":
      return 220;
    case "entry":
      return 170;
    case "step":
      return 450;
    case "terminal":
      return 160;
    case "evaluations":
      return 120 + evaluationHeight(data.broad) + evaluationHeight(data.unmapped);
  }
};

const norm = (value: string): string => value.trim().toLowerCase().replace(/\s+/g, " ");

const ENTRY_ID = "entry";

const baseEdgeStyle = { stroke: "var(--muted-foreground)", strokeOpacity: 0.7, strokeWidth: 1.5 };

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
  transitions: CapabilityFlowTransition[];
  anchors: Set<string>;
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

// Shared steps deduplicate by source anchors, execution contract and conditional tools.
export const buildTrajectoryGraph = (
  flow: CapabilityFlow,
  capabilityName: string,
  evaluations: EvalMatrix[] = [],
  entrypoint = ""
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

  const stepKey = (step: CapabilityFlowTrajectoryStep) =>
    JSON.stringify([
      norm(step.step ?? ""),
      (step.anchors ?? []).slice().sort(),
      step.kind ?? "agent_step",
      step.input ?? "",
      step.action ?? "",
      step.output ?? "",
      step.transitions ?? [],
      (step.mayUse ?? []).map(({ tool, when }) => [tool, when ?? ""]).sort(),
    ]);
  const sharedKeyPaths = new Map<string, Set<string>>();
  for (const p of paths) {
    for (const step of backbones.get(p.id)!) {
      const key = stepKey(step);
      const bucket = sharedKeyPaths.get(key) ?? new Set();
      bucket.add(p.id);
      sharedKeyPaths.set(key, bucket);
    }
  }

  const stepMetas = new Map<string, StepMeta>();
  const chains = new Map<string, string[]>();

  const sharedIds = new Map<string, string>();
  const stepIdFor = (pathId: string, step: CapabilityFlowTrajectoryStep, index: number): string => {
    const key = stepKey(step);
    const shared = (sharedKeyPaths.get(key)?.size ?? 0) > 1;
    if (!shared) return `step:${pathId}:${index}`;
    if (!sharedIds.has(key)) sharedIds.set(key, `shared:${pathId}:${index}`);
    return sharedIds.get(key)!;
  };

  for (const path of paths) {
    const chain = [ENTRY_ID];
    const extend = (id: string) => {
      if (chain[chain.length - 1] !== id) chain.push(id);
    };
    backbones.get(path.id)!.forEach((step, index) => {
      const label = (step.step ?? "").trim();
      const id = stepIdFor(path.id, step, index);
      const isModel = step.kind === "model_invocation";
      const meta: StepMeta = stepMetas.get(id) ?? {
        actor: isModel ? ("model" as const) : ("agent" as const),
        anchors: new Set<string>(),
        contract: null,
        decisionSurface: false,
        id,
        label,
        mayUse: new Map<string, string>(),
        modelContexts: [],
        pathIds: new Set<string>(),
        transitions: step.transitions ?? [],
      };
      for (const anchor of step.anchors ?? []) meta.anchors.add(anchor);
      if (isModel) meta.modelContexts.push(modelContextFor(step));
      if (!meta.contract && (isModel || step.input || step.action || step.output)) {
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
      if (stepMetas.get(chain[i])?.transitions.length) continue;
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
      label: routing || undefined,
      source: meta.source,
      style: baseEdgeStyle,
      target: meta.target,
      type: "condition",
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
        source,
        style: { ...baseEdgeStyle, strokeDasharray: "4 4", strokeOpacity: 0.35 },
        target,
        type: "condition",
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
      width: NODE_WIDTH,
    });

  const actionGroups: {
    id: string;
    ownerId: string;
    label: string;
    tools: { id: string; tool: CapabilityFlowTool; when: string }[];
  }[] = [];
  for (const meta of stepMetas.values()) {
    const options = meta.decisionSurface
      ? (flow.toolSpec ?? [])
      : [...meta.mayUse].map(
          ([name]) =>
            (flow.toolSpec ?? []).find((tool) => tool.name === name) ?? {
              args: "",
              name,
              purpose: "",
            }
        );
    const grouped = new Map<string, typeof options>();
    for (const tool of options) {
      const label = tool.cluster?.trim() || "Other actions";
      grouped.set(label, [...(grouped.get(label) ?? []), tool]);
    }
    for (const [label, tools] of grouped) {
      actionGroups.push({
        id: `actions:${meta.id}:${label}`,
        label,
        ownerId: meta.id,
        tools: tools.map((tool) => ({
          id: `tool:${meta.id}:${tool.name}`,
          tool,
          when: meta.mayUse.get(tool.name) ?? "",
        })),
      });
    }
  }

  const bindings = bindTrajectoryEvaluations(
    evaluations,
    entrypoint,
    [...stepMetas.values()]
      .map((meta) => ({ anchors: [...meta.anchors], id: meta.id }))
      .concat(
        actionGroups.flatMap((group) =>
          group.tools.map(({ id, tool }) => ({ anchors: [tool.name], id }))
        )
      )
  );
  push(ENTRY_ID, {
    capabilityName,
    entrypoint,
    evaluations: bindings.byNode.get(ENTRY_ID) ?? [],
    kind: "entry",
    pathCount: paths.length,
    task: flow.task ?? "",
  });

  for (const meta of stepMetas.values()) {
    push(meta.id, {
      actor: meta.actor,
      anchors: [...meta.anchors],
      condition: cardCondition.get(meta.id) ?? "",
      contract: meta.contract,
      evaluations: bindings.byNode.get(meta.id) ?? [],
      hasActions: actionGroups.some((group) => group.ownerId === meta.id),
      inputs: inputsOf.get(meta.id) ?? [],
      kind: "step",
      label: meta.label,
      ...commonContext(meta.modelContexts),
      modelResolution: meta.actor === "model" ? flow.modelResolution : undefined,
      outputs: outputsOf.get(meta.id) ?? [],
      pathNames: paths.filter((p) => meta.pathIds.has(p.id)).map((p) => p.name || p.id),
    });
  }

  const connect = (source: string, target: string, sourceHandle?: string) =>
    edges.push({
      id: `${source}->${target}`,
      source,
      sourceHandle,
      style: { ...baseEdgeStyle, strokeDasharray: "4 4" },
      target,
      type: "condition",
    });
  const targetsFor = (owner: StepMeta, target: string) => {
    if (target === "terminal") return [...owner.pathIds].map((id) => `terminal-${id}`);
    const candidates = [...stepMetas.values()].filter(
      (meta) => meta.anchors.has(target) && [...meta.pathIds].some((id) => owner.pathIds.has(id))
    );
    return candidates.length === 1 ? [candidates[0].id] : [];
  };
  const returnEdge = (source: string, target: string, condition: string, groupId?: string) => {
    edges.push({
      data: { groupId, relation: "continuation" },
      id: `return:${source}->${target}`,
      label: condition,
      markerEnd: { color: "var(--muted-foreground)", type: MarkerType.ArrowClosed },
      source,
      sourceHandle: source.startsWith("outcome:") ? "outcomes" : undefined,
      style: baseEdgeStyle,
      target,
      targetHandle: target.startsWith("terminal-") ? undefined : "return",
      type: "continuation",
    });
  };
  for (const meta of stepMetas.values()) {
    for (const transition of meta.transitions)
      for (const target of targetsFor(meta, transition.target)) {
        const existing = edges.find((edge) => edge.source === meta.id && edge.target === target);
        if (existing) {
          existing.label = transition.condition;
          existing.type = "condition";
        } else {
          const chain = chains.get([...meta.pathIds][0])!;
          const backward = chain.indexOf(target) <= chain.indexOf(meta.id);
          edges.push({
            data: backward ? { relation: "continuation" } : undefined,
            id: `${meta.id}->${target}`,
            label: transition.condition,
            markerEnd: { color: "var(--muted-foreground)", type: MarkerType.ArrowClosed },
            source: meta.id,
            style: backward
              ? { ...baseEdgeStyle, strokeDasharray: "5 5", strokeOpacity: 0.5 }
              : baseEdgeStyle,
            target,
            targetHandle: backward ? "retry" : undefined,
            type: "condition",
          });
        }
      }
  }
  for (const group of actionGroups) {
    const modelSelected = stepMetas.get(group.ownerId)!.decisionSurface;
    push(group.id, {
      kind: "actionGroup",
      label: group.label,
      modelSelected,
      ownerId: group.ownerId,
    });
    connect(group.ownerId, group.id, "actions");
    edges[edges.length - 1].style = {
      ...baseEdgeStyle,
      stroke: modelSelected ? "var(--cat-4)" : "var(--cat-3)",
      strokeDasharray: "5 5",
    };
    for (const { id, tool, when } of group.tools) {
      push(id, {
        evaluations: bindings.byNode.get(id) ?? [],
        expanded: true,
        groupId: group.id,
        kind: "action",
        modelSelected,
        tool,
        when,
      });
      connect(group.id, id);
      edges[edges.length - 1].data = { relation: "available-tool" };
      (tool.branches?.length ? tool.branches : [null]).forEach((branch, index) => {
        const outcomeId = `outcome:${id}:${index}`;
        const destinations = (branch?.transitions ?? []).flatMap((transition) =>
          targetsFor(stepMetas.get(group.ownerId)!, transition.target).map((target) => ({
            condition: transition.condition,
            target,
          }))
        );
        push(outcomeId, {
          actionId: id,
          branch,
          continues: destinations.length > 0,
          groupId: group.id,
          kind: "outcome",
          returns: tool.returns,
        });
        connect(id, outcomeId, "outcomes");
        const edge = edges[edges.length - 1];
        edge.type = "condition";
        edge.data = { basis: branch?.basis, relation: "tool-outcome" };
        edge.style = {
          ...baseEdgeStyle,
          stroke: branch?.basis === "code" ? "var(--cat-1)" : "var(--cat-6)",
          strokeDasharray: branch?.basis === "code" ? undefined : "5 5",
        };
        edge.label = branch?.condition || "Condition not captured";
        for (const destination of destinations)
          returnEdge(outcomeId, destination.target, destination.condition, group.id);
      });
    }
  }

  for (const path of paths) {
    push(`terminal-${path.id}`, {
      kind: "terminal",
      pathName: path.name || path.id,
      routing: pathsWithUnique.has(path.id) ? "" : path.routing,
      terminalDescription: path.terminal.description,
      terminalKind: path.terminal.kind,
    });
  }

  if (bindings.broad.length || bindings.unmapped.length) {
    push("evaluation-scopes", {
      broad: bindings.broad,
      kind: "evaluations",
      unmapped: bindings.unmapped,
    });
  }

  return {
    edges,
    nodes,
    relayout: async (measuredNodes, visibleEdges = edges) => {
      const next = measuredNodes.map((node) => ({ ...node }));
      await layout(next, visibleEdges);
      return next;
    },
  };
};

const elk = new ELK();
const layoutCandidate = async (
  nodes: TrajectoryNode[],
  edges: Edge[],
  verticalTools: boolean,
  singleRow: boolean
): Promise<void> => {
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const heightOf = (n: TrajectoryNode) => n.measured?.height ?? estimateHeight(n.data);
  const scopes = byId.get("evaluation-scopes");
  const backbone = nodes.filter((n) => ["entry", "step", "terminal"].includes(n.data.kind));
  const backboneIds = new Set(backbone.map((n) => n.id));
  const backboneEdges = edges.filter((e) => backboneIds.has(e.source) && backboneIds.has(e.target));
  const laid = await elk.layout<ElkNode>({
    children: backbone.map((n) => ({
      height: heightOf(n) + (n.id === ENTRY_ID && scopes ? ROW_GAP + heightOf(scopes) : 0),
      id: n.id,
      layoutOptions: { "elk.portConstraints": "FIXED_POS" },
      ports: [
        {
          height: 0,
          id: `${n.id}:out`,
          layoutOptions: { "elk.port.side": "EAST" },
          width: 0,
          x: n.data.layoutPorts?.out?.x ?? NODE_WIDTH,
          y: n.data.layoutPorts?.out?.y ?? heightOf(n) / 2,
        },
        {
          height: 0,
          id: `${n.id}:in`,
          layoutOptions: { "elk.port.side": "WEST" },
          width: 0,
          x: n.data.layoutPorts?.in?.x ?? 0,
          y: n.data.layoutPorts?.in?.y ?? heightOf(n) / 2,
        },
        {
          height: 0,
          id: `${n.id}:retry`,
          layoutOptions: { "elk.port.side": "NORTH" },
          width: 0,
          x: n.data.layoutPorts?.retry?.x ?? NODE_WIDTH / 2,
          y: n.data.layoutPorts?.retry?.y ?? 0,
        },
        {
          height: 0,
          id: `${n.id}:return`,
          layoutOptions: { "elk.port.side": "SOUTH" },
          width: 0,
          x: n.data.layoutPorts?.return?.x ?? NODE_WIDTH / 2,
          y: n.data.layoutPorts?.return?.y ?? heightOf(n),
        },
      ],
      width: n.width ?? NODE_WIDTH,
    })),
    edges: backboneEdges.map((e) => ({
      id: e.id,
      layoutOptions: {
        "elk.layered.priority.straightness":
          backbone.findIndex((n) => n.id === e.target) ===
          backbone.findIndex((n) => n.id === e.source) + 1
            ? "100"
            : "0",
      },
      sources: [`${e.source}:out`],
      targets: [`${e.target}:${e.targetHandle || "in"}`],
      ...(e.label
        ? {
            labels: [
              {
                height: CONDITION_HEIGHT,
                layoutOptions: {
                  "elk.edgeLabels.inline": "true",
                  "elk.edgeLabels.placement": "CENTER",
                },
                text: String(e.label),
                width: CONDITION_WIDTH,
              },
            ],
          }
        : {}),
    })),
    id: "root",
    layoutOptions: {
      "elk.algorithm": "layered",
      "elk.direction": "RIGHT",
      "elk.edgeRouting": "ORTHOGONAL",
      "elk.layered.considerModelOrder.strategy": "NODES_AND_EDGES",
      "elk.layered.cycleBreaking.strategy": "MODEL_ORDER",
      "elk.layered.feedbackEdges": "true",
      "elk.layered.layering.strategy": "LONGEST_PATH_SOURCE",
      "elk.layered.nodePlacement.strategy": "NETWORK_SIMPLEX",
      "elk.layered.spacing.edgeEdgeBetweenLayers": "24",
      "elk.layered.spacing.edgeNodeBetweenLayers": "32",
      "elk.layered.spacing.nodeNodeBetweenLayers": "36",
      "elk.spacing.edgeEdge": "24",
      "elk.spacing.edgeNode": "32",
      "elk.spacing.nodeNode": String(ROW_GAP),
    },
  });
  for (const child of laid.children ?? [])
    byId.get(child.id)!.position = { x: child.x ?? 0, y: child.y ?? 0 };
  for (const node of nodes) node.data = { ...node.data, routes: {} };
  for (const edge of laid.edges ?? []) {
    const original = backboneEdges.find((e) => e.id === edge.id)!;
    const section = edge.sections?.[0];
    if (!section) continue;
    const label = edge.labels?.[0];
    let points = [section.startPoint, ...(section.bendPoints ?? []), section.endPoint];
    const source = byId.get(original.source)!,
      target = byId.get(original.target)!;
    const sourcePort = source.data.layoutPorts?.out;
    const targetPort =
      target.data.layoutPorts?.[
        original.targetHandle === "retry"
          ? "retry"
          : original.targetHandle === "return"
            ? "return"
            : "in"
      ];
    // ELK clamps fixed ports to the card; React Flow handles protrude beyond it.
    for (const [from, port, node] of [
      [points[0], sourcePort, source],
      [points.at(-1)!, targetPort, target],
    ] as const) {
      if (!port) continue;
      const to = { x: node.position.x + port.x, y: node.position.y + port.y };
      points = points.map((p) => ({
        x: p.x === from.x ? to.x : p.x,
        y: p.y === from.y ? to.y : p.y,
      }));
    }
    // ELK positions self-loop text beside its top leg even with inline labels.
    if (original.source === original.target && label) {
      const top = Math.min(...points.map((p) => p.y));
      for (const point of points)
        if (point.y === top) point.y = (label.y ?? 0) + CONDITION_HEIGHT / 2;
    }
    byId.get(original.source)!.data.routes![edge.id] = {
      points,
      ...(label ? { label: { x: label.x ?? 0, y: label.y ?? 0 } } : {}),
    };
  }
  const labelBoxes = backbone.flatMap((n) =>
    Object.values(n.data.routes ?? {}).flatMap((r) =>
      r.label ? [{ ...r.label, height: CONDITION_HEIGHT, width: CONDITION_WIDTH }] : []
    )
  );
  if (scopes) {
    scopes.position = {
      x: byId.get(ENTRY_ID)?.position.x ?? 0,
      y: (byId.get(ENTRY_ID)?.position.y ?? 0) + heightOf(byId.get(ENTRY_ID)!) + ROW_GAP,
    };
  }
  // Tool → outcomes is the atomic layout block. Pack blocks into families,
  // then families around their owner, rather than growing one unbounded column.
  const groups = nodes.filter((n) => n.data.kind === "actionGroup");
  const returnRows = new Map<string, number>();
  const clusters = groups.map((group) => {
    const members = [group];
    const actions = nodes.filter((n) => n.data.kind === "action" && n.data.groupId === group.id);
    const blocks = actions.map((action) => {
      const outcomes = nodes.filter(
        (n) => n.data.kind === "outcome" && n.data.actionId === action.id
      );
      const width = Math.max(NODE_WIDTH, outcomes.length * (NODE_WIDTH + ROW_GAP) - ROW_GAP);
      const outcomeY = heightOf(action) + 24 + CONDITION_SPACE;
      return {
        action,
        height: outcomes.length ? outcomeY + Math.max(...outcomes.map(heightOf)) : heightOf(action),
        outcomes,
        outcomeY,
        width,
      };
    });
    const packed = packTrajectoryBlocks(blocks, 120, verticalTools ? 1 : undefined);
    const width = Math.max(NODE_WIDTH, packed.width);
    group.position = { x: (width - NODE_WIDTH) / 2, y: 0 };
    const offset = heightOf(group) + 80;
    blocks.forEach((block, i) => {
      const { x, y, rowBottom } = packed.items[i];
      block.action.position = { x, y: y + offset };
      members.push(block.action);
      returnRows.set(block.action.id, rowBottom + offset + 32);
      block.outcomes.forEach((outcome, j) => {
        outcome.position = {
          x: x + j * (NODE_WIDTH + ROW_GAP),
          y: y + offset + block.outcomeY,
        };
        members.push(outcome);
        returnRows.set(outcome.id, rowBottom + offset + 32);
      });
    });
    return {
      group,
      height: actions.length ? offset + packed.height + 64 : heightOf(group),
      members,
      width,
    };
  });
  const groupRegions = new Map<string, { x: number; y: number; width: number; height: number }>();
  const executionCorridors = backbone.flatMap((n) =>
    Object.values(n.data.routes ?? {}).flatMap((r) => [
      ...r.points.slice(1).map((p, i) => ({
        height: Math.abs(p.y - r.points[i].y) + 32,
        width: Math.abs(p.x - r.points[i].x) + 32,
        x: Math.min(p.x, r.points[i].x) - 16,
        y: Math.min(p.y, r.points[i].y) - 16,
      })),
      ...(r.label ? [{ ...r.label, height: CONDITION_HEIGHT, width: CONDITION_WIDTH }] : []),
    ])
  );
  const placedRegions: { x: number; y: number; width: number; height: number }[] = [];
  for (const ownerId of new Set(groups.map((n) => (n.data as ActionGroupNodeData).ownerId))) {
    const owner = byId.get(ownerId)!;
    const owned = clusters
      .filter((c) => (c.group.data as ActionGroupNodeData).ownerId === ownerId)
      .sort((a, b) => b.members.length - a.members.length);
    const packed = packTrajectoryBlocks(
      owned,
      owned.every((c) => c.members.length === 1) ? 80 : 160,
      singleRow ? owned.length : undefined
    );
    const x = Math.max(0, owner.position.x + NODE_WIDTH / 2 - packed.width / 2);
    const obstacles = [...backbone, ...(scopes ? [scopes] : [])]
      .map((n) => ({
        height: heightOf(n),
        width: NODE_WIDTH,
        x: n.position.x,
        y: n.position.y,
      }))
      .concat(placedRegions, executionCorridors);
    const y =
      Math.max(
        owner.position.y + heightOf(owner),
        ...obstacles
          .filter((b) => b.x < x + packed.width && b.x + b.width > x)
          .map((b) => b.y + b.height)
      ) + 140;
    owned.forEach((cluster, i) => {
      const at = packed.items[i];
      groupRegions.set(cluster.group.id, {
        height: cluster.height,
        width: cluster.width,
        x: x + at.x,
        y: y + at.y,
      });
      for (const member of cluster.members) {
        member.position = { x: member.position.x + x + at.x, y: member.position.y + y + at.y };
        if (returnRows.has(member.id))
          returnRows.set(member.id, returnRows.get(member.id)! + y + at.y);
      }
    });
    placedRegions.push({ height: packed.height, width: packed.width, x, y });
  }
  // All visible edges share one geometry pass, so crossings include tool lanes as well as steps.
  const endpoint = (node: TrajectoryNode, source: boolean, handle?: string | null) => {
    const key = source ? (handle === "return" ? "returnOut" : handle || "out") : handle || "in";
    const measured =
      node.data.layoutPorts?.[key as keyof NonNullable<typeof node.data.layoutPorts>];
    const width = NODE_WIDTH,
      height = heightOf(node);
    const bottom =
      (source &&
        (handle === "outcomes" ||
          handle === "actions" ||
          (!handle && node.data.kind === "actionGroup"))) ||
      (!source && handle === "return");
    const top =
      !source &&
      (handle === "retry" || node.data.kind === "outcome" || node.data.kind === "actionGroup");
    const offset = measured ?? {
      x: bottom || top ? width / 2 : source ? width : 0,
      y: top ? 0 : bottom ? height : height / 2,
    };
    return { x: node.position.x + offset.x, y: node.position.y + offset.y };
  };
  const visibleEdges = edges.filter((e) => byId.has(e.source) && byId.has(e.target));
  const globalObstacles = [...backbone, ...(scopes ? [scopes] : [])]
    .map((n) => ({
      height: heightOf(n),
      width: NODE_WIDTH,
      x: n.position.x,
      y: n.position.y,
    }))
    .concat([...groupRegions.values()], labelBoxes);
  const sharedReturns = new Map<string, { x: number; y: number }[]>();
  for (const edge of visibleEdges) {
    const source = byId.get(edge.source)!,
      target = byId.get(edge.target)!;
    if (source.data.routes?.[edge.id]) continue;
    const start = endpoint(source, true, edge.sourceHandle),
      end = endpoint(target, false, edge.targetHandle);
    let points = outcomeRoute({ source: start, target: end });
    if (source.data.kind === "step" && target.data.kind === "actionGroup") {
      points = [
        start,
        ...routeAroundBoxes(
          { x: start.x, y: start.y + 16 },
          { x: end.x, y: end.y - 16 },
          globalObstacles
        ),
        end,
      ];
    } else if (edge.data?.relation === "available-tool") {
      const x = target.position.x - 24;
      points = [
        start,
        { x: start.x, y: start.y + 24 },
        { x, y: start.y + 24 },
        { x, y: end.y },
        end,
      ];
    } else if (edge.data?.relation === "continuation") {
      const group = byId.get(String(edge.data?.groupId || source.id))!;
      const region = groupRegions.get(group.id)!;
      const right = region.x + region.width;
      const destinations = [
        ...new Set(
          visibleEdges
            .filter((e) => e.data?.relation === "continuation" && e.data?.groupId)
            .map((e) => e.target)
        ),
      ];
      const lane = Math.max(0, destinations.indexOf(edge.target));
      const x = right + 40 + lane * 20,
        y = group.position.y - 40 - lane * 24;
      const bottom = returnRows.get(source.id) ?? null;
      const key = `${group.id}:${edge.target}`;
      let shared = sharedReturns.get(key);
      if (!shared) {
        const enter =
          edge.targetHandle === "return"
            ? { x: end.x, y: end.y + 16 }
            : { x: end.x - 16, y: end.y };
        shared = [...routeAroundBoxes({ x, y }, enter, globalObstacles), end];
        sharedReturns.set(key, shared);
      }
      points = [
        start,
        ...(bottom === null
          ? [{ x, y: start.y }]
          : [
              { x: start.x, y: bottom },
              { x, y: bottom },
            ]),
        ...shared,
      ];
    } else if (
      edge.data?.relation !== "tool-outcome" &&
      Math.abs(start.x - end.x) > 1 &&
      Math.abs(start.y - end.y) > 1
    ) {
      points = [
        start,
        { x: (start.x + end.x) / 2, y: start.y },
        { x: (start.x + end.x) / 2, y: end.y },
        end,
      ];
    }
    source.data.routes![edge.id] = {
      points,
      ...(edge.data?.relation === "tool-outcome" && edge.label
        ? { label: { x: end.x - CONDITION_WIDTH / 2, y: end.y - CONDITION_HEIGHT - 28 } }
        : {}),
    };
  }
  const routes = visibleEdges.map((edge) => ({
    ...edge,
    ...byId.get(edge.source)!.data.routes![edge.id],
  }));
  const crossings = crossingPoints(routes);
  routes.forEach((edge, i) => {
    byId.get(edge.source)!.data.routes![edge.id].crossings = crossings[i];
  });
};

export function measureTrajectoryLayout(nodes: TrajectoryNode[], edges: Edge[]) {
  const rectangles = nodes.map((n) => ({
    id: n.id,
    ...n.position,
    height: n.measured?.height ?? estimateHeight(n.data),
    width: NODE_WIDTH,
  }));
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const routes = edges.flatMap((edge) => {
    const route = byId.get(edge.source)?.data.routes?.[edge.id];
    return route ? [{ id: edge.id, source: edge.source, target: edge.target, ...route }] : [];
  });
  const labels = routes.flatMap((r) =>
    r.label ? [{ id: r.id, ...r.label, height: CONDITION_HEIGHT, width: CONDITION_WIDTH }] : []
  );
  const overlaps = (a: (typeof rectangles)[number], b: (typeof rectangles)[number]) =>
    a.x < b.x + b.width && a.x + a.width > b.x && a.y < b.y + b.height && a.y + a.height > b.y;
  let collisions = 0;
  const boxes = [...rectangles, ...labels];
  boxes.forEach((a, i) => {
    for (const b of boxes.slice(i + 1)) if (overlaps(a, b)) collisions++;
  });
  for (const r of routes)
    for (let i = 1; i < r.points.length; i++) {
      const a = r.points[i - 1],
        b = r.points[i];
      for (const box of boxes) {
        if ([r.source, r.target, r.id].includes(box.id)) continue;
        if (
          a.x === b.x
            ? a.x > box.x &&
              a.x < box.x + box.width &&
              Math.max(a.y, b.y) > box.y &&
              Math.min(a.y, b.y) < box.y + box.height
            : a.y === b.y &&
              a.y > box.y &&
              a.y < box.y + box.height &&
              Math.max(a.x, b.x) > box.x &&
              Math.min(a.x, b.x) < box.x + box.width
        )
          collisions++;
      }
    }
  const crossings = new Set(routes.flatMap((r) => (r.crossings ?? []).map((p) => `${p.x}:${p.y}`)))
    .size;
  const length = routes.reduce(
    (sum, r) =>
      sum +
      r.points
        .slice(1)
        .reduce((s, p, i) => s + Math.abs(p.x - r.points[i].x) + Math.abs(p.y - r.points[i].y), 0),
    0
  );
  const width = Math.max(
    ...boxes.map((n) => n.x + n.width),
    ...routes.flatMap((r) => r.points.map((p) => p.x))
  );
  const height = Math.max(
    ...boxes.map((n) => n.y + n.height),
    ...routes.flatMap((r) => r.points.map((p) => p.y))
  );
  return { collisions, crossings, height, length, width };
}

/** Compare a bounded set of structural arrangements, using geometry rather
 * than screenshots. Crossings matter more than shaving a little canvas area. */
const layout = async (nodes: TrajectoryNode[], edges: Edge[]): Promise<void> => {
  const hasTools = nodes.some((n) => n.data.kind === "action");
  let best: { nodes: TrajectoryNode[]; score: number } | undefined;
  for (const vertical of hasTools ? [true, false] : [true])
    for (const singleRow of [true, false]) {
      const candidate = nodes.map((n) => ({
        ...n,
        data: { ...n.data },
        position: { ...n.position },
      }));
      await layoutCandidate(candidate, edges, vertical, singleRow);
      const { collisions, crossings, length, width, height } = measureTrajectoryLayout(
        candidate,
        edges
      );
      const score =
        collisions * 10000 +
        crossings * 4 +
        length / (320 * Math.max(1, edges.length)) +
        Math.sqrt(width * height) / 320;
      if (!best || score < best.score) best = { nodes: candidate, score };
    }
  nodes.splice(0, nodes.length, ...best!.nodes);
};

/** Upstream subgraph of `focusId`: every node and edge on a chain that leads
 * into it (the focused node included). */
export const collectAncestors = (
  graph: TrajectoryGraph,
  focusId: string
): { nodeIds: Set<string>; edgeIds: Set<string> } => {
  const incoming = new Map<string, Edge[]>();
  for (const edge of graph.edges) {
    if (edge.data?.relation === "continuation") continue;
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
