import type { ReactNode } from "react";

import type { ColumnDef } from "@tanstack/react-table";

import { FtStatusBadge } from "@/components/finetuning/finetuning-chrome";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { CreditsAmount } from "@/components/ui/credits";
import { DataTable } from "@/components/ui/data-table";
import { DateTime } from "@/components/ui/datetime";
import { EmptyState } from "@/components/ui/empty-state";
import { Icon } from "@/components/ui/icons";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Spinner } from "@/components/ui/spinner";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  isActiveDecisionJob,
  useCancelDecisionJobMutation,
  useDecisionJobQuery,
  useDecisionJobsQuery,
} from "@/hooks/use-decision-jobs";
import { buildDecisionCurlSnippet } from "@/lib/inference-snippets";
import { errorMessage } from "@/lib/notify";
import type { DecisionJob, DecisionVersion } from "@/openapi";

interface Scores {
  n: number;
  accuracy: number | null;
  majority_baseline: number | null;
  log_loss: number | null;
  brier: number | null;
  questions?: Record<string, Scores>;
}

interface Evaluation {
  rows?: number;
  train_overlap?: number;
  base?: Scores;
  trained?: Scores;
  error?: string;
}

const versionText = (version: DecisionVersion | null) =>
  version ? `${version.datasetName} · ${version.version}` : "—";

const columns: ColumnDef<DecisionJob, unknown>[] = [
  {
    accessorKey: "modelId",
    cell: ({ row }) => <span className="font-mono text-sm">{row.original.modelId}</span>,
    header: "Model",
    size: 260,
  },
  {
    accessorKey: "status",
    cell: ({ row }) => <FtStatusBadge fallback="queued" status={row.original.status} />,
    header: "Status",
    size: 140,
  },
  {
    cell: ({ row }) => <span className="text-sm">{versionText(row.original.training)}</span>,
    header: "Training data",
    id: "training",
    size: 220,
  },
  {
    cell: ({ row }) => (
      <Accuracy scores={(row.original.evaluation as Evaluation | null)?.trained} />
    ),
    header: "Accuracy",
    id: "accuracy",
    size: 200,
  },
  {
    cell: ({ row }) =>
      row.original.costUsd == null ? (
        <span className="text-muted-foreground">—</span>
      ) : (
        <CreditsAmount usd={Number(row.original.costUsd)} />
      ),
    header: "Cost",
    id: "cost",
    size: 110,
  },
  {
    accessorKey: "createdAt",
    cell: ({ row }) => (
      <DateTime className="text-sm text-muted-foreground" value={row.original.createdAt} />
    ),
    header: "Created",
    size: 140,
  },
];

export function DecisionJobsTable({
  projectId,
  page,
  pageSize,
  action,
  onPageChange,
  onPageSizeChange,
  onOpenJob,
}: {
  projectId: string;
  page: number;
  pageSize: number;
  action: ReactNode;
  onPageChange: (page: number) => void;
  onPageSizeChange: (size: number) => void;
  onOpenJob: (id: string) => void;
}) {
  const { data, isLoading, error } = useDecisionJobsQuery(projectId, page, pageSize);
  return (
    <DataTable
      columns={columns}
      data={data}
      emptyState={
        <EmptyState
          className="flex-1"
          description="Train a model that answers choice, yes/no and score questions with probabilities."
          icon={Icon.training}
          size="section"
          title="No decision models yet"
        />
      }
      error={error}
      getRowId={(job) => job.id}
      isLoading={isLoading}
      onPageChange={onPageChange}
      onPageSizeChange={onPageSizeChange}
      onRowClick={(job) => onOpenJob(job.id)}
      page={page}
      pageSize={pageSize}
      storageKey="training:decision-jobs-cols"
      toolbar={<div className="flex w-full justify-end">{action}</div>}
    />
  );
}

const percent = (value: number | null | undefined) =>
  value == null ? "—" : `${(value * 100).toFixed(1)}%`;
const decimal = (value: number | null | undefined) => (value == null ? "—" : value.toFixed(3));

function Accuracy({ scores }: { scores: Scores | null | undefined }) {
  return (
    <span className="tabular-nums text-sm">
      {percent(scores?.accuracy)}
      {scores?.majority_baseline != null && (
        <span className="text-muted-foreground">
          {" "}
          · baseline {percent(scores.majority_baseline)}
        </span>
      )}
    </span>
  );
}

