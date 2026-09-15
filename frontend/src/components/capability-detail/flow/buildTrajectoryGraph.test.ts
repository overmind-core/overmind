import { describe, expect, it, vi } from "vitest";

import type { CapabilityFlow } from "@/openapi";
import { buildTrajectoryGraph, collectAncestors } from "./buildTrajectoryGraph";

// ELK is a GWT-compiled browser bundle that reaches for a Web Worker vitest has
// not got. Layered placement is its job, not this module's — the stand-in lays
// boxes out by edge depth so the lanes this module packs stay measurable.
vi.mock("elkjs/lib/elk.bundled.js", () => {
  const LAYER_X = 200;
  const STACK_GAP = 60;
  type Box = { id: string; height?: number; width?: number };
  type Graph = {
    children?: Box[];
    edges?: { id: string; sources: string[]; targets: string[] }[];
  };
  return {
    default: class {
      layout(graph: Graph) {
        const children = graph.children ?? [];
        const edges = graph.edges ?? [];
        const depth = new Map(children.map((c) => [c.id, 0]));
        for (let pass = 0; pass < children.length; pass++) {
          for (const edge of edges) {
            const source = depth.get(edge.sources[0]) ?? 0;
            if ((depth.get(edge.targets[0]) ?? 0) < source + 1) {
              depth.set(edge.targets[0], source + 1);
            }
          }
        }
        const nextY = new Map<number, number>();
        return Promise.resolve({
          ...graph,
          children: children.map((child) => {
            const layer = depth.get(child.id) ?? 0;
            const y = nextY.get(layer) ?? 0;
            nextY.set(layer, y + (child.height ?? 0) + STACK_GAP);
            return { ...child, x: layer * LAYER_X, y };
          }),
        });
      }
    },
  };
});

const path = (overrides: Record<string, unknown>) => ({
  divergences: [],
  name: "",
  provenance: [],
  routing: "",
  steps: [],
  terminal: { description: "", kind: "emits_record" },
  tools: [],
  ...overrides,
});

const step = (label: string, overrides: Record<string, unknown> = {}) => ({
  anchors: [],
  kind: "agent_step",
  mayUse: [],
  step: label,
  ...overrides,
});

const SATELLITE_KINDS = new Set(["capabilities", "cluster"]);

