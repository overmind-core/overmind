import { useMemo, useState } from "react";

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
import { Icon } from "@/components/ui/icons";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Spinner } from "@/components/ui/spinner";
import {
  datasetDisplayName,
  intentOf,
  useDatasetInventoryQuery,
} from "@/hooks/use-datasets";
import { useCreateDecisionJobMutation, useDecisionModelsQuery } from "@/hooks/use-decision-jobs";
import { errorMessage } from "@/lib/notify";
import type { Dataset } from "@/openapi";

const NO_EVAL = "none";

const decisionReady = (dataset: Dataset, intent: "train" | "eval") =>
  intentOf(dataset) === intent && dataset.decisionReady;

export function NewDecisionJobDialog({
  open,
  projectId,
  initialDatasetId,
  onOpenChange,
  onStarted,
}: {
  open: boolean;
  projectId: string;
  initialDatasetId?: string;
  onOpenChange: (open: boolean) => void;
  onStarted: (jobId: string) => void;
}) {
  const datasets = useDatasetInventoryQuery(projectId).data ?? [];
  const models = useDecisionModelsQuery().data ?? [];
  const create = useCreateDecisionJobMutation();
  const train = useMemo(() => datasets.filter((d) => decisionReady(d, "train")), [datasets]);
  const evaluation = useMemo(() => datasets.filter((d) => decisionReady(d, "eval")), [datasets]);
  const [datasetId, setDatasetId] = useState(initialDatasetId ?? "");
  const [evalDatasetId, setEvalDatasetId] = useState(NO_EVAL);
  const [modelId, setModelId] = useState("");
  const chosen = datasetId || (train.length === 1 ? train[0].id : "");

  const submit = () =>
    create.mutate(
      {
        dataset: chosen,
        evalDataset: evalDatasetId === NO_EVAL ? null : evalDatasetId,
        model: modelId,
        project: projectId,
      },
      { onSuccess: (job) => onStarted(job.id) }
    );

  return (
    <Dialog onOpenChange={onOpenChange} open={open}>
      <DialogContent size="md">
        <DialogHeader>
          <DialogTitle>New decision job</DialogTitle>
          <DialogDescription>
            Trains on the active version of a dataset of decision rows.
          </DialogDescription>
        </DialogHeader>
        <DialogBody className="flex flex-col gap-4">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="decision-dataset">Training data</Label>
            <Select onValueChange={setDatasetId} value={chosen}>
              <SelectTrigger className="w-full" id="decision-dataset">
                <SelectValue
                  placeholder={train.length ? "Choose a dataset" : "No dataset of decision rows"}
                />
              </SelectTrigger>
              <SelectContent>
                {train.map((dataset) => (
                  <SelectItem key={dataset.id} value={dataset.id}>
                    {datasetDisplayName(dataset)} · {dataset.activeVersion}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="decision-eval">Evaluation data</Label>
            <Select onValueChange={setEvalDatasetId} value={evalDatasetId}>
              <SelectTrigger className="w-full" id="decision-eval">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={NO_EVAL}>None</SelectItem>
                {evaluation.map((dataset) => (
                  <SelectItem key={dataset.id} value={dataset.id}>
                    {datasetDisplayName(dataset)} · {dataset.activeVersion}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="decision-model">Base model</Label>
            <Select onValueChange={setModelId} value={modelId}>
              <SelectTrigger className="w-full" id="decision-model">
                <SelectValue placeholder="Choose a model" />
              </SelectTrigger>
              <SelectContent>
                {models.map((model) => (
                  <SelectItem key={model.id} value={model.id}>
                    {model.id}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          {create.error && (
            <p className="text-xs text-destructive" role="alert">
              {errorMessage(create.error)}
            </p>
          )}
        </DialogBody>
        <DialogFooter>
          <Button onClick={() => onOpenChange(false)} variant="secondary">
            Cancel
          </Button>
          <Button disabled={!chosen || !modelId || create.isPending} onClick={submit}>
            {create.isPending ? <Spinner className="size-3" /> : <Icon.training />}
            Start training
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
