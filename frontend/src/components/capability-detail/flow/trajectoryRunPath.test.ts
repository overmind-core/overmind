import { describe, expect, it } from "vitest";

import type { TrajectoryGraph, TrajectoryNode } from "./buildTrajectoryGraph";
import { trajectoryRunPath } from "./trajectoryRunPath";

const node = (id: string, anchors: string[]) =>
  ({
    data: { anchors, kind: "step" },
    id,
    position: { x: 0, y: 0 },
  }) as TrajectoryNode;
const edge = (source: string, target: string) => ({ id: `${source}->${target}`, source, target });
const graph: TrajectoryGraph = {
  edges: [
    edge("entry", "model"),
    edge("model", "accepted"),
    edge("model", "retry"),
    edge("retry", "model"),
  ],
  nodes: [
    {
      data: { entrypoint: "Agent.run", kind: "entry" },
      id: "entry",
      position: { x: 0, y: 0 },
    } as TrajectoryNode,
    node("model", ["agent.Agent.decide"]),
    node("accepted", ["agent.Agent.accept"]),
    node("retry", ["agent.Agent.retry"]),
  ],
  relayout: async (nodes) => nodes,
};
const span = (name: string, startTimeNs: number, overrides = {}) => ({
  attributes: {},
  capability: "cap",
  endTimeNs: startTimeNs + 1,
  name,
  operation: "",
  parentSpanId: null,
  spanId: `${name}-${startTimeNs}`,
  startTimeNs,
  ...overrides,
});

