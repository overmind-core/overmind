import { useNavigate } from "@tanstack/react-router";
import { useEffect, useMemo, useState } from "react";

import { stockCheckName } from "@/components/evaluations/evaluation-presentation";
import type { RewardCheckEntry } from "@/components/rl/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
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
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Spinner } from "@/components/ui/spinner";
import { useDatasetsQuery } from "@/hooks/use-datasets";
import { useCreateRLRun, useRLCatalogQuery, useRLColumnsQuery } from "@/hooks/use-rl";

const NO_COLUMN = "__none__";
// Constants a check reads but nobody sets per run.
const INTERNAL_PARAMS = new Set(["TOOL_TYPES", "ARG_PREFIX"]);
const REFERENCE_ATTRIBUTE = "overmind.reference_trajectory";

interface ParamDraft {
  mode: "column" | "value";
  column: string;
  value: string;
}

interface RewardDraft {
  id: string;
  check: string;
  params: Record<string, ParamDraft>;
  referenceColumn: string;
  weight: string;
}

const NUMBER_FIELDS = [
  { hint: "Optimizer steps", key: "max_steps", label: "Steps" },
  { hint: "Attempts sampled per prompt", key: "group_size", label: "Group size" },
  { hint: "Prompts scored, never trained on", key: "holdout_size", label: "Held-out prompts" },
  { hint: "LoRA learning rate", key: "learning_rate", label: "Learning rate" },
] as const;

const ADVANCED_FIELDS = [
  { key: "prompts_per_step", label: "Prompts per step" },
  { key: "max_completion_tokens", label: "Max completion tokens" },
  { key: "temperature", label: "Sampling temperature" },
  { key: "lora_rank", label: "LoRA rank" },
  { key: "beta", label: "KL β" },
] as const;

function guessColumn(param: string, columns: string[]) {
  const wanted = param.toLowerCase();
  return columns.find((c) => c.toLowerCase() === wanted) ?? "";
}

function draftFor(entry: RewardCheckEntry, columns: string[]): RewardDraft {
  const params: Record<string, ParamDraft> = {};
  for (const [name, fallback] of Object.entries(entry.parameters)) {
    if (INTERNAL_PARAMS.has(name)) continue;
    const column = guessColumn(name, columns);
    params[name] = { column, mode: column ? "column" : "value", value: fallback };
  }
  return {
    check: entry.check,
    id: crypto.randomUUID(),
    params,
    referenceColumn:
      entry.check === "tool_selection" ? (columns.find((c) => c === "expected_tools") ?? "") : "",
    weight: "1",
  };
}

