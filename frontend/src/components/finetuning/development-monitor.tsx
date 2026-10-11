import { useQuery } from "@tanstack/react-query";

import apiClient from "@/client";
import { MetricSeriesChart } from "@/components/finetuning/finetuning-charts";
import { isTerminalStatus } from "@/components/finetuning/job-snapshot";
import { MonitorChartCard } from "@/components/finetuning/monitor-chart-card";
import { Alert } from "@/components/ui/alert";
import { seriesColor } from "@/lib/colors";
import { errorMessage } from "@/lib/notify";
import type { TrainingCheck } from "@/openapi";

export function DevelopmentMonitor({
  jobId,
  status,
  modelName,
}: {
  jobId: string;
  status: string;
  modelName: string;
}) {
  const history = useQuery({
    queryFn: async ({ signal }) => {
      const checks: TrainingCheck[] = [];
      let offset: number | null = 0;
      while (offset !== null) {
        const page = await apiClient.finetuningJobs.finetuningJobsMonitoringRetrieve(
          { id: jobId, limit: 100, offset },
          { signal }
        );
        checks.push(...page.checks);
        offset = page.nextOffset;
      }
      return checks;
    },
    queryKey: ["training-validation-loss", jobId],
    refetchInterval: isTerminalStatus(status) ? false : 5_000,
  });
  const loss = (history.data ?? [])
    .filter(
      (check) =>
        check.state === "completed" &&
        check.stream === "development" &&
        typeof check.metrics?.eval_loss === "number" &&
        Number.isFinite(check.metrics.eval_loss)
    )
    .map((check) => ({ step: check.step, value: check.metrics.eval_loss }));

  return (
    <MonitorChartCard
      emptyHint="No validation loss recorded"
      help="Loss on the same held-out validation sample as training progresses. Lower is better. Final benchmark results are reported separately."
      isLoading={history.isPending}
      subtitle="fixed sample · per step"
      summary={
        history.error
          ? undefined
          : {
              kind: "loss",
              series: [{ color: seriesColor(1), id: jobId, name: modelName, points: loss }],
              terminal: isTerminalStatus(status),
            }
      }
      title="Validation loss"
    >
      {history.error ? (
        <Alert variant="destructive">{errorMessage(history.error)}</Alert>
      ) : loss.length > 0 ? (
        <MetricSeriesChart color={seriesColor(1)} data={loss} height={180} name={modelName} />
      ) : null}
    </MonitorChartCard>
  );
}
