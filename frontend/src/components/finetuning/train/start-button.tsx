import { useState } from "react";

import { getModelProviderInfo } from "@/components/model-provider";
import { Button } from "@/components/ui/button";
import { Icon } from "@/components/ui/icons";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Spinner } from "@/components/ui/spinner";
import { count } from "@/lib/formatters";
import { TITLE } from "@/lib/typography";
import { trainingContextChecks } from "./context-checks";
import type { TrainWizard } from "./use-train-wizard";

export function TrainingStartButton({ wizard }: { wizard: TrainWizard }) {
  const warnings = trainingContextChecks(wizard).filter((check) => check.status === "warning");
  const summaries = new Map<string, string>();
  for (const check of warnings) {
    const key = `${check.role}:${check.model}`;
    const label =
      check.role === "judge"
        ? `Judge · ${getModelProviderInfo(check.model).modelLabel}`
        : check.model === wizard.benchmarkModel
          ? "Benchmark model"
          : (wizard.candidateByModel?.get(check.model)?.displayName ?? check.model);
    summaries.set(key, label);
  }
  const disabled = !wizard.canLaunch || wizard.launching;
  const label = wizard.launching
    ? "Starting"
    : wizard.selectedDrafts.length > 1
      ? `Start ${count(wizard.selectedDrafts.length, "experiment")}`
      : "Start training";
  return (
    <EvaluationStartButton
      disabled={disabled}
      label={label}
      onStart={() => void wizard.launch()}
      pending={wizard.launching}
      title={wizard.launchBlocker ?? undefined}
      warnings={[...summaries.values()]}
    />
  );
}

export function EvaluationStartButton({
  label,
  warnings,
  disabled,
  pending,
  title,
  onStart,
}: {
  label: string;
  warnings: string[];
  disabled: boolean;
  pending: boolean;
  title?: string;
  onStart: () => void;
}) {
  const [open, setOpen] = useState(false);
  const hasWarning = warnings.length > 0;
  const start = () => {
    setOpen(false);
    onStart();
  };
  const button = (
    <Button
      disabled={disabled}
      onClick={hasWarning ? undefined : start}
      title={title}
      type="button"
      variant={hasWarning ? "warning" : "default"}
    >
      {pending ? (
        <Spinner className="text-current" size="sm" />
      ) : hasWarning ? (
        <Icon.warning />
      ) : (
        <Icon.play />
      )}
      {label}
    </Button>
  );
  if (!hasWarning) return button;
  return (
    <Popover onOpenChange={setOpen} open={open}>
      <PopoverTrigger asChild>{button}</PopoverTrigger>
      <PopoverContent
        align="end"
        aria-label="Evaluation warning"
        className="w-80 max-w-[calc(100vw-2rem)] space-y-3 p-4"
        side="top"
      >
        <div className="space-y-2" role="alert">
          <p className={TITLE.card}>Evaluations may fail</p>
          <ul className="max-h-48 space-y-2 overflow-y-auto text-sm">
            {warnings.map((label) => (
              <li key={label}>{label}: some rows may exceed its limits.</li>
            ))}
          </ul>
        </div>
        <div className="flex justify-end gap-2">
          <Button onClick={() => setOpen(false)} type="button" variant="secondary">
            Cancel
          </Button>
          <Button disabled={disabled} onClick={start} type="button" variant="warning">
            Start anyway
          </Button>
        </div>
      </PopoverContent>
    </Popover>
  );
}
