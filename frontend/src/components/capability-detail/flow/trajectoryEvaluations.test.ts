import { describe, expect, it } from "vitest";

import type { EvalMatrix } from "@/openapi";
import { bindTrajectoryEvaluations } from "./bindTrajectoryEvaluations";

const row = (id: string, extra: Partial<EvalMatrix> = {}): EvalMatrix => ({
  createdAt: new Date(0),
  definitionSource: "code",
  enabled: true,
  id,
  name: id,
  project: "project",
  source: id,
  type: "llm",
  updatedAt: new Date(0),
  ...extra,
});
const targets = [
  { anchors: ["package.Agent.run"], id: "start" },
  { anchors: ["package.Agent.decide"], id: "model" },
];

describe("trajectory evaluation placement", () => {
  it("keeps entrypoint rules on the whole run and matches a unique qualified step anchor", () => {
    const result = bindTrajectoryEvaluations(
      [
        row("run", { spanName: "Agent.run" }),
        row("answer"),
        row("step", { spanName: "Agent.decide", spanType: "llm_call" }),
        row("disabled", { enabled: false, spanName: "Agent.decide" }),
      ],
      "Agent.run",
      targets
    );
    expect(result.byNode.get("entry")?.map((r) => r.id)).toEqual(["run", "answer"]);
    expect(result.byNode.get("start")).toBeUndefined();
    expect(result.byNode.get("model")?.map((r) => r.id)).toEqual(["step"]);
  });

  it("keeps broad checks and span-type scopes separate from exact step bindings", () => {
    const result = bindTrajectoryEvaluations(
      [
        row("stock", { definitionSource: "stock", type: "deterministic" }),
        row("all-model-calls", { spanType: "llm_call" }),
      ],
      "Agent.run",
      targets
    );
    expect(result.byNode.size).toBe(0);
    expect(result.broad.map((r) => r.id)).toEqual(["stock", "all-model-calls"]);
  });

  it("does not guess from leaf names, missing steps or ambiguous function anchors", () => {
    const result = bindTrajectoryEvaluations(
      [
        row("leaf", { spanName: "decide" }),
        row("missing", { spanName: "Agent.judge" }),
        row("ambiguous", { spanName: "Agent.decide" }),
      ],
      "Agent.run",
      [...targets, { anchors: ["other.Agent.decide"], id: "other" }]
    );
    expect(result.byNode.size).toBe(0);
    expect(result.unmapped.map((r) => r.id)).toEqual(["leaf", "missing", "ambiguous"]);
  });
});
