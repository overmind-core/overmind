import { describe, expect, it } from "vitest";

import {
  buildRunningJobs,
  completionToastCopy,
  detectCompletedJobs,
  type RunningJobItem,
} from "./running-jobs";

describe("buildRunningJobs", () => {
  it("keeps only in-progress fine-tuning and eval runs", () => {
    const items = buildRunningJobs({
      evalRuns: [
        {
          createdAt: new Date("2026-07-20T10:00:00.000Z"),
          id: "e1",
          name: "Nightly",
          status: "running",
        },
        {
          createdAt: new Date("2026-07-20T09:00:00.000Z"),
          id: "e2",
          name: "Done",
          status: "completed",
        },
        {
          createdAt: new Date("2026-07-20T10:30:00.000Z"),
          id: "e3",
          name: "Optimizer · bot · candidate 1",
          origin: "optimizer",
          status: "running",
        },
      ],
      finetuning: [
        {
          baseModel: "qwen",
          createdAt: new Date("2026-07-20T11:00:00.000Z"),
          id: "f1",
          name: "swift fox",
          startedAt: new Date("2026-07-20T11:05:00.000Z"),
          status: "running",
        },
        {
          createdAt: new Date("2026-07-20T08:00:00.000Z"),
          id: "f2",
          name: "old",
          status: "succeeded",
        },
        {
          createdAt: new Date("2026-07-20T10:30:00.000Z"),
          id: "f3",
          name: "queued job",
          status: "queued",
        },
      ],
    });

    expect(items.map((i) => i.key)).toEqual(["ft:f1", "ft:f3", "eval:e1"]);
    expect(items[0]?.title).toBe("swift fox");
    expect(items[0]?.startedAt).toBe("2026-07-20T11:05:00.000Z");
  });

  it("rolls multi-model fine-tuning jobs into one experiment row", () => {
    const items = buildRunningJobs({
      finetuning: [
        {
          baseModel: "qwen/qwen3-8b",
          createdAt: new Date("2026-07-20T11:00:00.000Z"),
          groupId: "grp-1",
          id: "f1",
          name: "swift fox",
          startedAt: new Date("2026-07-20T11:05:00.000Z"),
          status: "running",
        },
        {
          baseModel: "meta-llama/llama-3.1-8b",
          createdAt: new Date("2026-07-20T11:00:01.000Z"),
          groupId: "grp-1",
          id: "f2",
          name: "swift fox",
          startedAt: new Date("2026-07-20T11:06:00.000Z"),
          status: "queued",
        },
        {
          baseModel: "google/gemma-2-9b",
          createdAt: new Date("2026-07-20T11:00:02.000Z"),
          groupId: "grp-1",
          id: "f3",
          name: "swift fox",
          status: "preparing",
        },
        {
          baseModel: "mistral",
          createdAt: new Date("2026-07-20T11:00:03.000Z"),
          groupId: "grp-1",
          id: "f4",
          name: "swift fox",
          status: "succeeded",
        },
      ],
    });

    expect(items).toHaveLength(1);
    expect(items[0]).toMatchObject({
      groupId: "grp-1",
      key: "ft:grp-1",
      kind: "finetuning",
      status: "running",
      title: "swift fox · 4 models",
    });
  });

  it("rolls optimizer eval runs into a single experiment row", () => {
    const items = buildRunningJobs({
      evalRuns: [
        {
          createdAt: new Date("2026-07-20T10:00:00.000Z"),
          id: "e1",
          name: "Optimizer · bot · baseline",
          origin: "optimizer",
          status: "running",
        },
        {
          createdAt: new Date("2026-07-20T10:01:00.000Z"),
          id: "e2",
          name: "Optimizer · bot · candidate 1",
          origin: "optimizer",
          status: "pending",
        },
      ],
      optimizerExperiments: [
        {
          capabilityName: "bot",
          createdAt: new Date("2026-07-20T09:55:00.000Z"),
          currentIteration: 2,
          id: "x1",
          numIterations: 5,
          status: "iterating",
        },
      ],
    });

    expect(items.map((i) => i.key)).toEqual(["optimizer:x1"]);
    expect(items[0]).toMatchObject({
      kind: "optimizer",
      status: "running",
      title: "Optimizer · bot · iteration 2/5",
    });
  });

  it("includes terminal jobs when activeOnly is false (all-jobs page)", () => {
    const items = buildRunningJobs(
      {
        evalRuns: [
          {
            createdAt: new Date("2026-07-20T10:00:00.000Z"),
            id: "e2",
            name: "Done",
            status: "completed",
          },
        ],
        finetuning: [
          {
            createdAt: new Date("2026-07-20T08:00:00.000Z"),
            id: "f2",
            name: "old",
            status: "succeeded",
          },
        ],
      },
      { activeOnly: false }
    );

    expect(items.map((i) => i.key)).toEqual(["eval:e2", "ft:f2"]);
  });
});

