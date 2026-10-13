import { type ReactNode, useState } from "react";

import { useMutation, useQuery } from "@tanstack/react-query";

import apiClient from "@/client";
import { ProbabilityEvaluationDetails } from "@/components/evaluations/probability-evaluation-details";
import { ProbabilityEvaluationMonitor } from "@/components/evaluations/probability-evaluation-monitor";
import { Button } from "@/components/ui/button";
import {
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { TITLE } from "@/lib/typography";
import type { DecisionParticipantRequest, NativeEvaluation } from "@/openapi";
import { Choice, Failure, Field, NumberField, useVersions } from "./common";

export function useDecisionCatalog(projectId: string) {
  return useQuery({
    queryFn: () => apiClient.nativeEvaluations.nativeEvaluationsCatalogList({ project: projectId }),
    queryKey: ["decision-models", projectId],
  });
}
export function NewComparison({
  projectId,
  modeControl,
  onClose,
  onCreated,
}: {
  projectId: string;
  modeControl: ReactNode;
  onClose: () => void;
  onCreated: (id: string) => void;
}) {
  const versions = useVersions(projectId);
  const catalog = useDecisionCatalog(projectId);
  const [name, setName] = useState("");
  const [final, setFinal] = useState("");
  const [calibration, setCalibration] = useState("");
  const [selection, setSelection] = useState([""]);
  const [seed, setSeed] = useState(73491);
  const [bootstrap, setBootstrap] = useState(1000);
  const [requestKey] = useState(() => crypto.randomUUID());
  const models = catalog.data ?? [];
  const options = models.map((model) => ({
    label: `${model.name} · ${model.kind}`,
    model: model.kind === "trained" ? undefined : model.id,
    value: `${model.kind}:${model.id}`,
  }));
  const versionsOptions = versions.data?.filter((v) => v.intent === "eval") ?? [];
  const create = useMutation({
    mutationFn: () => {
      const participants: DecisionParticipantRequest[] = selection.map((selected, index) => {
        const model = models.find((m) => `${m.kind}:${m.id}` === selected)!;
        return {
          key: `model-${index}`,
          kind: model.kind,
          name: model.name,
          ...(model.kind === "trained" ? { job: model.id } : { model: model.id }),
        };
      });
      return apiClient.nativeEvaluations.nativeEvaluationsCreate({
        decisionComparisonRequestRequest: {
          baseline: "model-0",
          bootstrapSamples: bootstrap,
          calibrationCell: calibration || undefined,
          finalCell: final,
          name,
          participants,
          project: projectId,
          requestKey,
          seed,
        },
      });
    },
    onSuccess: (plan) => onCreated(plan.id),
  });
  return (
    <DialogContent size="md">
      <DialogHeader>
        <DialogTitle>New evaluation</DialogTitle>
        <DialogDescription>
          Saving freezes the selected input versions. The first model is the baseline.
        </DialogDescription>
      </DialogHeader>
      <form
        className="flex min-h-0 flex-col"
        onSubmit={(e) => {
          e.preventDefault();
          create.mutate();
        }}
      >
        <DialogBody className="flex flex-col gap-4">
          {modeControl}
          <Field label="Name">
            <Input onChange={(e) => setName(e.target.value)} required value={name} />
          </Field>
          <Choice label="Final suite" onChange={setFinal} options={versionsOptions} value={final} />
          <Choice
            label="Calibration suite"
            onChange={setCalibration}
            optional
            options={versionsOptions.filter((v) => v.value !== final)}
            value={calibration}
          />
          {selection.map((value, index) => (
            <div className="flex items-end gap-2" key={`participant-${index}`}>
              <div className="min-w-0 flex-1">
                <Choice
                  label={index === 0 ? "Baseline" : `Model ${index + 1}`}
                  onChange={(next) =>
                    setSelection(selection.map((old, i) => (i === index ? next : old)))
                  }
                  options={options}
                  value={value}
                />
              </div>
              {index > 0 && (
                <Button
                  aria-label={`Remove model ${index + 1}`}
                  onClick={() => setSelection(selection.filter((_, i) => i !== index))}
                  type="button"
                  variant="outline"
                >
                  Remove
                </Button>
              )}
            </div>
          ))}
          <Button
            className="self-start"
            disabled={selection.length >= 8}
            onClick={() => setSelection([...selection, ""])}
            type="button"
            variant="outline"
          >
            Add model
          </Button>
          <p className="text-xs text-muted-foreground">
            Catalog eligibility is unmeasured. Exact preparation and checkpoint checks run before
            prediction.
          </p>
          <div className="grid gap-4 sm:grid-cols-2">
            <NumberField label="Seed" max={4294967295} onChange={setSeed} value={seed} />
            <NumberField
              label="Bootstrap samples"
              max={10000}
              min={2}
              onChange={setBootstrap}
              value={bootstrap}
            />
          </div>
          <Failure error={create.error ?? versions.error ?? catalog.error} />
        </DialogBody>
        <DialogFooter>
          <Button disabled={create.isPending} onClick={onClose} type="button" variant="secondary">
            Cancel
          </Button>
          <Button
            disabled={
              create.isPending ||
              !final ||
              !name.trim() ||
              selection.some((v) => !v) ||
              final === calibration
            }
            type="submit"
          >
            {create.isPending ? "Saving…" : "Save draft"}
          </Button>
        </DialogFooter>
      </form>
    </DialogContent>
  );
}
export function ComparisonDetail({ plan }: { plan: NativeEvaluation }) {
  return (
    <section className="flex min-w-0 flex-col gap-4">
      <h2 className={TITLE.card}>{plan.name}</h2>
      <Failure error={plan.error} />
      <ProbabilityEvaluationDetails plan={plan} />
      <ProbabilityEvaluationMonitor key={plan.id} plan={plan} />
    </section>
  );
}
