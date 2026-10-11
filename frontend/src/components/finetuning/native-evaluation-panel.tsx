import { useState } from "react";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import apiClient from "@/client";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { errorMessage } from "@/lib/notify";
import type { FinetuningJobList } from "@/openapi";

type Metric = {
  candidate_minus_baseline: number;
  decisions: number;
  interval_95?: number[] | null;
};
type Slice = {
  expected: number;
  paired_decisions: number;
  baseline_only: number;
  candidate_only: number;
  metrics: Record<string, Metric>;
};

export function NativeEvaluationPanel({
  job,
  projectId,
}: {
  job: FinetuningJobList;
  projectId: string;
}) {
  const [configure, setConfigure] = useState(false);
  const [metricView, setMetricView] = useState<"raw" | "calibrated">("raw");
  const [calibration, setCalibration] = useState("");
  const [final, setFinal] = useState("");
  const qc = useQueryClient();
  const datasets = useQuery({
    enabled: configure,
    queryFn: async () => {
      const all = [];
      for (let page = 1; ; page++) {
        const response = await apiClient.datasets.datasetsList({
          intent: "eval",
          page,
          pageSize: 200,
          project: projectId,
        });
        all.push(...response.results);
        if (!response.next) return all;
      }
    },
    queryKey: ["native-evaluation-suites", projectId],
  });
  const schedule = useMutation({
    mutationFn: () =>
      apiClient.finetuningJobs.finetuningJobsNativeEvaluationCreate({
        id: job.id,
        nativeEvaluationRequestRequest: { calibrationCell: calibration, finalCell: final },
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["finetuning-jobs"] });
      void qc.invalidateQueries({ queryKey: ["finetuning-job", job.id] });
      setConfigure(false);
    },
  });
  const plan = job.nativeEvaluation;
  const suites = plan?.config?.suites as
    | Record<string, { cell: string; dataset?: string; rows: number }>
    | undefined;
  const slices = plan?.results?.[metricView]?.benchmarks as Record<string, Slice> | undefined;
  return (
    <Card className="flex flex-col gap-3 p-4">
      <h3 className="text-sm font-medium">{job.name || job.baseModel} · Native evaluation</h3>
      {plan ? (
        <>
          <p className="text-sm">
            {plan.state === "waiting_for_checkpoint"
              ? "Scheduled after checkpoint verification"
              : plan.state.replaceAll("_", " ")}
          </p>
          <dl className="flex flex-wrap gap-6 text-sm">
            {Object.entries(suites ?? {}).map(([role, suite]) => (
              <div key={role}>
                <dt className="capitalize text-muted-foreground">{role}</dt>
                <dd>
                  {suite.rows.toLocaleString()} decisions ·{" "}
                  {suite.dataset ? (
                    <a
                      className="underline underline-offset-2"
                      href={`/datasets/${encodeURIComponent(suite.dataset)}?projectId=${encodeURIComponent(projectId)}&cell=${encodeURIComponent(suite.cell)}`}
                    >
                      Open {role} dataset
                    </a>
                  ) : (
                    <span className="font-mono text-xs">{suite.cell}</span>
                  )}
                </dd>
              </div>
            ))}
          </dl>
          <p className="text-xs text-muted-foreground">
            Unchanged base and trained candidate. Calibration is fitted on the calibration suite
            before final predictions.
          </p>
          {plan.error && (
            <p className="text-sm text-destructive" role="alert">
              {plan.error}
            </p>
          )}
          {plan.config?.boundaries && (
            <details className="text-xs">
              <summary className="cursor-pointer focus-visible:outline focus-visible:outline-ring">
                Training overlap and coverage boundaries
              </summary>
              <pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap">
                {JSON.stringify(
                  { boundaries: plan.config.boundaries, limits: plan.config.contamination_limits },
                  null,
                  2
                )}
              </pre>
            </details>
          )}
          {plan.results?.calibrated && (
            <div className="flex gap-2">
              {(["raw", "calibrated"] as const).map((view) => (
                <Button
                  aria-pressed={metricView === view}
                  key={view}
                  onClick={() => setMetricView(view)}
                  size="sm"
                  variant={metricView === view ? "secondary" : "outline"}
                >
                  {view === "raw" ? "Raw metrics" : "Calibrated metrics"}
                </Button>
              ))}
            </div>
          )}
          {slices && (
            <div className="-mx-4 overflow-x-auto">
              <table className="w-full text-left text-xs [&_td:last-child]:pr-4 [&_th:first-child]:pl-4 [&_th:last-child]:pr-4">
                <caption className="px-4 pb-2 text-left text-muted-foreground">
                  {metricView === "raw" ? "Raw" : "Calibrated"} paired metrics · candidate minus
                  base · negative cross entropy and Brier differences are better
                </caption>
                <thead>
                  <tr>
                    <th className="p-2">Benchmark</th>
                    <th className="p-2">Paired / expected</th>
                    <th className="p-2">Cross entropy Δ</th>
                    <th className="p-2">Brier Δ</th>
                    <th className="p-2">Unpaired</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(slices).map(([name, slice]) => (
                    <tr className="border-t border-border/70" key={name}>
                      <th className="p-2">{name}</th>
                      <td className="p-2 tabular-nums">
                        {slice.paired_decisions.toLocaleString()} /{" "}
                        {slice.expected.toLocaleString()}
                      </td>
                      {["cross_entropy", "brier"].map((metric) => (
                        <td className="p-2 tabular-nums" key={metric}>
                          {slice.metrics[metric]?.candidate_minus_baseline.toFixed(4) ??
                            "Not measured"}
                          {slice.metrics[metric]?.interval_95 && (
                            <div className="text-muted-foreground">
                              95% interval:{" "}
                              {slice.metrics[metric].interval_95
                                ?.map((n) => n.toFixed(4))
                                .join(" to ")}
                            </div>
                          )}
                        </td>
                      ))}
                      <td className="p-2">{slice.baseline_only + slice.candidate_only}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {plan.results?.calibrated && (
            <details className="text-xs">
              <summary className="cursor-pointer">Calibration, coverage and slice results</summary>
              <pre className="mt-2 max-h-96 overflow-auto whitespace-pre-wrap">
                {JSON.stringify({ calibration: plan.calibration, results: plan.results }, null, 2)}
              </pre>
            </details>
          )}
        </>
      ) : (
        <>
          <p className="text-sm text-muted-foreground">No native evaluation plan selected.</p>
          {!configure ? (
            <Button className="self-start" onClick={() => setConfigure(true)} variant="secondary">
              Select evaluation suites
            </Button>
          ) : (
            <>
              {(["Calibration", "Final"] as const).map((role) => (
                <div className="flex items-center gap-3" key={role}>
                  <span className="w-24 text-sm">{role}</span>
                  <Select
                    onValueChange={role === "Calibration" ? setCalibration : setFinal}
                    value={role === "Calibration" ? calibration : final}
                  >
                    <SelectTrigger aria-label={`${role} suite`}>
                      <SelectValue placeholder="Select a prepared dataset" />
                    </SelectTrigger>
                    <SelectContent>
                      {datasets.data
                        ?.filter((d) => d.active)
                        .map((d) => (
                          <SelectItem key={d.id} value={d.active!}>
                            {d.name} · {d.rows.toLocaleString()} rows
                          </SelectItem>
                        ))}
                    </SelectContent>
                  </Select>
                </div>
              ))}
              {datasets.isPending && <p className="text-sm">Loading suites…</p>}
              {datasets.error && <p role="alert">{errorMessage(datasets.error)}</p>}
              {schedule.error && (
                <p className="text-destructive" role="alert">
                  {errorMessage(schedule.error)}
                </p>
              )}
              <p className="text-xs text-muted-foreground">
                Schedules paid evaluation after checkpoint verification. Evaluation cost is not yet
                estimated. The selected versions and calibration method will be frozen.
              </p>
              <Button
                className="self-start"
                disabled={!calibration || !final || calibration === final || schedule.isPending}
                onClick={() => schedule.mutate()}
              >
                Schedule native evaluation
              </Button>
            </>
          )}
        </>
      )}
    </Card>
  );
}
