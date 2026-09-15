/** Active / recently-finished job rows for the header jobs popover. */

import { userFacingJobError } from "@/components/finetuning/train/model-config";

export type RunningJobKind = "finetuning" | "eval" | "optimizer";

export interface RunningJobItem {
  /** Stable across polls. */
  key: string;
  kind: RunningJobKind;
  id: string;
  /** FT monitor group; equals `id` for lone jobs. */
  groupId?: string;
  title: string;
  status: string;
  error?: string;
  /** ISO timestamp; rows sort on it as a string. */
  startedAt: string;
}

const FT_ACTIVE = new Set(["queued", "preparing", "running", "deploying", "validating_files"]);
const FT_TERMINAL = new Set(["succeeded", "failed", "cancelled"]);
const EVAL_ACTIVE = new Set(["pending", "running"]);
const EVAL_TERMINAL = new Set(["completed", "failed", "cancelled"]);
// overbae/models/optimizer.py OptimizerExperiment.Status — every non-terminal
// status counts as in-flight.
const OPTIMIZER_TERMINAL = new Set(["completed", "failed", "cancelled"]);

function isActiveFinetuningStatus(status: string | undefined): boolean {
  return !!status && FT_ACTIVE.has(status);
}

function isActiveEvalStatus(status: string | undefined): boolean {
  return !!status && EVAL_ACTIVE.has(status);
}

function isActiveOptimizerStatus(status: string | undefined): boolean {
  return !!status && !OPTIMIZER_TERMINAL.has(status) && status !== "paused";
}

export function isTerminalFinetuningStatus(status: string | undefined): boolean {
  return !!status && FT_TERMINAL.has(status);
}

function isTerminalEvalStatus(status: string | undefined): boolean {
  return !!status && EVAL_TERMINAL.has(status);
}

function isTerminalOptimizerStatus(status: string | undefined): boolean {
  return !!status && OPTIMIZER_TERMINAL.has(status);
}

function foldOptimizerStatus(status: string): string {
  if (OPTIMIZER_TERMINAL.has(status) || status === "paused") return status;
  if (status === "scheduled") return "pending";
  return "running";
}

type OptimizerExperimentRow = {
  id: string;
  capabilityName?: string | null;
  status: string;
  failureReason?: string | null;
  currentIteration?: number;
  numIterations?: number;
  createdAt?: Date | string;
};

function optimizerTitle(exp: OptimizerExperimentRow): string {
  const capability = exp.capabilityName?.trim();
  const base = capability ? `Optimizer · ${capability}` : "Optimizer experiment";
  if (!exp.numIterations) return base;
  const current = Math.min(exp.currentIteration ?? 0, exp.numIterations);
  return `${base} · iteration ${current}/${exp.numIterations}`;
}

export function displayJobStatus(status: string): string {
  if (status === "finished") return "completed";
  return status;
}

function toIso(value: Date | string | null | undefined): string {
  if (!value) return "";
  if (value instanceof Date) return value.toISOString();
  return value;
}

type FinetuningJobRow = {
  id: string;
  groupId?: string | null;
  name?: string | null;
  baseModel?: string | null;
  errorMessage?: string | null;
  status: string;
  startedAt?: Date | string | null;
  createdAt: Date | string;
  updatedAt?: Date | string | null;
};

/** Most "in progress" first — the representative of a multi-model run. */
const FT_STATUS_RANK = ["running", "deploying", "validating_files", "preparing", "queued"] as const;

function ftGroupId(job: { id: string; groupId?: string | null }): string {
  return job.groupId ?? job.id;
}

function ftGroupTitle(members: Array<{ name?: string | null; baseModel?: string | null }>): string {
  const name = members.map((m) => m.name?.trim()).find(Boolean);
  const base = name || members.map((m) => m.baseModel?.trim()).find(Boolean) || "Fine-tuning job";
  return members.length > 1 ? `${base} · ${members.length} models` : base;
}

function pickFtRepresentative<T extends { status: string }>(members: T[]): T {
  for (const status of FT_STATUS_RANK) {
    const hit = members.find((m) => m.status === status);
    if (hit) return hit;
  }
  return members[0]!;
}

function foldFtGroupStatus(members: Array<{ status: string }>): string {
  if (members.some((m) => isActiveFinetuningStatus(m.status))) {
    return pickFtRepresentative(members.filter((m) => isActiveFinetuningStatus(m.status))).status;
  }
  if (members.some((m) => m.status === "failed")) return "failed";
  if (members.some((m) => m.status === "cancelled")) return "cancelled";
  if (members.every((m) => m.status === "succeeded")) return "succeeded";
  return members[0]?.status ?? "succeeded";
}

