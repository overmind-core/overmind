import { describe, expect, it } from "vitest";

import type { ExperimentSnapshot } from "@/components/finetuning/job-snapshot";
import type { FinetuningJudgeEvalRow } from "@/hooks/use-finetuning";
import type { FinetuningJobList } from "@/openapi";
import {
  evaluationDisplayRows,
  groupEvaluationDisplayRows,
  hasPendingEvaluations,
} from "./evaluation-plan";

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

describe("shared training evaluations", () => {
  const baseline: FinetuningJudgeEvalRow = {
    aggregate_score: 0.8,
    baseline_delta: 0.2,
    created_at: "2026-09-21T10:32:00Z",
    eval_run_id: "shared-run",
    id: "baseline-a",
    kind: "baseline",
    model_id: "incumbent",
    status: "running",
    updated_at: "2026-09-21T10:32:00Z",
  };
  const snapshot = (id: string, rows: FinetuningJudgeEvalRow[]) =>
    ({
      job: { ...job, evalModelBefore: true, id },
      judgeEvals: rows,
    }) as ExperimentSnapshot;

  it("shows one shared incumbent and independent base/after evaluations", () => {
    const a = snapshot("a", [baseline]);
    const b = snapshot("b", [{ ...baseline, id: "baseline-b" }]);
    const rows = groupEvaluationDisplayRows([a, b]);
    expect(rows).toHaveLength(5);
    expect(rows[0].snapshots).toEqual([a, b]);
    expect(rows[0].row.eval_run_id).toBe("shared-run");
    expect(rows[0].row.baseline_delta).toBeNull();
    expect(rows.filter(({ row }) => row.kind === "model_before")).toHaveLength(2);
    expect(rows.filter(({ row }) => row.kind === "final")).toHaveLength(2);
    expect(baseline.baseline_delta).toBe(0.2);
  });

  it("keeps per-experiment comparison scores in the focused view", () => {
    const a = snapshot("a", [baseline]);
    const rows = groupEvaluationDisplayRows([a]);
    expect(rows[0].row).toBe(baseline);
    expect(rows[0].row.baseline_delta).toBe(0.2);
    expect(rows[0].snapshots).toEqual([a]);
  });

  it("does not infer sharing from a model name or a pending plan", () => {
    const rows = groupEvaluationDisplayRows([
      snapshot("a", [baseline]),
      snapshot("b", [{ ...baseline, eval_run_id: "other-run" }]),
      snapshot("c", []),
    ]);
    expect(rows.filter(({ row }) => row.kind === "baseline")).toHaveLength(3);
    expect(rows.every(({ snapshots }) => snapshots.length === 1)).toBe(true);
  });

  it("uses the latest result and the first launch time, not the later attachment time", () => {
    const rows = groupEvaluationDisplayRows([
      snapshot("a", [baseline]),
      snapshot("b", [
        {
          ...baseline,
          aggregate_score: 0.9,
          created_at: "2026-09-21T10:33:00Z",
          status: "completed",
          updated_at: "2026-09-21T10:34:00Z",
        },
      ]),
    ]);
    expect(rows[0].row).toMatchObject({
      aggregate_score: 0.9,
      created_at: baseline.created_at,
      status: "completed",
    });
  });

  it.each([
    false,
    true,
  ])("does not report a shared run cancelled for one detached job (%s)", (reverse) => {
    const snapshots = [
      snapshot("a", [baseline]),
      snapshot("b", [{ ...baseline, status: "cancelled", updated_at: "2026-09-21T10:34:00Z" }]),
    ];
    const rows = groupEvaluationDisplayRows(reverse ? snapshots.reverse() : snapshots);
    expect(rows[0].row.status).toBe("running");
    expect(rows[0].snapshots).toHaveLength(2);
  });
});
