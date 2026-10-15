import { useState } from "react";

import { MatrixRowDetail } from "@/components/evaluations/eval-matrix-table";
import { evaluationName } from "@/components/evaluations/evaluation-presentation";
import { Icon } from "@/components/ui/icons";
import type { EvalMatrix } from "@/openapi";
import { TrajectoryMetricScore } from "./TrajectoryRunScores";

export function TrajectoryEvaluationList({ rows, label }: { rows: EvalMatrix[]; label: string }) {
  const [viewing, setViewing] = useState<EvalMatrix | null>(null);
  if (!rows.length) return null;
  const content = (
    <>
      <p className="mb-2 px-1 text-xs text-muted-foreground">{label}</p>
      <div className="nodrag nopan flex flex-col gap-1.5">
        {rows.map((row) => (
          <button
            aria-label={evaluationName(row)}
            className="flex w-full items-center gap-3 rounded-sm border border-border bg-card px-3 py-3 text-left text-xs hover:bg-accent focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring focus-visible:-outline-offset-2"
            key={row.id}
            onClick={(event) => {
              event.stopPropagation();
              setViewing(row);
            }}
            type="button"
          >
            <Icon.evaluations className="size-3 shrink-0 text-muted-foreground" />
            <span className="min-w-0 flex-1">
              <span className="line-clamp-2 leading-5" title={evaluationName(row)}>
                {evaluationName(row)}
              </span>
            </span>
            <TrajectoryMetricScore name={row.name} />
            <span className="shrink-0 text-muted-foreground">
              {row.definitionSource === "stock" && "Stock · "}
              {row.type === "llm" ? "Judge" : "Check"}
            </span>
          </button>
        ))}
      </div>
    </>
  );
  return (
    <section aria-label={label}>
      {content}
      <div onClick={(event) => event.stopPropagation()} role="presentation">
        <MatrixRowDetail onClose={() => setViewing(null)} row={viewing} />
      </div>
    </section>
  );
}
