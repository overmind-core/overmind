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
import { datasetDisplayName, intentOf, useDatasetInventoryQuery } from "@/hooks/use-datasets";
import {
  useCreateDecisionJobMutation,
  useDecisionModelsQuery,
  useDecisionRecommendation,
} from "@/hooks/use-decision-jobs";
import { errorMessage } from "@/lib/notify";
import type { Dataset, DecisionLimits } from "@/openapi";

const NO_EVAL = "none";

const decisionReady = (dataset: Dataset, intent: "train" | "eval") =>
  intentOf(dataset) === intent && dataset.decisionReady;

type DatasetShape = { options: number; score_levels: number; questions: number };

// The widest question shape of a dataset's active version, as the server measured it.
const shapeOf = (dataset: Dataset | undefined): DatasetShape | null => {
  const active = dataset?.cells.find((cell) => cell.version === dataset.activeVersion);
  return (
    (active?.intentReport as { decision?: { shape?: DatasetShape } } | null)?.decision?.shape ??
    null
  );
};

// The first serving limit the dataset's shape exceeds, or null when the model takes it.
const overLimit = (limits: DecisionLimits, shape: DatasetShape | null): string | null => {
  if (!shape) return null;
  if (shape.options > limits.options)
    return `Takes up to ${limits.options} options; this dataset has ${shape.options}.`;
  if (shape.score_levels > limits.scoreLevels)
    return `Takes up to ${limits.scoreLevels} score levels; this dataset has ${shape.score_levels}.`;
  if (shape.questions > limits.questions)
    return `Takes up to ${limits.questions} questions; this dataset has ${shape.questions}.`;
  return null;
};

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
  const [pick, setPick] = useState<{ dataset: string; model: string } | null>(null);
  const listed = train.some((dataset) => dataset.id === datasetId) ? datasetId : "";
  const chosen = listed || (train.length === 1 ? train[0].id : "");
  const recommendation = useDecisionRecommendation(projectId, chosen);
  const modelId = pick?.dataset === chosen ? pick.model : (recommendation.data?.model ?? "");
  const shape = shapeOf(train.find((dataset) => dataset.id === chosen));
  const selected = models.find((model) => model.id === modelId);
  const blocked = selected !== undefined && overLimit(selected.limits, shape) !== null;

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
            <Select onValueChange={(model) => setPick({ dataset: chosen, model })} value={modelId}>
              <SelectTrigger className="w-full" id="decision-model">
                <SelectValue placeholder="Choose a model" />
              </SelectTrigger>
              <SelectContent>
                {models.map((model) => {
                  const reason = overLimit(model.limits, shape);
                  return (
                    <SelectItem disabled={reason !== null} key={model.id} value={model.id}>
                      <span className="flex flex-col">
                        <span>{model.id}</span>
                        {reason && <span className="text-xs text-muted-foreground">{reason}</span>}
                      </span>
                    </SelectItem>
                  );
                })}
              </SelectContent>
            </Select>
            {recommendation.data?.reason && (
              <p className="text-xs text-muted-foreground">{recommendation.data.reason}</p>
            )}
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
          <Button disabled={!chosen || !modelId || blocked || create.isPending} onClick={submit}>
            {create.isPending ? <Spinner className="size-3" /> : <Icon.training />}
            Start training
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
