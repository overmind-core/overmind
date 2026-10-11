import { useId, useRef, useState } from "react";

import { EntityRef } from "@/components/entity-ref";
import { Card } from "@/components/ui/card";
import { Icon } from "@/components/ui/icons";
import { SearchInput } from "@/components/ui/search-input";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { cn } from "@/lib/utils";
import type { FinetuningJob } from "@/openapi";

const LABELS: Record<string, string> = {
  accepted_findings: "Accepted findings",
  base_model: "Base model",
  baseline_model: "Baseline model",
  batch_size: "Batch size",
  checkpoint_policy: "Checkpoint policy",
  context_length: "Context length (tokens)",
  decision_pre_training_baseline: "Pre-training baseline",
  eval_cell: "Evaluation cell",
  eval_dataset: "Evaluation dataset",
  eval_incumbent_after: "Evaluate incumbent after training",
  eval_incumbent_before: "Evaluate incumbent before training",
  eval_judge_model: "Evaluation judge model",
  eval_model_after: "Evaluate model after training",
  eval_model_before: "Evaluate model before training",
  eval_rows: "Evaluation rows",
  eval_set: "Evaluation set",
  gpu_count: "GPU count",
  gpu_type: "GPU type",
  grad_accum: "Gradient accumulation steps",
  lora_alpha: "LoRA alpha",
  lora_dropout: "LoRA dropout",
  lora_r: "LoRA rank",
  lora_rank: "LoRA rank",
  lora_target_modules: "LoRA target modules",
  loss_sample: "Validation loss sample (rows)",
  max_gpus: "Maximum GPUs",
  max_length: "Context length (tokens)",
  n_epochs: "Epochs",
  overhead_fraction: "Monitoring time fraction",
  pack_rows: "Packing",
  per_device_batch: "Batch size per device",
  pre_training_baseline: "Pre-training baseline",
  target_seconds: "Target check interval (seconds)",
  train_cell: "Training cell",
  train_dataset: "Training dataset",
  train_rows: "Training rows",
  train_sample: "Training reference sample (rows)",
};

type ConfigurationRow = { path: string[]; label: string; value: unknown };
type ConfigurationSection = { title: string; source: string; rows: ConfigurationRow[] };

