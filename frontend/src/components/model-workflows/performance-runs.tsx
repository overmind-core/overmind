import { useState } from "react";

import { useMutation, useQuery } from "@tanstack/react-query";

import apiClient from "@/client";
import { Button } from "@/components/ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Failure, NumberField, SavedEvidence, WorkflowState } from "./common";

type ParticipantMeasurement = {
  expected_requests: number;
  valid_requests: number;
  failed_requests: number;
  unknown_requests: number;
  latency_ms: { p50: number | null; p95: number | null };
  warm_latency_ms?: { p50: number | null };
  first_request_ms: number | null;
  recorded_cost_usd: number;
  unknown_cost_requests: number;
  valid_decisions_per_active_second: number | null;
  conditions: unknown;
};
const shown = (v: number | null | undefined) => (v == null ? "Unmeasured" : v.toFixed(2));
export function PerformanceRuns({
  evaluation,
  projectId,
}: {
  evaluation: string;
  projectId: string;
}) {
  const [page, setPage] = useState(1);
  const [configure, setConfigure] = useState(false);
  const [settings, setSettings] = useState({
    amortizationDecisions: 1000000,
    concurrency: 1,
    questionsPerRequest: 1,
    repetitions: 3,
    sampleSize: 32,
    seed: 73491,
  });
  const [requestKey, setRequestKey] = useState(() => crypto.randomUUID());
  const query = useQuery({
    queryFn: () =>
      apiClient.decisionPerformance.decisionPerformanceList({
        evaluation,
        page,
        project: projectId,
      }),
    queryKey: ["decision-performance", evaluation, page],
    refetchInterval: 10000,
  });
  const start = useMutation({
    mutationFn: () =>
      apiClient.decisionPerformance.decisionPerformanceCreate({
        performanceRequestRequest: {
          evaluation,
          name: `Performance · ${settings.sampleSize} states × ${settings.repetitions}`,
          requestKey,
          workload: settings,
        },
      }),
    onSuccess: () => {
      setConfigure(false);
      setRequestKey(crypto.randomUUID());
      void query.refetch();
    },
  });
  const resume = useMutation({
    mutationFn: (id: string) =>
      apiClient.decisionPerformance.decisionPerformanceResumeCreate({ id }),
    onSuccess: () => {
      void query.refetch();
    },
  });
  return (
    <section className="flex flex-col gap-4 border-t border-border pt-4">
      <div className="flex items-center justify-between gap-3">
        <h3 className="font-medium">Cost and speed</h3>
        <Button onClick={() => setConfigure(!configure)} variant="outline">
          Measure workload
        </Button>
      </div>
      <p className="text-sm text-muted-foreground">
        Client latency through complete responses. Provider caching and hardware conditions are
        reported separately.
      </p>
      {configure && (
        <form
          className="grid gap-4 sm:grid-cols-3"
          onSubmit={(e) => {
            e.preventDefault();
            start.mutate();
          }}
        >
          {(
            [
              ["sampleSize", "States", 64],
              ["repetitions", "Repetitions", 10],
              ["concurrency", "Concurrency", 16],
              ["questionsPerRequest", "Questions per request", 16],
              ["amortizationDecisions", "Amortization volume (decisions)", 1000000000000],
              ["seed", "Seed", 4294967295],
            ] as const
          ).map(([key, label, max]) => (
            <NumberField
              key={key}
              label={label}
              max={max}
              min={key === "seed" ? 0 : 1}
              onChange={(value) => setSettings({ ...settings, [key]: value })}
              value={settings[key]}
            />
          ))}
          <div className="flex items-end">
            <Button disabled={start.isPending} type="submit">
              {start.isPending ? "Starting…" : "Start measurement"}
            </Button>
          </div>
        </form>
      )}
      <Failure error={query.error ?? start.error ?? resume.error} />
      {query.data?.results.map((run) => (
        <div className="flex flex-col gap-3" key={run.id}>
          <div className="flex items-center gap-3">
            <h4 className="text-sm font-medium">{run.name}</h4>
            <WorkflowState state={run.state} />
            {run.state === "failed" && (
              <Button
                disabled={resume.isPending}
                onClick={() => resume.mutate(run.id)}
                variant="outline"
              >
                Resume
              </Button>
            )}
          </div>
          <Failure error={run.error} />
          {run.results?.participants && (
            <div className="overflow-x-auto rounded-md border border-border">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Model</TableHead>
                    <TableHead>Valid / expected</TableHead>
                    <TableHead>Failures / unknown</TableHead>
                    <TableHead>p50 / p95 ms</TableHead>
                    <TableHead>First / warm p50 ms</TableHead>
                    <TableHead>Decisions / sec</TableHead>
                    <TableHead>Recorded USD</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {Object.entries(
                    run.results.participants as Record<string, ParticipantMeasurement>
                  ).map(([key, p]) => (
                    <TableRow key={key}>
                      <TableCell>{key}</TableCell>
                      <TableCell>
                        {p.valid_requests} / {p.expected_requests}
                      </TableCell>
                      <TableCell>
                        {p.failed_requests} / {p.unknown_requests}
                      </TableCell>
                      <TableCell>
                        {shown(p.latency_ms.p50)} / {shown(p.latency_ms.p95)}
                      </TableCell>
                      <TableCell>
                        {shown(p.first_request_ms)} / {shown(p.warm_latency_ms?.p50)}
                      </TableCell>
                      <TableCell>{shown(p.valid_decisions_per_active_second)}</TableCell>
                      <TableCell>
                        {p.recorded_cost_usd.toFixed(4)}
                        <span className="block text-xs text-muted-foreground">
                          {p.unknown_cost_requests} unknown
                        </span>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}
          <SavedEvidence
            label="Workload, identities and measurement conditions"
            value={{ results: run.results, workload: run.workload }}
          />
        </div>
      ))}
      {(query.data?.count ?? 0) > 25 && (
        <div className="flex gap-2">
          <Button disabled={page === 1} onClick={() => setPage(page - 1)} variant="outline">
            Previous
          </Button>
          <Button disabled={!query.data?.next} onClick={() => setPage(page + 1)} variant="outline">
            Next
          </Button>
        </div>
      )}
    </section>
  );
}
