import { ModelProviderChip } from "@/components/model-provider-chip";
import type { RunEvaluator } from "@/openapi";

export function RunJudges({ evaluators }: { evaluators: RunEvaluator[] }) {
  const models = [
    ...new Set(
      evaluators
        .filter((row) => row.enabled)
        .flatMap((row) => {
          const snapshot = row.snapshot;
          if (!snapshot || typeof snapshot !== "object") return [];
          const judged =
            snapshot.kind === "llm_judge" ||
            snapshot.kind === "agentic" ||
            (snapshot.kind === "trajectory" && snapshot.config?.mode === "judge");
          return judged
            ? [typeof snapshot.judge_model === "string" ? snapshot.judge_model : ""]
            : [];
        })
    ),
  ];
  if (!models.length) return null;
  return (
    <div className="flex min-w-0 flex-col gap-2">
      <h3 className="text-xs text-foreground">{models.length === 1 ? "Judge" : "Judges"}</h3>
      <div className="flex flex-wrap gap-2">
        {models.map((model) =>
          model ? (
            <ModelProviderChip key={model} model={model} />
          ) : (
            <span className="text-sm text-muted-foreground" key="automatic">
              Automatic judge
            </span>
          )
        )}
      </div>
    </div>
  );
}
