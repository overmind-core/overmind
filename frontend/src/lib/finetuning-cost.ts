export function jobCostUsd(job: { costUsd?: string | null }): number | null {
  if (job.costUsd == null || job.costUsd === "") return null;
  const n = Number(job.costUsd);
  return Number.isFinite(n) ? n : null;
}

export function groupCostUsd(jobs: Array<{ costUsd?: string | null }>): number | null {
  let sum = 0;
  let any = false;
  for (const j of jobs) {
    const c = jobCostUsd(j);
    if (c != null) {
      sum += c;
      any = true;
    }
  }
  return any ? sum : null;
}
