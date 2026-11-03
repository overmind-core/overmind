import { useEffect, useState } from "react";

import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";

import apiClient from "@/client";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { DismissibleAlert } from "@/components/ui/dismissible-alert";
import { Icon } from "@/components/ui/icons";
import { Spinner } from "@/components/ui/spinner";
import { traceSelectionBody, useCreateDatasetSplitMutation } from "@/hooks/use-datasets";
import { useTrainingPlan } from "@/hooks/use-issues";
import { formatNumber } from "@/lib/formatters";
import { cn } from "@/lib/utils";
import type { Dataset } from "@/openapi";

const POLL_MS = 1_500;
const MIN_TRAINING_ROWS = 10;

function SplitRow({
  color,
  label,
  note,
  rows,
}: {
  color: string;
  label: string;
  note: string;
  rows: number;
}) {
  return (
    <li className="grid grid-cols-[0.5rem_minmax(0,1fr)_auto] items-baseline gap-3 py-2.5">
      <span aria-hidden="true" className={cn("size-2 rounded-xs", color)} />
      <span className="min-w-0">
        <span className="block text-sm text-foreground">{label}</span>
        <span className="block text-xs text-muted-foreground">{note}</span>
      </span>
      <span className="text-lg tabular-nums text-foreground">{formatNumber(rows)}</span>
    </li>
  );
}

function isLanded(dataset: Dataset | undefined) {
  return dataset?.state === "error" || (dataset?.state === "idle" && dataset.rows > 0);
}

/** A dataset still landing can't be validated, so the wizard opens only once both are ready. */
function useLanded(id: string | undefined) {
  return useQuery({
    enabled: !!id,
    queryFn: () => apiClient.datasets.datasetsRetrieve({ id: id as string }),
    queryKey: ["train-dialog-dataset", id],
    refetchInterval: (query) => (isLanded(query.state.data) ? false : POLL_MS),
  });
}

/** Turns a signal into the datasets a run needs, then hands over to the training wizard.
 *  Datasets cost nothing; spend starts only at the wizard's own launch step. */
export function TrainDialog({
  capability,
  capabilityName,
  projectId,
  range,
  onClose,
}: {
  capability?: string;
  capabilityName?: string;
  projectId: string;
  range: string;
  onClose: () => void;
}) {
  const navigate = useNavigate();
  const plan = useTrainingPlan(projectId, capability, range);
  const create = useCreateDatasetSplitMutation();
  const [error, setError] = useState<string | null>(null);
  const [pair, setPair] = useState<{
    capability: string;
    train: string;
    evaluation: string;
  } | null>(null);
  const train = useLanded(pair?.train);
  const evaluation = useLanded(pair?.evaluation);
  const data = plan.data;
  const ready = !!data && data.trainingRows >= MIN_TRAINING_ROWS;
  const preparing = create.isPending || !!pair;

  useEffect(() => {
    if (!capability) {
      setPair(null);
      setError(null);
    }
  }, [capability]);

  useEffect(() => {
    if (!pair || !isLanded(train.data) || !isLanded(evaluation.data)) return;
    setPair(null);
    if (train.data?.state === "error" || evaluation.data?.state === "error") {
      setError("Couldn't prepare these traces. Nothing was started.");
      return;
    }
    onClose();
    void navigate({
      search: {
        capabilityId: pair.capability,
        datasetId: pair.train,
        evalDatasetId: pair.evaluation,
        projectId,
        train: true,
      },
      to: "/training",
    });
  }, [evaluation.data, navigate, onClose, pair, projectId, train.data]);

  const start = async () => {
    if (!data || !capability) return;
    setError(null);
    try {
      const created = await create.mutateAsync({
        capabilityId: capability,
        deduplicate: true,
        evalPercent: data.evalPercent,
        name: `${capabilityName ?? "Capability"} passing traces ${new Date().toISOString().slice(0, 10)}`,
        position: "random",
        projectId,
        source: { traces: traceSelectionBody(data.selection) },
      });
      setPair({ capability, evaluation: created.eval.id, train: created.train.id });
    } catch {
      setError("Couldn't create the datasets. Nothing was started.");
    }
  };

  return (
    <Dialog onOpenChange={(open) => !open && !preparing && onClose()} open={!!capability}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Train a model for {capabilityName}</DialogTitle>
          <DialogDescription>Nothing is charged until you start the run.</DialogDescription>
        </DialogHeader>
        <DialogBody className="flex flex-col gap-3">
          {data ? (
            <>
              <div aria-hidden="true" className="flex h-2 gap-0.5 overflow-hidden rounded-xs">
                <span className="bg-success" style={{ flexGrow: data.trainingRows }} />
                <span className="min-w-1 bg-info" style={{ flexGrow: data.evalRows }} />
                <span className="min-w-1 bg-destructive" style={{ flexGrow: data.failingTraces }} />
              </div>
              <ul className="divide-y divide-border/70">
                <SplitRow
                  color="bg-success"
                  label="Train"
                  note="Passing traces the model learns from"
                  rows={data.trainingRows}
                />
                <SplitRow
                  color="bg-info"
                  label="Test"
                  note="Passing traces it never sees in training"
                  rows={data.evalRows}
                />
                <SplitRow
                  color="bg-destructive"
                  label="Leave out"
                  note="Failing traces record the wrong answer"
                  rows={data.failingTraces}
                />
              </ul>
              {data.passingTraces > data.trainingRows + data.evalRows && (
                <p className="text-xs text-muted-foreground">
                  Train and Test use the newest {formatNumber(data.trainingRows + data.evalRows)} of{" "}
                  {formatNumber(data.passingTraces)} passing traces.
                </p>
              )}
              {!ready && (
                <p className="text-sm text-muted-foreground">
                  {formatNumber(data.trainingRows)} passing traces is too few to train on. Select a
                  longer range.
                </p>
              )}
            </>
          ) : (
            <div className="flex h-40 items-center justify-center">
              <Spinner />
            </div>
          )}
          {error && <DismissibleAlert message={error} />}
        </DialogBody>
        <DialogFooter>
          <Button disabled={preparing} onClick={onClose} variant="secondary">
            Cancel
          </Button>
          <Button disabled={!ready || preparing} onClick={start}>
            {preparing ? <Spinner size="sm" /> : <Icon.training />}
            {preparing ? "Preparing traces" : "Prepare data"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
