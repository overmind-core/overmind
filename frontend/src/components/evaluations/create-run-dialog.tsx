import { useState } from "react";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";

import api from "@/client";
import { JudgeModelSelect } from "@/components/evaluations/judge-model-select";
import { SearchableSelect } from "@/components/evaluations/searchable-select";
import { WARNING_SELECT } from "@/components/finetuning/train/context-checks";
import { Field } from "@/components/finetuning/train/field";
import { EvaluationStartButton } from "@/components/finetuning/train/start-button";
import { ModelOptionLabel } from "@/components/model-option-label";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { DismissibleAlert } from "@/components/ui/dismissible-alert";
import { Icon } from "@/components/ui/icons";
import { Input } from "@/components/ui/input";
import { TooltipProvider } from "@/components/ui/tooltip";
import {
  useEvalSetsQuery,
  useModelCatalogQuery,
  useProjectCapabilitiesQuery,
  useProjectDatasetsForEvalQuery,
} from "@/hooks/use-evaluations";
import { errorMessage } from "@/lib/notify";
import { cn } from "@/lib/utils";
import type { EvalRunRequest, EvaluationContextRequestRequest } from "@/openapi";

export function CreateRunDialog({ projectId }: { projectId: string }) {
  const [open, setOpen] = useState(false);
  return (
    <Dialog onOpenChange={setOpen} open={open}>
      <DialogTrigger asChild>
        <Button>
          <Icon.add />
          New evaluation
        </Button>
      </DialogTrigger>
      {open && <CreateRunForm onClose={() => setOpen(false)} projectId={projectId} />}
    </Dialog>
  );
}

