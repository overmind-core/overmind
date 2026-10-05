import { useState } from "react";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

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
import { EmptyState } from "@/components/ui/empty-state";
import { Input } from "@/components/ui/input";
import { LoadingState } from "@/components/ui/spinner";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { TITLE } from "@/lib/typography";
import type { DecisionParticipantRequest, NativeEvaluation } from "@/openapi";
import {
  Choice,
  Failure,
  Field,
  NumberField,
  SavedEvidence,
  saveBlob,
  useVersions,
  WorkflowState,
} from "./common";
import { ComparisonMetrics, type Comparisons } from "./comparison-metrics";
import { PerformanceRuns } from "./performance-runs";

export function useDecisionCatalog(projectId: string) {
  return useQuery({
    queryFn: () => apiClient.nativeEvaluations.nativeEvaluationsCatalogList({ project: projectId }),
    queryKey: ["decision-models", projectId],
  });
}
export function DecisionComparisons({ projectId }: { projectId: string }) {
  const [creating, setCreating] = useState(false);
  const [selected, setSelected] = useState<string>();
  const [page, setPage] = useState(1);
  const query = useQuery({
    queryFn: () => apiClient.nativeEvaluations.nativeEvaluationsList({ page, project: projectId }),
    queryKey: ["decision-comparisons", projectId, page],
    refetchInterval: 10000,
  });
  const focused = useQuery({
    enabled: !!selected,
    queryFn: () => apiClient.nativeEvaluations.nativeEvaluationsRetrieve({ id: selected! }),
    queryKey: ["decision-comparison", selected],
    refetchInterval: 10000,
  });
  return (
    <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-auto pb-4">
      <div className="flex items-center justify-between gap-3">
        <p className="text-sm text-muted-foreground">Compare model probabilities on frozen data.</p>
        <Button onClick={() => setCreating(true)}>New comparison</Button>
      </div>
      <Failure error={query.error ?? focused.error} />
      {query.isPending && <LoadingState label="Loading comparisons" />}
      {query.data?.count === 0 && <EmptyState size="section" title="No decision comparisons" />}
      <div className="flex flex-wrap gap-2">
        {query.data?.results.map((plan) => (
          <Button
            key={plan.id}
            onClick={() => setSelected(plan.id)}
            variant={selected === plan.id ? "secondary" : "outline"}
          >
            {plan.name}
            <WorkflowState state={plan.state} />
          </Button>
        ))}
      </div>
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
      {focused.data && <ComparisonDetail key={focused.data.id} plan={focused.data} />}
      {creating && (
        <NewComparison
          onClose={() => setCreating(false)}
          onCreated={(id) => {
            setCreating(false);
            setSelected(id);
            void query.refetch();
          }}
          projectId={projectId}
        />
      )}
    </div>
  );
}
function NewComparison({
  projectId,
  onClose,
  onCreated,
}: {
  projectId: string;
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
    <Dialog onOpenChange={(open) => !open && !create.isPending && onClose()} open>
      <DialogContent size="md">
        <DialogHeader>
          <DialogTitle>New decision comparison</DialogTitle>
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
            <Field label="Name">
              <Input onChange={(e) => setName(e.target.value)} required value={name} />
            </Field>
            <Choice
              label="Final suite"
              onChange={setFinal}
              options={versionsOptions}
              value={final}
            />
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
    </Dialog>
  );
}
function ComparisonDetail({ plan }: { plan: NativeEvaluation }) {
  const [mode, setMode] = useState<"raw" | "calibrated">("raw");
  const [callId, setCallId] = useState("");
  const client = useQueryClient();
  const resume = useMutation({
    mutationFn: () =>
      apiClient.nativeEvaluations.nativeEvaluationsResumeCreate({
        evaluationResumeRequest: { callId: callId || undefined },
        id: plan.id,
      }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["decision-comparison", plan.id] });
    },
  });
  const transition = useMutation({
    mutationFn: (action: "prepare" | "launch" | "pause") => {
      if (action === "prepare")
        return apiClient.nativeEvaluations.nativeEvaluationsPrepareCreate({ id: plan.id });
      if (action === "launch")
        return apiClient.nativeEvaluations.nativeEvaluationsLaunchCreate({ id: plan.id });
      return apiClient.nativeEvaluations.nativeEvaluationsPauseCreate({ id: plan.id });
    },
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["decision-comparison", plan.id] });
    },
  });
  const download = useMutation({
    mutationFn: () =>
      apiClient.nativeEvaluations.nativeEvaluationsReportRetrieve({ format: "json", id: plan.id }),
    onSuccess: (blob) => saveBlob(blob, `${plan.name}-results.json`),
  });
  const participants = (plan.config?.participants ?? []) as { key: string; name: string }[];
  const names = Object.fromEntries(participants.map((p) => [p.key, p.name]));
  return (
    <section className="flex min-w-0 flex-col gap-4 border-t border-border pt-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className={TITLE.card}>{plan.name}</h2>
        <WorkflowState state={plan.state} />
      </div>
      <Failure error={plan.error || resume.error || download.error || transition.error} />
      {["failed", "submission_unknown", "paused"].includes(plan.state) && (
        <div className="flex flex-wrap items-end gap-3">
          <Field label="Existing provider call ID (if acknowledgement was lost)">
            <Input onChange={(e) => setCallId(e.target.value)} value={callId} />
          </Field>
          <Button disabled={resume.isPending} onClick={() => resume.mutate()}>
            Resume saved work
          </Button>
        </div>
      )}
      <div className="flex gap-2">
        {plan.state === "draft" && (
          <Button disabled={transition.isPending} onClick={() => transition.mutate("prepare")}>
            Prepare inputs
          </Button>
        )}
        {["draft", "prepared"].includes(plan.state) && (
          <Button disabled={transition.isPending} onClick={() => transition.mutate("launch")}>
            Launch comparison
          </Button>
        )}
        {["queued", "running", "waiting_for_checkpoint", "preparing"].includes(plan.state) && (
          <Button
            disabled={transition.isPending}
            onClick={() => transition.mutate("pause")}
            variant="outline"
          >
            Pause new work
          </Button>
        )}
      </div>
      <SavedEvidence
        label="Frozen inputs, model identities and execution receipts"
        value={{ calibration: plan.calibration, calls: plan.calls, config: plan.config }}
      />
      {plan.state === "completed" && (
        <>
          <div className="flex flex-wrap justify-between gap-3">
            <Tabs onValueChange={(v) => setMode(v as "raw" | "calibrated")} value={mode}>
              <TabsList>
                <TabsTrigger value="raw">Raw</TabsTrigger>
                <TabsTrigger disabled={!plan.calibrationCell} value="calibrated">
                  Calibrated
                </TabsTrigger>
              </TabsList>
            </Tabs>
            <Button
              disabled={download.isPending}
              onClick={() => download.mutate()}
              variant="outline"
            >
              Download all metrics
            </Button>
          </div>
          <p className="text-xs text-muted-foreground">
            Differences are model minus baseline. Cross entropy, Brier and error metrics improve
            downward. Accuracy improves upward. Denominators include only the applicable target
            type.
          </p>
          <ComparisonMetrics
            comparisons={(plan.results?.comparisons ?? {}) as Comparisons}
            mode={mode}
            names={names}
          />
          {plan.results?.calibration && (
            <details className="border-t border-border pt-4">
              <summary className="cursor-pointer text-sm font-medium">
                Calibration suite · in-sample
              </summary>
              <div className="mt-4">
                <ComparisonMetrics
                  comparisons={plan.results.calibration.comparisons as Comparisons}
                  mode={mode}
                  names={names}
                />
              </div>
            </details>
          )}
          <SavedEvidence
            label="Recorded costs and unreported components"
            value={plan.results?.costs}
          />
          <SavedEvidence label="Scope and contamination limits" value={plan.results?.limitations} />
        </>
      )}
      <PerformanceRuns evaluation={plan.id} projectId={plan.project} />
    </section>
  );
}