describe("buildTrajectoryGraph", () => {
  const FLOW = {
    model: "claude-sonnet-4-5",
    task: "Operate the platform",
    toolSpec: [
      { args: "", name: "agent_health", purpose: "Health snapshot", sideEffect: "read" },
      { args: "", name: "get_trace", purpose: "One trace", sideEffect: "read" },
      { args: "", name: "suggest_navigation", purpose: "Destination card", sideEffect: "none" },
    ],
    trajectoryMap: [
      path({
        divergences: ["refuse-off-topic"],
        id: "answer-question",
        name: "Answer platform question",
        routing: "in-scope question",
        steps: [
          step("assemble turn context"),
          step("decide: gather evidence or answer", {
            action: "decide whether to call tools or answer",
            input: "history + question + tool schemas",
            kind: "model_invocation",
            mayUse: [
              { tool: "agent_health", when: "the question is about health" },
              { tool: "get_trace", when: "a failure needs drill-down" },
            ],
            output: "tool_calls -> dispatch; answer -> emit",
          }),
          step("emit answer"),
        ],
        terminal: { description: "OpenUI answer", kind: "emits_record" },
        tools: ["agent_health", "get_trace"],
      }),
      path({
        divergences: ["answer-question"],
        id: "refuse-off-topic",
        name: "Refuse off-topic",
        routing: "request is out of scope",
        steps: [step("assemble turn context"), step("reply with fixed refusal")],
        terminal: { description: "", kind: "returns_empty" },
      }),
    ],
  } as unknown as CapabilityFlow;
  const graph = buildTrajectoryGraph(FLOW, "Brain");
  const nodeById = new Map(graph.nodes.map((n) => [n.id, n]));
  const edgeIds = new Set(graph.edges.map((e) => e.id));

  it("chains entry through backbone steps to the terminal", () => {
    for (const id of [
      "entry->shared:assemble-turn-context",
      "shared:assemble-turn-context->step:answer-question:1",
      "step:answer-question:1->step:answer-question:2",
      "step:answer-question:2->terminal-answer-question",
      "shared:assemble-turn-context->step:refuse-off-topic:1",
      "step:refuse-off-topic:1->terminal-refuse-off-topic",
    ]) {
      expect(edgeIds.has(id), id).toBe(true);
    }
  });

  it("dedupes steps shared across paths into one fan-in/fan-out node", () => {
    expect(nodeById.get("shared:assemble-turn-context")?.data).toMatchObject({
      actor: "agent",
      contract: null,
      inputs: ["Operate the platform"],
      kind: "step",
      pathNames: ["Answer platform question", "Refuse off-topic"],
    });
  });

  it("marks model invocations with their In/Action/Out contract", () => {
    expect(nodeById.get("step:answer-question:1")?.data).toMatchObject({
      actor: "model",
      contract: {
        action: "decide whether to call tools or answer",
        input: "history + question + tool schemas",
        output: "tool_calls -> dispatch; answer -> emit",
      },
      kind: "step",
      label: "decide: gather evidence or answer",
    });
  });

  it("hangs may_use capabilities off the invocation with a tool_calls/results loop", async () => {
    const capsId = "caps:step:answer-question:1";
    expect(nodeById.get(capsId)?.data).toMatchObject({
      forLabel: "decide: gather evidence or answer",
      kind: "capabilities",
      tools: [
        {
          purpose: "Health snapshot",
          sideEffect: "read",
          tool: "agent_health",
          when: "the question is about health",
        },
        {
          purpose: "One trace",
          sideEffect: "read",
          tool: "get_trace",
          when: "a failure needs drill-down",
        },
      ],
    });
    const out = graph.edges.find((e) => e.id === `step:answer-question:1->${capsId}`);
    expect(out?.label).toBe("tool_calls");
    const back = graph.edges.find((e) => e.id === `${capsId}->step:answer-question:1`);
    expect(back?.label).toBe("results");
    expect(back?.style?.strokeDasharray).toBeTruthy();
    // The satellite hugs the invocation in its own lane, an excursion off
    // the spine.
    const laid = await graph.relayout(graph.nodes);
    const capsX = laid.find((n) => n.id === capsId)!.position.x;
    const ownerX = laid.find((n) => n.id === "step:answer-question:1")!.position.x;
    expect(capsX).toBeGreaterThan(ownerX);
    expect(capsX - ownerX).toBeLessThan(500);
  });

  it("labels the fan-out edges from the shared step with each task's routing", () => {
    const answer = graph.edges.find(
      (e) => e.id === "shared:assemble-turn-context->step:answer-question:1"
    );
    expect(answer?.label).toBe("in-scope question");
    const refuse = graph.edges.find(
      (e) => e.id === "shared:assemble-turn-context->step:refuse-off-topic:1"
    );
    expect(refuse?.label).toBe("request is out of scope");
  });

  it("skips dashed diverges edges the fan-out already expresses", () => {
    expect(graph.edges.filter((e) => e.id.startsWith("diverge:"))).toHaveLength(0);
  });

  it("gives terminals the path's tone and keeps routing off paths with unique cards", () => {
    expect(nodeById.get("terminal-refuse-off-topic")?.data).toMatchObject({
      kind: "terminal",
      pathName: "Refuse off-topic",
      routing: "",
      terminalKind: "returns_empty",
    });
  });

  it("keeps routing on the terminal when the path has no card of its own", () => {
    const flow = {
      trajectoryMap: [path({ id: "direct", routing: "always", steps: [] })],
    } as unknown as CapabilityFlow;
    const g = buildTrajectoryGraph(flow, "A");
    expect(g.nodes.find((n) => n.id === "terminal-direct")?.data).toMatchObject({
      routing: "always",
    });
    expect(g.edges.map((e) => e.id)).toEqual(["entry->terminal-direct"]);
  });

  it("collects the upstream chain for the click highlight", () => {
    const { nodeIds } = collectAncestors(graph, "terminal-answer-question");
    expect([...nodeIds].sort()).toEqual([
      "caps:step:answer-question:1",
      "entry",
      "shared:assemble-turn-context",
      "step:answer-question:1",
      "step:answer-question:2",
      "terminal-answer-question",
    ]);
  });

  it("renders the empty map as an empty graph", () => {
    const empty = buildTrajectoryGraph({ trajectoryMap: [] } as unknown as CapabilityFlow, "A");
    expect(empty.nodes.map((n) => n.data.kind)).toEqual(["entry"]);
    expect(empty.edges).toEqual([]);
  });
});

