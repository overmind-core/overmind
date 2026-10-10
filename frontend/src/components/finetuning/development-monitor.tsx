import { useState } from "react";

import { useInfiniteQuery, useQuery } from "@tanstack/react-query";

import { trackEvent } from "@/analytics";
import apiClient from "@/client";
import { ConfusionMatrixGrid } from "@/components/finetuning/class-metrics";
import { MetricSeriesChart } from "@/components/finetuning/finetuning-charts";
import { isTerminalStatus } from "@/components/finetuning/job-snapshot";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { seriesColor } from "@/lib/colors";
import { errorMessage } from "@/lib/notify";
import {
  FinetuningJobsMonitoringEvidenceRetrieveProbeEnum,
  type TrainingCheck,
  type TrainingMonitoring,
} from "@/openapi";

function number(value: unknown, percent = false) {
  return typeof value === "number" && Number.isFinite(value)
    ? percent
      ? `${(value * 100).toFixed(1)}%`
      : value.toFixed(4)
    : "Not measured";
}

export function DevelopmentMonitor({ jobId, status }: { jobId: string; status: string }) {
  const [selected, setSelected] = useState<TrainingCheck | null>(null);
  const [offset, setOffset] = useState(0);
  const [probe, setProbe] = useState<FinetuningJobsMonitoringEvidenceRetrieveProbeEnum | null>(
    null
  );
  const [probeOffset, setProbeOffset] = useState(0);
  const history = useInfiniteQuery({
    getNextPageParam: (page: TrainingMonitoring) => page.nextOffset ?? undefined,
    initialPageParam: 0,
    queryFn: ({ pageParam }) =>
      apiClient.finetuningJobs.finetuningJobsMonitoringRetrieve({
        id: jobId,
        limit: 25,
        offset: pageParam,
      }),
    queryKey: ["training-monitoring", jobId],
    refetchInterval: isTerminalStatus(status) ? false : 5_000,
  });
  const evidence = useQuery({
    enabled: Boolean(selected?.evidenceAvailable),
    queryFn: () =>
      apiClient.finetuningJobs.finetuningJobsMonitoringEvidenceRetrieve({
        check: selected!.id,
        id: jobId,
        limit: 10,
        offset,
      }),
    queryKey: ["training-monitoring-evidence", jobId, selected?.id, offset],
  });
  const sample = useQuery({
    enabled: probe !== null,
    queryFn: () =>
      apiClient.finetuningJobs.finetuningJobsMonitoringEvidenceRetrieve({
        id: jobId,
        limit: 25,
        offset: probeOffset,
        probe: probe!,
      }),
    queryKey: ["training-monitoring-sample", jobId, probe, probeOffset],
  });
  const checks = history.data?.pages.flatMap((page) => page.checks) ?? [];
  const first = history.data?.pages[0];
  const checkpoints = first?.checkpoints ?? [];
  const current = first?.current;
  const loss = checks
    .filter(
      (check) =>
        check.state === "completed" &&
        check.stream === "development" &&
        typeof check.metrics?.eval_loss === "number"
    )
    .map((check) => ({ step: check.step, value: check.metrics.eval_loss }));
  const generated = selected?.metrics?.generation;
  const paired = selected?.metrics?.paired_generation;
  const contractProbe = ["exact_match", "json_schema", "json_fields"].includes(
    first?.policy?.generation?.kind
  );

  return (
    <Card className="flex flex-col gap-4 p-4">
      <div>
        <h3 className="text-sm font-medium">Development monitoring</h3>
        <p className="mt-1 text-xs text-muted-foreground">
          Development evidence · final benchmark reported separately
        </p>
      </div>
      {history.isPending && <Skeleton className="h-24" />}
      {history.error && <Alert variant="destructive">{errorMessage(history.error)}</Alert>}
      {current && (
        <dl className="grid grid-cols-2 gap-3 text-xs sm:grid-cols-4">
          <div>
            <dt className="text-muted-foreground">Next recorded check</dt>
            <dd>
              {typeof current.schedule?.next_step === "number"
                ? `Step ${current.schedule.next_step}`
                : "None scheduled"}
            </dd>
          </div>
          <div>
            <dt className="text-muted-foreground">Optimisation time</dt>
            <dd>
              {typeof current.optimizer_seconds === "number"
                ? `${current.optimizer_seconds.toFixed(1)} s`
                : "Not measured"}
            </dd>
          </div>
          <div>
            <dt className="text-muted-foreground">Monitoring time</dt>
            <dd>
              {typeof current.monitoring_seconds === "number"
                ? `${current.monitoring_seconds.toFixed(1)} s`
                : "Not measured"}
            </dd>
          </div>
          <div>
            <dt className="text-muted-foreground">Evidence collection</dt>
            <dd>{current.collection?.state ?? "Not observed"}</dd>
          </div>
        </dl>
      )}
      {current?.collection?.state === "unavailable" && (
        <Alert variant="warning">{current.collection.error}</Alert>
      )}
      {first && checks.length === 0 && (
        <p className="text-sm text-muted-foreground">No development checks recorded.</p>
      )}
      {loss.length > 1 && (
        <div>
          <p className="text-xs text-muted-foreground">
            Fixed-sample development loss · displayed checks
          </p>
          <MetricSeriesChart color={seriesColor(1)} data={loss} name="Development loss" />
        </div>
      )}
      {checks.length > 0 && (
        <div className="overflow-x-auto">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Step</TableHead>
                <TableHead>Check</TableHead>
                <TableHead>Loss</TableHead>
                <TableHead>{contractProbe ? "Contract pass rate" : "Generated accuracy"}</TableHead>
                <TableHead>Loss coverage</TableHead>
                <TableHead>Evidence</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {checks.map((check) => (
                <TableRow key={check.id}>
                  <TableCell className="tabular-nums">{check.step}</TableCell>
                  <TableCell>
                    <span>
                      {check.state} ·{" "}
                      {check.stream === "final_development" ? "full development" : "fixed sample"}
                    </span>
                    {check.error?.message && (
                      <p className="mt-1 text-xs text-destructive">{check.error.message}</p>
                    )}
                  </TableCell>
                  <TableCell className="tabular-nums">{number(check.metrics?.eval_loss)}</TableCell>
                  <TableCell className="tabular-nums">
                    {number(
                      contractProbe
                        ? check.metrics?.generation?.pass_rate
                        : check.metrics?.generation?.accuracy,
                      true
                    )}
                  </TableCell>
                  <TableCell className="tabular-nums">
                    {check.coverage?.scored ?? "—"} / {check.coverage?.expected ?? "—"}
                  </TableCell>
                  <TableCell>
                    <Button
                      aria-label={`Inspect step ${check.step}`}
                      onClick={() => {
                        setSelected(check);
                        setOffset(0);
                        trackEvent("training_development_evidence_opened", {
                          check_id: check.id,
                          job_id: jobId,
                          state: check.state,
                        });
                      }}
                      size="sm"
                      variant="outline"
                    >
                      Inspect
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
      {history.hasNextPage && (
        <Button
          className="self-start"
          disabled={history.isFetchingNextPage}
          onClick={() => void history.fetchNextPage()}
          variant="outline"
        >
          Load more checks
        </Button>
      )}
      {selected && (
        <section
          aria-label={`Step ${selected.step} evidence`}
          className="flex flex-col gap-3 border-t border-border pt-4"
        >
          <div className="flex items-center justify-between gap-3">
            <h4 className="text-sm font-medium">
              Step {selected.step} · {selected.stream.replaceAll("_", " ")}
            </h4>
            <Button onClick={() => setSelected(null)} size="sm" variant="outline">
              Close
            </Button>
          </div>
          <dl className="grid grid-cols-2 gap-3 text-xs sm:grid-cols-4">
            <div>
              <dt className="text-muted-foreground">Training reference loss</dt>
              <dd>{number(selected.metrics?.training_reference_loss)}</dd>
            </div>
            <div>
              <dt className="text-muted-foreground">Development gap</dt>
              <dd>{number(selected.metrics?.development_gap)}</dd>
            </div>
            <div>
              <dt className="text-muted-foreground">Generation coverage</dt>
              <dd>{number(generated?.coverage, true)}</dd>
            </div>
            <div>
              <dt className="text-muted-foreground">Macro F1</dt>
              <dd>{number(generated?.macro_f1)}</dd>
            </div>
          </dl>
          {typeof selected.metrics?.distribution_decisions === "number" && (
            <dl className="grid grid-cols-2 gap-3 text-xs sm:grid-cols-3">
              <div>
                <dt className="text-muted-foreground">
                  Brier · {selected.metrics.distribution_decisions} distributions
                </dt>
                <dd>{number(selected.metrics.brier)}</dd>
              </div>
              <div>
                <dt className="text-muted-foreground">
                  Expected-score MAE · {selected.metrics.mean_decisions} means
                </dt>
                <dd>{number(selected.metrics.expected_score_mae)}</dd>
              </div>
              <div>
                <dt className="text-muted-foreground">Hard-label accuracy</dt>
                <dd>{number(selected.metrics.hard_label_accuracy, true)}</dd>
              </div>
            </dl>
          )}
          {generated?.per_class && (
            <div className="overflow-x-auto">
              <Table aria-label="Development class metrics">
                <TableHeader>
                  <TableRow>
                    <TableHead>Label</TableHead>
                    <TableHead>Support</TableHead>
                    <TableHead>Precision</TableHead>
                    <TableHead>Recall</TableHead>
                    <TableHead>F1</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {Object.keys(generated.per_class).map((label) => {
                    const metrics = generated.per_class[label];
                    return (
                      <TableRow key={label}>
                        <TableCell>{label}</TableCell>
                        <TableCell>{metrics.support}</TableCell>
                        <TableCell>{number(metrics.precision)}</TableCell>
                        <TableCell>{number(metrics.recall)}</TableCell>
                        <TableCell>{number(metrics.f1)}</TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
              {generated.unrepresented_labels?.length > 0 && (
                <p className="mt-2 text-xs text-muted-foreground">
                  Unrepresented labels: {generated.unrepresented_labels.join(", ")}
                </p>
              )}
            </div>
          )}
          {generated?.labels?.length > 0 && generated.confusion_matrix && (
            <section aria-label="Development confusion matrix">
              <ConfusionMatrixGrid
                data={{ labels: generated.labels, matrix: generated.confusion_matrix }}
              />
            </section>
          )}
          {paired && (
            <div className="text-xs">
              <p>
                {paired.improved} improved · {paired.regressed} regressed · {paired.paired} paired ·{" "}
                {paired.unpaired} unpaired
              </p>
              <p className="mt-1 text-muted-foreground">
                {contractProbe ? "Pass-rate change" : "Accuracy change"}:{" "}
                {number(paired.delta, true)} · {paired.groups} groups
              </p>
              {paired.interval_95 && (
                <p className="mt-1 text-muted-foreground">
                  95% descriptive interval: {number(paired.interval_95[0], true)} to{" "}
                  {number(paired.interval_95[1], true)}
                </p>
              )}
            </div>
          )}
          <details className="text-xs">
            <summary className="cursor-pointer">Metrics, findings and sample identities</summary>
            <pre className="mt-2 max-h-72 overflow-auto whitespace-pre-wrap break-all">
              {JSON.stringify(
                {
                  coverage: selected.coverage,
                  evidence_sha256: selected.evidenceSha256,
                  facts: selected.facts,
                  metrics: selected.metrics,
                  observed_at: selected.observedAt,
                  policy: selected.policyFingerprint,
                  sample: selected.sampleFingerprint,
                },
                null,
                2
              )}
            </pre>
          </details>
          {evidence.isFetching && (
            <p className="text-xs text-muted-foreground">Loading examples…</p>
          )}
          {evidence.error && <Alert variant="destructive">{errorMessage(evidence.error)}</Alert>}
          {!selected.evidenceAvailable && (
            <p className="text-xs text-muted-foreground">
              No prediction examples retained for this check.
            </p>
          )}
          {evidence.data?.items.map((item, index) => (
            <dl
              className="grid gap-2 border-t border-border/70 pt-3 text-xs sm:grid-cols-3"
              key={`${selected.id}:${offset + index}`}
            >
              {(Array.isArray(item.probabilities)
                ? [
                    "probabilities",
                    "option_values",
                    item.target_mean !== undefined ? "target_mean" : "target_probabilities",
                  ]
                : ["input", "output", "reference"]
              ).map((field) => (
                <div className="min-w-0" key={field}>
                  <dt className="text-muted-foreground">
                    {field[0].toUpperCase() + field.slice(1).replaceAll("_", " ")}
                  </dt>
                  <dd className="mt-1 max-h-56 overflow-auto whitespace-pre-wrap break-words">
                    {item[field] == null
                      ? "Not recorded"
                      : typeof item[field] === "object"
                        ? JSON.stringify(item[field])
                        : String(item[field])}
                  </dd>
                </div>
              ))}
              <div className="sm:col-span-3">
                {item.status}
                {item.error?.message ? ` · ${item.error.message}` : ""}
                <details className="mt-2">
                  <summary className="cursor-pointer">Prediction receipt</summary>
                  <pre className="mt-2 max-h-56 overflow-auto whitespace-pre-wrap break-all">
                    {JSON.stringify(item, null, 2)}
                  </pre>
                </details>
              </div>
            </dl>
          ))}
          {evidence.data && (
            <div className="flex items-center gap-3 text-xs">
              <Button
                disabled={offset === 0}
                onClick={() => setOffset(Math.max(0, offset - 10))}
                size="sm"
                variant="outline"
              >
                Previous examples
              </Button>
              <span>
                {Math.min(offset + 1, evidence.data.count)}–
                {Math.min(offset + 10, evidence.data.count)} of {evidence.data.count}
              </span>
              <Button
                disabled={evidence.data.nextOffset === null}
                onClick={() => setOffset(evidence.data!.nextOffset!)}
                size="sm"
                variant="outline"
              >
                Next examples
              </Button>
            </div>
          )}
        </section>
      )}
      {checkpoints.length > 0 && (
        <section className="border-t border-border pt-4">
          <h4 className="mb-2 text-sm font-medium">Retained checkpoints</h4>
          <ul className="flex flex-col gap-2 text-xs">
            {checkpoints.map((checkpoint) => (
              <li key={checkpoint.id}>
                Step {checkpoint.step} · {checkpoint.state} ·{" "}
                {checkpoint.verification?.reload_verified ? "Reload verified" : "Reload unverified"}
                {checkpoint.selected ? " · Selected" : ""} ·{" "}
                {checkpoint.verification?.resume_supported
                  ? "Resumable"
                  : "Optimizer resume not verified"}
                <details className="mt-1">
                  <summary className="cursor-pointer text-muted-foreground">
                    Artifact manifest
                  </summary>
                  <pre className="mt-2 max-h-56 overflow-auto whitespace-pre-wrap break-all">
                    {JSON.stringify(
                      {
                        identity: checkpoint.identity,
                        manifest: checkpoint.manifest,
                        verification: checkpoint.verification,
                      },
                      null,
                      2
                    )}
                  </pre>
                </details>
              </li>
            ))}
          </ul>
        </section>
      )}
      {first?.policy && (
        <details className="text-xs text-muted-foreground">
          <summary className="cursor-pointer">Frozen monitoring policy</summary>
          <pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap">
            {JSON.stringify(first.policy, null, 2)}
          </pre>
        </details>
      )}
      {first?.probes && Object.keys(first.probes).length > 0 && (
        <section
          aria-label="Frozen monitoring samples"
          className="flex flex-col gap-3 border-t border-border pt-4"
        >
          <h4 className="text-sm font-medium">Frozen samples</h4>
          <div className="flex flex-wrap gap-2">
            {Object.values(FinetuningJobsMonitoringEvidenceRetrieveProbeEnum)
              .filter((name) => first.probes[name])
              .map((name) => (
                <Button
                  aria-label={`Inspect ${name} sample`}
                  key={name}
                  onClick={() => {
                    setProbe(name);
                    setProbeOffset(0);
                  }}
                  size="sm"
                  variant="outline"
                >
                  {name.replaceAll("_", " ")} · {first.probes[name].actual_rows} /{" "}
                  {first.probes[name].population_rows}
                </Button>
              ))}
          </div>
          {sample.isFetching && (
            <p className="text-xs text-muted-foreground">Loading sample identities…</p>
          )}
          {sample.error && <Alert variant="destructive">{errorMessage(sample.error)}</Alert>}
          {sample.data && (
            <>
              <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-all text-xs">
                {JSON.stringify(sample.data.items, null, 2)}
              </pre>
              <div className="flex gap-2">
                <Button
                  disabled={probeOffset === 0}
                  onClick={() => setProbeOffset(Math.max(0, probeOffset - 25))}
                  size="sm"
                  variant="outline"
                >
                  Previous rows
                </Button>
                <Button
                  disabled={sample.data.nextOffset === null}
                  onClick={() => setProbeOffset(sample.data!.nextOffset!)}
                  size="sm"
                  variant="outline"
                >
                  Next rows
                </Button>
              </div>
            </>
          )}
        </section>
      )}
    </Card>
  );
}
