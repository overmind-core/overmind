import { Icon } from "@/components/ui/icons";
import type { EvalMatrixTypeEnum } from "@/openapi";

export function EvaluationMethodBadge({ type }: { type: EvalMatrixTypeEnum }) {
  const isJudge = type === "llm";
  return (
    <span
      className={`chip-label inline-flex h-6 items-center gap-1.5 rounded-sm border px-2 text-xs ${
        isJudge
          ? "border-cat-4/40 bg-cat-4/10 text-cat-4"
          : "border-cat-1/40 bg-cat-1/10 text-cat-1"
      }`}
    >
      {isJudge ? <Icon.eval className="size-3" /> : <Icon.code className="size-3" />}
      {isJudge ? "Model judge" : "Code check"}
    </span>
  );
}
