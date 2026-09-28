import type { EvaluationContextCheck } from "@/openapi";
import type { TrainWizard } from "./use-train-wizard";

export function trainingContextChecks(wizard: TrainWizard) {
  const checks = (wizard.contextQuery?.data?.checks ?? []).map((check) => {
    const option = wizard.contextQuery?.data?.judgeModels?.find(
      (option) => option.model === check.model
    );
    return check.role === "judge" && check.status === "fits" && option && option.status !== "fits"
      ? { ...check, status: option.status }
      : check;
  });
  if (wizard.evaluationPlan.evalModelAfter) {
    for (const draft of wizard.selectedDrafts) {
      const plan = wizard.candidateByModel?.get(draft.model)?.servingContext;
      if (!plan?.warnings?.length) continue;
      const index = checks.findIndex(
        (check) => check.role === "generation" && check.model === draft.model
      );
      const warning: EvaluationContextCheck = {
        affectedRows: 0,
        checkedRows: plan.rows,
        contextWindow: plan.maxModelLen,
        estimated: true,
        estimatedInputTokens: plan.inputTokens,
        label: draft.model,
        maxOutputTokens: null,
        message: plan.warnings.join(" "),
        model: draft.model,
        requiredContext: plan.requiredContext,
        reservedOutputTokens: plan.outputTokens,
        role: "generation",
        rowIndices: [],
        status: "warning",
      };
      if (index >= 0) checks[index] = warning;
      else checks.push(warning);
    }
  }
  return checks;
}

export function benchmarkContextStatus(wizard: TrainWizard, model: string) {
  if (!wizard.evaluationPlan.evalIncumbentBefore && !wizard.evaluationPlan.evalIncumbentAfter) {
    return undefined;
  }
  if (wizard.contextQuery?.isError) return "unknown";
  const check = wizard.contextQuery?.data?.checks.find(
    (check) => check.role === "generation" && check.model === wizard.benchmarkModel
  );
  if (!check) return undefined;
  if (model === check.model) return check.status;
  const deployment = wizard.benchmarksQuery.data?.find((item) => item.modelId === model);
  if (!check.checkedRows || !deployment || deployment.status !== "ready") return "unknown";
  return deployment.maxModelLen >= check.requiredContext ? "fits" : "warning";
}

export function contextStatusLabel(status: string) {
  return status === "fits"
    ? "Fits estimated context"
    : status === "warning"
      ? "Context too small"
      : "Context unverified";
}

export const WARNING_SELECT = "border-warning/40 bg-warning/10 text-warning hover:bg-warning/15";
export const MUTED_MODEL_OPTION = "text-muted-foreground focus:text-muted-foreground";
