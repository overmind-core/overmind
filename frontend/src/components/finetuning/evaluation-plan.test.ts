import { describe, expect, it } from "vitest";

import type { FinetuningJudgeEvalRow } from "@/hooks/use-finetuning";
import type { FinetuningJobList } from "@/openapi";
import { evaluationDisplayRows, hasPendingEvaluations } from "./evaluation-plan";

const job = {
  baseModel: "Qwen/Qwen3.5-9B",
  capability: "capability",
  evalDataset: "data",
  evalIncumbentAfter: false,
  evalIncumbentBefore: true,
  evalModelAfter: true,
  evalModelBefore: false,
  evalSet: "set",
  id: "job",
  status: "queued",
} as FinetuningJobList;

describe("planned training evaluations", () => {
  it("shows only selected evaluations before runs exist", () => {
    const rows = evaluationDisplayRows(job, []);
    expect(rows.map((row) => row.kind)).toEqual(["baseline", "final"]);
    expect(rows.map((row) => row.waitingReason)).toEqual([
      "Waiting to start",
      "Waiting for training",
    ]);
    expect(rows.every((row) => !row.eval_run_id && row.aggregate_score == null)).toBe(true);
  });

  it("replaces a planned evaluation with the real run and preserves its result", () => {
    const actual: FinetuningJudgeEvalRow = {
      aggregate_score: 0.8,
      eval_run_id: "run",
      id: "eval",
      kind: "baseline",
      status: "completed",
    };
    const rows = evaluationDisplayRows({ ...job, status: "deploying" }, [actual]);
    expect(rows).toHaveLength(2);
    expect(rows[0]).toBe(actual);
    expect(rows[1].waitingReason).toBe("Waiting for deployment");
  });

  it("separates all four selected targets and uses the base without a capability", () => {
    const rows = evaluationDisplayRows(
      { ...job, evalIncumbentAfter: true, evalModelBefore: true },
      []
    );
    expect(rows.map((row) => row.kind)).toEqual([
      "baseline",
      "model_before",
      "incumbent_after",
      "final",
    ]);
    const base = evaluationDisplayRows({ ...job, capability: null }, [])[0];
    expect(base.label).toBe("Base model · before");
    expect(base.model_id).toBe(job.baseModel);
  });

  it.each(["failed", "cancelled"] as const)("stops waiting when training is %s", (status) => {
    const rows = evaluationDisplayRows({ ...job, status }, []);
    expect(rows.every((row) => row.status === "skipped")).toBe(true);
    expect(hasPendingEvaluations({ ...job, status }, [])).toBe(false);
  });

  it("shows no plan when evals are disabled or no eval data is linked", () => {
    expect(
      evaluationDisplayRows({ ...job, evalIncumbentBefore: false, evalModelAfter: false }, [])
    ).toEqual([]);
    expect(evaluationDisplayRows({ ...job, evalDataset: null }, [])).toEqual([]);
  });

  it("keeps polling after deployment until selected evals finish", () => {
    const deployed = { ...job, status: "succeeded" as const };
    const before = { id: "b", kind: "baseline", status: "completed" };
    expect(hasPendingEvaluations(deployed, [before])).toBe(true);
    expect(
      hasPendingEvaluations(deployed, [before, { id: "f", kind: "final", status: "running" }])
    ).toBe(true);
    expect(
      hasPendingEvaluations(deployed, [before, { id: "f", kind: "final", status: "completed" }])
    ).toBe(false);
  });

  it("keeps a missing baseline evaluation pending after training has run", () => {
    const deployed = { ...job, status: "succeeded" as const };
    const final = { id: "f", kind: "final", status: "completed" };
    const rows = evaluationDisplayRows(deployed, [final]);
    expect(rows[0].status).toBe("pending");
    expect(rows[0].waitingReason).toBe("Waiting for model");
    expect(hasPendingEvaluations(deployed, [final])).toBe(true);
  });
});
