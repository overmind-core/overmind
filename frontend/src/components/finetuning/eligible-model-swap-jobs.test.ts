import { describe, expect, it } from "vitest";

import type { FinetuningJobList } from "@/openapi";
// Helper lives outside the component file so this test skips its icon/tooltip
// dependency chain.
import { eligibleModelSwapJobs, hasInProgressModelSwapJobs } from "./eligible-model-swap-jobs";

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

  it("returns empty for no eligible jobs", () => {
    expect(eligibleModelSwapJobs([job({ status: "failed" })])).toEqual([]);
    expect(eligibleModelSwapJobs([])).toEqual([]);
  });
});

describe("hasInProgressModelSwapJobs", () => {
  it("is true for queued/preparing/running/deploying", () => {
    expect(hasInProgressModelSwapJobs([job({ status: "queued" })])).toBe(true);
    expect(hasInProgressModelSwapJobs([job({ status: "preparing" })])).toBe(true);
    expect(hasInProgressModelSwapJobs([job({ status: "running" })])).toBe(true);
    expect(hasInProgressModelSwapJobs([job({ status: "deploying" })])).toBe(true);
  });

  it("is false for succeeded/failed/cancelled only", () => {
    expect(hasInProgressModelSwapJobs([job({ status: "succeeded" })])).toBe(false);
    expect(hasInProgressModelSwapJobs([job({ status: "failed" })])).toBe(false);
    expect(hasInProgressModelSwapJobs([job({ status: "cancelled" })])).toBe(false);
    expect(hasInProgressModelSwapJobs([])).toBe(false);
  });

  it("is true when any job in a mix is still in progress", () => {
    expect(
      hasInProgressModelSwapJobs([
        job({ id: "a", status: "failed" }),
        job({ id: "b", status: "running" }),
      ])
    ).toBe(true);
  });
});
