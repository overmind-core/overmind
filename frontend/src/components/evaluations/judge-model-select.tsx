import { CostComparison } from "@/components/evaluations/cost-comparison";
import {
  contextStatusLabel,
  MUTED_MODEL_OPTION,
  WARNING_SELECT,
} from "@/components/finetuning/train/context-checks";
import { Field } from "@/components/finetuning/train/field";
import { ModelOptionLabel, modelOptionName } from "@/components/model-option-label";
import { Icon } from "@/components/ui/icons";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Spinner } from "@/components/ui/spinner";
import { cn } from "@/lib/utils";
import type { EvaluationContextReport } from "@/openapi";

const SET_DEFAULT = "__eval_set__";

export function JudgeModelSelect({
  report,
  value,
  onChange,
  disabled = false,
  checking = false,
  id = "eval-judge-model",
  className,
}: {
  report?: EvaluationContextReport;
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
  checking?: boolean;
  id?: string;
  className?: string;
}) {
  const checks = report?.checks.filter((check) => check.role === "judge") ?? [];
  const configured = [...new Set(checks.map((check) => check.configuredModel || check.model))];
  const options = report?.judgeModels ?? [];
  const defaultLabel =
    configured.length === 1
      ? modelOptionName(
          configured[0],
          options.find((option) => option.model === configured[0])?.name
        )
      : configured.length > 1
        ? "Mixed models"
        : "Eval set default";
  const selected = options.find((option) => option.model === value);
  const defaultContent =
    configured.length === 1 ? (
      <ModelOptionLabel model={configured[0]} name={defaultLabel} />
    ) : (
      <span className="truncate">{defaultLabel}</span>
    );
  const status =
    selected?.status ??
    (checks.length
      ? checks.some((check) => check.status === "warning")
        ? "warning"
        : checks.every((check) => check.status === "fits")
          ? "fits"
          : "unknown"
      : "unknown");
  const needsReview = status === "warning";
  const defaultNeedsReview = !value
    ? needsReview
    : configured.some(
        (model) => options.find((option) => option.model === model)?.status === "warning"
      );
  return (
    <Field
      className={className}
      hint="Generative judge for this run. The saved eval set is unchanged."
      htmlFor={id}
      label="Judge model"
    >
      <Select
        disabled={disabled}
        onValueChange={(value) => onChange(value === SET_DEFAULT ? "" : value)}
        value={value || SET_DEFAULT}
      >
        <SelectTrigger className={cn("w-full", needsReview && WARNING_SELECT)} id={id}>
          <SelectValue className="min-w-0 flex-1">
            {value ? <ModelOptionLabel model={value} name={selected?.name} /> : defaultContent}
          </SelectValue>
          {needsReview && <Icon.warning className="size-3 text-warning" />}
        </SelectTrigger>
        <SelectContent align="start" className="w-112 max-w-[calc(100vw-2rem)]" position="popper">
          <p className="max-w-80 px-2 py-1.5 text-xs text-muted-foreground">
            USD budget · all judges · one dataset pass per evaluated model · reserved output.
            Excludes retries and caching.
          </p>
          <SelectItem
            className={defaultNeedsReview ? MUTED_MODEL_OPTION : undefined}
            textValue={`${defaultLabel} Eval set`}
            value={SET_DEFAULT}
          >
            {defaultContent} <span className="text-xs text-muted-foreground">Eval set</span>
          </SelectItem>
          {options.map((option) => (
            <SelectItem
              className={cn(
                "[&>span:last-child]:min-w-0 [&>span:last-child]:flex-1",
                option.status === "warning" && MUTED_MODEL_OPTION
              )}
              key={option.model}
              textValue={modelOptionName(option.model, option.name)}
              value={option.model}
            >
              <ModelOptionLabel model={option.model} name={option.name}>
                <span className="ml-auto flex shrink-0 flex-col items-end text-xs">
                  <span
                    className={option.status === "fits" ? "text-success" : "text-muted-foreground"}
                  >
                    {contextStatusLabel(option.status)}
                  </span>
                  <span className="text-muted-foreground">
                    <CostComparison cost={option.estimatedCostUsd} delta={option.costDeltaUsd} />
                  </span>
                </span>
              </ModelOptionLabel>
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      {checking ? (
        <span className="flex items-center gap-1 text-xs text-muted-foreground">
          <Spinner size="sm" />
          Checking context
        </span>
      ) : (
        <p className="text-xs text-muted-foreground">
          {!checks.length
            ? "No generative judge estimate"
            : value
              ? "This run only"
              : "From eval set"}
        </p>
      )}
    </Field>
  );
}