function object(value: unknown): Record<string, unknown> {
  return value != null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function label(key: string) {
  const normalized = key.toLowerCase();
  const words = normalized.replaceAll("_", " ");
  return LABELS[normalized] ?? words.charAt(0).toUpperCase() + words.slice(1);
}

function rows(value: unknown, path: string[] = []): ConfigurationRow[] {
  if (Array.isArray(value) && value.some((item) => item != null && typeof item === "object")) {
    return value.flatMap((item, index) => rows(item, [...path, `Item ${index + 1}`]));
  }
  const entries = Object.entries(object(value));
  if (entries.length) return entries.flatMap(([key, item]) => rows(item, [...path, key]));
  return path.length ? [{ label: path.map(label).join(" · "), path, value }] : [];
}

export function configurationValue(value: unknown): string {
  if (value === undefined) return "Not recorded";
  if (value === null) return "Not set";
  if (value === "") return "Empty";
  if (typeof value === "boolean") return value ? "Enabled" : "Disabled";
  if (Array.isArray(value)) return value.length ? value.map(configurationValue).join("\n") : "None";
  if (typeof value === "object") return JSON.stringify(value, null, 2);
  return String(value);
}

export function configurationSections(job: Partial<FinetuningJob>): ConfigurationSection[] {
  const record = object(job.record);
  const requested = object(job.requestedConfiguration ?? record.requested);
  const hasReceipt = Object.keys(requested).length > 0;
  const source = hasReceipt ? "Requested at launch" : "Job record";
  const config = hasReceipt
    ? object(requested.configuration)
    : {
        base_model: job.baseModel,
        baseline_model: job.baselineModel,
        benchmark_models: job.benchmarkModels,
        eval_incumbent_after: job.evalIncumbentAfter,
        eval_incumbent_before: job.evalIncumbentBefore,
        eval_judge_model: job.evalJudgeModel,
        eval_model_after: job.evalModelAfter,
        eval_model_before: job.evalModelBefore,
        hyperparameters: job.hyperparameters,
        split_method: job.splitMethod,
        validation_enabled: job.validationEnabled,
        validation_split_ratio: job.validationSplitRatio,
      };
  const { hyperparameters, ...setup } = config;
  const { monitoring, checkpoint_policy, ...parameters } = object(hyperparameters);
  const { configuration: _config, selection, contract, runtime, ...receipt } = requested;
  const sections: ConfigurationSection[] = [];
  const add = (title: string, values: unknown, origin = source) => {
    const entries = rows(values);
    if (entries.length) sections.push({ rows: entries, source: origin, title });
  };
  add("Model, validation and evaluation", setup);
  add("Training parameters", parameters);
  sections.push({
    rows: rows(record.effective),
    source: "Recorded at provider submission",
    title: "Effective configuration",
  });
  if (monitoring !== undefined) {
    add("Monitoring", Object.keys(object(monitoring)).length ? monitoring : { monitoring });
  }
  if (checkpoint_policy !== undefined) {
    add(
      "Checkpoints",
      Object.keys(object(checkpoint_policy)).length ? checkpoint_policy : { checkpoint_policy }
    );
  }
  add(
    "Data selection",
    hasReceipt
      ? selection
      : {
          eval_cell: job.evalCell,
          eval_dataset: job.evalDataset,
          eval_set: job.evalSet,
          train_cell: job.cell,
          train_dataset: job.dataset,
          validation_cell: job.validationCell,
          validation_dataset: job.validationDataset,
        }
  );
  add("Training contract", contract);
  add("Worker release", runtime);
  add("Launch receipt", receipt);
  add(
    "Run",
    {
      capability: job.capability,
      name: job.name,
      provider: job.provider,
      request_key: job.requestKey,
      use_case: job.useCase,
    },
    "Job record"
  );
  return sections;
}

export function RunConfiguration({
  job,
  projectId,
  datasetNames,
}: {
  job: FinetuningJob;
  projectId: string;
  datasetNames: ReadonlyMap<string, string>;
}) {
  const [expanded, setExpanded] = useState(false);
  const [search, setSearch] = useState("");
  const contentId = useId();
  const tableContainer = useRef<HTMLDivElement>(null);
  const sections = configurationSections(job);
  const terms = search.toLowerCase().trim().split(/\s+/).filter(Boolean);
  const matches = (text: string) => terms.every((term) => text.toLowerCase().includes(term));
  const visibleSections = terms.length
    ? sections.flatMap((section) => {
        const heading = `${section.title} ${section.source}`;
        if (!section.rows.length) return matches(`${heading} Not recorded`) ? [section] : [];
        const matchingRows = section.rows.filter((row) =>
          matches(
            `${heading} ${row.label} ${row.path.join(" ")} ${configurationValue(row.value)} ${datasetNames.get(String(row.value)) ?? ""}`
          )
        );
        return matchingRows.length ? [{ ...section, rows: matchingRows }] : [];
      })
    : sections;
  const updateSearch = (value: string) => {
    setSearch(value);
    if (tableContainer.current) tableContainer.current.scrollTop = 0;
  };

  return (
    <Card className="flex flex-col p-0">
      <button
        aria-controls={expanded ? contentId : undefined}
        aria-expanded={expanded}
        className="flex min-h-11 items-center gap-2 rounded-md px-4 py-2.5 text-left transition-colors hover:bg-wash-subtle focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        onClick={() => setExpanded((value) => !value)}
        type="button"
      >
        <Icon.settings className="size-4 shrink-0 text-muted-foreground" />
        <span className="shrink-0 text-xs font-medium leading-none">Run configuration</span>
        <Icon.chevronDown
          className={cn(
            "ml-auto size-4 shrink-0 text-muted-foreground transition-transform",
            expanded && "rotate-180"
          )}
        />
      </button>
      {expanded && (
        <div className="border-t border-border/70" id={contentId}>
          <div className="px-4 py-3">
            <SearchInput
              className="w-full sm:max-w-sm"
              label="Search run configuration"
              onChange={(event) => updateSearch(event.target.value)}
              onClear={() => updateSearch("")}
              placeholder="Search settings and values…"
              value={search}
            />
          </div>
          <Table
            aria-label="Run configuration"
            className="min-w-0 table-fixed text-xs"
            containerClassName="max-h-[32rem] border-t border-border/70"
            containerRef={tableContainer}
          >
            <TableHeader>
              <TableRow>
                <TableHead className="w-2/5 px-4">Setting</TableHead>
                <TableHead className="px-4">Value</TableHead>
              </TableRow>
            </TableHeader>
            {visibleSections.length === 0 && (
              <TableBody>
                <TableRow>
                  <TableCell className="px-4 py-6 text-center text-muted-foreground" colSpan={2}>
                    No matching settings
                  </TableCell>
                </TableRow>
              </TableBody>
            )}
            {visibleSections.map((section) => (
              <TableBody key={section.title}>
                <TableRow className="bg-wash-raised">
                  <TableHead
                    className="whitespace-normal bg-wash-raised px-4 py-2"
                    colSpan={2}
                    scope="rowgroup"
                  >
                    <span className="text-foreground">{section.title}</span>
                    <span className="ml-3 font-normal">{section.source}</span>
                  </TableHead>
                </TableRow>
                {section.rows.length ? (
                  section.rows.map((row) => (
                    <TableRow key={JSON.stringify(row.path)}>
                      <TableHead
                        className="whitespace-normal break-words bg-transparent px-4 py-2 align-top font-normal"
                        scope="row"
                      >
                        {row.label}
                      </TableHead>
                      <TableCell className="whitespace-pre-wrap break-words px-4 py-2 align-top font-mono tabular-nums [overflow-wrap:anywhere]">
                        {row.path.at(-1)?.endsWith("_dataset") &&
                        typeof row.value === "string" &&
                        row.value ? (
                          <EntityRef
                            className="text-xs"
                            id={row.value}
                            kind="dataset"
                            name={datasetNames.get(row.value)}
                            projectId={projectId}
                          />
                        ) : (
                          configurationValue(row.value)
                        )}
                      </TableCell>
                    </TableRow>
                  ))
                ) : (
                  <TableRow>
                    <TableCell className="px-4 py-3 text-muted-foreground" colSpan={2}>
                      Not recorded
                    </TableCell>
                  </TableRow>
                )}
              </TableBody>
            ))}
          </Table>
        </div>
      )}
    </Card>
  );
}
