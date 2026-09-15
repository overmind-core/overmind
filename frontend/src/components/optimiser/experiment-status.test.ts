import { describe, expect, it } from "vitest";

import type { OptimizerExperiment } from "@/openapi";
import { optimizerModelIds } from "./experiment-status";

const experiment = (overrides: Partial<OptimizerExperiment> = {}) =>
  ({ mode: "optimize", modelIds: null, ...overrides }) as OptimizerExperiment;

describe("optimiser run type", () => {
  it("keeps only valid model ids for counts and search", () => {
    expect(optimizerModelIds(experiment({ modelIds: ["openai/gpt-4o", 7, null] }))).toEqual([
      "openai/gpt-4o",
    ]);
  });
});