describe("detectCompletedJobs", () => {
  it("emits jobs that left the active set with a terminal status", () => {
    const prev = new Map<string, RunningJobItem>([
      [
        "ft:g1",
        {
          groupId: "g1",
          id: "f1",
          key: "ft:g1",
          kind: "finetuning",
          startedAt: "2026-07-20T11:05:00.000Z",
          status: "running",
          title: "swift fox · 2 models",
        },
      ],
    ]);

    const completed = detectCompletedJobs(prev, [], {
      finetuning: [
        { groupId: "g1", id: "f1", name: "swift fox", status: "succeeded" },
        { groupId: "g1", id: "f2", name: "swift fox", status: "succeeded" },
      ],
    });

    expect(completed).toHaveLength(1);
    expect(completed[0]?.status).toBe("succeeded");
    expect(completionToastCopy(completed[0]!)).toEqual({
      description: "swift fox · 2 models",
      message: "Fine-tuning finished",
    });
  });

  it("waits for every model in a fine-tuning group to finish", () => {
    const prev = new Map<string, RunningJobItem>([
      [
        "ft:g1",
        {
          groupId: "g1",
          id: "f1",
          key: "ft:g1",
          kind: "finetuning",
          startedAt: "2026-07-20T11:05:00.000Z",
          status: "running",
          title: "swift fox · 2 models",
        },
      ],
    ]);

    const completed = detectCompletedJobs(prev, [], {
      finetuning: [
        { groupId: "g1", id: "f1", name: "swift fox", status: "succeeded" },
        { groupId: "g1", id: "f2", name: "swift fox", status: "running" },
      ],
    });

    expect(completed).toHaveLength(0);
  });

  it("ignores jobs that are still non-terminal but missing from the active filter", () => {
    const prev = new Map<string, RunningJobItem>([
      [
        "ft:f1",
        {
          id: "f1",
          key: "ft:f1",
          kind: "finetuning",
          startedAt: "2026-07-20T11:05:00.000Z",
          status: "running",
          title: "swift fox",
        },
      ],
    ]);

    const completed = detectCompletedJobs(prev, [], {
      finetuning: [{ id: "f1", status: "preparing" }],
    });

    expect(completed).toHaveLength(0);
  });

  it("emits optimizer experiment completions with dedicated toast copy", () => {
    const prev = new Map<string, RunningJobItem>([
      [
        "optimizer:x1",
        {
          id: "x1",
          key: "optimizer:x1",
          kind: "optimizer",
          startedAt: "2026-07-20T09:55:00.000Z",
          status: "running",
          title: "Optimizer · bot · iteration 2/5",
        },
      ],
    ]);

    const completed = detectCompletedJobs(prev, [], {
      optimizerExperiments: [
        {
          capabilityName: "bot",
          currentIteration: 5,
          id: "x1",
          numIterations: 5,
          status: "completed",
        },
      ],
    });

    expect(completed).toHaveLength(1);
    expect(completed[0]?.status).toBe("completed");
    expect(completionToastCopy(completed[0]!)).toEqual({
      description: "Optimizer · bot · iteration 5/5",
      message: "Optimization finished",
    });
  });

  it("ignores optimizer experiments that are paused rather than finished", () => {
    const prev = new Map<string, RunningJobItem>([
      [
        "optimizer:x1",
        {
          id: "x1",
          key: "optimizer:x1",
          kind: "optimizer",
          startedAt: "2026-07-20T09:55:00.000Z",
          status: "running",
          title: "Optimizer · bot · iteration 2/5",
        },
      ],
    ]);

    const completed = detectCompletedJobs(prev, [], {
      optimizerExperiments: [{ id: "x1", status: "paused" }],
    });

    expect(completed).toHaveLength(0);
  });
});
