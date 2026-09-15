import { describe, expect, it } from "vitest";

import { pickCapabilityNudge } from "./pick-capability-nudge";

const ALL_FLAGS = {
  datasets: true,
  evaluations: true,
  finetuning: true,
  inference: true,
};

describe("pickCapabilityNudge", () => {
  it("returns upload when the capability has no datasets", () => {
    expect(
      pickCapabilityNudge({
        datasets: [],
        deployedModels: [],
        finetuningJobs: [],
        flags: ALL_FLAGS,
        optimiserExperimentCount: 0,
      })
    ).toEqual({ kind: "upload" });
  });

  it("returns optimise when an eval dataset exists and no experiments", () => {
    expect(
      pickCapabilityNudge({
        datasets: [{ contract: "eval" }],
        deployedModels: [],
        finetuningJobs: [],
        flags: ALL_FLAGS,
        optimiserExperimentCount: 0,
      })
    ).toEqual({ kind: "optimise" });
  });

  it("returns finetune when an ft dataset exists and no jobs", () => {
    expect(
      pickCapabilityNudge({
        datasets: [{ contract: "train" }],
        deployedModels: [],
        finetuningJobs: [],
        flags: ALL_FLAGS,
        optimiserExperimentCount: 0,
      })
    ).toEqual({ kind: "finetune" });
  });

  it("prefers finetune over optimise when both datasets exist", () => {
    expect(
      pickCapabilityNudge({
        datasets: [{ contract: "eval" }, { contract: "train" }],
        deployedModels: [],
        finetuningJobs: [],
        flags: ALL_FLAGS,
        optimiserExperimentCount: 0,
      })?.kind
    ).toBe("finetune");
  });

  it("returns deploy when a job succeeded and no live model exists", () => {
    expect(
      pickCapabilityNudge({
        datasets: [{ contract: "train" }],
        deployedModels: [{ finetuningJobId: "j1", id: "m1", status: "failed" }],
        finetuningJobs: [{ createdAt: "2024-01-02", id: "j1", status: "succeeded" }],
        flags: ALL_FLAGS,
        optimiserExperimentCount: 1,
      })
    ).toEqual({ jobId: "j1", kind: "deploy", modelId: "m1" });
  });

  it("skips deploy when a live model already exists", () => {
    expect(
      pickCapabilityNudge({
        datasets: [{ contract: "train" }],
        deployedModels: [{ finetuningJobId: "j1", id: "m1", status: "ready" }],
        finetuningJobs: [{ createdAt: "2024-01-02", id: "j1", status: "succeeded" }],
        flags: ALL_FLAGS,
        optimiserExperimentCount: 1,
      })
    ).toBeNull();
  });

  it("skips finetune once any job exists", () => {
    expect(
      pickCapabilityNudge({
        datasets: [{ contract: "train" }],
        deployedModels: [],
        finetuningJobs: [{ id: "j1", status: "running" }],
        flags: ALL_FLAGS,
        optimiserExperimentCount: 0,
      })
    ).toBeNull();
  });

  it("respects feature flags", () => {
    expect(
      pickCapabilityNudge({
        datasets: [],
        deployedModels: [],
        finetuningJobs: [],
        flags: { ...ALL_FLAGS, datasets: false },
        optimiserExperimentCount: 0,
      })
    ).toBeNull();
  });
});
