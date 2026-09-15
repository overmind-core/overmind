import type { FinetuningJobList } from "@/openapi";

const IN_PROGRESS_STATUSES = new Set(["queued", "preparing", "running", "deploying"]);

/** `succeeded` covers deployment too — the backend only sets it once the model is live. */
export const eligibleModelSwapJobs = (jobs: FinetuningJobList[]): FinetuningJobList[] =>
  jobs.filter((job) => job.status === "succeeded" && !!job.outputModelName);

export const hasInProgressModelSwapJobs = (jobs: FinetuningJobList[]): boolean =>
  jobs.some((job) => IN_PROGRESS_STATUSES.has(job.status));