describe("buildTrajectoryGraph decision surface", () => {
  const FLOW = {
    model: "claude-sonnet-4-5",
    task: "Operate the platform",
    toolSpec: [
      {
        args: "",
        cluster: "context graph",
        name: "graph_walk",
        purpose: "Walk",
        sideEffect: "read",
      },
      {
        args: "",
        cluster: "context graph",
        name: "graph_search",
        purpose: "Search",
        sideEffect: "read",
      },
      {
        args: "",
        cluster: "finetuning",
        name: "create_finetune_job",
        purpose: "Launch",
        sideEffect: "write",
      },
      { args: "", name: "odd_one_out", purpose: "", sideEffect: "none" },
    ],
    trajectoryMap: [
      path({
        claim: "decision_surface",
        id: "handle-user-turn",
        name: "Handle user turn",
        routing: "every user turn enters here",
        steps: [
          step("assemble turn context"),
          step("model decision", {
            action: "select tools or answer",
            input: "history + message + tool schemas",
            kind: "model_invocation",
            output: "tool_calls -> dispatch; answer -> emit",
          }),
          step("emit answer"),
        ],
        terminal: { description: "OpenUI answer", kind: "emits_record" },
        verified: true,
      }),
      path({
        claim: "declared_task",
        id: "refuse-off-topic",
        name: "Refuse off-topic",
        promptQuote: "do NOT answer it, do NOT call tools for it",
        routing: "request is out of scope",
        steps: [step("reply with fixed refusal")],
        terminal: { description: "", kind: "returns_empty" },
      }),
    ],
  } as unknown as CapabilityFlow;
  const graph = buildTrajectoryGraph(FLOW, "Brain");
  const nodeById = new Map(graph.nodes.map((n) => [n.id, n]));
  const modelStepId = "step:handle-user-turn:1";

  it("hangs one cluster satellite per semantic cluster off the decision invocation", async () => {
    const clusterIds = graph.nodes.filter((n) => n.data.kind === "cluster").map((n) => n.id);
    expect(clusterIds.sort()).toEqual([
      "cluster:context-graph",
      "cluster:finetuning",
      "cluster:other-capabilities",
    ]);
    const contextGraph = nodeById.get("cluster:context-graph");
    expect(contextGraph?.data).toMatchObject({
      forLabel: "model decision",
      name: "context graph",
      sideEffectMix: "2 read",
      tools: [
        { label: "graph_walk", purpose: "Walk", sideEffect: "read" },
        { label: "graph_search", purpose: "Search", sideEffect: "read" },
      ],
    });
    const out = graph.edges.find((e) => e.id === `${modelStepId}->cluster:context-graph`);
    expect(out?.label).toBe("tool_calls");
    const back = graph.edges.find((e) => e.id === `cluster:context-graph->${modelStepId}`);
    expect(back?.label).toBe("results");
    expect(back?.style?.strokeDasharray).toBeTruthy();
    // Satellites hug the invocation in their own lane, an excursion off the
    // spine.
    const laid = await graph.relayout(graph.nodes);
    const clusterX = laid.find((n) => n.id === "cluster:context-graph")!.position.x;
    const ownerX = laid.find((n) => n.id === modelStepId)!.position.x;
    expect(clusterX).toBeGreaterThan(ownerX);
    expect(clusterX - ownerX).toBeLessThan(500);
  });

  it("shares one cluster set across multiple decision invocations", () => {
    const twoSurfaces = {
      ...(FLOW as object),
      trajectoryMap: [
        (FLOW.trajectoryMap ?? [])[0],
        path({
          claim: "decision_surface",
          id: "delta-pass",
          name: "Delta pass",
          routing: "resuming a prior session",
          steps: [
            step("resume session"),
            step("model re-proves findings", {
              action: "re-check standing findings",
              input: "prior session + changed rows",
              kind: "model_invocation",
              output: "tool_calls -> dispatch; report -> emit",
            }),
          ],
          terminal: { description: "", kind: "emits_record" },
        }),
      ],
    } as unknown as CapabilityFlow;
    const g = buildTrajectoryGraph(twoSurfaces, "Analyst");
    const clusterNodes = g.nodes.filter((n) => n.data.kind === "cluster");
    // One node per cluster, never one per invocation.
    expect(clusterNodes.map((n) => n.id).sort()).toEqual([
      "cluster:context-graph",
      "cluster:finetuning",
      "cluster:other-capabilities",
    ]);
    const ids = new Set(g.edges.map((e) => e.id));
    expect(ids.has("step:handle-user-turn:1->cluster:context-graph")).toBe(true);
    expect(ids.has("step:delta-pass:1->cluster:context-graph")).toBe(true);
    expect(ids.has("cluster:context-graph->step:delta-pass:1")).toBe(true);
  });

  it("stamps the decision invocation with the runtime-decided note", () => {
    const data = nodeById.get(modelStepId)?.data as { decisionNote: string };
    expect(data.decisionNote).toContain("decided at runtime");
    expect(data.decisionNote).toContain("4 capabilities across 3 clusters");
    const agentStep = nodeById.get("step:handle-user-turn:0")?.data as { decisionNote: string };
    expect(agentStep.decisionNote).toBe("");
  });

  it("carries claim, prompt quote and verification onto terminals", () => {
    expect(nodeById.get("terminal-handle-user-turn")?.data).toMatchObject({
      claim: "decision_surface",
      promptQuote: "",
      verified: true,
    });
    expect(nodeById.get("terminal-refuse-off-topic")?.data).toMatchObject({
      claim: "declared_task",
      promptQuote: "do NOT answer it, do NOT call tools for it",
      verified: false,
    });
  });

  it("relayout with measured heights keeps a spacer between every satellite card and caps lane height", async () => {
    // Tall enough that the three-cluster orbit exceeds the lane cap and wraps.
    const H = 400;
    const measured = graph.nodes.map((n) => ({
      ...n,
      measured: { height: H, width: 300 },
    }));
    const next = await graph.relayout(measured);

    const lanes = new Map<number, number[]>();
    for (const node of next) {
      if (!SATELLITE_KINDS.has(node.data.kind)) continue;
      const ys = lanes.get(node.position.x) ?? [];
      ys.push(node.position.y);
      lanes.set(node.position.x, ys);
    }
    for (const ys of lanes.values()) {
      ys.sort((a, b) => a - b);
      for (let i = 1; i < ys.length; i++) {
        // Never touching: real spacer between consecutive cards in a lane.
        expect(ys[i] - (ys[i - 1] + H)).toBeGreaterThanOrEqual(40);
      }
      // No lane grows into a tower: wrapped well under the cap plus slack.
      expect(ys[ys.length - 1] + H - ys[0]).toBeLessThanOrEqual(1500);
    }
    // The brain's big satellite orbit actually wraps into multiple lanes.
    const clusterXs = new Set(
      next.filter((n) => n.data.kind === "cluster").map((n) => n.position.x)
    );
    expect(clusterXs.size).toBeGreaterThan(1);
  });
});
