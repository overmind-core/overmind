import { describe, expect, it } from "vitest";

import type {
  CapabilityFlow,
  CapabilityFlowTrajectoryPath,
  CapabilityFlowTrajectoryStep,
} from "@/openapi";
import { buildTrajectoryGraph, collectAncestors } from "./buildTrajectoryGraph";

const path = (overrides: Partial<CapabilityFlowTrajectoryPath> & { id: string }) => ({
  divergences: [],
  name: "",
  provenance: [],
  routing: "",
  steps: [],
  terminal: { description: "", kind: "emits_record" },
  tools: [],
  ...overrides,
});

const step = (label: string, overrides: Partial<CapabilityFlowTrajectoryStep> = {}) => ({
  anchors: [],
  kind: "agent_step",
  mayUse: [],
  step: label,
  ...overrides,
});

describe("buildTrajectoryGraph", () => {
  it("keeps equally named steps separate when their source anchors differ", () => {
    const graph = buildTrajectoryGraph(
      {
        trajectoryMap: [
          path({ id: "one", steps: [step("Decide", { anchors: ["module.First.decide"] })] }),
          path({ id: "two", steps: [step("Decide", { anchors: ["module.Second.decide"] })] }),
        ],
      } as unknown as CapabilityFlow,
      "Agent"
    );
    expect(graph.nodes.filter((n) => n.data.kind === "step")).toHaveLength(2);
  });

  it.each([
    [{ input: "Request" }, { input: "Retry after rejection" }],
    [
      { mayUse: [{ tool: "search", when: "Missing evidence" }] },
      { mayUse: [{ tool: "search", when: "Conflicting evidence" }] },
    ],
  ])("preserves path-specific contracts and tool conditions", (first, second) => {
    const graph = buildTrajectoryGraph(
      {
        trajectoryMap: [
          path({
            id: "one",
            steps: [
              step("Decide", { anchors: ["Agent.decide"], kind: "model_invocation", ...first }),
            ],
          }),
          path({
            id: "two",
            steps: [
              step("Decide", { anchors: ["Agent.decide"], kind: "model_invocation", ...second }),
            ],
          }),
        ],
      } as unknown as CapabilityFlow,
      "Agent"
    );
    expect(graph.nodes.filter((node) => node.data.kind === "step")).toHaveLength(2);
  });

  it("keeps execution contracts on deterministic steps", () => {
    const graph = buildTrajectoryGraph(
      {
        trajectoryMap: [
          path({
            id: "dispatch",
            steps: [
              step("Apply tool policy", {
                action: "Check authorization",
                input: "Requested call",
                output: "Allowed call or blocked result",
              }),
            ],
          }),
        ],
      } as unknown as CapabilityFlow,
      "Agent"
    );
    expect(graph.nodes.find((node) => node.data.kind === "step")?.data).toMatchObject({
      contract: {
        action: "Check authorization",
        input: "Requested call",
        output: "Allowed call or blocked result",
      },
    });
  });

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
      "entry->shared:answer-question:0",
      "shared:answer-question:0->step:answer-question:1",
      "step:answer-question:1->step:answer-question:2",
      "step:answer-question:2->terminal-answer-question",
      "shared:answer-question:0->step:refuse-off-topic:1",
      "step:refuse-off-topic:1->terminal-refuse-off-topic",
    ]) {
      expect(edgeIds.has(id), id).toBe(true);
    }
  });

  it("dedupes steps shared across paths into one fan-in/fan-out node", () => {
    expect(nodeById.get("shared:answer-question:0")?.data).toMatchObject({
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

  const invocation = (overrides: Partial<CapabilityFlow>) =>
    buildTrajectoryGraph({ ...FLOW, ...overrides }, "Brain").nodes.find(
      (node) => node.id === "step:answer-question:1"
    )?.data;

  it("attaches the captured model and full prompt to model calls only", () => {
    const result = buildTrajectoryGraph({ ...FLOW, systemPrompt: "  Full prompt  " }, "Brain");
    expect(result.nodes.find((node) => node.id === "step:answer-question:1")?.data).toMatchObject({
      model: FLOW.model,
      promptIsExcerpt: false,
      systemPrompt: "Full prompt",
    });
    expect(result.nodes.find((node) => node.id === "shared:answer-question:0")?.data).toMatchObject(
      {
        model: "",
        promptIsExcerpt: false,
        systemPrompt: "",
      }
    );
  });

  it("labels excerpts and uses them when the full prompt is blank", () => {
    expect(
      invocation({ systemPrompt: " \n", systemPromptExcerpt: "  Partial prompt  " })
    ).toMatchObject({
      promptIsExcerpt: true,
      systemPrompt: "Partial prompt",
    });
    expect(
      invocation({ systemPrompt: "Full prompt", systemPromptExcerpt: "Partial" })
    ).toMatchObject({
      promptIsExcerpt: false,
      systemPrompt: "Full prompt",
    });
  });

  it("does not invent uncaptured models or prompts", () => {
    expect(invocation({ model: "" })).toMatchObject({ model: "", systemPrompt: "" });
  });

  const mode = (overrides: Partial<NonNullable<CapabilityFlow["modes"]>[number]> = {}) => ({
    entrypointFn: "workers.extract",
    model: "openai/gpt-4.1-mini",
    name: "Extraction",
    prompt: "Extract documents",
    sourcePath: "workers.py",
    ...overrides,
  });

  it("does not assign a capability prompt to an unbound worker call", () => {
    expect(invocation({ modes: [mode()], systemPrompt: "Orchestrate workers" })).toMatchObject({
      model: "",
      systemPrompt: "",
    });
  });

  it("uses a mode's model and prompt when its entrypoint anchors the call", () => {
    expect(
      invocation({
        modes: [mode()],
        systemPrompt: "Orchestrate workers",
        trajectoryMap: [
          path({
            id: "answer-question",
            steps: [
              step("Prepare"),
              step("Extract", { anchors: ["workers.extract"], kind: "model_invocation" }),
            ],
          }),
        ],
      })
    ).toMatchObject({ model: "openai/gpt-4.1-mini", systemPrompt: "Extract documents" });
  });

  it("keeps distinct worker steps bound to their own mode prompts", () => {
    const result = buildTrajectoryGraph(
      {
        ...FLOW,
        modes: [mode(), mode({ entrypointFn: "workers.screen", prompt: "Screen entities" })],
        trajectoryMap: ["extract", "screen"].map((name) =>
          path({
            id: name,
            steps: [
              step("Call worker", { anchors: [`workers.${name}`], kind: "model_invocation" }),
            ],
          })
        ),
      },
      "Brain"
    );
    expect(
      result.nodes.filter((node) => node.data.kind === "step").map((node) => node.data)
    ).toMatchObject([
      { model: "openai/gpt-4.1-mini", systemPrompt: "Extract documents" },
      { model: "openai/gpt-4.1-mini", systemPrompt: "Screen entities" },
    ]);
  });

  it("keeps optional tools on their owning step without adding routing edges", () => {
    const owner = nodeById.get("step:answer-question:1");
    expect(owner?.data).toMatchObject({ hasActions: true });
    expect(
      graph.nodes.filter((node) => node.data.kind === "action").map((node) => node.data)
    ).toMatchObject([
      { tool: { name: "agent_health", purpose: "Health snapshot" } },
      { tool: { name: "get_trace", purpose: "One trace" } },
    ]);
    expect(graph.nodes.some((n) => n.id.startsWith("caps:"))).toBe(false);
    expect(graph.edges.some((e) => e.label === "tool_calls" || e.label === "results")).toBe(false);
  });

  it("labels the fan-out edges from the shared step with each task's routing", () => {
    const answer = graph.edges.find(
      (e) => e.id === "shared:answer-question:0->step:answer-question:1"
    );
    expect(answer?.label).toBe("in-scope question");
    const refuse = graph.edges.find(
      (e) => e.id === "shared:answer-question:0->step:refuse-off-topic:1"
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
      "entry",
      "shared:answer-question:0",
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

  it("connects the option space to the decision through action groups", () => {
    const owner = nodeById.get(modelStepId);
    expect(owner?.data).toMatchObject({ hasActions: true });
    expect(
      graph.nodes.filter((node) => node.data.kind === "actionGroup").map((node) => node.data)
    ).toMatchObject([
      { label: "context graph", modelSelected: true, ownerId: modelStepId },
      { label: "finetuning", modelSelected: true, ownerId: modelStepId },
      { label: "Other actions", modelSelected: true, ownerId: modelStepId },
    ]);
    expect(graph.nodes.filter((n) => n.data.kind === "actionGroup")).toHaveLength(3);
    expect(graph.nodes.filter((n) => n.data.kind === "action")).toHaveLength(4);
    expect(graph.edges.some((e) => e.label === "tool_calls" || e.label === "results")).toBe(false);
  });

  it("keeps measured cards in forward order without overlapping", async () => {
    const laid = await graph.relayout(
      graph.nodes.map((n) => ({ ...n, measured: { height: 400, width: 300 } }))
    );
    const entry = laid.find((n) => n.id === "entry")!;
    const step = laid.find((n) => n.id === "step:handle-user-turn:0")!;
    expect(step.position.x).toBeGreaterThan(entry.position.x);
    const positions = new Set(laid.map((n) => `${n.position.x}:${n.position.y}`));
    expect(positions.size).toBe(laid.length);
  });
});
