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
import { Input } from "@/components/ui/input";
import { Choice, Failure, Field, NumberField, SavedEvidence, WorkflowState } from "./common";

const roles = ["train", "development", "calibration", "final"] as const;
export function Partitions({ projectId, cellId }: { projectId: string; cellId: string }) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("Data partitions");
  const [fractions, setFractions] = useState({
    calibration: 10,
    development: 10,
    final: 10,
    train: 70,
  });
  const [seed, setSeed] = useState(73491);
  const [groups, setGroups] = useState("");
  const [strata, setStrata] = useState("");
  const [holdoutField, setHoldoutField] = useState("");
  const [holdoutValues, setHoldoutValues] = useState("");
  const [holdoutRole, setHoldoutRole] = useState("final");
  const [key, setKey] = useState(() => crypto.randomUUID());
  const list = useQuery({
    queryFn: () =>
      apiClient.dataPartitions.dataPartitionsList({ project: projectId, sourceCell: cellId }),
    queryKey: ["data-partitions", projectId, cellId],
    refetchInterval: 10000,
  });
  const create = useMutation({
    mutationFn: () =>
      apiClient.dataPartitions.dataPartitionsCreate({
        partitionRequestRequest: {
          name,
          project: projectId,
          recipe: {
            fractions: Object.fromEntries(
              Object.entries(fractions)
                .filter(([, value]) => value > 0)
                .map(([role, value]) => [role, value / 100])
            ),
            group_by: groups
              .split(",")
              .map((v) => v.trim())
              .filter(Boolean),
            holdouts: holdoutField
              ? [
                  {
                    field: holdoutField.trim(),
                    role: holdoutRole,
                    values: holdoutValues.split(",").map((v) => {
                      try {
                        return JSON.parse(v.trim());
                      } catch {
                        return v.trim();
                      }
                    }),
                  },
                ]
              : [],
            seed,
            stratify_by: strata.trim() || null,
          },
          requestKey: key,
          sourceCell: cellId,
        },
      }),
    onSuccess: () => {
      setOpen(false);
      setKey(crypto.randomUUID());
      void list.refetch();
    },
  });
  const retry = useMutation({
    mutationFn: (id: string) => apiClient.dataPartitions.dataPartitionsRetryCreate({ id }),
    onSuccess: () => {
      void list.refetch();
    },
  });
  const sum = Object.values(fractions).reduce((a, b) => a + b, 0);
  return (
    <section className="flex flex-col gap-3 border-b border-border pb-4">
      <div className="flex items-center justify-between gap-3">
        <h3 className="text-sm font-medium">Data partitions</h3>
        <Button onClick={() => setOpen(true)} size="sm" variant="outline">
          Create partitions
        </Button>
      </div>
      <Failure error={list.error ?? retry.error} />
      {list.data?.results.map((plan) => (
        <div className="flex flex-col gap-2" key={plan.id}>
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-sm">{plan.name}</span>
            <WorkflowState state={plan.state} />
            {plan.state === "failed" && (
              <Button
                disabled={retry.isPending}
                onClick={() => retry.mutate(plan.id)}
                size="sm"
                variant="outline"
              >
                Retry construction
              </Button>
            )}
          </div>
          <Failure error={plan.error} />
          <div className="flex flex-wrap gap-3">
            {plan.members.map((member) => (
              <Link
                className="text-sm underline underline-offset-4"
                key={member.role}
                params={{ datasetId: member.dataset }}
                search={{ cell: member.cell, projectId }}
                to="/datasets/$datasetId"
              >
                {member.role} · {member.rows} rows
              </Link>
            ))}
          </div>
          <SavedEvidence
            label="Assignments, coverage and lineage"
            value={{ recipe: plan.recipe, report: plan.report, source: plan.sourceFingerprint }}
          />
        </div>
      ))}
      <Dialog onOpenChange={setOpen} open={open}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Create data partitions</DialogTitle>
            <DialogDescription>
              Content duplicates, declared groups and synthetic lineage stay together. Actual counts
              can differ from requested fractions.
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
              <div className="grid grid-cols-2 gap-3">
                {roles.map((role) => (
                  <NumberField
                    key={role}
                    label={`${role} %`}
                    max={99}
                    onChange={(value) => setFractions({ ...fractions, [role]: value })}
                    value={fractions[role]}
                  />
                ))}
              </div>
              <p className="text-sm text-muted-foreground">Total {sum}% · zero excludes a role.</p>
              <Field label="Group columns (comma separated)">
                <Input onChange={(e) => setGroups(e.target.value)} value={groups} />
              </Field>
              <Field label="Stratification column (optional)">
                <Input onChange={(e) => setStrata(e.target.value)} value={strata} />
              </Field>
              <NumberField label="Seed" max={4294967295} onChange={setSeed} value={seed} />
              <details>
                <summary className="cursor-pointer text-sm">Explicit holdout</summary>
                <div className="mt-3 flex flex-col gap-3">
                  <Field label="Holdout column">
                    <Input onChange={(e) => setHoldoutField(e.target.value)} value={holdoutField} />
                  </Field>
                  <Field label="Held-out values (comma separated)">
                    <Input
                      onChange={(e) => setHoldoutValues(e.target.value)}
                      value={holdoutValues}
                    />
                  </Field>
                  <Choice
                    label="Holdout role"
                    onChange={setHoldoutRole}
                    options={roles.map((role) => ({ label: role, value: role }))}
                    value={holdoutRole}
                  />
                </div>
              </details>
              <Failure error={create.error} />
            </DialogBody>
            <DialogFooter>
              <Button
                disabled={create.isPending}
                onClick={() => setOpen(false)}
                type="button"
                variant="secondary"
              >
                Cancel
              </Button>
              <Button disabled={create.isPending || sum !== 100 || !name.trim()} type="submit">
                {create.isPending ? "Saving…" : "Create partitions"}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </section>
  );
}
