// covers: console-logic
import { describe, expect, it } from "vitest";

import { pickCapabilityNudge } from "./pick-capability-nudge";

const ALL_FLAGS = {
  datasets: true,
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
      })
    ).toEqual({ kind: "upload" });
  });

  it("returns finetune when an ft dataset exists and no jobs", () => {
    expect(
      pickCapabilityNudge({
        datasets: [{ contract: "train" }],
        deployedModels: [],
        finetuningJobs: [],
        flags: ALL_FLAGS,
      })
    ).toEqual({ kind: "finetune" });
  });

  it("returns finetune when eval and training datasets both exist", () => {
    expect(
      pickCapabilityNudge({
        datasets: [{ contract: "eval" }, { contract: "train" }],
        deployedModels: [],
        finetuningJobs: [],
        flags: ALL_FLAGS,
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
      })
    ).toBeNull();
  });
});