function parseLiteral(text: string): unknown {
  const trimmed = text.trim();
  if (trimmed === "True") return true;
  if (trimmed === "False") return false;
  try {
    return JSON.parse(trimmed.replace(/^\((.*)\)$/s, "[$1]"));
  } catch {
    return trimmed.replace(/^["'](.*)["']$/s, "$1");
  }
}

function toRequest(draft: RewardDraft, defaults: Record<string, string>) {
  const params: Record<string, unknown> = {};
  const bind: Record<string, string> = {};
  for (const [name, p] of Object.entries(draft.params)) {
    if (p.mode === "column" && p.column) bind[name] = p.column;
    else if (p.mode === "value" && p.value.trim() !== (defaults[name] ?? "").trim())
      params[name] = parseLiteral(p.value);
  }
  return {
    attributes: draft.referenceColumn ? { [REFERENCE_ATTRIBUTE]: draft.referenceColumn } : {},
    bind,
    check: draft.check,
    params,
    weight: Number(draft.weight) || 1,
  };
}

export function NewRLRunDialog({
  projectId,
  open,
  onOpenChange,
}: {
  projectId: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const navigate = useNavigate();
  const catalog = useRLCatalogQuery(open);
  const datasets = useDatasetsQuery(open ? projectId : undefined);
  const [datasetId, setDatasetId] = useState<string>();
  const columns = useRLColumnsQuery(datasetId);
  const [model, setModel] = useState("Qwen/Qwen3-1.7B");
  const [promptColumn, setPromptColumn] = useState("");
  const [toolsColumn, setToolsColumn] = useState("");
  const [rewards, setRewards] = useState<RewardDraft[]>([]);
  const [hp, setHp] = useState<Record<string, string>>({});
  const [name, setName] = useState("");
  const create = useCreateRLRun(projectId);

  const columnNames = useMemo(() => columns.data?.columns ?? [], [columns.data]);
  const checksByName = useMemo(
    () => Object.fromEntries((catalog.data?.checks ?? []).map((c) => [c.check, c])),
    [catalog.data]
  );

  useEffect(() => {
    if (!catalog.data) return;
    setHp((current) =>
      Object.keys(current).length
        ? current
        : Object.fromEntries(
            Object.entries(catalog.data.hyperparameters).map(([k, v]) => [k, String(v.default)])
          )
    );
  }, [catalog.data]);

  // A dataset with tool-call references starts from the two tool checks.
  useEffect(() => {
    if (!columnNames.length || !catalog.data) return;
    setPromptColumn(
      columnNames.find((c) => c === "messages") ??
        columnNames.find((c) => c === "input") ??
        columnNames[0]
    );
    setToolsColumn(columnNames.find((c) => c === "tools") ?? "");
    const preset = columnNames.includes("expected_calls")
      ? ["tool_args_match", "tool_selection"]
      : columnNames.includes("expected_output")
        ? ["exact_match"]
        : [];
    setRewards(
      preset.filter((c) => checksByName[c]).map((c) => draftFor(checksByName[c], columnNames))
    );
  }, [columnNames, catalog.data, checksByName]);

  const addCheck = (check: string) => {
    const entry = checksByName[check];
    if (entry) setRewards((current) => [...current, draftFor(entry, columnNames)]);
  };
  const updateReward = (id: string, patch: Partial<RewardDraft>) =>
    setRewards((current) => current.map((r) => (r.id === id ? { ...r, ...patch } : r)));
  const updateParam = (id: string, param: string, patch: Partial<ParamDraft>) =>
    setRewards((current) =>
      current.map((r) =>
        r.id === id
          ? { ...r, params: { ...r.params, [param]: { ...r.params[param], ...patch } } }
          : r
      )
    );

  const ready = !!datasetId && !!promptColumn && rewards.length > 0 && !create.isPending;

  const submit = () => {
    if (!ready || !datasetId) return;
    create.mutate(
      {
        baseModel: model,
        dataset: datasetId,
        hyperparameters: Object.fromEntries(Object.entries(hp).map(([k, v]) => [k, Number(v)])),
        name,
        project: projectId,
        promptColumn,
        requestKey: crypto.randomUUID(),
        rewards: rewards.map((r) => toRequest(r, checksByName[r.check]?.parameters ?? {})),
        toolsColumn,
      },
      {
        onSuccess: (run) => {
          onOpenChange(false);
          navigate({ params: { runId: run.id }, search: { projectId }, to: "/rl-training/$runId" });
        },
      }
    );
  };

  return (
    <Dialog onOpenChange={onOpenChange} open={open}>
      <DialogContent size="lg">
        <DialogHeader>
          <DialogTitle>Train from checks</DialogTitle>
          <DialogDescription>
            Sample attempts per prompt, score them with checks, and train a LoRA adapter toward the
            attempts that score higher. Held-out prompts are scored before and after.
          </DialogDescription>
        </DialogHeader>
        <DialogBody className="flex flex-col gap-5">
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Dataset">
              <Select onValueChange={setDatasetId} value={datasetId}>
                <SelectTrigger className="w-full" id="rl-dataset">
                  <SelectValue placeholder="Choose a dataset" />
                </SelectTrigger>
                <SelectContent>
                  {(datasets.data?.results ?? []).map((d) => (
                    <SelectItem key={d.id} value={d.id}>
                      {d.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Field>
            <Field label="Base model">
              <Select onValueChange={setModel} value={model}>
                <SelectTrigger className="w-full" id="rl-model">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {(catalog.data?.models ?? []).map((m) => (
                    <SelectItem key={m.id} value={m.id}>
                      {m.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Field>
            <Field label="Prompt column">
              <ColumnSelect
                columns={columnNames}
                id="rl-prompt"
                onChange={setPromptColumn}
                value={promptColumn}
              />
            </Field>
            <Field label="Tools column">
              <ColumnSelect
                allowNone
                columns={columnNames}
                id="rl-tools"
                onChange={setToolsColumn}
                value={toolsColumn}
              />
            </Field>
          </div>

          {columns.isFetching ? (
            <p className="flex items-center gap-2 text-sm text-muted-foreground">
              <Spinner size="sm" /> Reading the dataset's columns
            </p>
          ) : columns.data ? (
            <p className="text-xs text-muted-foreground">
              Version {columns.data.cell?.version} · {columns.data.cell?.rows} rows ·{" "}
              {columnNames.length} columns
            </p>
          ) : null}

          <div className="flex flex-col gap-2">
            <div className="flex items-center justify-between">
              <Label>Reward checks</Label>
              <Select onValueChange={addCheck} value="">
                <SelectTrigger className="w-48" disabled={!datasetId} id="rl-add-check">
                  <SelectValue placeholder="Add a check" />
                </SelectTrigger>
                <SelectContent>
                  {(catalog.data?.checks ?? []).map((c) => (
                    <SelectItem key={c.check} value={c.check}>
                      {stockCheckName(c.check)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            {rewards.length === 0 ? (
              <p className="rounded-md border border-border/70 bg-wash-subtle px-3 py-2 text-sm text-muted-foreground">
                {datasetId ? "Add at least one check." : "Choose a dataset first."}
              </p>
            ) : null}
            {rewards.map((reward) => (
              <div
                className="flex flex-col gap-2 rounded-md border border-border/70 bg-wash-subtle p-3"
                key={reward.id}
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="text-sm font-medium">{stockCheckName(reward.check)}</p>
                    <p className="text-xs text-muted-foreground">
                      {checksByName[reward.check]?.description}
                    </p>
                  </div>
                  <div className="flex shrink-0 items-center gap-2">
                    <Input
                      aria-label="Weight"
                      className="w-16"
                      id={`rl-weight-${reward.id}`}
                      onChange={(e) => updateReward(reward.id, { weight: e.target.value })}
                      value={reward.weight}
                    />
                    <Button
                      aria-label="Remove check"
                      onClick={() =>
                        setRewards((current) => current.filter((r) => r.id !== reward.id))
                      }
                      size="icon"
                      variant="ghost"
                    >
                      <Icon.close />
                    </Button>
                  </div>
                </div>
                {reward.check === "tool_selection" ? (
                  <ParamRow label="Reference tools">
                    <ColumnSelect
                      allowNone
                      columns={columnNames}
                      id={`rl-ref-${reward.id}`}
                      onChange={(column) => updateReward(reward.id, { referenceColumn: column })}
                      value={reward.referenceColumn}
                    />
                  </ParamRow>
                ) : null}
                {Object.entries(reward.params).map(([param, draft]) => (
                  <ParamRow key={param} label={param}>
                    <div className="flex min-w-0 gap-2">
                      <Select
                        onValueChange={(mode) =>
                          updateParam(reward.id, param, { mode: mode as ParamDraft["mode"] })
                        }
                        value={draft.mode}
                      >
                        <SelectTrigger
                          className="w-28 shrink-0"
                          id={`rl-mode-${reward.id}-${param}`}
                        >
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="column">Per row</SelectItem>
                          <SelectItem value="value">Fixed</SelectItem>
                        </SelectContent>
                      </Select>
                      {draft.mode === "column" ? (
                        <ColumnSelect
                          columns={columnNames}
                          id={`rl-col-${reward.id}-${param}`}
                          onChange={(column) => updateParam(reward.id, param, { column })}
                          value={draft.column}
                        />
                      ) : (
                        <Input
                          className="font-mono text-xs"
                          id={`rl-val-${reward.id}-${param}`}
                          onChange={(e) => updateParam(reward.id, param, { value: e.target.value })}
                          value={draft.value}
                        />
                      )}
                    </div>
                  </ParamRow>
                ))}
              </div>
            ))}
          </div>

          <div className="grid gap-3 sm:grid-cols-4">
            {NUMBER_FIELDS.map((field) => (
              <Field hint={field.hint} key={field.key} label={field.label}>
                <Input
                  className="font-mono"
                  id={`rl-hp-${field.key}`}
                  inputMode="decimal"
                  onChange={(e) =>
                    setHp((current) => ({ ...current, [field.key]: e.target.value }))
                  }
                  value={hp[field.key] ?? ""}
                />
              </Field>
            ))}
          </div>

          <Collapsible>
            <CollapsibleTrigger asChild>
              <Button size="sm" type="button" variant="secondary">
                <Icon.settings /> Advanced
              </Button>
            </CollapsibleTrigger>
            <CollapsibleContent className="mt-3 grid gap-3 sm:grid-cols-3">
              {ADVANCED_FIELDS.map((field) => (
                <Field key={field.key} label={field.label}>
                  <Input
                    className="font-mono"
                    id={`rl-hp-${field.key}`}
                    inputMode="decimal"
                    onChange={(e) =>
                      setHp((current) => ({ ...current, [field.key]: e.target.value }))
                    }
                    value={hp[field.key] ?? ""}
                  />
                </Field>
              ))}
              <Field label="Run name">
                <Input
                  id="rl-name"
                  onChange={(e) => setName(e.target.value)}
                  placeholder="Dataset · model"
                  value={name}
                />
              </Field>
            </CollapsibleContent>
          </Collapsible>
        </DialogBody>
        <DialogFooter>
          <span className="mr-auto flex items-center gap-2 text-xs text-muted-foreground">
            <Badge variant="info">H100</Badge> One Modal GPU · billed by the second
          </span>
          <Button onClick={() => onOpenChange(false)} type="button" variant="secondary">
            Cancel
          </Button>
          <Button disabled={!ready} onClick={submit} type="button">
            {create.isPending ? <Spinner size="sm" /> : <Icon.play />} Start training
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex min-w-0 flex-col gap-1.5">
      <Label title={hint}>{label}</Label>
      {children}
    </div>
  );
}

function ParamRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[9rem_minmax(0,1fr)] items-center gap-2">
      <span className="truncate font-mono text-xs text-muted-foreground">{label}</span>
      {children}
    </div>
  );
}

function ColumnSelect({
  id,
  columns,
  value,
  onChange,
  allowNone = false,
}: {
  id: string;
  columns: string[];
  value: string;
  onChange: (value: string) => void;
  allowNone?: boolean;
}) {
  return (
    <Select
      onValueChange={(v) => onChange(v === NO_COLUMN ? "" : v)}
      value={value || (allowNone ? NO_COLUMN : undefined)}
    >
      <SelectTrigger className="w-full min-w-0 flex-1" disabled={!columns.length} id={id}>
        <SelectValue placeholder="Column" />
      </SelectTrigger>
      <SelectContent>
        {allowNone ? <SelectItem value={NO_COLUMN}>None</SelectItem> : null}
        {columns.map((column) => (
          <SelectItem key={column} value={column}>
            {column}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}