function CreateRunForm({ projectId, onClose }: { projectId: string; onClose: () => void }) {
  const datasets = useProjectDatasetsForEvalQuery(projectId);
  const sets = useEvalSetsQuery(projectId);
  const catalog = useModelCatalogQuery();
  const capabilities = useProjectCapabilitiesQuery(projectId);
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [name, setName] = useState("");
  const [datasetId, setDatasetId] = useState("");
  const [evalSetId, setEvalSetId] = useState("");
  const [model, setModel] = useState("");
  const [judgeModel, setJudgeModel] = useState("");
  const [maxItems, setMaxItems] = useState(100);
  const dataset = datasets.data?.results.find((row) => row.id === datasetId);
  const evalSets = (sets.data?.results ?? []).filter(
    (row) =>
      row.project === projectId &&
      row.generativeCount > 0 &&
      (!dataset?.capability || !row.capability || row.capability === dataset.capability)
  );
  const evalSet = evalSets.find((row) => row.id === evalSetId);
  const variants = model ? [{ label: model, mode: "generate", model_name: model }] : [];
  const preview = useQuery({
    enabled: !!dataset && !!evalSet && !!model,
    queryFn: () =>
      api.evalRuns.evalRunsContextCheckCreate({
        evaluationContextRequestRequest: {
          capability: dataset?.capability ?? evalSet?.capability,
          cell: dataset?.active,
          dataset: datasetId,
          evalSet: evalSetId,
          judgeModel: judgeModel as EvaluationContextRequestRequest["judgeModel"],
          project: projectId,
          variants: [{ modelName: model }],
        },
      }),
    queryKey: [
      "standalone-eval-context",
      projectId,
      datasetId,
      dataset?.updatedAt,
      evalSetId,
      evalSet?.updatedAt,
      model,
      judgeModel,
    ],
    retry: false,
    staleTime: 30_000,
  });
  const create = useMutation({
    mutationFn: () =>
      api.evalRuns.evalRunsCreate({
        evalRunRequest: {
          cell: dataset?.active,
          dataSource: "dataset",
          dataset: datasetId,
          evalSet: evalSetId,
          judgeModel: judgeModel as EvalRunRequest["judgeModel"],
          maxItems,
          name: name.trim(),
          project: projectId,
          sampling: 1,
          variantsInput: variants,
        },
      }),
    onSuccess: (run) => {
      void queryClient.invalidateQueries({ queryKey: ["eval-runs"] });
      onClose();
      void navigate({
        params: { runId: run.id },
        search: { projectId },
        to: "/evaluations/runs/$runId",
      });
    },
  });
  const checks = preview.data?.checks ?? [];
  const warnings = [
    ...new Set(
      checks
        .filter((check) => check.status === "warning")
        .map((check) => (check.role === "judge" ? `Judge · ${check.model}` : check.model))
    ),
  ];
  const generationWarning = checks.some(
    (check) => check.role === "generation" && check.status === "warning"
  );
  const ready =
    !!name.trim() &&
    !!dataset &&
    !!evalSet &&
    !!model &&
    Number.isInteger(maxItems) &&
    maxItems > 0 &&
    maxItems <= 100000;
  const loadError = datasets.error || sets.error || catalog.error;
  return (
    <DialogContent
      onEscapeKeyDown={(event) => {
        if (create.isPending) event.preventDefault();
      }}
      onInteractOutside={(event) => {
        if (create.isPending) event.preventDefault();
      }}
      showCloseButton={!create.isPending}
      size="lg"
    >
      <DialogHeader>
        <div>
          <DialogTitle>New evaluation</DialogTitle>
          <DialogDescription>
            Generate answers from an existing model and grade them against an eval set. No training.
          </DialogDescription>
        </div>
      </DialogHeader>
      <TooltipProvider>
        <DialogBody className="space-y-4">
          <fieldset className="grid min-w-0 gap-4 sm:grid-cols-2" disabled={create.isPending}>
            <Field className="sm:col-span-2" htmlFor="eval-run-name" label="Name">
              <Input
                id="eval-run-name"
                maxLength={255}
                onChange={(e) => setName(e.target.value)}
                placeholder="Evaluation name"
                value={name}
              />
            </Field>
            <Field label="Eval dataset">
              <SearchableSelect
                ariaLabel="Eval dataset"
                onChange={(value) => {
                  setDatasetId(value);
                  setEvalSetId("");
                  setJudgeModel("");
                }}
                options={(datasets.data?.results ?? []).map((row) => ({
                  label: row.name,
                  value: row.id,
                }))}
                placeholder="Select dataset"
                triggerClassName="w-full"
                value={datasetId}
              />
            </Field>
            <Field label="Model being tested">
              <SearchableSelect
                ariaLabel="Model being tested"
                onChange={setModel}
                options={(catalog.data?.models ?? [])
                  .filter((row) => !row.id.includes(":batch"))
                  .map((row) => ({
                    label: row.name,
                    value: row.id,
                  }))}
                placeholder="Select model"
                renderOption={(option) => (
                  <ModelOptionLabel model={option.value} name={option.label} />
                )}
                triggerClassName={cn("w-full", generationWarning && WARNING_SELECT)}
                value={model}
              />
            </Field>
            <Field label="Eval set">
              <SearchableSelect
                ariaLabel="Eval set"
                onChange={(value) => {
                  setEvalSetId(value);
                  setJudgeModel("");
                }}
                options={evalSets.map((row) => ({
                  label: row.name,
                  tag: row.capability
                    ? capabilities.data?.results.find((cap) => cap.id === row.capability)?.name ||
                      "Capability set"
                    : "Project set",
                  value: row.id,
                }))}
                placeholder="Select eval set"
                triggerClassName="w-full"
                value={evalSetId}
              />
            </Field>
            <Field htmlFor="eval-max-items" label="Maximum samples">
              <Input
                id="eval-max-items"
                max={100000}
                min={1}
                onChange={(e) => setMaxItems(e.target.valueAsNumber)}
                type="number"
                value={Number.isNaN(maxItems) ? "" : maxItems}
              />
            </Field>
            <JudgeModelSelect
              checking={preview.isFetching}
              className="sm:col-start-1"
              disabled={!evalSet || create.isPending}
              onChange={setJudgeModel}
              report={preview.data}
              value={judgeModel}
            />
          </fieldset>
          <p className="text-xs text-muted-foreground">
            Context and judge-cost estimates cover the full dataset. This run uses up to{" "}
            {Number.isNaN(maxItems) ? "—" : maxItems.toLocaleString()} samples.
          </p>
          <DismissibleAlert
            error={
              loadError
                ? new Error(errorMessage(loadError, "Couldn't load evaluation options."))
                : null
            }
            variant="destructive"
          />
          <DismissibleAlert
            error={
              create.error
                ? new Error(errorMessage(create.error, "Couldn't start evaluation."))
                : null
            }
            variant="destructive"
          />
        </DialogBody>
      </TooltipProvider>
      <DialogFooter>
        <Button disabled={create.isPending} onClick={onClose} variant="secondary">
          Cancel
        </Button>
        <EvaluationStartButton
          disabled={!ready || create.isPending}
          label={create.isPending ? "Starting" : "Start evaluation"}
          onStart={() => create.mutate()}
          pending={create.isPending}
          warnings={warnings}
        />
      </DialogFooter>
    </DialogContent>
  );
}