function Metrics({ evaluation }: { evaluation: Evaluation }) {
  if (evaluation.error) return <Alert variant="destructive">{evaluation.error}</Alert>;
  const { base, trained } = evaluation;
  if (!base || !trained) return null;
  const questions = Object.keys(trained.questions ?? {});
  return (
    <div className="flex flex-col gap-3">
      {!!evaluation.train_overlap && (
        <Alert variant="warning">
          {evaluation.train_overlap} training rows overlap the evaluation version
        </Alert>
      )}
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Model</TableHead>
            <TableHead className="text-right">Accuracy</TableHead>
            <TableHead className="text-right">Log loss</TableHead>
            <TableHead className="text-right">Brier</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {[
            ["Base", base],
            ["Trained", trained],
          ].map(([label, scores]) => (
            <TableRow key={label as string}>
              <TableCell>{label as string}</TableCell>
              <TableCell className="text-right">
                <Accuracy scores={scores as Scores} />
              </TableCell>
              <TableCell className="text-right tabular-nums">
                {decimal((scores as Scores).log_loss)}
              </TableCell>
              <TableCell className="text-right tabular-nums">
                {decimal((scores as Scores).brier)}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      {questions.length > 1 && (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Question</TableHead>
              <TableHead className="text-right">Base</TableHead>
              <TableHead className="text-right">Trained</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {questions.map((key) => (
              <TableRow key={key}>
                <TableCell className="font-mono">{key}</TableCell>
                <TableCell className="text-right">
                  <Accuracy scores={base.questions?.[key]} />
                </TableCell>
                <TableCell className="text-right">
                  <Accuracy scores={trained.questions?.[key]} />
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </div>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="pixel-label text-xs text-muted-foreground">{label}</span>
      <span className="text-sm">{children}</span>
    </div>
  );
}

function JobDetail({ jobId }: { jobId: string }) {
  const { data: job, error } = useDecisionJobQuery(jobId);
  const cancel = useCancelDecisionJobMutation();
  if (error && !job) return <Alert variant="destructive">{errorMessage(error)}</Alert>;
  if (!job) return <Spinner />;
  const evaluation = (job.evaluation ?? {}) as Evaluation;
  return (
    <div className="flex flex-col gap-5">
      <div className="flex items-center gap-2">
        <FtStatusBadge fallback="queued" status={job.status} />
        <span className="flex-1" />
        {isActiveDecisionJob(job) && (
          <Button
            disabled={cancel.isPending}
            onClick={() => cancel.mutate(job.id)}
            size="sm"
            variant="secondary"
          >
            <Icon.stop />
            Cancel
          </Button>
        )}
      </div>
      {job.error && <Alert variant="destructive">{job.error}</Alert>}
      <div className="grid grid-cols-2 gap-3">
        <Field label="Training data">{versionText(job.training)}</Field>
        <Field label="Evaluation data">{versionText(job.evaluationData)}</Field>
        <Field label="Started">
          <DateTime value={job.startedAt} />
        </Field>
        <Field label="Cost">
          {job.costUsd == null ? "—" : <CreditsAmount usd={Number(job.costUsd)} />}
        </Field>
      </div>
      {job.evaluationData && (
        <section className="flex flex-col gap-2">
          <h3 className="text-sm font-medium">
            Evaluation
            {evaluation.rows != null && (
              <span className="ml-2 text-xs text-muted-foreground">{evaluation.rows} rows</span>
            )}
            {(job.evaluationStatus === "partial" || job.evaluationStatus === "failed") && (
              <Badge className="ml-2" variant="warning">
                {job.evaluationStatus}
              </Badge>
            )}
          </h3>
          {job.status === "succeeded" ? (
            <Metrics evaluation={evaluation} />
          ) : (
            <p className="text-xs text-muted-foreground">
              The base and the trained model are scored after training.
            </p>
          )}
        </section>
      )}
      {job.checkpointReady && (
        <section className="flex flex-col gap-2">
          <h3 className="text-sm font-medium">Use the model</h3>
          <pre className="overflow-x-auto rounded-sm border border-border/70 bg-wash-raised p-3 font-mono text-xs">
            {buildDecisionCurlSnippet({ jobId: job.id })}
          </pre>
        </section>
      )}
    </div>
  );
}

export function DecisionJobSheet({
  jobId,
  onOpenChange,
}: {
  jobId: string | null;
  onOpenChange: (open: boolean) => void;
}) {
  return (
    <Sheet onOpenChange={onOpenChange} open={!!jobId}>
      <SheetContent size="lg">
        <SheetHeader>
          <SheetTitle>Decision job</SheetTitle>
          <SheetDescription className="sr-only">Status, evaluation and usage.</SheetDescription>
        </SheetHeader>
        <div className="px-4 pb-4">{jobId && <JobDetail jobId={jobId} />}</div>
      </SheetContent>
    </Sheet>
  );
}