export function buildRunningJobs(
  input: {
    finetuning?: FinetuningJobRow[];
    evalRuns?: Array<{
      id: string;
      name: string;
      capabilityName?: string | null;
      error?: string | null;
      status: string;
      createdAt: Date | string;
      /** "manual" / "optimizer". */
      origin?: string | null;
    }>;
    optimizerExperiments?: OptimizerExperimentRow[];
  },
  opts?: { activeOnly?: boolean }
): RunningJobItem[] {
  const activeOnly = opts?.activeOnly ?? true;
  const items: RunningJobItem[] = [];

  // Multi-model launches share groupId — one row per experiment, not per model.
  const ftGroups = new Map<string, FinetuningJobRow[]>();
  for (const job of input.finetuning ?? []) {
    const gid = ftGroupId(job);
    const list = ftGroups.get(gid);
    if (list) list.push(job);
    else ftGroups.set(gid, [job]);
  }

  for (const [gid, members] of ftGroups) {
    const activeMembers = members.filter((m) => isActiveFinetuningStatus(m.status));
    if (!activeOnly || activeMembers.length > 0) {
      const pool = activeOnly ? activeMembers : members;
      if (pool.length > 0) {
        const rep = pickFtRepresentative(pool);
        const startedAt = pool
          .map((m) => toIso(m.startedAt ?? m.createdAt))
          .filter(Boolean)
          .sort()[0];

        items.push({
          error: pool.map((m) => m.errorMessage?.trim()).find(Boolean)
            ? userFacingJobError(pool.map((m) => m.errorMessage?.trim()).find(Boolean))
            : undefined,
          groupId: gid,
          id: rep.id,
          key: `ft:${gid}`,
          kind: "finetuning",
          startedAt: startedAt || toIso(rep.startedAt ?? rep.createdAt),
          status: activeOnly ? rep.status : foldFtGroupStatus(members),
          title: ftGroupTitle(members),
        });
      }
    }
  }

  for (const run of input.evalRuns ?? []) {
    // Candidate runs are internals; the experiment itself is the progress row.
    if (run.origin === "optimizer") continue;
    if (activeOnly && !isActiveEvalStatus(run.status)) continue;
    const capability = run.capabilityName?.trim();
    items.push({
      error: run.error?.trim() || undefined,
      id: run.id,
      key: `eval:${run.id}`,
      kind: "eval",
      startedAt: toIso(run.createdAt),
      status: run.status,
      title: run.name?.trim() || (capability ? `${capability} eval` : "Eval run"),
    });
  }

  for (const exp of input.optimizerExperiments ?? []) {
    if (activeOnly && !isActiveOptimizerStatus(exp.status)) continue;
    items.push({
      error: exp.failureReason?.trim() || undefined,
      id: exp.id,
      key: `optimizer:${exp.id}`,
      kind: "optimizer",
      startedAt: toIso(exp.createdAt),
      status: foldOptimizerStatus(exp.status),
      title: optimizerTitle(exp),
    });
  }

  items.sort((a, b) => (a.startedAt < b.startedAt ? 1 : a.startedAt > b.startedAt ? -1 : 0));
  return items;
}

/** Jobs that left the active set since the last poll. */
export function detectCompletedJobs(
  previouslyActive: Map<string, RunningJobItem>,
  currentlyActive: RunningJobItem[],
  latest: {
    finetuning?: Array<{
      id: string;
      groupId?: string | null;
      status: string;
      name?: string | null;
      baseModel?: string | null;
    }>;
    evalRuns?: Array<{ id: string; status: string; name?: string }>;
    datasetAnalyses?: Array<{
      id: string;
      status: string;
      datasetName?: string | null;
    }>;
    optimizerExperiments?: OptimizerExperimentRow[];
  }
): RunningJobItem[] {
  const activeKeys = new Set(currentlyActive.map((j) => j.key));
  const completed: RunningJobItem[] = [];

  for (const [key, prev] of previouslyActive) {
    if (activeKeys.has(key)) continue;

    if (prev.kind === "finetuning") {
      const gid = prev.groupId ?? prev.id;
      const members = (latest.finetuning ?? []).filter(
        (j) => ftGroupId(j) === gid || j.id === prev.id
      );
      if (members.some((j) => !isTerminalFinetuningStatus(j.status))) continue;
      const status = members.length > 0 ? foldFtGroupStatus(members) : "succeeded";
      completed.push({
        ...prev,
        status,
        title: members.length > 0 ? ftGroupTitle(members) : prev.title,
      });
      continue;
    }

    if (prev.kind === "optimizer") {
      const row = latest.optimizerExperiments?.find((e) => e.id === prev.id);
      if (row && !isTerminalOptimizerStatus(row.status)) continue;
      completed.push({
        ...prev,
        status: row ? foldOptimizerStatus(row.status) : "completed",
        title: row ? optimizerTitle(row) : prev.title,
      });
      continue;
    }

    if (prev.kind === "eval") {
      const row = latest.evalRuns?.find((j) => j.id === prev.id);
      if (row && !isTerminalEvalStatus(row.status)) continue;
      completed.push({
        ...prev,
        status: row?.status ?? "completed",
        title: row?.name?.trim() || prev.title,
      });
    }
  }

  return completed;
}

export function completionToastCopy(job: RunningJobItem): { message: string; description: string } {
  const ok =
    job.status === "succeeded" || job.status === "completed" || job.status === "finished"
      ? "finished"
      : job.status === "cancelled"
        ? "was cancelled"
        : "failed";
  const kind =
    job.kind === "finetuning" ? "Fine-tuning" : job.kind === "eval" ? "Eval run" : "Optimization";
  return {
    description: job.title,
    message: `${kind} ${ok}`,
  };
}
