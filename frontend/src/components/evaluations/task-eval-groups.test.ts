import { describe, expect, it } from "vitest";

import type { BehaviourCoverageEntry } from "@/hooks/use-behaviours";
import type { BehaviourEvaluator, EvaluatorCatalog } from "@/openapi";
import {
  anchorShortName,
  splitSuite,
  suiteSize,
  unassignedCatalog,
  unscoredStepLabels,
} from "./task-eval-groups";

function coverageEntry(overrides: Partial<BehaviourCoverageEntry> = {}): BehaviourCoverageEntry {
  return {
    behaviourId: "b1",
    outcomeCovered: true,
    outcomeEvaluators: ["outcome-ok"],
    steps: [
      { covered: true, evaluators: ["fetch-quality"], segment: ["app.io.fetch", "app.rank.rank"] },
      { covered: false, evaluators: [], segment: ["app.rank.rank", "app.out.emit"] },
    ],
    ...overrides,
  };
}

function behaviourEvaluator(id: string, binding: Record<string, unknown>): BehaviourEvaluator {
  return {
    behaviourBinding: binding,
    id,
    kind: "llm_judge",
    name: id,
  } as BehaviourEvaluator;
}

describe("anchor labels", () => {
  it("shortens qualnames to their last segment", () => {
    expect(anchorShortName("app.io.fetch")).toBe("fetch");
    expect(anchorShortName("emit")).toBe("emit");
  });
});

describe("coverage rollups", () => {
  it("counts the suite across steps and outcome", () => {
    expect(suiteSize(coverageEntry())).toBe(2);
  });

  it("lists only unscored step labels", () => {
    expect(unscoredStepLabels(coverageEntry())).toEqual(["rank → emit"]);
  });
});

describe("splitSuite", () => {
  it("separates step evals from outcome evals by binding role", () => {
    const step = behaviourEvaluator("s", { anchorSegment: ["a", "b"], role: "step" });
    const outcome = behaviourEvaluator("o", { role: "outcome" });
    const unlabelled = behaviourEvaluator("u", {});
    const result = splitSuite([step, outcome, unlabelled]);
    expect(result.step.map((e) => e.id)).toEqual(["s"]);
    expect(result.outcome.map((e) => e.id)).toEqual(["o", "u"]);
  });
});

describe("unassignedCatalog", () => {
  const catalogRow = (name: string, capability: string | null): EvaluatorCatalog =>
    ({ capability, id: name, name }) as unknown as EvaluatorCatalog;

  it("drops evaluators bound in their capability's suites, keeps the rest", () => {
    const catalog = [
      catalogRow("fetch-quality", "capability-1"),
      catalogRow("outcome-ok", "capability-1"),
      catalogRow("legacy-grader", "capability-1"),
      catalogRow("fetch-quality", "capability-2"),
      catalogRow("generic-judge", null),
    ];
    const entries = new Map([["capability-1", [coverageEntry()]]]);
    expect(unassignedCatalog(catalog, entries).map((e) => `${e.capability}:${e.name}`)).toEqual([
      "capability-1:legacy-grader",
      "capability-2:fetch-quality",
      "null:generic-judge",
    ]);
  });
});
