import { describe, expect, it } from "vitest";

import type { OptimizerExperiment } from "@/openapi";
import { optimizerModelIds, optimizerRunTypeMeta } from "./experiment-status";

const experiment = (overrides: Partial<OptimizerExperiment> = {}) =>
  ({ mode: "optimize", modelIds: null, ...overrides }) as OptimizerExperiment;

describe("optimiser run type", () => {
  it("uses descriptive names for code and model runs", () => {
    expect(optimizerRunTypeMeta("optimize").label).toBe("Prompt & code optimisation");
    expect(optimizerRunTypeMeta("model_comparison").label).toBe("Model comparison");
    expect(optimizerRunTypeMeta("hybrid").label).toBe("Code + model optimisation");
  });
  it("keeps only valid model ids for counts and search", () => {
    expect(optimizerModelIds(experiment({ modelIds: ["openai/gpt-4o", 7, null] }))).toEqual([
      "openai/gpt-4o",
    ]);
  });
});
