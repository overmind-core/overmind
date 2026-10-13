import { Badge } from "@/components/ui/badge";
import type { EvalMatrix } from "@/openapi";

export const MATRIX_TYPE_LABEL: Record<string, string> = {
  deterministic: "Deterministic",
  llm: "LLM as judge",
};

export function MatrixRowLabel({ row }: { row: EvalMatrix }) {
  return (
    <>
      <span className="min-w-0 flex-1 truncate">{row.name}</span>
      <Badge size="chip" variant="neutral">
        {MATRIX_TYPE_LABEL[row.type] ?? row.type}
      </Badge>
      {row.spanType && (
        <span className="font-mono text-xs text-muted-foreground">{row.spanType}</span>
      )}
      {!row.capability && <span className="text-xs text-muted-foreground">Project-wide</span>}
      {row.enabled === false && (
        <Badge size="chip" variant="neutral">
          Disabled
        </Badge>
      )}
    </>
  );
}