describe("recorded trajectory routes", () => {
  it("highlights the final return and its connector from a completed entrypoint", () => {
    const terminalGraph: TrajectoryGraph = {
      ...graph,
      edges: [...graph.edges, edge("accepted", "result")],
      nodes: [...graph.nodes, { data: { kind: "terminal" }, id: "result" } as TrajectoryNode],
    };
    const spans = [
      span("Agent.run", 1, {
        attributes: { "overmind.output_data": '{"answer":"Finished"}' },
        endTimeNs: 10,
      }),
      span("Agent.accept", 8, { parentSpanId: "Agent.run-1" }),
    ];
    const result = trajectoryRunPath(terminalGraph, spans, "cap", true);
    expect(result.nodeIds.has("result")).toBe(true);
    expect(result.edgeIds.has("accepted->result")).toBe(true);
    expect(result.graph.nodes.find(({ id }) => id === "result")?.data).toMatchObject({
      recordedOutput: '{\n  "answer": "Finished"\n}',
    });
    expect(
      trajectoryRunPath(
        terminalGraph,
        [
          {
            ...spans[0],
            attributes: { "overmind.output_data": '{"final_response":"Finished","messages":[]}' },
          },
          spans[1],
        ],
        "cap",
        true
      ).graph.nodes.find(({ id }) => id === "result")?.data
    ).toMatchObject({ recordedOutput: "Finished" });
    expect(terminalGraph.nodes.find(({ id }) => id === "result")?.data).not.toHaveProperty(
      "recordedOutput"
    );
    expect(trajectoryRunPath(terminalGraph, spans, "cap", false).nodeIds.has("result")).toBe(false);
    expect(
      trajectoryRunPath(
        terminalGraph,
        [{ ...spans[0], attributes: {} }, spans[1]],
        "cap",
        true
      ).nodeIds.has("result")
    ).toBe(false);
    expect(
      trajectoryRunPath(
        terminalGraph,
        [spans[0], { ...spans[1], parentSpanId: null }],
        "cap",
        true
      ).nodeIds.has("result")
    ).toBe(false);
    expect(
      trajectoryRunPath(
        {
          ...terminalGraph,
          edges: [...terminalGraph.edges, edge("accepted", "other-result")],
          nodes: [
            ...terminalGraph.nodes,
            { data: { kind: "terminal" }, id: "other-result" } as TrajectoryNode,
          ],
        },
        spans,
        "cap",
        true
      ).nodeIds.has("result")
    ).toBe(false);
  });

  it("highlights the observed branch and retries without inventing the alternative", () => {
    const result = trajectoryRunPath(
      graph,
      [
        span("Agent.run", 1),
        span("Agent.decide", 2),
        span("Agent.retry", 3),
        span("Agent.decide", 4),
        span("Agent.accept", 5),
      ],
      "cap"
    );
    expect([...result.edgeIds]).toEqual([
      "entry->model",
      "model->accepted",
      "model->retry",
      "retry->model",
    ]);
    expect(result.mappedSpans).toBe(5);
    const accepted = trajectoryRunPath(
      graph,
      [span("Agent.run", 1), span("Agent.decide", 2), span("Agent.accept", 3)],
      "cap"
    );
    expect(accepted.nodeIds.has("retry")).toBe(false);
    expect(accepted.edgeIds.has("model->retry")).toBe(false);
  });

  it("uses recorded code identity when a span display name differs", () => {
    const result = trajectoryRunPath(
      graph,
      [
        span("Provider request", 1, {
          attributes: { "code.function.name": "Agent.decide", "code.namespace": "agent" },
        }),
      ],
      "cap"
    );
    expect([...result.nodeIds]).toEqual(["model"]);
  });

  it("does not infer missing steps, foreign capability spans, or ambiguous anchors", () => {
    const result = trajectoryRunPath(
      graph,
      [
        span("Agent.run", 1),
        span("Agent.accept", 2),
        span("Agent.decide", 3, { capability: "other" }),
      ],
      "cap"
    );
    expect(result.nodeIds.has("model")).toBe(false);
    expect(result.edgeIds.size).toBe(0);
    expect(
      trajectoryRunPath(
        { ...graph, nodes: [...graph.nodes, node("another-model", ["agent.Agent.decide"])] },
        [span("Agent.decide", 1)],
        "cap"
      ).mappedSpans
    ).toBe(0);
  });

  it("does not mistake concurrent sibling spans for sequential execution", () => {
    const result = trajectoryRunPath(
      graph,
      [span("Agent.decide", 1, { endTimeNs: 10 }), span("Agent.accept", 2)],
      "cap"
    );
    expect(result.nodeIds.size).toBe(2);
    expect(result.edgeIds.size).toBe(0);
  });

  it("marks executed tools and their category without guessing local outcomes", () => {
    const tools: TrajectoryGraph = {
      ...graph,
      edges: [
        ...graph.edges,
        edge("model", "group"),
        edge("group", "search"),
        edge("search", "outcome"),
      ],
      nodes: [
        ...graph.nodes,
        { data: { kind: "actionGroup", ownerId: "model" }, id: "group" } as TrajectoryNode,
        {
          data: { groupId: "group", kind: "action", tool: { name: "search" } },
          id: "search",
        } as TrajectoryNode,
        { data: { actionId: "search", kind: "outcome" }, id: "outcome" } as TrajectoryNode,
      ],
    };
    const result = trajectoryRunPath(
      tools,
      [
        span("Agent.decide", 1),
        span("tool call", 2, { attributes: { "tool.name": "search" } }),
        span("Agent.accept", 3),
      ],
      "cap"
    );
    expect(result.nodeIds.has("group")).toBe(true);
    expect(result.nodeIds.has("outcome")).toBe(false);
    expect(result.edgeIds.has("model->group")).toBe(true);
    expect(result.edgeIds.has("group->search")).toBe(true);
    expect(result.transitions.has("group->accepted")).toBe(true);
    expect(result.edgeIds.has("search->outcome")).toBe(false);
    expect(result.expandedIds.has("group")).toBe(true);
    expect(result.expandedIds.has("search")).toBe(false);
  });

  it("reveals a captured tool result without choosing a conditional branch", () => {
    const resultGraph: TrajectoryGraph = {
      ...graph,
      edges: [
        edge("model", "group"),
        edge("group", "search"),
        edge("search", "result"),
        { ...edge("result", "accepted"), data: { relation: "continuation" } },
      ],
      nodes: [
        ...graph.nodes,
        { data: { kind: "actionGroup", ownerId: "model" }, id: "group" } as TrajectoryNode,
        {
          data: { groupId: "group", kind: "action", tool: { name: "search" } },
          id: "search",
        } as TrajectoryNode,
        {
          data: { actionId: "search", branch: null, kind: "outcome" },
          id: "result",
        } as TrajectoryNode,
      ],
    };
    const recorded = span("search", 2, {
      attributes: { "overmind.output.data": '{"result":"found"}' },
    });
    const result = trajectoryRunPath(
      resultGraph,
      [span("Agent.decide", 1), recorded, span("Agent.accept", 3)],
      "cap"
    );
    expect(result.nodeIds.has("result")).toBe(true);
    expect(result.expandedIds.has("search")).toBe(true);
    expect(result.expandedIds.has("group")).toBe(true);
    expect(result.edgeIds.has("result->accepted")).toBe(true);
    expect(
      trajectoryRunPath(
        resultGraph,
        [{ ...recorded, attributes: { "overmind.output.data": "null" } }],
        "cap"
      ).nodeIds.has("result")
    ).toBe(false);
  });

  it("highlights only a named outcome captured beneath its own tool span", () => {
    const outcomes: TrajectoryGraph = {
      ...graph,
      edges: [
        edge("model", "group"),
        edge("group", "search"),
        edge("search", "opened"),
        edge("search", "error"),
      ],
      nodes: [
        ...graph.nodes,
        { data: { kind: "actionGroup", ownerId: "model" }, id: "group" } as TrajectoryNode,
        {
          data: { groupId: "group", kind: "action", tool: { name: "search" } },
          id: "search",
        } as TrajectoryNode,
        {
          data: { actionId: "search", branch: { action: "Open results" }, kind: "outcome" },
          id: "opened",
        } as TrajectoryNode,
        {
          data: { actionId: "search", branch: { action: "Report error" }, kind: "outcome" },
          id: "error",
        } as TrajectoryNode,
      ],
    };
    const child = span("Open results", 3, { parentSpanId: "search-2" });
    const result = trajectoryRunPath(
      outcomes,
      [span("Agent.decide", 1), span("search", 2, { endTimeNs: 5 }), child],
      "cap"
    );
    expect(result.nodeIds.has("opened")).toBe(true);
    expect(result.nodeIds.has("error")).toBe(false);
    expect(result.expandedIds.has("search")).toBe(true);
    expect(result.edgeIds.has("search->opened")).toBe(true);
    expect(
      trajectoryRunPath(outcomes, [{ ...child, parentSpanId: null }], "cap").nodeIds.has("opened")
    ).toBe(false);
  });

  it("does not select between two conditional edges with identical endpoints", () => {
    const result = trajectoryRunPath(
      {
        ...graph,
        edges: [
          edge("model", "accepted"),
          { ...edge("model", "accepted"), id: "another-condition" },
        ],
      },
      [span("Agent.decide", 1), span("Agent.accept", 2)],
      "cap"
    );
    expect(result.edgeIds.size).toBe(0);
  });

  it("shows captured outputs for branched tools and keeps unused tools out of the run view", () => {
    const source: TrajectoryGraph = {
      ...graph,
      edges: [
        edge("model", "group"),
        edge("group", "search"),
        edge("group", "unused"),
        edge("search", "success"),
        edge("search", "failure"),
        {
          ...edge("success", "accepted"),
          data: { relation: "continuation" },
          type: "continuation",
        },
      ],
      nodes: [
        ...graph.nodes,
        { data: { kind: "actionGroup", ownerId: "model" }, id: "group" } as TrajectoryNode,
        {
          data: { groupId: "group", kind: "action", tool: { name: "search" } },
          id: "search",
        } as TrajectoryNode,
        {
          data: { groupId: "group", kind: "action", tool: { name: "unused" } },
          id: "unused",
        } as TrajectoryNode,
        {
          data: { actionId: "search", branch: { action: "Found results" }, kind: "outcome" },
          id: "success",
        } as TrajectoryNode,
        {
          data: { actionId: "search", branch: { action: "Failed search" }, kind: "outcome" },
          id: "failure",
        } as TrajectoryNode,
      ],
    };
    const result = trajectoryRunPath(
      source,
      [
        span("Agent.decide", 1),
        span("search", 2, { attributes: { "overmind.output_data": '{"found":1}' } }),
        span("search", 3, { attributes: { "traceloop.entity.output": '{"found":2}' } }),
        span("Agent.accept", 4),
      ],
      "cap"
    );
    const captured = result.graph.nodes.find(({ id }) => id === "outcome:search:recorded")!;
    expect(captured.data).toMatchObject({
      branch: null,
      continues: true,
      recordedInvocations: 2,
      returns: '{\n  "found": 2\n}',
    });
    for (const id of ["unused", "success", "failure"])
      expect(result.graph.nodes.some((node) => node.id === id)).toBe(false);
    expect(result.expandedIds.has("search")).toBe(true);
    expect(result.edgeIds.has("return:outcome:search:recorded->accepted")).toBe(true);
    expect(source.nodes.some(({ id }) => id === "unused")).toBe(true);
    expect(source.nodes.some(({ id }) => id === captured.id)).toBe(false);
    const withoutOutput = trajectoryRunPath(
      source,
      [span("Agent.decide", 1), span("search", 2), span("Agent.accept", 3)],
      "cap"
    );
    expect(
      withoutOutput.graph.edges.find(
        ({ source, target }) => source === "search" && target === "accepted"
      )
    ).toMatchObject({ data: { relation: "continuation", summary: true }, label: undefined });
    expect(withoutOutput.nodeIds.has("success")).toBe(false);
  });
});
