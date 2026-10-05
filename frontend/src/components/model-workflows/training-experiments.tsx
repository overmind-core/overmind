import { useState } from "react";

import { useMutation, useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";

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
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Textarea } from "@/components/ui/textarea";
import { TITLE } from "@/lib/typography";
import type { TrainingExperiment } from "@/openapi";
import {
  Choice,
  Failure,
  Field,
  NumberField,
  SavedEvidence,
  useVersions,
  WorkflowState,
} from "./common";
import { useDecisionCatalog } from "./decision-comparisons";

type Draft = {
  id: string;
  name: string;
  model: string;
  seed: number;
  epochs: number;
  rate: number;
  rank: number;
};
const draft = (): Draft => ({
  epochs: 1,
  id: crypto.randomUUID(),
  model: "",
  name: "",
  rank: 16,
  rate: 0.0001,
  seed: 73491,
});
export function TrainingExperiments({ projectId }: { projectId: string }) {
  const [creating, setCreating] = useState(false);
  const [page, setPage] = useState(1);
  const query = useQuery({
    queryFn: () =>
      apiClient.trainingExperiments.trainingExperimentsList({ page, project: projectId }),
    queryKey: ["training-experiments", projectId, page],
    refetchInterval: 10000,
  });
  return (
    <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-auto pb-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm text-muted-foreground">
          Save explicit candidates, data versions and a comparison protocol.
        </p>
        <Button onClick={() => setCreating(true)}>New experiment</Button>
      </div>
      <Failure error={query.error} />
      {query.isPending && <LoadingState label="Loading experiments" />}
      {query.data?.count === 0 && <EmptyState size="section" title="No saved experiments" />}
      {query.data?.results.map((experiment) => (
        <Experiment
          key={experiment.id}
          onChanged={() => {
            void query.refetch();
          }}
          value={experiment}
        />
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
      {creating && (
        <NewExperiment
          onClose={() => setCreating(false)}
          onCreated={() => {
            setCreating(false);
            void query.refetch();
          }}
          projectId={projectId}
        />
      )}
    </div>
  );
}
function Experiment({ value, onChanged }: { value: TrainingExperiment; onChanged: () => void }) {
  const [reviewing, setReviewing] = useState(false);
  const launch = useMutation({
    mutationFn: () =>
      apiClient.trainingExperiments.trainingExperimentsLaunchCreate({
        experimentLaunchRequest: { quoteId: value.protocol.forecast?.id },
        id: value.id,
      }),
    onSuccess: () => {
      setReviewing(false);
      onChanged();
    },
  });
  const prepare = useMutation({
    mutationFn: () =>
      apiClient.trainingExperiments.trainingExperimentsPrepareCreate({ id: value.id }),
    onSuccess: onChanged,
  });
  const variants = value.variants as {
    name: string;
    base_model: string;
    hyperparameters: { seed?: number; n_epochs?: number; learning_rate?: number; lora_r?: number };
  }[];
  const jobs = value.record.jobs as {
    job_id: string;
    name: string;
    status: string;
    qualification: {
      catalog_eligible: boolean;
      exact_preparation?: { state?: string };
      hardware_execution: { observed: boolean };
      reload_verification?: { decisions: number };
      quality: string;
    };
    cost: { recorded_training_usd?: number; coverage: string };
    checkpoint_selection?: unknown;
  }[];
  return (
    <section className="flex flex-col gap-3 rounded-md border border-border p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className={TITLE.card}>{value.name}</h2>
        <WorkflowState state={value.state} />
      </div>
      <p className="text-sm text-muted-foreground">{value.purpose}</p>
      <div className="overflow-x-auto">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Candidate</TableHead>
              <TableHead>Foundation</TableHead>
              <TableHead>Seed</TableHead>
              <TableHead>Epochs</TableHead>
              <TableHead>Learning rate</TableHead>
              <TableHead>LoRA rank</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {variants.map((v, i) => (
              <TableRow key={`${value.id}-${i}`}>
                <TableCell>{v.name}</TableCell>
                <TableCell>{v.base_model}</TableCell>
                <TableCell>{v.hyperparameters.seed}</TableCell>
                <TableCell>{v.hyperparameters.n_epochs}</TableCell>
                <TableCell>{v.hyperparameters.learning_rate}</TableCell>
                <TableCell>{v.hyperparameters.lora_r}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
      <p className="text-xs text-muted-foreground">
        Varying fields: {(value.record.varying_fields as string[]).join(", ") || "None"}. Training
        seed variation is separate from evaluation uncertainty.
      </p>
      {jobs.map((job) => (
        <div className="flex flex-col gap-2 border-t border-border pt-3" key={job.job_id}>
          <Link
            className="text-sm underline underline-offset-4"
            search={{ groupId: value.id, jobId: job.job_id, projectId: value.project }}
            to="/training"
          >
            {job.name} · {job.status}
          </Link>
          <div className="flex flex-wrap gap-x-5 gap-y-2 text-xs text-muted-foreground">
            <span>Catalog: {job.qualification.catalog_eligible ? "eligible" : "unqualified"}</span>
            <span>Preparation: {job.qualification.exact_preparation?.state ?? "pending"}</span>
            <span>
              Execution: {job.qualification.hardware_execution.observed ? "observed" : "unmeasured"}
            </span>
            <span>
              Reload: {job.qualification.reload_verification?.decisions ? "verified" : "unverified"}
            </span>
            <span>Quality: {job.qualification.quality}</span>
            <span>
              Recorded training:{" "}
              {job.cost.recorded_training_usd == null
                ? "unreported"
                : `$${job.cost.recorded_training_usd.toFixed(2)}`}{" "}
              · {job.cost.coverage}
            </span>
          </div>
          {job.checkpoint_selection != null && (
            <SavedEvidence
              label="Retained checkpoints and development selection"
              value={job.checkpoint_selection}
            />
          )}
        </div>
      ))}
      <SavedEvidence
        label="Frozen protocol, requested and effective conditions"
        value={value.record}
      />
      {value.evaluation && (
        <Link
          className="text-sm underline underline-offset-4"
          search={{ projectId: value.project, view: "decisions" }}
          to="/evaluations"
        >
          View decision comparisons
        </Link>
      )}
      {["draft", "preparation_failed"].includes(value.state) && (
        <Button
          className="self-start"
          disabled={prepare.isPending}
          onClick={() => prepare.mutate()}
        >
          Prepare experiment
        </Button>
      )}
      <Failure error={prepare.error} />
      <SavedEvidence label="Forecast, constraints and authorization" value={value.protocol} />
      {value.state === "prepared" && (
        <Button className="self-start" onClick={() => setReviewing(true)}>
          Review launch
        </Button>
      )}
      <Dialog onOpenChange={setReviewing} open={reviewing}>
        <DialogContent size="sm">
          <DialogHeader>
            <DialogTitle>Launch {variants.length} candidates</DialogTitle>
            <DialogDescription>{value.name}</DialogDescription>
          </DialogHeader>
          <DialogBody>
            <p className="text-sm">
              This starts paid preparation and training for the saved candidates
              {value.protocol.source_evaluation ? ", followed by their saved comparison" : ""}.
              Exact preparation determines compatibility. Provider costs are recorded separately
              from unreported spending.
            </p>
            <Failure error={launch.error} />
          </DialogBody>
          <DialogFooter>
            <Button
              disabled={launch.isPending}
              onClick={() => setReviewing(false)}
              variant="secondary"
            >
              Cancel
            </Button>
            <Button disabled={launch.isPending} onClick={() => launch.mutate()}>
              {launch.isPending ? "Launching…" : "Launch experiment"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </section>
  );
}
function NewExperiment({
  projectId,
  onClose,
  onCreated,
}: {
  projectId: string;
  onClose: () => void;
  onCreated: () => void;
}) {
  const versions = useVersions(projectId);
  const models = useDecisionCatalog(projectId);
  const protocols = useQuery({
    queryFn: async () => {
      const records = [];
      for (let page = 1; ; page++) {
        const response = await apiClient.nativeEvaluations.nativeEvaluationsList({
          page,
          project: projectId,
        });
        records.push(...response.results);
        if (!response.next) return records;
      }
    },
    queryKey: ["experiment-protocols", projectId],
  });
  const [name, setName] = useState("");
  const [purpose, setPurpose] = useState("");
  const [train, setTrain] = useState("");
  const [development, setDevelopment] = useState("");
  const [evaluation, setEvaluation] = useState("");
  const [selection, setSelection] = useState("last");
  const [retention, setRetention] = useState("1");
  const [variants, setVariants] = useState<Draft[]>([draft()]);
  const [key] = useState(() => crypto.randomUUID());
  const update = (id: string, patch: Partial<Draft>) =>
    setVariants(variants.map((v) => (v.id === id ? { ...v, ...patch } : v)));
  const create = useMutation({
    mutationFn: () =>
      apiClient.trainingExperiments.trainingExperimentsCreate({
        experimentRequestRequest: {
          evaluation: evaluation || undefined,
          name,
          project: projectId,
          purpose,
          requestKey: key,
          variants: variants.map((v, i) => ({
            baseModel: v.model,
            cell: train,
            developmentCell: development || undefined,
            hyperparameters: {
              checkpoint_policy: { fractions: retention.split(",").map(Number), selection },
              learning_rate: v.rate,
              lora_r: v.rank,
              n_epochs: v.epochs,
              seed: v.seed,
            },
            name: v.name || `Candidate ${i + 1}`,
          })),
        },
      }),
    onSuccess: onCreated,
  });
  const choices = versions.data?.filter((v) => v.intent === "train") ?? [];
  return (
    <Dialog onOpenChange={(open) => !open && !create.isPending && onClose()} open>
      <DialogContent size="lg">
        <DialogHeader>
          <DialogTitle>New training experiment</DialogTitle>
          <DialogDescription>
            Saving freezes data and recipes. Training starts separately.
          </DialogDescription>
        </DialogHeader>
        <form
          className="flex min-h-0 flex-col"
          onSubmit={(e) => {
            e.preventDefault();
            create.mutate();
          }}
        >
          <DialogBody className="flex flex-col gap-5">
            <Field label="Name">
              <Input onChange={(e) => setName(e.target.value)} required value={name} />
            </Field>
            <Field label="Purpose">
              <Textarea onChange={(e) => setPurpose(e.target.value)} required value={purpose} />
            </Field>
            <div className="grid gap-4 sm:grid-cols-2">
              <Choice label="Training data" onChange={setTrain} options={choices} value={train} />
              <Choice
                label="Development data"
                onChange={setDevelopment}
                optional
                options={choices.filter((v) => v.value !== train)}
                value={development}
              />
            </div>
            <Choice
              label="Comparison protocol"
              onChange={setEvaluation}
              optional
              options={(protocols.data ?? []).map((p) => ({ label: p.name, value: p.id }))}
              value={evaluation}
            />
            {variants.map((v, index) => (
              <fieldset className="flex flex-col gap-3 border-t border-border pt-4" key={v.id}>
                <legend className="text-sm font-medium">Candidate {index + 1}</legend>
                <div className="grid gap-3 sm:grid-cols-2">
                  <Field label={`Candidate ${index + 1} name`}>
                    <Input
                      onChange={(e) => update(v.id, { name: e.target.value })}
                      value={v.name}
                    />
                  </Field>
                  <Choice
                    label={`Candidate ${index + 1} foundation`}
                    onChange={(model) => update(v.id, { model })}
                    options={(models.data ?? [])
                      .filter((m) => m.kind === "foundation")
                      .map((m) => ({ label: m.name, model: m.id, value: m.id }))}
                    value={v.model}
                  />
                </div>
                <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                  <NumberField
                    label="Seed"
                    max={4294967295}
                    onChange={(seed) => update(v.id, { seed })}
                    value={v.seed}
                  />
                  <NumberField
                    label="Epochs"
                    max={20}
                    min={1}
                    onChange={(epochs) => update(v.id, { epochs })}
                    value={v.epochs}
                  />
                  <NumberField
                    label="Learning rate"
                    max={1}
                    min={0.000001}
                    onChange={(rate) => update(v.id, { rate })}
                    step={0.000001}
                    value={v.rate}
                  />
                  <NumberField
                    label="LoRA rank"
                    max={256}
                    min={1}
                    onChange={(rank) => update(v.id, { rank })}
                    value={v.rank}
                  />
                </div>
                {index > 0 && (
                  <Button
                    className="self-start"
                    onClick={() => setVariants(variants.filter((old) => old.id !== v.id))}
                    type="button"
                    variant="outline"
                  >
                    Remove candidate {index + 1}
                  </Button>
                )}
              </fieldset>
            ))}
            <Button
              className="self-start"
              disabled={variants.length >= 6}
              onClick={() =>
                setVariants([
                  ...variants,
                  { ...variants[variants.length - 1], id: crypto.randomUUID(), name: "" },
                ])
              }
              type="button"
              variant="outline"
            >
              Add candidate
            </Button>
            <div className="grid gap-4 sm:grid-cols-2">
              <Choice
                label="Retain checkpoints"
                onChange={setRetention}
                options={[
                  { label: "Final", value: "1" },
                  { label: "Halfway and final", value: "0.5,1" },
                  { label: "Quarter intervals", value: "0.25,0.5,0.75,1" },
                ]}
                value={retention}
              />
              <Choice
                label="Checkpoint selection"
                onChange={setSelection}
                options={[
                  { label: "Last checkpoint", value: "last" },
                  ...(development
                    ? [{ label: "Lowest development loss", value: "development_loss" }]
                    : []),
                ]}
                value={selection}
              />
            </div>
            <Failure error={create.error ?? versions.error ?? models.error ?? protocols.error} />
          </DialogBody>
          <DialogFooter>
            <Button disabled={create.isPending} onClick={onClose} type="button" variant="secondary">
              Cancel
            </Button>
            <Button
              disabled={
                create.isPending ||
                !train ||
                !name.trim() ||
                !purpose.trim() ||
                variants.some((v) => !v.model) ||
                (selection === "development_loss" && !development)
              }
              type="submit"
            >
              {create.isPending ? "Saving…" : "Save experiment"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
