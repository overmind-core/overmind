// covers: console-logic
import { describe, expect, it } from "vitest";

import type { FinetuningJobList } from "@/openapi";
// Helper lives outside the component file so this test skips its icon/tooltip
// dependency chain.
import { eligibleModelSwapJobs } from "./eligible-model-swap-jobs";

const job = (overrides: Partial<FinetuningJobList>): FinetuningJobList =>
  ({
    baseModel: "Qwen/Qwen2.5-14B-Instruct",
    id: "j1",
    outputModelName: "ft-12345678-qwen2-5-14b-instruct",
    status: "succeeded",
    ...overrides,
  }) as FinetuningJobList;

describe("eligibleModelSwapJobs", () => {
  it("keeps only succeeded jobs with a servable output model", () => {
    const jobs = [
      job({ id: "ok" }),
      job({ id: "failed", status: "failed" }),
      job({ id: "running", status: "running" }),
      job({ id: "cancelled", status: "cancelled" }),
      job({ id: "deploying", status: "deploying" }),
      job({ id: "no-model", outputModelName: "" }),
    ];
    expect(eligibleModelSwapJobs(jobs).map((j) => j.id)).toEqual(["ok"]);
  });
});
