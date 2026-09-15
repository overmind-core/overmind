/** Live serving statuses — anything still in the deploy pipeline or ready. */
const LIVE_DEPLOY_STATUSES = new Set(["queued", "deploying", "warming", "ready"]);

type CapabilityNudgeKind = "deploy" | "finetune" | "optimise" | "upload";

export type CapabilityNudge = {
  kind: CapabilityNudgeKind;
  /** Succeeded job to deep-link when no deployed model row exists. */
  jobId?: string;
  /** Deployed model UUID when a non-live row exists (failed/deleted) for redeploy. */
  modelId?: string;
};

/** The product's contract, or null while nothing has run. */
type DatasetLike = { contract: string | null };
type JobLike = { id: string; status: string; createdAt?: string | Date };
type ModelLike = { id: string; status: string; finetuningJobId?: string | null };

/** First match wins, most-advanced step first, so the order below is significant. */
export function pickCapabilityNudge({
  datasets,
  finetuningJobs,
  deployedModels,
  optimiserExperimentCount,
  flags,
}: {
  datasets: DatasetLike[];
  finetuningJobs: JobLike[];
  deployedModels: ModelLike[];
  optimiserExperimentCount: number;
  flags: {
    datasets: boolean;
    evaluations: boolean;
    finetuning: boolean;
    inference: boolean;
  };
}): CapabilityNudge | null {
  const hasLive = deployedModels.some((m) => LIVE_DEPLOY_STATUSES.has(m.status));
  const succeeded = finetuningJobs
    .filter((j) => j.status === "succeeded")
    .sort((a, b) => +new Date(b.createdAt ?? 0) - +new Date(a.createdAt ?? 0));

  if (flags.finetuning && flags.inference && succeeded.length > 0 && !hasLive) {
    const job = succeeded[0];
    const model = deployedModels.find((m) => m.finetuningJobId === job.id);
    return { jobId: job.id, kind: "deploy", modelId: model?.id };
  }

  const hasFtDataset = datasets.some((d) => d.contract === "train");
  if (flags.finetuning && hasFtDataset && finetuningJobs.length === 0) {
    return { kind: "finetune" };
  }

  const hasEvalDataset = datasets.some((d) => d.contract === "eval");
  if (flags.evaluations && hasEvalDataset && optimiserExperimentCount === 0) {
    return { kind: "optimise" };
  }

  if (flags.datasets && datasets.length === 0) {
    return { kind: "upload" };
  }

  return null;
}
