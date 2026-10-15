import { describe, expect, it } from "vitest";

import type { Capability, EvalMatrix } from "@/openapi";
import { presentEvaluations } from "./evaluation-presentation";

const capability = {
  entrypointFn: "Agent.run",
  flow: {
    toolSpec: [{ cluster: "Page interaction", name: "click" }],
    trajectoryMap: [
      {
        id: "run",
        steps: [
          { anchors: ["browser.Agent.observe"], step: "Observe browser state" },
          { anchors: ["browser.Agent.choose"], kind: "model_invocation", step: "Choose an action" },
        ],
      },
    ],
  },
  name: "Browser automation",
} as unknown as Capability;
const row = (extra: Partial<EvalMatrix> = {}) =>
  ({
    definitionSource: "custom",
    id: "rule",
    name: "Action relevance",
    source: "Actions follow the user's task.",
    spanName: "Agent.choose",
    type: "llm",
    ...extra,
  }) as EvalMatrix;

describe("evaluation presentation", () => {
  it("shows the source criterion and its uniquely matched model step", () => {
    const [result] = presentEvaluations([row()], capability);
    expect(result.scope.label).toBe("Choose an action");
    expect(result.scope.kind).toBe("model");
    expect(result.description).toBe("Actions follow the user's task.");
  });
  it("distinguishes whole-run rules, broad span rules and tool checks", () => {
    const results = presentEvaluations(
      [
        row({
          id: "tool",
          name: "click · Argument contract",
          spanName: "click",
          type: "deterministic",
        }),
        row({ id: "broad", spanName: "", spanType: "llm_call" }),
        row({ id: "run", spanName: "Agent.run" }),
      ],
      capability
    );
    expect(results.map((r) => r.row.id)).toEqual(["run", "tool", "broad"]);
    expect(results.find((r) => r.row.id === "run")!.scope.label).toBe("Whole agent");
    const tool = results.find((r) => r.row.id === "tool")!;
    expect(tool.scope.label).toBe("click");
    expect(tool.scope.detail).toBe("Page interaction");
    expect(tool.name).toBe("Arguments match the tool contract");
    expect(results.find((r) => r.row.id === "broad")!.scope.label).toBe("All model calls");
  });
  it("uses the authored step name when a rule has no persisted span anchor", () => {
    const [result] = presentEvaluations(
      [
        row({
          name: "Step: Choose an action · 1",
          spanName: "",
          spanType: "",
        }),
      ],
      capability
    );
    expect(result.scope.label).toBe("Choose an action");
    expect(result.scope.kind).toBe("model");
    expect(result.name).toBe("Choose an action · 1");
  });
  it("keeps ambiguous or missing anchors visibly unmapped", () => {
    const ambiguous = {
      ...capability,
      flow: {
        ...capability.flow,
        trajectoryMap: [
          { id: "a", steps: [{ anchors: ["a.Agent.choose"], step: "A" }] },
          { id: "b", steps: [{ anchors: ["b.Agent.choose"], step: "B" }] },
        ],
      },
    } as Capability;
    expect(presentEvaluations([row()], ambiguous)[0].scope.label).toBe("Unmapped span");
    expect(presentEvaluations([row({ spanName: "missing" })], capability)[0].scope.detail).toBe(
      "missing"
    );
  });
  it("uses authored Python documentation without inventing a description from executable code", () => {
    expect(
      presentEvaluations([
        row({
          source: '"""Required fields are present."""\ndef evaluate(span): return True',
          type: "deterministic",
        }),
      ])[0].description
    ).toBe("Required fields are present.");
    expect(
      presentEvaluations([
        row({ source: "def evaluate(span): return True", type: "deterministic" }),
      ])[0].description
    ).toBe("");
  });
});
